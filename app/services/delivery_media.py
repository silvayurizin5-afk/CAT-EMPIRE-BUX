from __future__ import annotations

import asyncio
import hashlib
import io
from collections import OrderedDict
from dataclasses import dataclass
from urllib.parse import urlparse

import discord
import httpx
from PIL import Image, ImageSequence

_MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024
_PREFERRED_DELIVERY_BYTES = 6 * 1024 * 1024
_CACHE_SIZE = 8
_CACHE: OrderedDict[tuple[str, int], "PreparedDeliveryMedia"] = OrderedDict()


class DeliveryMediaError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PreparedDeliveryMedia:
    data: bytes
    filename: str
    source_url: str
    animated: bool
    normalized: bool

    @property
    def attachment_url(self) -> str:
        return f"attachment://{self.filename}"

    def to_file(self) -> discord.File:
        return discord.File(io.BytesIO(self.data), filename=self.filename)


def validate_media_url(value: str | None) -> str:
    raw = str(value or "").strip()
    try:
        parsed = urlparse(raw)
    except ValueError as exc:
        raise DeliveryMediaError("URL de banner inválida.") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise DeliveryMediaError("O banner aceita qualquer URL HTTP/HTTPS válida.")
    return raw


async def _download_media(url: str) -> bytes:
    timeout = httpx.Timeout(30.0, connect=10.0)
    headers = {"User-Agent": "NEXTBUY/1.0 DiscordBot"}
    data = bytearray()

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=timeout,
            headers=headers,
        ) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > _MAX_DOWNLOAD_BYTES:
                        raise DeliveryMediaError(
                            "A mídia ultrapassa 64 MB e não pode ser processada pelo bot."
                        )
    except httpx.HTTPError as exc:
        raise DeliveryMediaError("Não consegui baixar a mídia dessa URL.") from exc

    if not data:
        raise DeliveryMediaError("A URL retornou um arquivo vazio.")
    return bytes(data)


def _open_image(data: bytes) -> Image.Image:
    try:
        image = Image.open(io.BytesIO(data))
        image.seek(0)
        return image
    except (OSError, ValueError) as exc:
        raise DeliveryMediaError(
            "A URL não retornou uma imagem reconhecida. O bot tentará usá-la diretamente."
        ) from exc


def _coalesced_frames(data: bytes) -> tuple[list[Image.Image], list[int], int]:
    """Recompõe frames sobre o anterior para evitar flashes/frames vazios do GIF."""

    image = _open_image(data)
    frame_count = int(getattr(image, "n_frames", 1) or 1)
    if frame_count <= 1:
        return [image.convert("RGBA").copy()], [100], 0

    frames: list[Image.Image] = []
    durations: list[int] = []
    canvas = Image.new("RGBA", image.size, (0, 0, 0, 0))

    for frame in ImageSequence.Iterator(image):
        duration = max(
            20,
            int(frame.info.get("duration", image.info.get("duration", 100)) or 100),
        )
        rgba = frame.convert("RGBA")

        # Alguns GIFs limpam o canvas entre frames por disposal/transparência.
        # Compor sobre o último quadro elimina o efeito de "aparecer e sumir".
        canvas = Image.alpha_composite(canvas, rgba)
        frames.append(canvas.copy())
        durations.append(duration)

    loop = int(image.info.get("loop", 0) or 0)
    return frames, durations, loop


