from datetime import UTC, datetime

from app.bot.cogs.audit_logs import build_audit_embed
from app.services.audit import PendingAuditDelivery


def test_audit_embed_is_human_readable() -> None:
    item = PendingAuditDelivery(
        delivery_id=1,
        audit_log_id=42,
        guild_id=123,
        logs_channel_id=456,
        actor_discord_id=111,
        action="order.payment_confirmed_manual_pix",
        target_type="order",
        target_id="4fb4263b-e216-4f0a-9426-39c56fa96d43",
        details={
            "amount_brl": "22.00",
            "customer_discord_id": 222,
            "channel_id": 333,
            "transcript_saved": True,
            "discount_percent": "10",
        },
        created_at=datetime.now(UTC),
    )

    embed = build_audit_embed(item)
    assert embed.title == "NEXTBUY • Pagamento PIX confirmado"
    fields = {field.name: field.value for field in embed.fields}
    assert fields["Responsável"] == "<@111>"
    assert fields["Pedido"] == "`#4fb4263b`"
    assert fields["Valor"] == "R$ 22,00"
    assert fields["Cliente"] == "<@222>"
    assert fields["Canal"] == "<#333>"
    assert fields["Transcript salvo"] == "Sim"
    assert fields["Desconto"] == "10%"
    assert embed.footer.text == (
        "NEXTBUY • Auditoria #42 • Código: order.payment_confirmed_manual_pix"
    )
