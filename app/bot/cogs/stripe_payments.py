from decimal import Decimal, InvalidOperation

import discord
from discord.ext import commands

from app.bot.views import store
from app.db.session import SessionLocal
from app.integrations.stripe_gateway import StripeGateway, StripeGatewayError
from app.services.stripe_topups import create_stripe_topup
from app.services.users import get_or_create_user


class StripeCheckoutLinkView(discord.ui.View):
    def __init__(self, url: str) -> None:
        super().__init__(timeout=900)
        self.add_item(discord.ui.Button(label="Pagar com Stripe", url=url))


class StripeTopUpModal(discord.ui.Modal, title="Adicionar créditos"):
    amount = discord.ui.TextInput(
        label="Valor em reais",
        placeholder="Ex: 10,80",
        min_length=1,
        max_length=20,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        try:
            amount = Decimal(str(self.amount).strip().replace(",", "."))
            if amount <= 0:
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            await interaction.response.send_message("Valor inválido.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            async with SessionLocal() as session, session.begin():
                user = await get_or_create_user(session, interaction.user.id)
                topup = await create_stripe_topup(
                    session,
                    user_id=user.id,
                    guild_id=interaction.guild.id,
                    amount_brl=amount,
                    stripe_gateway=StripeGateway(),
                )
        except StripeGatewayError:
            await interaction.followup.send(
                "Não consegui abrir o checkout da Stripe agora. A configuração do gateway precisa ser revisada.",
                ephemeral=True,
            )
            return

        if not topup.checkout_url:
            await interaction.followup.send(
                "A Stripe não retornou um checkout válido. Tente novamente mais tarde.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"Recarga criada: **R$ {topup.amount_brl:.2f} → {topup.credits_amount:.2f} créditos**.\n"
            "O saldo só entra depois que a Stripe confirmar o pagamento pelo webhook.",
            view=StripeCheckoutLinkView(topup.checkout_url),
            ephemeral=True,
        )


class StripePaymentsCog(commands.Cog):
    """Ativa a Stripe como gateway principal sem apagar suporte a recargas legadas."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot


async def setup(bot: commands.Bot) -> None:
    # StoreHomeView resolve TopUpModal no namespace do módulo no momento do clique.
    # Isso preserva o restante do fluxo da loja e troca somente o gateway de recarga.
    store.TopUpModal = StripeTopUpModal
    store.CheckoutLinkView = StripeCheckoutLinkView
    await bot.add_cog(StripePaymentsCog(bot))
