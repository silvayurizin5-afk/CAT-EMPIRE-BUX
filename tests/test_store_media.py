import io

from PIL import Image

from app.services.store_media import _looks_like_gif, _optimize_gif, prepare_store_banner


def _animated_gif() -> bytes:
    frames = []
    for index in range(12):
        image = Image.new(
            "RGB",
            (420, 220),
            (
                (index * 17) % 255,
                (index * 31) % 255,
                (index * 47) % 255,
            ),
        )
        frames.append(image)

    output = io.BytesIO()
    frames[0].save(
        output,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=80,
        loop=0,
    )
    return output.getvalue()


def test_gif_url_detection_ignores_query_string() -> None:
    assert _looks_like_gif("https://cdn.example.com/banner.gif?size=4096")
    assert not _looks_like_gif("https://cdn.example.com/banner.png")


def test_large_gif_can_be_reencoded_as_animated_webp() -> None:
    source = _animated_gif()
    encoded = _optimize_gif(source, target_bytes=200_000)

    assert len(encoded) <= 200_000
    image = Image.open(io.BytesIO(encoded))
    assert image.format == "WEBP"
    assert getattr(image, "is_animated", False)


async def test_non_gif_banner_keeps_normal_url_flow() -> None:
    assert (
        await prepare_store_banner(
            "https://cdn.example.com/banner.png",
            upload_limit=10 * 1024 * 1024,
        )
        is None
    )
