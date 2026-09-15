import discord
import pytest

from app.bot.views.admin_feedback import ExtendedAdminPanelView
from app.bot.views.product_admin import ProductActionsView
from app.bot.views.terms_gate import TermsGateView, build_terms_required_embed
from app.db.models import TermsDocument


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

    labels = _button_labels(gate)
    assert "Aceitar termos" in labels
    assert any(isinstance(item, discord.ui.Select) for item in gate.children)

    embed = build_terms_required_embed(terms)
    assert embed.title == "Termos necessários"
    assert "Segurança" in embed.fields[0].value
    assert "versão 2" in embed.fields[0].value

    gate.stop()
    resume_view.stop()


@pytest.mark.asyncio
async def test_terms_gate_rejects_empty_snapshot() -> None:
    resume_view = discord.ui.View(timeout=30)
    with pytest.raises(ValueError, match="pelo menos um termo"):
        TermsGateView([], resume_view)
    resume_view.stop()
