import pytest

from app.bot.views.embed_builder import EmbedDraft, normalize_http_url, parse_hex_color


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("#5865F2", 0x5865F2),
        ("5865f2", 0x5865F2),
        ("0x5865F2", 0x5865F2),
        ("#abc", 0xAABBCC),
    ],
)
def test_parse_hex_color(raw: str, expected: int) -> None:
    assert parse_hex_color(raw) == expected


@pytest.mark.parametrize("raw", ["", "#12", "#gggggg", "1234567"])
def test_parse_hex_color_rejects_invalid_values(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_hex_color(raw)


def test_normalize_http_url() -> None:
    assert normalize_http_url(" https://example.com/a.png ") == "https://example.com/a.png"
    assert normalize_http_url("") == ""
    with pytest.raises(ValueError):
        normalize_http_url("javascript:alert(1)")


def test_embed_draft_builds_preview_and_link_buttons() -> None:
    draft = EmbedDraft(
        title="NEXTBUY",
        description="Painel da loja",
        color=0x123456,
        footer_text="Rodapé",
    )
    draft.fields.append(("Preço", "10 créditos", True))
    draft.buttons.append(("Abrir loja", "https://example.com"))

    embed = draft.build_embed()
    view = draft.build_link_view()

    assert embed.title == "NEXTBUY"
    assert embed.description == "Painel da loja"
    assert embed.color is not None
    assert embed.color.value == 0x123456
    assert embed.fields[0].name == "Preço"
    assert embed.fields[0].value == "10 créditos"
    assert embed.fields[0].inline is True
    assert embed.footer.text == "Rodapé"
    assert view is not None
    assert len(view.children) == 1
