import io

from PIL import Image

from app.services.delivery_media import (
    _coalesced_frames,
    _encode_animated_webp,
    validate_media_url,
)


def _gif_with_transparent_frame() -> bytes:
    first = Image.new("RGBA", (80, 40), (123, 44, 191, 255))
    blank = Image.new("RGBA", (80, 40), (0, 0, 0, 0))
    third = Image.new("RGBA", (80, 40), (20, 20, 20, 255))
    output = io.BytesIO()
    first.save(
        output,
        format="GIF",
        save_all=True,
        append_images=[blank, third],
        duration=[100, 100, 100],
        loop=0,
        disposal=2,
        transparency=0,
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


def test_coalescing_keeps_content_during_transparent_gif_frame() -> None:
    frames, durations, loop = _coalesced_frames(_gif_with_transparent_frame())
    assert len(frames) >= 2
    assert durations[0] == 100
    assert loop == 0
    assert frames[1].getbbox() is not None


def test_animation_is_reencoded_as_animated_webp() -> None:
    encoded = _encode_animated_webp(
        _gif_with_transparent_frame(),
        target_bytes=500_000,
    )
    image = Image.open(io.BytesIO(encoded))
    assert image.format == "WEBP"
    assert getattr(image, "is_animated", False)
    assert image.n_frames > 1
