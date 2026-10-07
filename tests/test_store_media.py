import io

from PIL import Image

from app.services.store_media import (
    _looks_like_gif,
    _optimize_gif,
    prepare_store_banner,
    prepare_store_thumbnail,
    reusable_thumbnail_attachment_url,
    thumbnail_attachment_prefix,
)


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


async def test_thumbnail_is_prepared_as_stable_attachment(monkeypatch) -> None:
    image = Image.new("RGB", (64, 64), (120, 40, 220))
    output = io.BytesIO()
    image.save(output, format="PNG")
    source = output.getvalue()

    async def fake_download(_url: str) -> bytes:
        return source

    monkeypatch.setattr("app.services.store_media._download_image", fake_download)
    url = "https://example.com/store-title.png?token=temporary"
    prepared = await prepare_store_thumbnail(url, upload_limit=8 * 1024 * 1024)

    assert prepared is not None
    assert prepared.data == source
    assert not prepared.optimized
    assert prepared.filename == f"{thumbnail_attachment_prefix(url)}.png"
    assert prepared.attachment_url == f"attachment://{prepared.filename}"


async def test_banner_within_upload_limit_keeps_original_gif(monkeypatch) -> None:
    source = _animated_gif()

    async def fake_download(_url: str) -> bytes:
        return source

    def fail_optimize(*_args, **_kwargs):
        raise AssertionError("GIF não deveria ser recomprimido dentro do limite")

    monkeypatch.setattr("app.services.store_media._download_image", fake_download)
    monkeypatch.setattr("app.services.store_media._optimize_gif", fail_optimize)

    prepared = await prepare_store_banner(
        "https://example.com/brand-fast.gif",
        upload_limit=len(source) + 1024,
    )

    assert prepared is not None
    assert prepared.data == source
    assert not prepared.optimized
    assert prepared.filename.endswith(".gif")


def test_reusable_thumbnail_attachment_is_found_by_source_hash() -> None:
    class Attachment:
        def __init__(self, filename: str) -> None:
            self.filename = filename

    url = "https://example.com/title.png?signature=abc"
    filename = f"{thumbnail_attachment_prefix(url)}.png"
    assert reusable_thumbnail_attachment_url(url, [Attachment(filename)]) == (
        f"attachment://{filename}"
    )