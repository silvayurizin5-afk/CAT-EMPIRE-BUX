from decimal import Decimal, InvalidOperation

import discord
from sqlalchemy.exc import IntegrityError

from app.bot.workflows.feedback_permissions import (
    FeedbackPermissionSyncError,
    sync_feedback_channel_permissions,
)
from app.db.session import SessionLocal
from app.services.catalog import create_product, upsert_robux_rate, upsert_terms
from app.services.configs import get_or_create_guild_config
from app.services.faq import upsert_auto_reply
from app.services.ranks import upsert_rank_tier

ROLE_FIELDS = {
    "admin_role_id": "Administrador",
    "support_role_id": "Atendente",
    "delivery_role_id": "Entregador",
    "customer_role_id": "Cliente",
}

CHANNEL_FIELDS = {
    "ticket_category_id": "Categoria de tickets",
    "transcript_channel_id": "Transcripts",
    "deliveries_channel_id": "Entregas",
    "feedback_channel_id": "Feedbacks",
    "calculator_channel_id": "Calculadora",
    "faq_channel_id": "FAQ automático",
    "leaderboard_channel_id": "Ranking",
    "logs_channel_id": "Logs",
}


async def _sync_feedback_permissions_after_response(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        return
    try:
        await sync_feedback_channel_permissions(interaction.guild)
    except FeedbackPermissionSyncError as exc:
        await interaction.followup.send(f"Aviso: {exc}", ephemeral=True)


class RolePicker(discord.ui.RoleSelect):
    def __init__(self, field_name: str) -> None:
        super().__init__(placeholder="Escolha o cargo", min_values=1, max_values=1)
        self.field_name = field_name

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        role = self.values[0]
        async with SessionLocal() as session, session.begin():
            config = await get_or_create_guild_config(session, interaction.guild.id)
            setattr(config, self.field_name, role.id)
        await interaction.response.edit_message(
            content=f"Cargo **{ROLE_FIELDS[self.field_name]}** definido como {role.mention}.",
            view=None,
        )
        await _sync_feedback_permissions_after_response(interaction)


class RolePickerView(discord.ui.View):
    def __init__(self, field_name: str) -> None:
        super().__init__(timeout=120)
        self.add_item(RolePicker(field_name))


class ChannelPicker(discord.ui.ChannelSelect):
    def __init__(self, field_name: str) -> None:
        channel_types = [discord.ChannelType.text]
        if field_name == "ticket_category_id":
            channel_types = [discord.ChannelType.category]
        super().__init__(
            placeholder="Escolha o canal/categoria",
            min_values=1,
            max_values=1,
            channel_types=channel_types,
        )
        self.field_name = field_name

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        channel = self.values[0]
        async with SessionLocal() as session, session.begin():
            config = await get_or_create_guild_config(session, interaction.guild.id)
            setattr(config, self.field_name, channel.id)
        await interaction.response.edit_message(
            content=f"**{CHANNEL_FIELDS[self.field_name]}** definido como {channel.mention}.",
            view=None,
        )
        if self.field_name == "feedback_channel_id":
            await _sync_feedback_permissions_after_response(interaction)


class ChannelPickerView(discord.ui.View):
    def __init__(self, field_name: str) -> None:
        super().__init__(timeout=120)
        self.add_item(ChannelPicker(field_name))


class ConfigTargetView(discord.ui.View):
    def __init__(self, *, kind: str) -> None:
        super().__init__(timeout=120)
        mapping = ROLE_FIELDS if kind == "role" else CHANNEL_FIELDS
        options = [discord.SelectOption(label=label, value=field) for field, label in mapping.items()]
        select = discord.ui.Select(placeholder="O que você quer configurar?", options=options)
        self.kind = kind
        select.callback = self._selected
        self.add_item(select)

    async def _selected(self, interaction: discord.Interaction) -> None:
        select = self.children[0]
        if not isinstance(select, discord.ui.Select):
            return
        field_name = select.values[0]
        view = RolePickerView(field_name) if self.kind == "role" else ChannelPickerView(field_name)
        await interaction.response.edit_message(content="Agora escolha o valor:", view=view)


class ProductModal(discord.ui.Modal, title="Criar produto"):
    name = discord.ui.TextInput(label="Nome", max_length=120)
    slug = discord.ui.TextInput(label="Identificador", placeholder="ex: blox-fruits-gp", max_length=140)
    product_type = discord.ui.TextInput(
        label="Tipo", placeholder="item, robux ou gamepass", max_length=24
    )
    price = discord.ui.TextInput(label="Preço em créditos", placeholder="10,80", max_length=20)
    game_name = discord.ui.TextInput(label="Jogo", required=False, max_length=120)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        try:
            price = Decimal(str(self.price).replace(",", "."))
        except InvalidOperation:
            await interaction.response.send_message("Preço inválido.", ephemeral=True)
            return
        try:
            async with SessionLocal() as session, session.begin():
                product = await create_product(
                    session,
                    guild_id=interaction.guild.id,
                    name=str(self.name),
                    slug=str(self.slug),
                    product_type=str(self.product_type),
                    price_credits=price,
                    game_name=str(self.game_name),
                )
        except IntegrityError:
            await interaction.response.send_message(
                "Já existe um produto com esse identificador.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"Produto **{product.name}** criado por **{product.price_credits:.2f} créditos**.",
            ephemeral=True,
        )


class RobuxRateModal(discord.ui.Modal, title="Configurar cotação de Robux"):
    code = discord.ui.TextInput(label="Código", placeholder="instantaneo", max_length=40)
    label = discord.ui.TextInput(label="Nome exibido", placeholder="Robux instantâneo", max_length=80)
    price = discord.ui.TextInput(
        label="Créditos por 1 Robux", placeholder="0,051000", max_length=24
    )
    delivery = discord.ui.TextInput(
        label="Prazo/descrição", placeholder="Cai na hora", required=False, max_length=120
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        try:
            price = Decimal(str(self.price).replace(",", "."))
            if price <= 0:
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            await interaction.response.send_message("Cotação inválida.", ephemeral=True)
            return
        async with SessionLocal() as session, session.begin():
            rate = await upsert_robux_rate(
                session,
                guild_id=interaction.guild.id,
                code=str(self.code),
                label=str(self.label),
                price_per_robux=price,
                delivery_label=str(self.delivery),
            )
        await interaction.response.send_message(
            f"Cotação **{rate.label}** salva.", ephemeral=True
        )


class TermsModal(discord.ui.Modal, title="Criar/atualizar termo"):
    code = discord.ui.TextInput(label="Código", placeholder="reembolsos", max_length=60)
    title_text = discord.ui.TextInput(label="Título", max_length=120)
    content = discord.ui.TextInput(label="Conteúdo", style=discord.TextStyle.paragraph, max_length=4000)
    emoji = discord.ui.TextInput(label="Emoji", required=False, max_length=128)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        async with SessionLocal() as session, session.begin():
            terms = await upsert_terms(
                session,
                guild_id=interaction.guild.id,
                code=str(self.code),
                title=str(self.title_text),
                content=str(self.content),
                emoji=str(self.emoji),
            )
        await interaction.response.send_message(
            f"Termo **{terms.title}** salvo na versão {terms.version}.", ephemeral=True
        )


class AutoReplyModal(discord.ui.Modal, title="Resposta automática"):
    name = discord.ui.TextInput(label="Nome", placeholder="Pagamento Pix", max_length=80)
    keywords = discord.ui.TextInput(
        label="Palavras-chave",
        placeholder="pix, pagamento, aceita pix",
        max_length=500,
    )
    title_text = discord.ui.TextInput(label="Título do embed", max_length=160)
    content = discord.ui.TextInput(
        label="Resposta",
        style=discord.TextStyle.paragraph,
        max_length=4000,
    )
    emoji = discord.ui.TextInput(label="Emoji", required=False, max_length=128)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        keywords = [item.strip() for item in str(self.keywords).replace("\n", ",").split(",")]
        try:
            async with SessionLocal() as session, session.begin():
                reply = await upsert_auto_reply(
                    session,
                    guild_id=interaction.guild.id,
                    name=str(self.name),
                    keywords=keywords,
                    title=str(self.title_text),
                    content=str(self.content),
                    emoji=str(self.emoji),
                )
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.send_message(
            f"Resposta automática **{reply.name}** salva.", ephemeral=True
        )


class RankTierModal(discord.ui.Modal, title="Faixa de cliente"):
    name = discord.ui.TextInput(label="Nome", placeholder="SFAQ", max_length=80)
    min_spend = discord.ui.TextInput(label="Meta em créditos", placeholder="5000,00", max_length=20)
    dm_message = discord.ui.TextInput(
        label="Mensagem ao atingir a meta",
        required=False,
        style=discord.TextStyle.paragraph,
        max_length=1200,
    )

    def __init__(self, role_id: int) -> None:
        super().__init__()
        self.role_id = role_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        try:
            min_spend = Decimal(str(self.min_spend).replace(",", "."))
        except InvalidOperation:
            await interaction.response.send_message("Meta inválida.", ephemeral=True)
            return
        try:
            async with SessionLocal() as session, session.begin():
                tier = await upsert_rank_tier(
                    session,
                    guild_id=interaction.guild.id,
                    name=str(self.name),
                    min_spend=min_spend,
                    role_id=self.role_id,
                    dm_message=str(self.dm_message),
                )
        except (ValueError, IntegrityError) as exc:
            await interaction.response.send_message(f"Não foi possível salvar: {exc}", ephemeral=True)
            return
        await interaction.response.send_message(
            f"Faixa **{tier.name}** configurada a partir de **{tier.min_spend:.2f} créditos**.",
            ephemeral=True,
        )
        await _sync_feedback_permissions_after_response(interaction)


class RankRoleSelect(discord.ui.RoleSelect):
    def __init__(self) -> None:
        super().__init__(placeholder="Escolha o cargo da faixa", min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(RankTierModal(self.values[0].id))


class RankRoleView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=120)
        self.add_item(RankRoleSelect())


class AdminPanelView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=300)

    @discord.ui.button(label="Cargos", style=discord.ButtonStyle.secondary)
    async def roles(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            content="Escolha qual cargo configurar:", view=ConfigTargetView(kind="role")
        )

    @discord.ui.button(label="Canais", style=discord.ButtonStyle.secondary)
    async def channels(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            content="Escolha qual canal configurar:", view=ConfigTargetView(kind="channel")
        )

    @discord.ui.button(label="Novo produto", style=discord.ButtonStyle.primary)
    async def product(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(ProductModal())

    @discord.ui.button(label="Cotação Robux", style=discord.ButtonStyle.primary)
    async def robux(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(RobuxRateModal())

    @discord.ui.button(label="Termos", style=discord.ButtonStyle.primary)
    async def terms(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(TermsModal())

    @discord.ui.button(label="Auto-resposta", style=discord.ButtonStyle.primary)
    async def auto_reply(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(AutoReplyModal())

    @discord.ui.button(label="Faixa de cliente", style=discord.ButtonStyle.primary)
    async def rank_tier(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_message(
            "Escolha o cargo que representa esta faixa:",
            view=RankRoleView(),
            ephemeral=True,
        )

    @discord.ui.button(label="Publicar loja", style=discord.ButtonStyle.success)
    async def publish_store(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if interaction.channel is None:
            await interaction.response.send_message("Canal inválido.", ephemeral=True)
            return
        from app.bot.views.store import StoreHomeView

        embed = discord.Embed(
            title="NEXTBUY",
            description=(
                "Compre usando créditos, veja seu perfil e consulte os termos.\n"
                "**1 crédito = R$ 1,00.**"
            ),
        )
        await interaction.channel.send(embed=embed, view=StoreHomeView())
        await interaction.response.send_message("Painel publicado neste canal.", ephemeral=True)

    @discord.ui.button(label="Publicar ranking", style=discord.ButtonStyle.success)
    async def publish_ranking(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if interaction.guild is None or interaction.channel is None:
            await interaction.response.send_message("Canal inválido.", ephemeral=True)
            return
        from app.bot.workflows.leaderboard import refresh_leaderboard

        await interaction.response.defer(ephemeral=True, thinking=True)
        message = await refresh_leaderboard(interaction.guild, channel=interaction.channel)
        if message is None:
            await interaction.followup.send("Não consegui publicar o ranking.", ephemeral=True)
            return
        await interaction.followup.send("Ranking publicado e vinculado para atualização automática.", ephemeral=True)
