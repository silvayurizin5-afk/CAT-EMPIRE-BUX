from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.services.calculator import format_brl

DELIVERY_EMOJI = "<:EntregaRealizada:1453310348542087325>"
ARROW_EMOJI = "<a:68523animatedarrowgreen:1441172995795193917>"
USER_EMOJI = "<:user:1453310405416849460>"
SEPARATOR_EMOJI = "<:emoji_4:1436151599268630568>"
VERIFIED_EMOJI = "<:verificado:1453310464770707489>"
ORDER_EMOJI = "<:PedidoSolicitado:1453309996199841812>"
GAME_EMOJI = "<:oi:1548603672030740510>"
PRODUCT_EMOJI = "<:oi:1544867740517670933>"
DISCOUNT_EMOJI = "<:oi:1538473235635503254>"

# Aliases mantidos para compatibilidade com imports/configurações antigas.
VERIFY_EMOJI = DELIVERY_EMOJI
MEMBER_EMOJI = USER_EMOJI
BOX_EMOJI = ORDER_EMOJI

_LEGACY_DEFAULTS: dict[str, set[str]] = {
    "title_template": {
        "{verify} Entrega Realizada",
        "{verify} {arrow} Entrega Realizada",
        "{verify}{arrow}Entrega Realizada",
    },
    "body_template": {
        (
            "{member} **Cliente:** {client}\n"
            "{verify} **Status:** Pedido entregue com sucesso\n"
            "### {box} Produto(s):\n"
            "{products}"
        ),
    },
    "product_template": {
        "**{product}**{game_part}{quantity_part}",
    },
    "footer_template": {
        "NEXTBUY • Pedido #{order_short}",
    },
}

DEFAULT_DELIVERY_CONFIG: dict[str, object] = {
    "title_template": "# {delivery}{arrow}Entrega Realizada",
    "body_template": (
        "- **{user}{separator}Cliente:** {client}\n"
        "- **{verified}{separator}Status: Pedido entregue com sucesso**\n"
        "# {order_icon}{arrow}Produto(s):\n\n"
        "{products}"
    ),
    "product_template": (
        "> **{game_emoji} {game_or_product}**\n"
        "**• {product_emoji} {product} {quantity}× · {line_total}{robux_part}**\n"
        "{discount_line}"
    ),
    "footer_template": "",
    "accent_color": "#23A55A",
    "show_image": True,
    "delivery_emoji": DELIVERY_EMOJI,
    "arrow_emoji": ARROW_EMOJI,
    "user_emoji": USER_EMOJI,
    "separator_emoji": SEPARATOR_EMOJI,
    "verified_emoji": VERIFIED_EMOJI,
    "order_emoji": ORDER_EMOJI,
    "game_emoji": GAME_EMOJI,
    "product_emoji": PRODUCT_EMOJI,
    "discount_emoji": DISCOUNT_EMOJI,
    # Chaves antigas continuam válidas em templates personalizados existentes.
    "verify_emoji": DELIVERY_EMOJI,
    "member_emoji": USER_EMOJI,
    "box_emoji": ORDER_EMOJI,
}


def effective_delivery_config(raw: dict[str, Any] | None) -> dict[str, object]:
    merged = dict(DEFAULT_DELIVERY_CONFIG)
    if not raw:
        return merged

    for key in DEFAULT_DELIVERY_CONFIG:
        if key not in raw or raw[key] is None:
            continue
        value = raw[key]
        legacy_values = _LEGACY_DEFAULTS.get(key)
        if legacy_values and isinstance(value, str) and value in legacy_values:
            # Atualiza apenas o padrão antigo. Templates realmente personalizados são preservados.
            continue
        merged[key] = value
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


def _format_percent(value: Decimal) -> str:
    number = Decimal(value)
    if number == number.to_integral_value():
        return f"{int(number)}%"
    return f"{number:.2f}".rstrip("0").rstrip(".") + "%"


def _robux_amount(metadata: dict[str, object], quantity: int) -> int | None:
    for key in ("robux_amount", "robux", "amount_robux", "gamepass_robux"):
        raw = metadata.get(key)
        if raw in (None, ""):
            continue
        try:
            amount = int(raw)
        except (TypeError, ValueError):
            continue
        if amount > 0:
            return amount * quantity
    return None


def _discount_values(metadata: dict[str, object], line_total: Decimal) -> tuple[str, str]:
    raw_percent = metadata.get("discount_percent")
    if raw_percent in (None, "", 0, "0", "0.00"):
        return "", ""
    try:
        percent = Decimal(str(raw_percent))
    except Exception:
        return "", ""
    if percent <= 0:
        return "", ""

    original = line_total
    discounted = line_total * (Decimal("1") - percent / Decimal("100"))
    try:
        if metadata.get("original_total") not in (None, ""):
            original = Decimal(str(metadata["original_total"]))
        if metadata.get("discounted_total") not in (None, ""):
            discounted = Decimal(str(metadata["discounted_total"]))
    except Exception:
        pass
    discount_amount = max(Decimal("0"), original - discounted)
    return _format_percent(percent), format_brl(-discount_amount)


