import pytest

from app.services.ticket_settings import (
    TicketTemplateContext,
    render_ticket_template,
    validate_ticket_template,
)


def test_render_ticket_template_replaces_supported_tokens() -> None:
    context = TicketTemplateContext(
        customer="<@123>",
        order="abc12345",
        total="10,80 créditos",
        items="• Robux × 1",
    )
    rendered = render_ticket_template(
        "{customer} | {order} | {total}\n{items}",
        context,
    )
    assert rendered == "<@123> | abc12345 | 10,80 créditos\n• Robux × 1"


def test_validate_ticket_template_rejects_unknown_tokens() -> None:
    with pytest.raises(ValueError, match="Variáveis desconhecidas"):
        validate_ticket_template(
            "Pedido {order} para {password}",
            max_length=160,
            field_name="Título",
        )


def test_validate_ticket_template_rejects_blank_and_oversized_values() -> None:
    with pytest.raises(ValueError, match="não pode ficar vazio"):
        validate_ticket_template("   ", max_length=20, field_name="Mensagem")
    with pytest.raises(ValueError, match="passou do limite"):
        validate_ticket_template("x" * 21, max_length=20, field_name="Mensagem")
