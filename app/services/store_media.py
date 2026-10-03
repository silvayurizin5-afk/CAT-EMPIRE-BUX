from __future__ import annotations

import asyncio
import hashlib
import io
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import urlparse

import discord
import httpx
from PIL import Image, ImageSequence

from app.core.network_security import PublicHTTPError, download_public_http_bytes

_MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024
_PREFERRED_PANEL_BYTES = 3 * 1024 * 1024
_PREFERRED_THUMBNAIL_BYTES = 2 * 1024 * 1024
_CACHE_SIZE = 4
_MAX_IMAGE_PIXELS = 24_000_000
_MAX_ANIMATION_FRAMES = 500
_CACHE: OrderedDict[tuple[str, int], PreparedStoreBanner] = OrderedDict()
_THUMBNAIL_CACHE: OrderedDict[tuple[str, int], PreparedStoreBanner] = OrderedDict()


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


def banner_attachment_prefix(source_url: str) -> str:
    digest = hashlib.sha256(source_url.encode("utf-8")).hexdigest()[:12]
    return f"nextbuy-banner-{digest}"


def thumbnail_attachment_prefix(source_url: str) -> str:
    digest = hashlib.sha256(source_url.encode("utf-8")).hexdigest()[:12]
    return f"nextbuy-thumbnail-{digest}"


def reusable_banner_attachment_url(
    source_url: str | None,
    attachments: list[discord.Attachment] | tuple[discord.Attachment, ...],
) -> str | None:
    if not source_url or not _looks_like_gif(source_url):
        return None
    prefix = banner_attachment_prefix(source_url)
    for attachment in attachments:
        if attachment.filename in {f"{prefix}.gif", f"{prefix}.webp"}:
            return f"attachment://{attachment.filename}"
    return None


def reusable_thumbnail_attachment_url(
    source_url: str | None,
    attachments: list[discord.Attachment] | tuple[discord.Attachment, ...],
) -> str | None:
    if not source_url:
        return None
    prefix = thumbnail_attachment_prefix(source_url)
    for attachment in attachments:
        if attachment.filename.startswith(f"{prefix}."):
            return f"attachment://{attachment.filename}"
    return None


def is_gif_banner_url(url: str | None) -> bool:
    return _looks_like_gif(url)


def _looks_like_gif(url: str | None) -> bool:
    if not url:
        return False
    try:
        path = PurePosixPath(urlparse(url).path)
    except ValueError:
        return False
    return path.suffix.casefold() == ".gif"


async def _download_image(url: str) -> bytes:
    timeout = httpx.Timeout(25.0, connect=10.0)
    headers = {"User-Agent": "NEXTBUY/1.0 DiscordBot"}
    try:
        return await download_public_http_bytes(
            url,
            max_bytes=_MAX_DOWNLOAD_BYTES,
            timeout=timeout,
            headers=headers,
            require_content_type_prefix="image/",
        )
    except PublicHTTPError as exc:
        raise StoreBannerError(
            "Não consegui baixar um banner público válido pela URL informada."
        ) from exc


def _open_animated(data: bytes) -> Image.Image:
    try:
        image = Image.open(io.BytesIO(data))
        image.seek(0)
    except (OSError, ValueError) as exc:
        raise StoreBannerError("O arquivo do banner não é uma imagem válida.") from exc
    if image.width * image.height > _MAX_IMAGE_PIXELS:
        raise StoreBannerError("O banner possui resolução excessiva.")
    frame_count = int(getattr(image, "n_frames", 1) or 1)
    if frame_count > _MAX_ANIMATION_FRAMES:
        raise StoreBannerError("O banner possui quadros demais para processamento seguro.")
    if not getattr(image, "is_animated", False) or frame_count <= 1:
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


def _inspect_image(data: bytes) -> tuple[str | None, bool]:
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.seek(0)
            if image.width * image.height > _MAX_IMAGE_PIXELS:
                raise StoreBannerError("A imagem possui resolução excessiva.")
            frame_count = int(getattr(image, "n_frames", 1) or 1)
            if frame_count > _MAX_ANIMATION_FRAMES:
                raise StoreBannerError("A imagem possui quadros demais para processamento seguro.")
            image.load()
            image_format = (image.format or "").upper()
            animated = bool(getattr(image, "is_animated", False) and frame_count > 1)
    except (OSError, ValueError) as exc:
        raise StoreBannerError("O arquivo informado não é uma imagem válida.") from exc

    extensions = {
        "PNG": "png",
        "JPEG": "jpg",
        "JPG": "jpg",
        "WEBP": "webp",
        "GIF": "gif",
    }
    return extensions.get(image_format), animated


