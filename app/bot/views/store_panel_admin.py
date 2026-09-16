from decimal import Decimal, InvalidOperation

import discord

from app.bot.views.admin import ProductModal
from app.bot.views.product_admin import send_product_management
from app.bot.views.store_panel import (
    build_store_panel_embed,
    publish_store_panel,
    refresh_published_store_panel,
)
from app.db.models import Product
from app.db.session import SessionLocal
from app.db.store_models import StoreCoupon
from app.services.catalog import list_products
from app.services.store_panel import (
    get_or_create_store_panel,
    list_coupons,
    list_store_products,
    save_store_product_selection,
    set_coupon_active,
    upsert_coupon,
)


def _parse_color(value: str) -> int:
    cleaned = value.strip().lower()
    if cleaned.startswith("#"):
        cleaned = cleaned[1:]
    if cleaned.startswith("0x"):
        cleaned = cleaned[2:]
    if len(cleaned) == 3:
        cleaned = "".join(char * 2 for char in cleaned)
    if len(cleaned) != 6:
        raise ValueError("Cor inválida")
    try:
        result = int(cleaned, 16)
    except ValueError as exc:
        raise ValueError("Cor inválida") from exc
    if not 0 <= result <= 0xFFFFFF:
        raise ValueError("Cor inválida")
    return result


def _http_url_or_none(value: str) -> str | None:
    cleaned = value.strip()
    if not cleaned:
        return None
    if not cleaned.lower().startswith(("http://", "https://")):
        raise ValueError("A URL precisa começar com http:// ou https://")
    return cleaned


async def _admin_preview(guild_id: int) -> discord.Embed:
    async with SessionLocal() as session, session.begin():
        config = await get_or_create_store_panel(session, guild_id)
        products = await list_store_products(session, guild_id=guild_id, config=config)
        return build_store_panel_embed(config, len(products))


class StoreAppearanceModal(discord.ui.Modal, title="Visual do painel da loja"):
    def __init__(self, *, config) -> None:
        super().__init__()
        self.title_input = discord.ui.TextInput(
            label="Título",
            max_length=256,
            default=config.title[:256],
        )
        self.description_input = discord.ui.TextInput(
            label="Descrição",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=4000,
            default=config.description[:4000],
        )
        self.color_input = discord.ui.TextInput(
            label="Cor hexadecimal",
            max_length=9,
            default=f"#{config.color:06X}",
        )
        self.image_input = discord.ui.TextInput(
            label="Banner / imagem (URL)",
            required=False,
            max_length=1000,
            default=(config.image_url or "")[:1000],
        )
        self.footer_input = discord.ui.TextInput(
            label="Rodapé",
            required=False,
            max_length=2048,
            default=config.footer_text[:2048],
        )
        for item in (
            self.title_input,
            self.description_input,
            self.color_input,
            self.image_input,
            self.footer_input,
        ):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        try:
            color = _parse_color(str(self.color_input))
            image_url = _http_url_or_none(str(self.image_input))
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        async with SessionLocal() as session, session.begin():
            config = await get_or_create_store_panel(session, interaction.guild.id)
            config.title = str(self.title_input).strip() or "NEXTBUY"
            config.description = str(self.description_input).strip()
            config.color = color
            config.image_url = image_url
            config.footer_text = str(self.footer_input).strip()

        await interaction.response.edit_message(
            content="Visual atualizado. A prévia abaixo já usa a nova configuração.",
            embed=await _admin_preview(interaction.guild.id),
            view=StorePanelAdminView(owner_id=interaction.user.id),
        )
        await refresh_published_store_panel(interaction.guild)


