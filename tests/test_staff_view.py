import discord

from app.bot.cogs.staff import StaffPanelView


def test_staff_panel_exposes_payment_lock_review() -> None:
    view = StaffPanelView()
    labels = {
        item.label
        for item in view.children
        if isinstance(item, discord.ui.Button) and item.label is not None
    }
    assert "Atualizar" in labels
    assert "Reembolsar" in labels
    assert "Revisar bloqueios" in labels
    view.stop()
