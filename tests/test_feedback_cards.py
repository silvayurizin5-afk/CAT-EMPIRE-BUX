from app.services.feedback_cards import _sanitize_card_text, render_feedback_card


def test_feedback_card_is_png() -> None:
    image = render_feedback_card(
        customer_name="Cliente Teste",
        stars=5,
        comment="Atendimento ótimo e entrega concluída sem problemas.",
        order_short_id="abc12345",
    )
    assert image.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(image) > 1000


def test_feedback_card_handles_accents_and_emoji_without_tofu_source() -> None:
    sanitized = _sanitize_card_text("😎 legal — ação, café e coração ❤️")
    assert "😎" not in sanitized
    assert "❤️" not in sanitized
    assert "ação" in sanitized
    assert "café" in sanitized

    image = render_feedback_card(
        customer_name="Danonín 😎",
        stars=5,
        comment="😎 legal — ação, café e coração ❤️",
        order_short_id="abc12345",
    )
    assert image.startswith(b"\x89PNG\r\n\x1a\n")


def test_feedback_card_rejects_invalid_stars() -> None:
    try:
        render_feedback_card(
            customer_name="Cliente",
            stars=0,
            comment="Teste",
            order_short_id="abc12345",
        )
    except ValueError:
        return
    raise AssertionError("era esperado ValueError para nota inválida")
