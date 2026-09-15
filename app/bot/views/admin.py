from decimal import Decimal, InvalidOperation

import discord
from sqlalchemy.exc import IntegrityError

from app.db.session import SessionLocal
from app.services.catalog import create_product, upsert_robux_rate, upsert_terms
from app.services.configs import get_or_create_guild_config

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
    "logs_channel_id": "Logs",
}


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
