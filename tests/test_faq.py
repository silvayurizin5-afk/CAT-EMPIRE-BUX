from app.services.faq import normalize_text


def test_normalize_text_removes_accents_and_extra_spaces() -> None:
    assert normalize_text("  Você aceita PIX?  ") == "voce aceita pix?"


def test_normalize_text_is_case_insensitive() -> None:
    assert normalize_text("PAGAMENTO") == "pagamento"
