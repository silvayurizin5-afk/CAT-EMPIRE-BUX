import discord
import pytest

from app.bot.views.automation_admin import AutoReplyActionsView, RobuxRateActionsView
from app.bot.views.product_admin import ProductActionsView
from app.bot.views.rank_admin import FullAdminPanelView, RankTierActionsView
from app.bot.views.terms_admin import TermsActionsView
from app.bot.views.terms_gate import TermsGateView
from app.db.models import TermsDocument


def _walk_items(items):
    for item in items:
        yield item
        children = getattr(item, "children", None)
        if children:
            yield from _walk_items(children)


def _button_labels(view: discord.ui.View | discord.ui.LayoutView) -> set[str]:
    return {
        item.label
        for item in _walk_items(view.children)
        if isinstance(item, discord.ui.Button) and item.label is not None
    }


@pytest.mark.asyncio
async def test_extended_admin_panel_keeps_base_actions() -> None:
    view = FullAdminPanelView()
    labels = _button_labels(view)
    assert "Cargos" in labels
    assert "Canais" in labels
    assert "Publicar loja" in labels
    assert "Publicar ranking" in labels
    assert "Gerenciar produtos" in labels
    assert "Cotações" in labels
    assert "FAQ" in labels
    assert "Feedbacks" in labels
    assert "Mensagens ticket" in labels
    assert "Gerenciar termos" in labels
    assert "Gerenciar faixas" in labels
    view.stop()


@pytest.mark.asyncio
async def test_product_admin_exposes_stock_management() -> None:
    view = ProductActionsView(product_id=1)
    labels = _button_labels(view)
    assert "Editar visual/preço" in labels
    assert "Estoque" in labels
    assert "Ativar/Desativar" in labels
    assert "Loja / Subpainel" in labels
    view.stop()


@pytest.mark.asyncio
async def test_automation_admin_exposes_edit_and_toggle() -> None:
    rate_view = RobuxRateActionsView(rate_id=1)
    reply_view = AutoReplyActionsView(reply_id=1)
    for view in (rate_view, reply_view):
        labels = _button_labels(view)
        assert "Editar" in labels
        assert "Ativar/Desativar" in labels
        view.stop()


@pytest.mark.asyncio
async def test_terms_admin_exposes_version_edit_and_toggle() -> None:
    view = TermsActionsView(terms_id=1)
    labels = _button_labels(view)
    assert "Editar / Nova versão" in labels
    assert "Ativar/Desativar" in labels
    view.stop()


@pytest.mark.asyncio
async def test_rank_admin_exposes_edit_and_toggle() -> None:
    view = RankTierActionsView(tier_id=1)
    labels = _button_labels(view)
    assert "Editar" in labels
    assert "Ativar/Desativar" in labels
    view.stop()


@pytest.mark.asyncio
async def test_terms_gate_exposes_review_and_acceptance() -> None:
    terms = [
        TermsDocument(
            id=1,
            guild_id=123,
            code="seguranca",
            title="Segurança",
            content="Leia antes de comprar.",
            version=2,
            active=True,
        )
    ]
    resume_view = discord.ui.View(timeout=30)
    gate = TermsGateView(terms, resume_view)
    nested = list(_walk_items(gate.children))

    labels = _button_labels(gate)
    assert "Aceitar termos" in labels
    assert any(isinstance(item, discord.ui.Select) for item in nested)

    text = "\n".join(
        item.content for item in nested if isinstance(item, discord.ui.TextDisplay)
    )
    assert "Termos necessários" in text
    assert "Segurança" in text
    assert "versão 2" not in text

    gate.stop()
    resume_view.stop()


@pytest.mark.asyncio
async def test_terms_gate_rejects_empty_snapshot() -> None:
    resume_view = discord.ui.View(timeout=30)
    with pytest.raises(ValueError, match="pelo menos um termo"):
        TermsGateView([], resume_view)
    resume_view.stop()
