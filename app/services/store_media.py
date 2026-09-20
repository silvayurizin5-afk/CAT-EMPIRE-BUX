from __future__ import annotations

import asyncio
import io
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import urlparse

import discord
import httpx
from PIL import Image, ImageSequence

_MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024
_CACHE_SIZE = 4
_CACHE: OrderedDict[tuple[str, int], PreparedStoreBanner] = OrderedDict()


class StoreBannerError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PreparedStoreBanner:
    data: bytes
    filename: str
    optimized: bool
    source_size: int

    @property
    def attachment_url(self) -> str:
        return f"attachment://{self.filename}"

    def to_file(self) -> discord.File:
        return discord.File(io.BytesIO(self.data), filename=self.filename)


def _looks_like_gif(url: str | None) -> bool:
    if not url:
        return False
    try:
        path = PurePosixPath(urlparse(url).path)
    except ValueError:
        return False
    return path.suffix.casefold() == ".gif"


async def _download_image(url: str) -> bytes:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise StoreBannerError("A URL do banner precisa ser HTTP/HTTPS.")

    timeout = httpx.Timeout(25.0, connect=10.0)
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
                content_type = (response.headers.get("content-type") or "").casefold()
                if content_type and not content_type.startswith("image/"):
                    raise StoreBannerError("A URL informada não retornou uma imagem.")
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > _MAX_DOWNLOAD_BYTES:
                        raise StoreBannerError(
                            "O GIF ultrapassa 64 MB. Use uma origem menor para o banner."
                        )
    except httpx.HTTPError as exc:
        raise StoreBannerError("Não consegui baixar o banner pela URL informada.") from exc

    if not data:
        raise StoreBannerError("O banner retornou um arquivo vazio.")
    return bytes(data)


def _open_animated(data: bytes) -> Image.Image:
    try:
        image = Image.open(io.BytesIO(data))
        image.seek(0)
    except (OSError, ValueError) as exc:
        raise StoreBannerError("O arquivo do banner não é uma imagem válida.") from exc
    if not getattr(image, "is_animated", False) or getattr(image, "n_frames", 1) <= 1:
        raise StoreBannerError("O arquivo informado não é um GIF animado.")
    return image


def _render_webp(
    data: bytes,
    *,
    scale: float,
    quality: int,
    frame_step: int,
) -> bytes:
    image = _open_animated(data)
    source_frames: list[Image.Image] = []
    durations: list[int] = []
    for frame in ImageSequence.Iterator(image):
        durations.append(
            max(20, int(frame.info.get("duration", image.info.get("duration", 100)) or 100))
        )
        source_frames.append(frame.convert("RGBA").copy())

    frames: list[Image.Image] = []
    output_durations: list[int] = []
    width = max(1, int(image.width * scale))
    height = max(1, int(image.height * scale))

    for index in range(0, len(source_frames), frame_step):
        frame = source_frames[index]
        if frame.size != (width, height):
            frame = frame.resize((width, height), Image.Resampling.LANCZOS)
        frames.append(frame)

        duration = sum(durations[index : index + frame_step])
        output_durations.append(max(20, duration))

    if not frames:
        raise StoreBannerError("O GIF não possui quadros válidos.")

    output = io.BytesIO()
    frames[0].save(
        output,
        format="WEBP",
        save_all=True,
        append_images=frames[1:],
        duration=output_durations,
        loop=int(image.info.get("loop", 0) or 0),
        quality=quality,
        method=6,
    )
    return output.getvalue()


def _optimize_gif(data: bytes, *, target_bytes: int) -> bytes:
    # WebP animado costuma ficar muito menor que GIF mantendo o movimento.
    attempts = (
        (1.00, 82, 1),
        (0.90, 76, 1),
        (0.80, 70, 1),
        (0.70, 64, 1),
        (0.65, 60, 2),
        (0.55, 54, 2),
        (0.50, 48, 2),
        (0.45, 42, 3),
        (0.40, 36, 3),
    )
    smallest: bytes | None = None
    for scale, quality, frame_step in attempts:
        encoded = _render_webp(
            data,
            scale=scale,
            quality=quality,
            frame_step=frame_step,
        )
        if smallest is None or len(encoded) < len(smallest):
            smallest = encoded
        if len(encoded) <= target_bytes:
            return encoded

    if smallest is None or len(smallest) > target_bytes:
        raise StoreBannerError(
            "Não consegui reduzir o GIF o suficiente para o limite de upload deste servidor."
        )
    return smallest


async def prepare_store_banner(
    source_url: str | None,
    *,
    upload_limit: int,
) -> PreparedStoreBanner | None:
    """Prepara GIF da loja como attachment estável do Discord.

    Imagens não-GIF continuam por URL e retornam None.
    """

    if not _looks_like_gif(source_url):
        return None

    assert source_url is not None
    # Reserva uma pequena margem para o multipart/Discord.
    target_bytes = max(2 * 1024 * 1024, int(upload_limit) - 256 * 1024)
    cache_key = (source_url, target_bytes)
    cached = _CACHE.get(cache_key)
    if cached is not None:
        _CACHE.move_to_end(cache_key)
        return cached

    raw = await _download_image(source_url)
    _open_animated(raw)

    if len(raw) <= target_bytes:
        prepared = PreparedStoreBanner(
            data=raw,
            filename="nextbuy-banner.gif",
            optimized=False,
            source_size=len(raw),
        )
    else:
        encoded = await asyncio.to_thread(
            _optimize_gif,
            raw,
            target_bytes=target_bytes,
        )
        prepared = PreparedStoreBanner(
            data=encoded,
            filename="nextbuy-banner.webp",
            optimized=True,
            source_size=len(raw),
        )

    _CACHE[cache_key] = prepared
    _CACHE.move_to_end(cache_key)
    while len(_CACHE) > _CACHE_SIZE:
        _CACHE.popitem(last=False)
    return prepared
