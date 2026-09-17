from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.services.calculator import format_brl

VERIFY_EMOJI = "<a:verify:1550043693510037546>"
MEMBER_EMOJI = "<:member:1550043925283344458>"
BOX_EMOJI = "<:CaixaStorm:1550043608952999996>"
ARROW_EMOJI = "<a:s_ASETA2_:1550044035522109511>"

DEFAULT_DELIVERY_CONFIG: dict[str, object] = {
    "title_template": "{verify} Entrega Realizada",
    "body_template": (
        "{member} **Cliente:** {client}\n"
        "{verify} **Status:** Pedido entregue com sucesso\n"
        "### {box} Produto(s):\n"
        "{products}"
    ),
    "product_template": "**{product}**{game_part}{quantity_part}",
    "footer_template": "NEXTBUY • Pedido #{order_short}",
    "accent_color": "#23A55A",
    "show_image": True,
    "verify_emoji": VERIFY_EMOJI,
    "member_emoji": MEMBER_EMOJI,
    "box_emoji": BOX_EMOJI,
    "arrow_emoji": ARROW_EMOJI,
}


def effective_delivery_config(raw: dict[str, Any] | None) -> dict[str, object]:
    merged = dict(DEFAULT_DELIVERY_CONFIG)
    if raw:
        for key in DEFAULT_DELIVERY_CONFIG:
            if key in raw and raw[key] is not None:
                merged[key] = raw[key]
    return merged


def parse_hex_color(value: str | int | None) -> int:
    if isinstance(value, int):
        if 0 <= value <= 0xFFFFFF:
            return value
        return 0x23A55A
    text = str(value or "").strip().lstrip("#")
    if len(text) != 6:
        return 0x23A55A
    try:
        number = int(text, 16)
    except ValueError:
        return 0x23A55A
    return number if 0 <= number <= 0xFFFFFF else 0x23A55A


def _format(template: str, values: dict[str, object]) -> str:
    try:
        return template.format(**values)
    except (KeyError, ValueError) as exc:
        raise ValueError(f"Template de entrega inválido: {exc}") from exc


def validate_delivery_templates(config: dict[str, object]) -> None:
    values = {
        "verify": VERIFY_EMOJI,
        "member": MEMBER_EMOJI,
        "box": BOX_EMOJI,
        "arrow": ARROW_EMOJI,
        "client": "@Cliente",
        "products": "Produto",
        "order": "00000000-0000-0000-0000-000000000000",
        "order_short": "00000000",
        "product": "Produto",
        "game": "Jogo",
        "game_part": " • Jogo",
        "quantity": 1,
        "quantity_part": "",
        "unit_price": "R$ 10,00",
        "line_total": "R$ 10,00",
    }
    _format(str(config.get("title_template") or ""), values)
    _format(str(config.get("body_template") or ""), values)
    _format(str(config.get("product_template") or ""), values)
    _format(str(config.get("footer_template") or ""), values)
    parse_hex_color(config.get("accent_color"))


def product_line_values(item) -> dict[str, object]:
    metadata = dict(item.metadata_json or {})
    game = str(metadata.get("game_name") or "").strip()
    quantity = max(1, int(item.quantity or 1))
    unit = Decimal(item.unit_price)
    line_total = unit * quantity
    return {
        "product": item.name_snapshot,
        "game": game,
        "game_part": f" • {game}" if game else "",
        "quantity": quantity,
        "quantity_part": f" × `{quantity}`" if quantity > 1 else "",
        "unit_price": format_brl(unit),
        "line_total": format_brl(line_total),
    }


def render_delivery(
    raw_config: dict[str, object] | None,
    *,
    order_id,
    client_mention: str,
    items,
) -> tuple[str, list[str], str, int, bool]:
    config = effective_delivery_config(raw_config)
    common = {
        "verify": str(config["verify_emoji"]),
        "member": str(config["member_emoji"]),
        "box": str(config["box_emoji"]),
        "arrow": str(config["arrow_emoji"]),
        "client": client_mention,
        "order": str(order_id),
        "order_short": str(order_id)[:8],
    }

    product_template = str(config["product_template"])
    rendered_products: list[str] = []
    for item in items:
        rendered_products.append(_format(product_template, {**common, **product_line_values(item)}))
    if not rendered_products:
        rendered_products.append("Pedido sem itens cadastrados")

    values = {**common, "products": "\n".join(rendered_products)}
    title = _format(str(config["title_template"]), values).strip()
    body = _format(str(config["body_template"]), values).strip()
    footer = _format(str(config["footer_template"]), values).strip()
    lines = body.splitlines() if body else []
    return (
        title,
        lines,
        footer,
        parse_hex_color(config["accent_color"]),
        bool(config["show_image"]),
    )
