from app.bot.cogs.feedback import parse_feedback_message


def test_feedback_parser_accepts_normal_single_order_format() -> None:
    assert parse_feedback_message("5 - entrega rápida") == (5, None, "entrega rápida")
    assert parse_feedback_message("4/5: gostei") == (4, None, "gostei")


def test_feedback_parser_accepts_explicit_order_prefix() -> None:
    assert parse_feedback_message("5 #a1b2c3d4 - perfeito") == (
        5,
        "a1b2c3d4",
        "perfeito",
    )


def test_feedback_parser_rejects_invalid_or_empty_feedback() -> None:
    assert parse_feedback_message("6 - inválido") is None
    assert parse_feedback_message("5 -   ") is None
    assert parse_feedback_message("texto sem nota") is None
