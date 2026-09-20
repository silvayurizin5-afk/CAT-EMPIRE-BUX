from decimal import Decimal
from types import SimpleNamespace

from app.bot.cogs import automation_refinement as refine
from app.bot.cogs import automation_runtime as ai
from app.bot.cogs.delivery_runtime import _delivery_image, _delivery_item_lines
from app.db.models import OrderItem, Product
from app.services.delivery_settings import (
    ARROW_EMOJI,
    DELIVERY_EMOJI,
    DISCOUNT_EMOJI,
    GAME_EMOJI,
    ORDER_EMOJI,
    PRODUCT_EMOJI,
    SEPARATOR_EMOJI,
    USER_EMOJI,
    VERIFIED_EMOJI,
    render_delivery,
)


def _products() -> list[Product]:
    return [
        Product(
            id=1,
            guild_id=1,
            name="Notifier",
            slug="notifier",
            product_type="gamepass",
            game_name="BLOX FRUITS",
            description="",
            price_credits=Decimal("22.00"),
            image_url="https://example.com/notifier.png",
            emoji=None,
            delivery_mode="manual",
            stock_quantity=None,
            active=True,
            sort_order=0,
            metadata_json={},
        ),
        Product(
            id=2,
            guild_id=1,
            name="Robux",
            slug="robux",
            product_type="robux",
            game_name=None,
            description="",
            price_credits=Decimal("2.90"),
            image_url=None,
            emoji=None,
            delivery_mode="manual",
            stock_quantity=50,
            active=True,
            sort_order=1,
            metadata_json={},
        ),
    ]


def _message(content: str):
    return SimpleNamespace(content=content)


def test_ai_lists_games_without_calling_product_unavailable() -> None:
    result = ai._quick_store_answer(_message("Quais jogos tem?"), _products(), {})
    assert result is not None
    assert result["intent"] == "store_question"
    assert "BLOX FRUITS" in str(result["reply"])


def test_ai_keeps_game_context_for_gamepass_follow_up() -> None:
    state = {"game_name": "BLOX FRUITS"}
    result = ai._quick_store_answer(_message("E quais gamepass"), _products(), state)
    assert result is not None
    assert "Notifier" in str(result["reply"])
    assert result["_context_product_name"] == "Notifier"


def test_ai_price_follow_up_uses_last_product() -> None:
    state = {"game_name": "BLOX FRUITS", "product_name": "Notifier"}
    result = ai._quick_store_answer(_message("Quanto custa"), _products(), state)
    assert result is not None
    reply = str(result["reply"])
    assert "A Game Pass" in reply
    assert "R$ 22,00" in reply


def test_ai_named_gamepass_uses_catalog_price() -> None:
    result = ai._quick_store_answer(
        _message("Quanto custa esse Notifier"),
        _products(),
        {},
    )
    assert result is not None
    assert "A Game Pass" in str(result["reply"])
    assert "R$ 22,00" in str(result["reply"])


def test_ai_generic_gamepass_quote_does_not_require_game() -> None:
    result = ai._quick_store_answer(
        _message("Quanto custa uma gamepass de 500 Robux"),
        _products(),
        {},
    )
    assert result is not None
    reply = str(result["reply"])
    assert "Uma Game Pass" in reply
    assert "500 Robux" in reply
    assert "R$ 14,50" in reply


def test_ai_recognizes_singular_game_name_variant() -> None:
    assert ai._match_game("Blox fruit", _products()) == "BLOX FRUITS"


def test_live_stock_variants_never_depend_on_llm() -> None:
    for text in ("Quais o estoque", "Me informe o estoque atual", "estoque da loja agora"):
        result = refine._quick_store_answer(_message(text), _products(), {})
        assert result is not None
        assert result["intent"] == "store_question"
        reply = str(result["reply"])
        assert "Notifier" in reply
        assert "Robux" in reply
        assert "50 em estoque" in reply


def test_live_stock_of_named_product_uses_database_snapshot() -> None:
    result = refine._quick_store_answer(
        _message("Qual o estoque do Notifier?"),
        _products(),
        {},
    )
    assert result is not None
    assert "Notifier" in str(result["reply"])
    assert "estoque ilimitado" in str(result["reply"])


