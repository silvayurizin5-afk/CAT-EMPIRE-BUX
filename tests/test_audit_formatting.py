from datetime import UTC, datetime

from app.bot.cogs.audit_logs import build_audit_embed
from app.services.audit import PendingAuditDelivery


def _audit_item() -> PendingAuditDelivery:
    return PendingAuditDelivery(
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


def test_audit_embed_is_human_readable_with_order_context() -> None:
    embed = build_audit_embed(
        _audit_item(),
        order_context={
            "customer_id": 222,
            "customer_name": "Danonin",
            "account_created_at": 1703187514,
            "products": ["Notifier × 1"],
            "total": "22.00",
            "status": "delivered",
            "channel_id": 333,
        },
    )

    assert embed.title == "NEXTBUY • Pagamento PIX confirmado"
    fields = {field.name: field.value for field in embed.fields}
    assert fields["Responsável"] == "<@111>"
    assert "<@222> `Danonin (222)`" in fields["Usuário"]
    assert "**ID:** `222`" in fields["Usuário"]
    assert "**Conta criada em:** <t:1703187514:F>" in fields["Usuário"]
    assert "**Produto(s):** Notifier × 1" in fields["Detalhes"]
    assert "**Valor:** R$ 22,00" in fields["Detalhes"]
    assert "**Status:** Entregue" in fields["Detalhes"]
    assert "**Canal:** <#333>" in fields["Detalhes"]
    assert "Pedido" not in fields
    assert fields["Transcript salvo"] == "Sim"
    assert fields["Desconto"] == "10%"
    assert embed.footer.text == "NEXTBUY • Registro de atividades"
    assert "Código:" not in embed.footer.text
    assert "#42" not in embed.footer.text


def test_audit_embed_falls_back_without_order_context() -> None:
    embed = build_audit_embed(_audit_item())
    fields = {field.name: field.value for field in embed.fields}
    assert "Pedido" not in fields
    assert fields["Valor"] == "R$ 22,00"
    assert fields["Cliente"] == "<@222>"
    assert fields["Canal"] == "<#333>"