class StoreControlsModal(discord.ui.Modal, title="Controles do painel"):
    def __init__(self, *, config) -> None:
        super().__init__()
        self.placeholder = discord.ui.TextInput(
            label="Texto do seletor de produtos",
            max_length=100,
            default=config.product_placeholder[:100],
        )
        self.topup = discord.ui.TextInput(
            label="Botão de créditos",
            max_length=80,
            default=config.topup_label[:80],
        )
        self.profile = discord.ui.TextInput(
            label="Botão de perfil",
            max_length=80,
            default=config.profile_label[:80],
        )
        self.terms = discord.ui.TextInput(
            label="Botão de termos",
            max_length=80,
            default=config.terms_label[:80],
        )
        self.thumbnail = discord.ui.TextInput(
            label="Thumbnail (URL)",
            required=False,
            max_length=1000,
            default=(config.thumbnail_url or "")[:1000],
        )
        for item in (self.placeholder, self.topup, self.profile, self.terms, self.thumbnail):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        try:
            thumbnail_url = _http_url_or_none(str(self.thumbnail))
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        async with SessionLocal() as session, session.begin():
            config = await get_or_create_store_panel(session, interaction.guild.id)
            config.product_placeholder = str(self.placeholder).strip() or "Selecione um produto"
            config.topup_label = str(self.topup).strip() or "Adicionar créditos"
            config.profile_label = str(self.profile).strip() or "Meu perfil"
            config.terms_label = str(self.terms).strip() or "Termos"
            config.thumbnail_url = thumbnail_url

        await interaction.response.edit_message(
            content="Seletor e botões atualizados.",
            embed=await _admin_preview(interaction.guild.id),
            view=StorePanelAdminView(owner_id=interaction.user.id),
        )
        await refresh_published_store_panel(interaction.guild)