def test_generic_price_question_lists_current_products() -> None:
    result = refine._quick_store_answer(
        _message("Quanto custa qualquer produto"),
        _products(),
        {},
    )
    assert result is not None
    reply = str(result["reply"])
    assert "R$ 22,00" in reply
    assert "R$ 2,90" in reply


def test_coupon_question_is_forced_to_live_database_lookup() -> None:
    result = refine._quick_store_answer(
        _message("Quais cupons estão disponíveis?"),
        _products(),
        {},
    )
    assert result is not None
    assert result["intent"] == "store_question"
    assert result["reply"] is None


def test_coupon_answer_lists_only_available_coupons() -> None:
    coupons = [
        SimpleNamespace(
            code="SAVE10",
            discount_percent=Decimal("10"),
            active=True,
            max_uses=5,
            uses=2,
        ),
        SimpleNamespace(
            code="ESGOTADO",
            discount_percent=Decimal("20"),
            active=True,
            max_uses=1,
            uses=1,
        ),
    ]
    reply = refine._coupon_answer(coupons, "Como usa os cupons e quais estão disponíveis?")
    assert "Adicionar cupom" in reply
    assert "SAVE10" in reply
    assert "10%" in reply
    assert "3 uso(s) restante(s)" in reply
    assert "ESGOTADO" not in reply


def test_delivery_uses_any_order_snapshot_image() -> None:
    item = OrderItem(
        name_snapshot="Notifier",
        unit_price=Decimal("22.00"),
        quantity=1,
        image_url_snapshot="https://example.com/notifier.png",
        metadata_json={"product_type": "custom-type", "game_name": "BLOX FRUITS"},
    )
    assert _delivery_image([item]) == "https://example.com/notifier.png"
    line = _delivery_item_lines([item])[0]
    assert "Notifier" in line
    assert "BLOX FRUITS" in line


def test_delivery_default_matches_requested_layout() -> None:
    item = OrderItem(
        name_snapshot="VIP",
        unit_price=Decimal("4.32"),
        quantity=1,
        metadata_json={
            "product_type": "gamepass",
            "game_name": "Dungeon Lootr › Morreti Gostoso",
            "robux_amount": 120,
            "discount_percent": "6",
            "original_total": "4.32",
            "discounted_total": "4.06",
        },
    )
    title, lines, footer, accent, show_image = render_delivery(
        None,
        order_id="12345678-0000-0000-0000-000000000000",
        client_mention="<@594648746495574069>",
        items=[item],
    )
    text = "\n".join([title, *lines, footer])
    assert title == f"# {DELIVERY_EMOJI}{ARROW_EMOJI}Entrega Realizada"
    assert f"{USER_EMOJI}{SEPARATOR_EMOJI}Cliente:" in text
    assert f"{VERIFIED_EMOJI}{SEPARATOR_EMOJI}Status: Pedido entregue com sucesso" in text
    assert f"# {ORDER_EMOJI}{ARROW_EMOJI}Produto(s):" in text
    assert f"{GAME_EMOJI} Dungeon Lootr › Morreti Gostoso" in text
    assert f"{PRODUCT_EMOJI} VIP 1× · R$ 4,32 (120 Robux)" in text
    assert DISCOUNT_EMOJI in text
    assert "•••••••••• (6%)" in text
    assert "R$ -0,26" in text
    assert footer == ""
    assert show_image is True
    assert accent == 0x23A55A


def test_delivery_accepts_fully_custom_text_templates() -> None:
    item = OrderItem(
        name_snapshot="Notifier",
        unit_price=Decimal("22.00"),
        quantity=1,
        metadata_json={"product_type": "gamepass", "game_name": "BLOX FRUITS"},
    )
    config = {
        "title_template": "MINHA ENTREGA",
        "body_template": "Cliente={client}\n{products}",
        "product_template": "Produto={product} | Jogo={game} | Total={line_total}",
        "footer_template": "#{order_short}",
        "accent_color": "#112233",
        "show_image": False,
    }
    title, lines, footer, accent, show_image = render_delivery(
        config,
        order_id="abcdef12-0000-0000-0000-000000000000",
        client_mention="@Cliente",
        items=[item],
    )
    assert title == "MINHA ENTREGA"
    assert "Cliente=@Cliente" in lines
    assert any("Produto=Notifier" in line for line in lines)
    assert footer == ""
    assert accent == 0x112233
    assert show_image is False