def _render_static_webp(data: bytes, *, scale: float, quality: int) -> bytes:
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.seek(0)
            frame = image.convert("RGBA")
    except (OSError, ValueError) as exc:
        raise StoreBannerError("O arquivo informado não é uma imagem válida.") from exc

    width = max(1, int(frame.width * scale))
    height = max(1, int(frame.height * scale))
    if frame.size != (width, height):
        frame = frame.resize((width, height), Image.Resampling.LANCZOS)

    output = io.BytesIO()
    frame.save(output, format="WEBP", quality=quality, method=6)
    return output.getvalue()


def _optimize_static(data: bytes, *, target_bytes: int) -> bytes:
    attempts = (
        (1.00, 88),
        (0.90, 82),
        (0.80, 76),
        (0.70, 70),
        (0.60, 62),
        (0.50, 54),
        (0.40, 46),
        (0.32, 38),
    )
    smallest: bytes | None = None
    for scale, quality in attempts:
        encoded = _render_static_webp(data, scale=scale, quality=quality)
        if smallest is None or len(encoded) < len(smallest):
            smallest = encoded
        if len(encoded) <= target_bytes:
            return encoded

    if smallest is None or len(smallest) > target_bytes:
        raise StoreBannerError(
            "Não consegui reduzir a imagem o suficiente para o limite de upload deste servidor."
        )
    return smallest


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
    # Mantém o banner pequeno mesmo quando o servidor aceita uploads maiores.
    # Isso reduz bastante o tempo de publicação/edição do painel.
    discord_cap = max(512 * 1024, int(upload_limit) - 256 * 1024)
    target_bytes = min(_PREFERRED_PANEL_BYTES, discord_cap)
    cache_key = (source_url, target_bytes)
    cached = _CACHE.get(cache_key)
    if cached is not None:
        _CACHE.move_to_end(cache_key)
        return cached

    raw = await _download_image(source_url)
    _open_animated(raw)

    prefix = banner_attachment_prefix(source_url)

    if len(raw) <= target_bytes:
        prepared = PreparedStoreBanner(
            data=raw,
            filename=f"{prefix}.gif",
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
            filename=f"{prefix}.webp",
            optimized=True,
            source_size=len(raw),
        )

    _CACHE[cache_key] = prepared
    _CACHE.move_to_end(cache_key)
    while len(_CACHE) > _CACHE_SIZE:
        _CACHE.popitem(last=False)
    return prepared

async def prepare_store_thumbnail(
    source_url: str | None,
    *,
    upload_limit: int,
) -> PreparedStoreBanner | None:
    """Baixa e estabiliza a thumbnail como attachment do Discord."""

    if not source_url:
        return None

    discord_cap = max(512 * 1024, int(upload_limit) - 256 * 1024)
    target_bytes = min(_PREFERRED_THUMBNAIL_BYTES, discord_cap)
    cache_key = (source_url, target_bytes)
    cached = _THUMBNAIL_CACHE.get(cache_key)
    if cached is not None:
        _THUMBNAIL_CACHE.move_to_end(cache_key)
        return cached

    raw = await _download_image(source_url)
    extension, animated = _inspect_image(raw)
    prefix = thumbnail_attachment_prefix(source_url)
    encoded = raw
    optimized = False

    if extension is None or len(raw) > target_bytes:
        if animated:
            encoded = await asyncio.to_thread(_optimize_gif, raw, target_bytes=target_bytes)
        else:
            encoded = await asyncio.to_thread(_optimize_static, raw, target_bytes=target_bytes)
        extension = "webp"
        optimized = True

    prepared = PreparedStoreBanner(
        data=encoded,
        filename=f"{prefix}.{extension}",
        optimized=optimized,
        source_size=len(raw),
    )
    _THUMBNAIL_CACHE[cache_key] = prepared
    _THUMBNAIL_CACHE.move_to_end(cache_key)
    while len(_THUMBNAIL_CACHE) > _CACHE_SIZE:
        _THUMBNAIL_CACHE.popitem(last=False)
    return prepared