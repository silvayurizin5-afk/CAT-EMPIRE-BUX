from app.bot.workflows.transcripts import _ticket_metadata


def test_ticket_metadata_extracts_order_and_customer() -> None:
    order, customer = _ticket_metadata(
        "NEXTBUY order=4fb4263b-e216-4f0a-9426-39c56fa96d43 customer=790652243292192818"
    )
    assert order == "4fb4263b"
    assert customer == "790652243292192818"


def test_ticket_metadata_ignores_unrelated_topic() -> None:
    assert _ticket_metadata("canal comum") == (None, None)