def _encode_animated_webp(
    data: bytes,
    *,
    target_bytes: int,
) -> bytes:
    frames, durations, loop = _coalesced_frames(data)
    if len(frames) <= 1:
        raise DeliveryMediaError("A mídia não é animada.")

    attempts = (
        (1.00, 88, 1),
        (0.92, 82, 1),
        (0.84, 76, 1),
        (0.76, 70, 1),
        (0.68, 64, 1),
        (0.62, 60, 2),
        (0.56, 54, 2),
        (0.50, 48, 2),
        (0.44, 42, 3),
    )

    smallest: bytes | None = None
    base_width, base_height = frames[0].size

    for scale, quality, frame_step in attempts:
        width = max(1, int(base_width * scale))
        height = max(1, int(base_height * scale))
        selected: list[Image.Image] = []
        selected_durations: list[int] = []

        for index in range(0, len(frames), frame_step):
            frame = frames[index]
            if frame.size != (width, height):
                frame = frame.resize((width, height), Image.Resampling.LANCZOS)
            selected.append(frame)
            selected_durations.append(sum(durations[index : index + frame_step]))

        output = io.BytesIO()
        selected[0].save(
            output,
            format="WEBP",
            save_all=True,
            append_images=selected[1:],
            duration=selected_durations,
            loop=loop,
            quality=quality,
            method=6,
        )
        encoded = output.getvalue()
        if smallest is None or len(encoded) < len(smallest):
            smallest = encoded
        if len(encoded) <= target_bytes:
            return encoded

    if smallest is None or len(smallest) > target_bytes:
        raise DeliveryMediaError(
            "Não consegui otimizar a animação para o limite de upload do servidor."
        )
    return smallest


def _encode_static_webp(data: bytes, *, target_bytes: int) -> bytes:
    image = _open_image(data).convert("RGBA")
    attempts = ((1.00, 92), (0.90, 86), (0.80, 80), (0.70, 74), (0.60, 68))
    smallest: bytes | None = None

    for scale, quality in attempts:
        frame = image
        if scale != 1.0:
            frame = image.resize(
                (
                    max(1, int(image.width * scale)),
                    max(1, int(image.height * scale)),
                ),
                Image.Resampling.LANCZOS,
            )
        output = io.BytesIO()
        frame.save(output, format="WEBP", quality=quality, method=6)
        encoded = output.getvalue()
        if smallest is None or len(encoded) < len(smallest):
            smallest = encoded
        if len(encoded) <= target_bytes:
            return encoded

    if smallest is None or len(smallest) > target_bytes:
        raise DeliveryMediaError(
            "Não consegui otimizar a imagem para o limite de upload do servidor."
        )
    return smallest


async def prepare_delivery_media(
    source_url: str,
    *,
    upload_limit: int,
    normalize_animation: bool = True,
) -> PreparedDeliveryMedia:
    url = validate_media_url(source_url)
    discord_cap = max(512 * 1024, int(upload_limit) - 256 * 1024)
    target_bytes = min(_PREFERRED_DELIVERY_BYTES, discord_cap)
    cache_key = (url, target_bytes if normalize_animation else -target_bytes)
    cached = _CACHE.get(cache_key)
    if cached is not None:
        _CACHE.move_to_end(cache_key)
        return cached

    raw = await _download_media(url)
    image = _open_image(raw)
    animated = bool(
        getattr(image, "is_animated", False)
        and int(getattr(image, "n_frames", 1) or 1) > 1
    )
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]

    if animated and normalize_animation:
        encoded = await asyncio.to_thread(
            _encode_animated_webp,
            raw,
            target_bytes=target_bytes,
        )
        prepared = PreparedDeliveryMedia(
            data=encoded,
            filename=f"nextbuy-entrega-{digest}.webp",
            source_url=url,
            animated=True,
            normalized=True,
        )
    elif animated and len(raw) <= target_bytes:
        prepared = PreparedDeliveryMedia(
            data=raw,
            filename=f"nextbuy-entrega-{digest}.gif",
            source_url=url,
            animated=True,
            normalized=False,
        )
    else:
        encoded = await asyncio.to_thread(
            _encode_static_webp,
            raw,
            target_bytes=target_bytes,
        )
        prepared = PreparedDeliveryMedia(
            data=encoded,
            filename=f"nextbuy-entrega-{digest}.webp",
            source_url=url,
            animated=False,
            normalized=True,
        )

    _CACHE[cache_key] = prepared
    _CACHE.move_to_end(cache_key)
    while len(_CACHE) > _CACHE_SIZE:
        _CACHE.popitem(last=False)
    return prepared
