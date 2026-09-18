import io
import os
import re
import unicodedata
from pathlib import Path
from textwrap import wrap

from PIL import Image, ImageDraw, ImageFont, ImageOps

CARD_WIDTH = 1100
CARD_HEIGHT = 520

_CUSTOM_EMOJI_RE = re.compile(r"<a?:([A-Za-z0-9_]{1,64}):\d+>")
_EMOJI_RANGES = (
    (0x1F000, 0x1FAFF),
    (0x2600, 0x27BF),
    (0x1F1E6, 0x1F1FF),
)


def _font_candidates(*, bold: bool) -> list[str]:
    custom = os.getenv("NEXTBUY_FEEDBACK_FONT", "").strip()
    names = [
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
    ]
    return ([custom] if custom else []) + names


def _font(size: int, *, bold: bool = False):
    for candidate in _font_candidates(bold=bold):
        try:
            if "/" in candidate or "\\" in candidate:
                if not Path(candidate).exists():
                    continue
            return ImageFont.truetype(candidate, size=size)
        except (OSError, ValueError):
            continue
    return ImageFont.load_default(size=size)


def _sanitize_card_text(value: str) -> str:
    """Mantém texto legível no PNG e remove glifos que virariam quadrados/tofu.

    O card usa uma fonte TrueType comum, não uma fonte de emoji colorido. Emojis Unicode,
    variation selectors e ZWJ são removidos; emojis customizados do Discord viram :nome:.
    Acentos e caracteres latinos continuam preservados.
    """

    text = _CUSTOM_EMOJI_RE.sub(lambda match: f":{match.group(1)}:", value)
    text = unicodedata.normalize("NFC", text).replace("\ufffd", "")
    cleaned: list[str] = []
    for char in text:
        code = ord(char)
        if code in {0x200D, 0xFE0E, 0xFE0F}:
            continue
        if any(start <= code <= end for start, end in _EMOJI_RANGES):
            continue
        category = unicodedata.category(char)
        if category.startswith("C") and char not in {"\n", "\t"}:
            continue
        # Fontes TrueType comuns usadas no card não têm cobertura confiável para
        # símbolos/emoji Unicode. Mantemos ASCII (inclusive $,+,-,=) e removemos
        # símbolos não ASCII e marcas isoladas que normalmente viram tofu/quadrados.
        if code > 0x7F and (category.startswith("S") or category.startswith("M")):
            continue
        cleaned.append(char)
    return "".join(cleaned)


def _wrap_comment(text: str, width: int = 62, max_lines: int = 6) -> list[str]:
    cleaned = " ".join(_sanitize_card_text(text).strip().split())
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

    safe_name = _sanitize_card_text(customer_name).strip() or "Cliente"
    safe_order = _sanitize_card_text(order_short_id).strip()[:32]

    image = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), (27, 29, 34))
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle(
        (24, 24, CARD_WIDTH - 24, CARD_HEIGHT - 24),
        radius=28,
        fill=(36, 39, 46),
    )
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
        initial = (safe_name[:1] or "N").upper()
        draw.text(
            (134, 132),
            initial,
            font=_font(46, bold=True),
            fill=(255, 255, 255),
            anchor="mm",
        )

    draw.text(
        (230, 72),
        "Compra verificada",
        font=_font(42, bold=True),
        fill=(255, 255, 255),
    )
    draw.text((230, 127), safe_name[:48], font=_font(30), fill=(219, 222, 225))
    draw.text(
        (230, 169),
        f"Nota: {stars}/5   •   Pedido #{safe_order}",
        font=_font(25),
        fill=(181, 186, 193),
    )

    draw.line((70, 225, CARD_WIDTH - 70, 225), fill=(63, 67, 74), width=2)
    draw.text((70, 257), "Feedback", font=_font(27, bold=True), fill=(181, 186, 193))

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
