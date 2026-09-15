import io
from textwrap import wrap

from PIL import Image, ImageDraw, ImageFont, ImageOps

CARD_WIDTH = 1100
CARD_HEIGHT = 520


def _font(size: int):
    return ImageFont.load_default(size=size)


def _wrap_comment(text: str, width: int = 62, max_lines: int = 6) -> list[str]:
    cleaned = " ".join(text.strip().split())
    lines = wrap(cleaned, width=width, break_long_words=True, break_on_hyphens=False)
    if len(lines) <= max_lines:
        return lines
    visible = lines[:max_lines]
    visible[-1] = visible[-1].rstrip(" .") + "..."
    return visible


def render_feedback_card(
    *,
    customer_name: str,
    stars: int,
    comment: str,
    order_short_id: str,
    avatar_bytes: bytes | None = None,
) -> bytes:
    if stars < 1 or stars > 5:
        raise ValueError("stars precisa estar entre 1 e 5")

    image = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), (27, 29, 34))
    draw = ImageDraw.Draw(image)

    # Barra lateral e cartão interno seguem o visual escuro usado nos embeds do Discord.
    draw.rounded_rectangle((24, 24, CARD_WIDTH - 24, CARD_HEIGHT - 24), radius=28, fill=(36, 39, 46))
    draw.rounded_rectangle((24, 24, 36, CARD_HEIGHT - 24), radius=6, fill=(88, 101, 242))

    avatar_box = (70, 68, 198, 196)
    if avatar_bytes:
        try:
            avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGB").resize((128, 128))
            mask = Image.new("L", (128, 128), 0)
            ImageDraw.Draw(mask).ellipse((0, 0, 127, 127), fill=255)
            image.paste(avatar, avatar_box[:2], mask)
        except (OSError, ValueError):
            avatar_bytes = None
    if not avatar_bytes:
        draw.ellipse(avatar_box, fill=(88, 101, 242))
        initial = (customer_name.strip()[:1] or "N").upper()
        draw.text((111, 101), initial, font=_font(46), fill=(255, 255, 255), anchor="mm")

    draw.text((230, 72), "Compra verificada", font=_font(42), fill=(255, 255, 255))
    draw.text((230, 127), customer_name[:48], font=_font(30), fill=(219, 222, 225))
    draw.text(
        (230, 169),
        f"Nota: {stars}/5   •   Pedido #{order_short_id}",
        font=_font(25),
        fill=(181, 186, 193),
    )

    draw.line((70, 225, CARD_WIDTH - 70, 225), fill=(63, 67, 74), width=2)
    draw.text((70, 257), "Feedback", font=_font(27), fill=(181, 186, 193))

    y = 304
    for line in _wrap_comment(comment):
        draw.text((70, y), line, font=_font(29), fill=(242, 243, 245))
        y += 39

    draw.text(
        (CARD_WIDTH - 70, CARD_HEIGHT - 55),
        "NEXTBUY • feedback de compra confirmada",
        font=_font(20),
        fill=(148, 155, 164),
        anchor="rs",
    )

    output = io.BytesIO()
    ImageOps.exif_transpose(image).save(output, format="PNG", optimize=True)
    return output.getvalue()
