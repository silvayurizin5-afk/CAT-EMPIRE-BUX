from decimal import Decimal, InvalidOperation

import discord
from sqlalchemy.exc import IntegrityError

from app.bot.workflows.feedback_permissions import (
    FeedbackPermissionSyncError,
    sync_feedback_channel_permissions,
)
from app.db.session import SessionLocal
from app.services.catalog import create_product, upsert_terms
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
        label="Tipo", placeholder="item ou gamepass", max_length=24
    )
    price = discord.ui.TextInput(label="Preço em reais", placeholder="10,80", max_length=20)
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
            f"Produto **{product.name}** criado por **R$ {product.price_credits:.2f}**.",
            ephemeral=True,
        )
        from app.bot.views.store_panel import refresh_published_store_panel

        await refresh_published_store_panel(interaction.guild)


class TermsModal(discord.ui.Modal, title="Criar/atualizar termo"):
    code = discord.ui.TextInput(label="Código", placeholder="reembolsos", max_length=60)
    title_text = discord.ui.TextInput(label="Título", max_length=120)
    content = discord.ui.TextInput(
        label="Conteúdo", style=discord.TextStyle.paragraph, max_length=4000
    )
    emoji = discord.ui.TextInput(label="Emoji", required=False, max_length=128)
    ephemeral_message = discord.ui.TextInput(
        label="Submensagem ephemeral ao selecionar",
        required=False,
        style=discord.TextStyle.paragraph,
        max_length=1800,
        placeholder="Mensagem privada mostrada quando o cliente selecionar este termo.",
    )

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
                ephemeral_message=str(self.ephemeral_message),
            )
        await interaction.response.send_message(
            f"Termo **{terms.title}** salvo na versão {terms.version}.", ephemeral=True
        )


class AutoReplyModal(discord.ui.Modal, title="Resposta automática"):
    name = discord.ui.TextInput(label="Nome", placeholder="Pagamento", max_length=80)
    keywords = discord.ui.TextInput(
        label="Palavras-chave",
        placeholder="pagamento, pedido, pix",
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
    name = discord.ui.TextInput(label="Nome", placeholder="VIP", max_length=80)
    min_spend = discord.ui.TextInput(label="Meta em reais", placeholder="5000,00", max_length=20)
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
            await interaction.response.send_message(
                f"Não foi possível salvar: {exc}", ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"Faixa **{tier.name}** configurada a partir de **R$ {tier.min_spend:.2f}**.",
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
