import discord

from app.bot.views.game_catalog import (
    GameCatalogOpenView,
    _button_style,
    _format_footer,
    build_game_catalog_embed,
    build_game_selector_embed,
)
from app.db.store_models import GameCatalogPanelConfig


def _config() -> GameCatalogPanelConfig:
    return GameCatalogPanelConfig(
        guild_id=123,
        title="CAT EMPIRE • Jogos",
        status_text="Atendimento on-line",
        status_emoji="🟢",
        description="Escolha seus jogos favoritos.",
        color=0x7B2CBF,
        image_url="https://example.com/banner.png",
        thumbnail_url="https://example.com/logo.png",
        footer_text="{count} jogo(s) disponível(is) • CAT EMPIRE",
        open_button_label="Abrir catálogo",
        open_button_emoji="🛒",
        open_button_style="success",
        catalog_title="CAT EMPIRE • Escolha seu jogo",
        catalog_description="Escolha um jogo para ver os produtos.",
        catalog_status_text="Atendimento: on-line.",
        catalog_footer_text="{count} jogos",
        game_placeholder="Selecione um jogo",
        selected_game_ids=[],
    )


def test_dynamic_footer_formats_game_count() -> None:
    assert _format_footer("{count} jogos disponíveis", 7) == "7 jogos disponíveis"
    assert _format_footer("Rodapé fixo", 7) == "Rodapé fixo"


def test_public_catalog_embed_is_fully_configured() -> None:
    embed = build_game_catalog_embed(_config(), 3)

    assert embed.title == "CAT EMPIRE • Jogos"
    assert "Atendimento on-line 🟢" in (embed.description or "")
    assert "Escolha seus jogos favoritos." in (embed.description or "")
    assert embed.footer.text == "3 jogo(s) disponível(is) • CAT EMPIRE"
    assert embed.image.url == "https://example.com/banner.png"
    assert embed.thumbnail.url == "https://example.com/logo.png"


def test_selector_embed_uses_catalog_configuration() -> None:
    embed = build_game_selector_embed(_config(), 4)

    assert embed.title == "CAT EMPIRE • Escolha seu jogo"
    assert "Escolha um jogo para ver os produtos." in (embed.description or "")
    assert "Atendimento: on-line." in (embed.description or "")
    assert embed.footer.text == "4 jogos"


def test_open_button_style_and_label_are_configurable() -> None:
    config = _config()
    config.open_button_label = "Ver jogos"
    config.open_button_style = "primary"
    view = GameCatalogOpenView(config)
    button = view.children[0]

    assert isinstance(button, discord.ui.Button)
    assert button.label == "Ver jogos"
    assert button.style is discord.ButtonStyle.primary
    assert _button_style("danger") is discord.ButtonStyle.danger
