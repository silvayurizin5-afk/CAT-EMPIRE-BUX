import discord
import pytest

from app.bot.views.admin_feedback import ExtendedAdminPanelView
from app.bot.views.product_admin import ProductActionsView


def _button_labels(view: discord.ui.View) -> set[str]:
    return {
        item.label
        for item in view.children
        if isinstance(item, discord.ui.Button) and item.label is not None
    }


@pytest.mark.asyncio
async def test_extended_admin_panel_keeps_base_actions() -> None:
    view = ExtendedAdminPanelView()
    labels = _button_labels(view)
    assert "Cargos" in labels
    assert "Canais" in labels
    assert "Publicar loja" in labels
    assert "Publicar ranking" in labels
    assert "Gerenciar produtos" in labels
    assert "Feedbacks" in labels
    view.stop()


@pytest.mark.asyncio
async def test_product_admin_exposes_stock_management() -> None:
    view = ProductActionsView(product_id=1)
    labels = _button_labels(view)
    assert "Editar visual/preço" in labels
    assert "Estoque" in labels
    assert "Ativar/Desativar" in labels
    view.stop()
