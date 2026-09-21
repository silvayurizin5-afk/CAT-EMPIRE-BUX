from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from app.services.calculator import format_brl

# Emojis enviados explicitamente pelo usuário para o servidor NEXTBUY.
VERIFY_EMOJI = "<a:verify:1550043693510037546>"
ARROW_EMOJI = "<a:s_ASETA2_:1550044035522109511>"
MEMBER_EMOJI = "<:member:1550043925283344458>"
BOX_EMOJI = "<:CaixaStorm:1550043608952999996>"

# Aliases usados pelos templates novos/antigos. Todos apontam somente para os quatro emojis acima;
# nenhum ID da mensagem de referência externa é utilizado no padrão da loja.
DELIVERY_EMOJI = VERIFY_EMOJI
USER_EMOJI = MEMBER_EMOJI
SEPARATOR_EMOJI = ""
VERIFIED_EMOJI = VERIFY_EMOJI
ORDER_EMOJI = BOX_EMOJI
GAME_EMOJI = BOX_EMOJI
PRODUCT_EMOJI = BOX_EMOJI
DISCOUNT_EMOJI = VERIFY_EMOJI

_FOREIGN_REFERENCE_EMOJIS = {
    "<:EntregaRealizada:1453310348542087325>",
    "<a:68523animatedarrowgreen:1441172995795193917>",
    "<:user:1453310405416849460>",
    "<:emoji_4:1436151599268630568>",
    "<:verificado:1453310464770707489>",
    "<:PedidoSolicitado:1453309996199841812>",
    "<:oi:1548603672030740510>",
    "<:oi:1544867740517670933>",
    "<:oi:1538473235635503254>",
}

_LEGACY_DEFAULTS: dict[str, set[str]] = {
    "title_template": {
        "{verify} Entrega Realizada",
        "{verify} {arrow} Entrega Realizada",
        "{verify}{arrow}Entrega Realizada",
        "# {delivery}{arrow}Entrega Realizada",
    },
    "body_template": {
        (
            "{member} **Cliente:** {client}\n"
            "{verify} **Status:** Pedido entregue com sucesso\n"
            "### {box} Produto(s):\n"
            "{products}"
        ),
        (
            "- **{user}{separator}Cliente:** {client}\n"
            "- **{verified}{separator}Status: Pedido entregue com sucesso**\n"
            "# {order_icon}{arrow}Produto(s):\n\n"
            "{products}"
        ),
    },
    "product_template": {
        "**{product}**{game_part}{quantity_part}",
        (
            "> **{game_emoji} {game_or_product}**\n"
            "**• {product_emoji} {product} {quantity}× · {line_total}{robux_part}**\n"
            "{discount_line}"
        ),
    },
    "footer_template": {
        "NEXTBUY • Pedido #{order_short}",
    },
}

DEFAULT_DELIVERY_CONFIG: dict[str, object] = {
    "title_template": "# {verify}{arrow}Entrega Realizada",
    "body_template": (
        "- **{member}Cliente:** {client}\n"
        "- **{verify}Status: Pedido entregue com sucesso**\n"
        "# {box}{arrow}Produto(s):\n\n"
        "{products}"
    ),
    "product_template": (
        "> **{box} {game_or_product}**\n"
        "**• {box} {product} {quantity}× · {line_total}{robux_part}**\n"
        "{discount_line}"
    ),
    "footer_template": "",
    "accent_color": "#23A55A",
    "show_image": True,
    "banner_enabled": True,
    "banner_source": "local",
    "banner_mode": "animated",
    "banner_url": "",
    "delivery_emoji": VERIFY_EMOJI,
    "arrow_emoji": ARROW_EMOJI,
    "user_emoji": MEMBER_EMOJI,
    "separator_emoji": "",
    "verified_emoji": VERIFY_EMOJI,
    "order_emoji": BOX_EMOJI,
    "game_emoji": BOX_EMOJI,
    "product_emoji": BOX_EMOJI,
    "discount_emoji": VERIFY_EMOJI,
    "verify_emoji": VERIFY_EMOJI,
    "member_emoji": MEMBER_EMOJI,
    "box_emoji": BOX_EMOJI,
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
            # Atualiza apenas templates padrão antigos. Templates realmente personalizados são preservados.
            continue
        if isinstance(value, str) and value in _FOREIGN_REFERENCE_EMOJIS:
            # Remove automaticamente IDs que vieram somente da mensagem visual de referência.
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


def sanitize_private_order_artifacts(value: str) -> str:
    """Remove linhas residuais de templates antigos que exibiam ID interno do pedido."""
    kept: list[str] = []
    for line in str(value or "").splitlines():
        stripped = line.strip()
        if not stripped:
            kept.append(line)
            continue
        plain = stripped.replace("**", "").replace("__", "").replace("`", "")
        if re.fullmatch(
            r"(?i)(?:nextbuy\s*[•|:\-]\s*)?(?:pedido|order|compra)?\s*[#:\-]?\s*",
            plain,
        ):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


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
        "delivery": VERIFY_EMOJI,
        "arrow": ARROW_EMOJI,
        "user": MEMBER_EMOJI,
        "separator": "",
        "verified": VERIFY_EMOJI,
        "order_icon": BOX_EMOJI,
        "game_emoji": BOX_EMOJI,
        "product_emoji": BOX_EMOJI,
        "discount_emoji": VERIFY_EMOJI,
        "verify": VERIFY_EMOJI,
        "member": MEMBER_EMOJI,
        "box": BOX_EMOJI,
        "client": "@Cliente",
        "products": "Produto",
        "order": "",
        "order_short": "",
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
        "discount_line": f"• {VERIFY_EMOJI} `•••••••••• (6%)` **R$ -0,60**",
    }
    _format(str(config.get("title_template") or ""), values)
    _format(str(config.get("body_template") or ""), values)
    _format(str(config.get("product_template") or ""), values)
    _format(str(config.get("footer_template") or ""), values)
    parse_hex_color(config.get("accent_color"))

    banner_source = str(config.get("banner_source") or "local").strip().casefold()
    if banner_source not in {"local", "url"}:
        raise ValueError("A fonte do banner deve ser `local` ou `url`.")

    banner_mode = str(config.get("banner_mode") or "static").strip().casefold()
    if banner_mode not in {"static", "animated"}:
        raise ValueError("O modo do banner deve ser `static` ou `animated`.")

    banner_url = str(config.get("banner_url") or "").strip()
    if banner_source == "url" and banner_url:
        if not banner_url.lower().startswith(("http://", "https://")):
            raise ValueError("A URL do banner deve começar com http:// ou https://.")


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
        "verify": str(config["verify_emoji"]),
        "member": str(config["member_emoji"]),
        "box": str(config["box_emoji"]),
        "client": client_mention,
        "order": "",
        "order_short": "",
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
    title = sanitize_private_order_artifacts(
        _format(str(config["title_template"]), values)
    )
    body = sanitize_private_order_artifacts(
        _format(str(config["body_template"]), values)
    )
    footer = sanitize_private_order_artifacts(
        _format(str(config["footer_template"]), values)
    )
    lines = body.splitlines() if body else []
    return (
        title,
        lines,
        footer,
        parse_hex_color(config["accent_color"]),
        bool(config["show_image"]),
    )
