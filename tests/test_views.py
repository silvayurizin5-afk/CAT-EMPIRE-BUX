import discord
import pytest

from app.bot.views.automation_admin import AutoReplyActionsView, RobuxRateActionsView
from app.bot.views.product_admin import ProductActionsView
from app.bot.views.rank_admin import FullAdminPanelView, RankTierActionsView
from app.bot.views.terms_admin import TermsActionsView


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