def validate_delivery_templates(config: dict[str, object]) -> None:
    values = {
        "delivery": DELIVERY_EMOJI,
        "arrow": ARROW_EMOJI,
        "user": USER_EMOJI,
        "separator": SEPARATOR_EMOJI,
        "verified": VERIFIED_EMOJI,
        "order_icon": ORDER_EMOJI,
        "game_emoji": GAME_EMOJI,
        "product_emoji": PRODUCT_EMOJI,
        "discount_emoji": DISCOUNT_EMOJI,
        "verify": VERIFY_EMOJI,
        "member": MEMBER_EMOJI,
        "box": BOX_EMOJI,
        "client": "@Cliente",
        "products": "Produto",
        "order": "00000000-0000-0000-0000-000000000000",
        "order_short": "00000000",
        "product": "VIP",
        "game": "Jogo",
        "game_or_product": "Jogo",
        "game_part": " • Jogo",
        "quantity": 1,
        "quantity_part": "",
        "unit_price": "R$ 10,00",
        "line_total": "R$ 10,00",
        "robux": 120,
        "robux_part": " (120 Robux)",
        "discount_percent": "6%",
        "discount_amount": "R$ -0,60",
        "discount_line": (
            f"• {DISCOUNT_EMOJI} `•••••••••• (6%)` **R$ -0,60**"
        ),
    }
    _format(str(config.get("title_template") or ""), values)
    _format(str(config.get("body_template") or ""), values)
    _format(str(config.get("product_template") or ""), values)
    _format(str(config.get("footer_template") or ""), values)
    parse_hex_color(config.get("accent_color"))


def product_line_values(item, config: dict[str, object] | None = None) -> dict[str, object]:
    active_config = effective_delivery_config(config)
    metadata = dict(item.metadata_json or {})
    game = str(metadata.get("game_name") or "").strip()
    quantity = max(1, int(item.quantity or 1))
    unit = Decimal(item.unit_price)
    line_total = unit * quantity
    robux = _robux_amount(metadata, quantity)
    discount_percent, discount_amount = _discount_values(metadata, line_total)
    discount_line = ""
    if discount_percent and discount_amount:
        discount_line = (
            f"• {active_config['discount_emoji']} `•••••••••• ({discount_percent})` "
            f"**{discount_amount}**"
        )

    product_emoji = str(metadata.get("product_emoji") or "").strip() or str(
        active_config["product_emoji"]
    )
    game_emoji = str(metadata.get("game_emoji") or "").strip() or str(
        active_config["game_emoji"]
    )

    return {
        "product": item.name_snapshot,
        "game": game,
        "game_or_product": game or item.name_snapshot,
        "game_part": f" • {game}" if game else "",
        "quantity": quantity,
        "quantity_part": f" × `{quantity}`" if quantity > 1 else "",
        "unit_price": format_brl(unit),
        "line_total": format_brl(line_total),
        "robux": robux or "",
        "robux_part": f" ({robux} Robux)" if robux else "",
        "discount_percent": discount_percent,
        "discount_amount": discount_amount,
        "discount_line": discount_line,
        "game_emoji": game_emoji,
        "product_emoji": product_emoji,
        "discount_emoji": str(active_config["discount_emoji"]),
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
        "delivery": str(config["delivery_emoji"]),
        "arrow": str(config["arrow_emoji"]),
        "user": str(config["user_emoji"]),
        "separator": str(config["separator_emoji"]),
        "verified": str(config["verified_emoji"]),
        "order_icon": str(config["order_emoji"]),
        "game_emoji": str(config["game_emoji"]),
        "product_emoji": str(config["product_emoji"]),
        "discount_emoji": str(config["discount_emoji"]),
        # Compatibilidade com templates antigos.
        "verify": str(config["verify_emoji"]),
        "member": str(config["member_emoji"]),
        "box": str(config["box_emoji"]),
        "client": client_mention,
        "order": str(order_id),
        "order_short": str(order_id)[:8],
    }

    product_template = str(config["product_template"])
    rendered_products: list[str] = []
    for item in items:
        rendered = _format(
            product_template,
            {**common, **product_line_values(item, config)},
        ).strip()
        if rendered:
            rendered_products.append(rendered)
    if not rendered_products:
        rendered_products.append("Pedido sem itens cadastrados")

    values = {**common, "products": "\n\n".join(rendered_products)}
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