class StoreProductMultiSelect(discord.ui.Select):
    def __init__(self, products: list[Product], selected_ids: list[int]) -> None:
        selected = set(int(item) for item in selected_ids)
        options = [
            discord.SelectOption(
                label="Todos os produtos ativos",
                value="all",
                description="Acompanha automaticamente todos os produtos ativos",
                default=not selected,
            )
        ]
        for product in products[:24]:
            options.append(
                discord.SelectOption(
                    label=product.name[:100],
                    value=str(product.id),
                    description=(
                        f"{product.product_type} • "
                        f"{'ativo' if product.active else 'desativado'} • "
                        f"estoque {'∞' if product.stock_quantity is None else product.stock_quantity}"
                    )[:100],
                    emoji=product.emoji or None,
                    default=product.id in selected,
                )
            )
        super().__init__(
            placeholder="Escolha quais produtos aparecem no painel",
            min_values=1,
            max_values=len(options),
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        product_ids = [] if "all" in self.values else [int(value) for value in self.values]
        async with SessionLocal() as session, session.begin():
            config = await get_or_create_store_panel(session, interaction.guild.id)
            await save_store_product_selection(
                session,
                config=config,
                product_ids=product_ids,
            )
        await interaction.response.edit_message(
            content=(
                "O painel agora acompanha todos os produtos ativos."
                if not product_ids
                else f"**{len(product_ids)}** produto(s) selecionado(s) para a loja."
            ),
            embed=await _admin_preview(interaction.guild.id),
            view=StorePanelAdminView(owner_id=interaction.user.id),
        )
        await refresh_published_store_panel(interaction.guild)


class StoreProductSelectionView(discord.ui.View):
    def __init__(self, products: list[Product], selected_ids: list[int]) -> None:
        super().__init__(timeout=300)
        self.add_item(StoreProductMultiSelect(products, selected_ids))


class CouponCreateModal(discord.ui.Modal, title="Criar / atualizar cupom"):
    code = discord.ui.TextInput(label="Código", placeholder="NEXT10", max_length=40)
    discount = discord.ui.TextInput(label="Desconto em %", placeholder="10", max_length=8)
    max_uses = discord.ui.TextInput(
        label="Limite de usos (vazio = ilimitado)",
        required=False,
        placeholder="100",
        max_length=10,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        try:
            discount = Decimal(str(self.discount).strip().replace(",", "."))
            if discount <= 0 or discount >= 100:
                raise InvalidOperation
            raw_limit = str(self.max_uses).strip()
            max_uses = int(raw_limit) if raw_limit else None
            if max_uses is not None and max_uses <= 0:
                raise ValueError
        except (InvalidOperation, ValueError):
            await interaction.response.send_message(
                "Use um desconto maior que 0 e menor que 100. O limite deve ser inteiro positivo.",
                ephemeral=True,
            )
            return

        try:
            async with SessionLocal() as session, session.begin():
                coupon = await upsert_coupon(
                    session,
                    guild_id=interaction.guild.id,
                    code=str(self.code),
                    discount_percent=discount,
                    max_uses=max_uses,
                )
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        await interaction.response.edit_message(
            content=f"Cupom `{coupon.code}` salvo com **{coupon.discount_percent:.2f}%** de desconto.",
            embed=await _admin_preview(interaction.guild.id),
            view=StorePanelAdminView(owner_id=interaction.user.id),
        )


class CouponActionsView(discord.ui.View):
    def __init__(self, coupon_id: int, owner_id: int) -> None:
        super().__init__(timeout=180)
        self.coupon_id = coupon_id
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("Esse painel pertence a outra pessoa.", ephemeral=True)
        return False

    @discord.ui.button(label="Ativar / Desativar", style=discord.ButtonStyle.secondary)
    async def toggle(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if interaction.guild is None:
            return
        async with SessionLocal() as session, session.begin():
            coupon = await session.get(StoreCoupon, self.coupon_id)
            if coupon is None or coupon.guild_id != interaction.guild.id:
                await interaction.response.send_message("Cupom não encontrado.", ephemeral=True)
                return
            await set_coupon_active(session, coupon=coupon, active=not coupon.active)
            status = "ativado" if coupon.active else "desativado"
        await interaction.response.edit_message(
            content=f"Cupom `{coupon.code}` {status}.",
            embed=await _admin_preview(interaction.guild.id),
            view=StorePanelAdminView(owner_id=self.owner_id),
        )


class CouponManageSelect(discord.ui.Select):
    def __init__(self, coupons: list[StoreCoupon], owner_id: int) -> None:
        self.owner_id = owner_id
        options = [
            discord.SelectOption(
                label=coupon.code[:100],
                value=str(coupon.id),
                description=(
                    f"{coupon.discount_percent:.2f}% • "
                    f"{'ativo' if coupon.active else 'desativado'} • "
                    f"usos {coupon.uses}/{coupon.max_uses if coupon.max_uses is not None else '∞'}"
                )[:100],
            )
            for coupon in coupons[:25]
        ]
        super().__init__(placeholder="Escolha o cupom", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        coupon_id = int(self.values[0])
        async with SessionLocal() as session:
            coupon = await session.get(StoreCoupon, coupon_id)
        if coupon is None or coupon.guild_id != interaction.guild.id:
            await interaction.response.edit_message(content="Cupom não encontrado.", view=None)
            return
        embed = discord.Embed(
            title=f"Cupom • {coupon.code}",
            color=discord.Color.from_rgb(43, 45, 49),
        )
        embed.add_field(name="Desconto", value=f"{coupon.discount_percent:.2f}%")
        embed.add_field(name="Status", value="Ativo" if coupon.active else "Desativado")
        embed.add_field(
            name="Usos",
            value=f"{coupon.uses}/{coupon.max_uses if coupon.max_uses is not None else '∞'}",
        )
        await interaction.response.edit_message(
            content=None,
            embed=embed,
            view=CouponActionsView(coupon.id, self.owner_id),
        )


class CouponManageView(discord.ui.View):
    def __init__(self, coupons: list[StoreCoupon], owner_id: int) -> None:
        super().__init__(timeout=300)
        self.add_item(CouponManageSelect(coupons, owner_id))


class StorePublishChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, owner_id: int) -> None:
        super().__init__(
            placeholder="Escolha o canal da loja",
            min_values=1,
            max_values=1,
            channel_types=[discord.ChannelType.text],
        )
        self.owner_id = owner_id

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or interaction.user.id != self.owner_id:
            return
        selected = self.values[0]
        channel = interaction.guild.get_channel(selected.id)
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.edit_message(content="Canal inválido.", view=None)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            message = await publish_store_panel(interaction, channel)
        except (discord.Forbidden, discord.HTTPException):
            await interaction.edit_original_response(
                content="Não consegui publicar nesse canal. Revise as permissões do bot.",
                embed=None,
                view=None,
            )
            return
        if message is None:
            await interaction.edit_original_response(content="Não consegui publicar o painel.")
            return
        await interaction.edit_original_response(
            content=f"Painel da loja publicado/atualizado em {channel.mention}.",
            embed=None,
            view=None,
        )


class StorePublishChannelView(discord.ui.View):
    def __init__(self, owner_id: int) -> None:
        super().__init__(timeout=180)
        self.add_item(StorePublishChannelSelect(owner_id))


STORE_PANEL_ACTIONS = (
    ("appearance", "Visual da embed", "Título, descrição, cor, banner e rodapé"),
    ("controls", "Seletor e botões", "Textos do seletor, créditos, perfil e termos"),
    ("create_product", "Criar produto", "Cadastre item, Robux ou Game Pass"),
    ("products", "Produtos exibidos", "Escolha os produtos do seletor da loja"),
    ("manage_products", "Preços e estoques", "Edite preço, estoque, imagem e status"),
    ("create_coupon", "Criar cupom", "Código, porcentagem e limite de usos"),
    ("manage_coupons", "Gerenciar cupons", "Consulte usos e ative/desative cupons"),
    ("publish", "Publicar / atualizar", "Escolha o canal e envie uma única embed"),
)


class StorePanelActionSelect(discord.ui.Select):
    def __init__(self) -> None:
        options = [
            discord.SelectOption(label=label, value=value, description=description)
            for value, label, description in STORE_PANEL_ACTIONS
        ]
        super().__init__(
            placeholder="O que deseja configurar na loja?",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if isinstance(self.view, StorePanelAdminView):
            await self.view.handle_action(interaction, self.values[0])


class StorePanelAdminView(discord.ui.View):
    def __init__(self, owner_id: int) -> None:
        super().__init__(timeout=900)
        self.owner_id = owner_id
        self.add_item(StorePanelActionSelect())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("Esse painel pertence a outra pessoa.", ephemeral=True)
        return False

    async def handle_action(self, interaction: discord.Interaction, action: str) -> None:
        if interaction.guild is None:
            return

        if action in {"appearance", "controls"}:
            async with SessionLocal() as session, session.begin():
                config = await get_or_create_store_panel(session, interaction.guild.id)
                modal = (
                    StoreAppearanceModal(config=config)
                    if action == "appearance"
                    else StoreControlsModal(config=config)
                )
            await interaction.response.send_modal(modal)
            return

        if action == "create_product":
            await interaction.response.send_modal(ProductModal())
            return

        if action == "products":
            async with SessionLocal() as session, session.begin():
                config = await get_or_create_store_panel(session, interaction.guild.id)
                selected_ids = list(config.selected_product_ids or [])
                products = await list_products(session, guild_id=interaction.guild.id)
            if not products:
                await interaction.response.send_message(
                    "Crie pelo menos um produto antes de configurar o seletor.",
                    ephemeral=True,
                )
                return
            await interaction.response.edit_message(
                content="Selecione os produtos do painel. **Todos** acompanha os ativos automaticamente.",
                embed=None,
                view=StoreProductSelectionView(products, selected_ids),
            )
            return

        if action == "manage_products":
            await send_product_management(interaction)
            return

        if action == "create_coupon":
            await interaction.response.send_modal(CouponCreateModal())
            return

        if action == "manage_coupons":
            async with SessionLocal() as session:
                coupons = await list_coupons(session, guild_id=interaction.guild.id)
            if not coupons:
                await interaction.response.send_message(
                    "Nenhum cupom cadastrado. Use **Criar cupom** primeiro.",
                    ephemeral=True,
                )
                return
            await interaction.response.edit_message(
                content="Escolha o cupom:",
                embed=None,
                view=CouponManageView(coupons, self.owner_id),
            )
            return

        if action == "publish":
            await interaction.response.edit_message(
                content="Escolha o canal onde a loja ficará publicada:",
                embed=None,
                view=StorePublishChannelView(self.owner_id),
            )


async def send_store_panel_admin(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        return
    await interaction.response.edit_message(
        content="Configure toda a loja por este seletor. A embed abaixo é a prévia atual.",
        embed=await _admin_preview(interaction.guild.id),
        view=StorePanelAdminView(owner_id=interaction.user.id),
    )