def test_ai_catalog_question_uses_live_products_without_llm() -> None:
    result = refine._quick_store_answer(
        _message("Quais produtos da loja?"),
        _products(),
        {},
    )
    assert result is not None
    reply = str(result["reply"])
    assert "Notifier" in reply
    assert "Robux" in reply
    assert "R$ 22,00" in reply


def test_ai_live_robux_rates_answer() -> None:
    rates = [
        SimpleNamespace(
            code="padrao",
            label="Robux padrão",
            price_per_robux=Decimal("0.029"),
            delivery_label="Cotação padrão da loja",
        )
    ]
    reply = refine._robux_rates_answer(rates, "Qual o valor do robux?")
    assert "Robux padrão" in reply
    assert "R$ 2,90 por 100 Robux" in reply


def test_ai_live_terms_answer() -> None:
    terms = [
        SimpleNamespace(
            code="reembolsos",
            title="Política de reembolso",
            version=3,
            content="Reembolsos seguem as regras publicadas pela NEXTBUY.",
        )
    ]
    reply = refine._terms_answer(terms, "Qual a política de reembolso?")
    assert "Política de reembolso" in reply
    assert "versão 3" in reply
    assert "Reembolsos seguem" in reply


def test_ai_product_snapshot_includes_gamepass_robux_value() -> None:
    products = _products()
    products[0].metadata_json = {"robux_amount": 2200}
    snapshot = refine._product_snapshot(products)
    assert snapshot[0]["robux_amount"] == 2200


def test_live_store_topics_bypass_llm_classification() -> None:
    for text in (
        "Quais as cotações atuais?",
        "Quais são os termos da loja?",
        "Qual a forma de pagamento?",
        "Como funciona a entrega?",
    ):
        result = refine._quick_store_answer(_message(text), _products(), {})
        assert result is not None
        assert result["intent"] == "store_question"
        assert result["multiple_requests"] is False


def test_ai_uses_admin_faq_as_official_store_source() -> None:
    replies = [
        SimpleNamespace(
            name="Prazo",
            title="Prazo de entrega",
            keywords=["prazo", "demora"],
            content="A entrega começa após a confirmação do PIX.",
        )
    ]
    reply = refine._faq_answer(replies, "Quanto demora a entrega?")
    assert reply is not None
    assert "Prazo de entrega" in reply
    assert "confirmação do PIX" in reply


def test_ai_lists_live_customer_rank_tiers() -> None:
    tiers = [
        SimpleNamespace(name="VIP", min_spend=Decimal("100.00")),
        SimpleNamespace(name="Elite", min_spend=Decimal("500.00")),
    ]
    reply = refine._ranks_answer(tiers)
    assert "VIP" in reply
    assert "R$ 100,00" in reply
    assert "Elite" in reply
    assert "R$ 500,00" in reply


def test_ai_fee_followup_uses_remembered_robux_amount() -> None:
    state = {"robux_amount": 200}
    result = ai._quick_store_answer(
        _message("E quanto é sem cobrir a taxa?"),
        _products(),
        state,
    )
    assert result is not None
    assert result["intent"] == "store_question"
    reply = str(result["reply"])
    assert "200 Robux" in reply
    assert "R$ 5,80" in reply
    assert "Via Plus" in reply


def test_ai_cover_fee_followup_uses_remembered_robux_amount() -> None:
    state = {"robux_amount": 200}
    result = ai._quick_store_answer(
        _message("E cobrindo a taxa?"),
        _products(),
        state,
    )
    assert result is not None
    reply = str(result["reply"])
    assert "R$ 8,29" in reply
