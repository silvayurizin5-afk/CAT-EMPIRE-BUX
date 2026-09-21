import io

from PIL import Image

from app.services.delivery_media import (
    _coalesced_frames,
    _encode_animated_webp,
    validate_media_url,
)


def _animated_gif() -> bytes:
    frames = [
        Image.new("RGB", (80, 40), (123, 44, 191)),
        Image.new("RGB", (80, 40), (80, 20, 140)),
        Image.new("RGB", (80, 40), (20, 20, 20)),
    ]
    output = io.BytesIO()
    frames[0].save(
        output,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=[100, 120, 140],
        loop=0,
    )
    return output.getvalue()


def test_banner_url_accepts_any_http_extension_or_query() -> None:
    assert validate_media_url("https://example.com/image.png") == (
        "https://example.com/image.png"
    )
    assert validate_media_url("https://example.com/file?id=123&format=gif") == (
        "https://example.com/file?id=123&format=gif"
    )
    assert validate_media_url("https://cdn.discordapp.com/a.webp?ex=123") == (
        "https://cdn.discordapp.com/a.webp?ex=123"
    )


def test_coalescing_preserves_animation_frames_and_durations() -> None:
    frames, durations, loop = _coalesced_frames(_animated_gif())
    assert len(frames) == 3
    assert durations == [100, 120, 140]
    assert loop == 0
    assert all(frame.getbbox() is not None for frame in frames)


def test_animation_is_reencoded_as_animated_webp() -> None:
    encoded = _encode_animated_webp(
        _animated_gif(),
        target_bytes=500_000,
    )
    image = Image.open(io.BytesIO(encoded))
    assert image.format == "WEBP"
    assert getattr(image, "is_animated", False)
    assert image.n_frames == 3
