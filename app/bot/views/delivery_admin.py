from __future__ import annotations

from types import SimpleNamespace

import discord

from app.bot.cogs.delivery_runtime import _configured_delivery_banner, _prepare_delivery_banner
from app.bot.components_v2 import CardLayout, add_action_row
from app.db.session import SessionLocal
from app.services.delivery_settings import (
    effective_delivery_config,
    render_delivery,
    validate_delivery_templates,
)
from app.services.store_panel import get_or_create_store_panel


async def _load_config(guild_id: int) -> dict[str, object]:
    async with SessionLocal() as session, session.begin():
        panel = await get_or_create_store_panel(session, guild_id)
        return effective_delivery_config(dict(panel.delivery_config or {}))


async def _save_config(guild_id: int, values: dict[str, object]) -> None:
    validate_delivery_templates(values)
    async with SessionLocal() as session, session.begin():
        panel = await get_or_create_store_panel(session, guild_id)
        panel.delivery_config = dict(values)


class DeliveryMessageModal(discord.ui.Modal, title="Mensagem de entrega"):
    def __init__(self, config: dict[str, object]) -> None:
        super().__init__()
        self.title_template = discord.ui.TextInput(
            label="Primeira linha / título (aceita markdown)",
            required=False,
            max_length=256,
            default=str(config["title_template"])[:256],
        )
        self.body_template = discord.ui.TextInput(
            label="Corpo da mensagem",
            style=discord.TextStyle.paragraph,
            max_length=3000,
            default=str(config["body_template"])[:3000],
        )
        self.product_template = discord.ui.TextInput(
            label="Bloco de cada produto",
            style=discord.TextStyle.paragraph,
            max_length=1000,
            default=str(config["product_template"])[:1000],
        )
        self.footer_template = discord.ui.TextInput(
            label="Rodapé",
            required=False,
            max_length=500,
            default=str(config["footer_template"])[:500],
        )
        for item in (
            self.title_template,
            self.body_template,
            self.product_template,
            self.footer_template,
        ):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        config = await _load_config(interaction.guild.id)
        config.update(
            {
                "title_template": str(self.title_template).strip(),
                "body_template": str(self.body_template).strip(),
                "product_template": str(self.product_template).strip(),
                "footer_template": str(self.footer_template).strip(),
            }
        )
        try:
            await _save_config(interaction.guild.id, config)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.send_message(
            "Mensagem pública de entrega atualizada.",
            ephemeral=True,
        )


class DeliveryVisualModal(discord.ui.Modal, title="Visual da entrega"):
    def __init__(self, config: dict[str, object]) -> None:
        super().__init__()
        self.accent = discord.ui.TextInput(
            label="Cor lateral em HEX",
            placeholder="#23A55A",
            max_length=7,
            default=str(config["accent_color"])[:7],
        )
        self.show_image = discord.ui.TextInput(
            label="Usar imagem/emoji como ícone? sim/não",
            max_length=3,
            default="sim" if bool(config["show_image"]) else "não",
        )
        self.add_item(self.accent)
        self.add_item(self.show_image)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        answer = str(self.show_image).strip().casefold()
        if answer not in {"sim", "nao", "não"}:
            await interaction.response.send_message(
                "Em usar imagem/emoji como ícone, use apenas `sim` ou `não`.",
                ephemeral=True,
            )
            return
        accent = str(self.accent).strip()
        if len(accent.lstrip("#")) != 6:
            await interaction.response.send_message(
                "A cor deve estar no formato HEX, por exemplo `#23A55A`.",
                ephemeral=True,
            )
            return
        try:
            int(accent.lstrip("#"), 16)
        except ValueError:
            await interaction.response.send_message(
                "A cor HEX é inválida.",
                ephemeral=True,
            )
            return

        config = await _load_config(interaction.guild.id)
        config.update(
            {
                "accent_color": "#" + accent.lstrip("#").upper(),
                "show_image": answer == "sim",
            }
        )
        await _save_config(interaction.guild.id, config)
        await interaction.response.send_message("Visual de entrega atualizado.", ephemeral=True)


class DeliveryBannerModal(discord.ui.Modal, title="Banner da entrega"):
    def __init__(self, config: dict[str, object]) -> None:
        super().__init__()
        self.enabled = discord.ui.TextInput(
            label="Ativar banner? sim/não",
            max_length=3,
            default="sim" if bool(config.get("banner_enabled", True)) else "não",
        )
        self.url = discord.ui.TextInput(
            label="URL da imagem/GIF/WebP",
            required=False,
            style=discord.TextStyle.paragraph,
            max_length=1800,
            default=str(config.get("banner_url") or "")[:1800],
            placeholder="Qualquer URL http/https de imagem, inclusive Discord CDN.",
        )
        self.normalize = discord.ui.TextInput(
            label="Corrigir/normalizar animação? sim/não",
            max_length=3,
            default=(
                "sim"
                if bool(config.get("banner_normalize_animation", True))
                else "não"
            ),
        )
        self.add_item(self.enabled)
        self.add_item(self.url)
        self.add_item(self.normalize)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return

        enabled = str(self.enabled).strip().casefold()
        normalize = str(self.normalize).strip().casefold()
        if enabled not in {"sim", "nao", "não"} or normalize not in {"sim", "nao", "não"}:
            await interaction.response.send_message(
                "Use apenas `sim` ou `não` nos campos de ativação.",
                ephemeral=True,
            )
            return

        url = str(self.url).strip()
        config = await _load_config(interaction.guild.id)
        config.update(
            {
                "banner_enabled": enabled == "sim",
                "banner_source": "url",
                "banner_mode": "animated",
                "banner_normalize_animation": normalize == "sim",
                "banner_url": url,
            }
        )

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await _save_config(interaction.guild.id, config)
        except ValueError as exc:
            await interaction.edit_original_response(content=str(exc))
            return

        if enabled != "sim":
            await interaction.edit_original_response(
                content="Banner de entregas desativado."
            )
            return

        if not url:
            await interaction.edit_original_response(
                content="Banner ativado, mas nenhuma URL foi informada."
            )
            return

        banner_file, banner_url, filename = await _prepare_delivery_banner(
            config,
            upload_limit=interaction.guild.filesize_limit,
        )
        if banner_file is not None:
            banner_file.close()

        if filename:
            await interaction.edit_original_response(
                content=(
                    "Banner salvo e processado com sucesso.\n"
                    f"**Arquivo enviado ao Discord:** `{filename}`\n"
                    f"**Referência usada no Components V2:** `{banner_url}`"
                )
            )
        else:
            await interaction.edit_original_response(
                content=(
                    "Banner salvo. Não consegui converter essa mídia, então o Discord "
                    "vai tentar carregar a **URL diretamente** no MediaGallery."
                )
            )


class DeliveryEmojiModal(discord.ui.Modal, title="Emojis principais da entrega"):
    def __init__(self, config: dict[str, object]) -> None:
        super().__init__()
        fields = (
            ("delivery", "Emoji Entrega Realizada", "delivery_emoji"),
            ("arrow", "Emoji seta", "arrow_emoji"),
            ("user", "Emoji cliente", "user_emoji"),
            ("separator", "Emoji separador", "separator_emoji"),
            ("verified", "Emoji status verificado", "verified_emoji"),
        )
        self._keys: list[tuple[str, discord.ui.TextInput]] = []
        for attr, label, key in fields:
            field = discord.ui.TextInput(
                label=label,
                required=False,
                max_length=128,
                default=str(config[key])[:128],
            )
            setattr(self, attr, field)
            self._keys.append((key, field))
            self.add_item(field)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        config = await _load_config(interaction.guild.id)
        for key, field in self._keys:
            config[key] = str(field).strip()
        await _save_config(interaction.guild.id, config)
        await interaction.response.send_message("Emojis principais atualizados.", ephemeral=True)


class DeliveryProductEmojiModal(discord.ui.Modal, title="Emojis dos produtos"):
    def __init__(self, config: dict[str, object]) -> None:
        super().__init__()
        fields = (
            ("order_icon", "Emoji Produto(s)", "order_emoji"),
            ("game", "Emoji do jogo", "game_emoji"),
            ("product", "Emoji do produto", "product_emoji"),
            ("discount", "Emoji do desconto", "discount_emoji"),
        )
        self._keys: list[tuple[str, discord.ui.TextInput]] = []
        for attr, label, key in fields:
            field = discord.ui.TextInput(
                label=label,
                required=False,
                max_length=128,
                default=str(config[key])[:128],
            )
            setattr(self, attr, field)
            self._keys.append((key, field))
            self.add_item(field)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        config = await _load_config(interaction.guild.id)
        for key, field in self._keys:
            config[key] = str(field).strip()
        await _save_config(interaction.guild.id, config)
        await interaction.response.send_message("Emojis de produtos atualizados.", ephemeral=True)


class DeliveryAdminView(discord.ui.LayoutView):
    def __init__(self, *, owner_id: int) -> None:
        super().__init__(timeout=900)
        self.owner_id = owner_id
        card = CardLayout(
            title="NEXTBUY • Entregas",
            description="Configure livremente a mensagem publicada quando um pedido é entregue.",
            lines=[
                "**Gerais:** `{client}`, `{products}`",
                "**Emojis:** `{delivery}`, `{arrow}`, `{user}`, `{separator}`, `{verified}`, `{order_icon}`, `{game_emoji}`, `{product_emoji}`, `{discount_emoji}`",
                "**Produto:** `{product}`, `{game}`, `{game_or_product}`, `{quantity}`, `{unit_price}`, `{line_total}`, `{robux_part}`, `{discount_line}`",
                "Texto, markdown e emojis customizados podem ser colocados diretamente nos templates.",
                "**Banner:** aceita qualquer URL HTTP/HTTPS de imagem.",
                "**Animação:** GIF/APNG/WebP animado pode ser normalizado automaticamente para evitar frames piscando.",
            ],
            footer=(
                "Imagens de produto/jogo continuam como ícone inline. "
                "O banner pode ser reprocessado e anexado à própria mensagem."
            ),
            timeout=900,
        )
        self.container = card.container
        card.remove_item(card.container)
        self.add_item(self.container)

        edit_message = discord.ui.Button(label="Mensagem", style=discord.ButtonStyle.primary)
        edit_message.callback = self._edit_message
        edit_visual = discord.ui.Button(label="Visual", style=discord.ButtonStyle.secondary)
        edit_visual.callback = self._edit_visual
        edit_emojis = discord.ui.Button(label="Emojis", style=discord.ButtonStyle.secondary)
        edit_emojis.callback = self._edit_emojis
        edit_product_emojis = discord.ui.Button(
            label="Emojis produtos",
            style=discord.ButtonStyle.secondary,
        )
        edit_product_emojis.callback = self._edit_product_emojis
        preview = discord.ui.Button(label="Prévia", style=discord.ButtonStyle.secondary)
        preview.callback = self._preview
        add_action_row(
            self.container,
            edit_message,
            edit_visual,
            edit_emojis,
            edit_product_emojis,
            preview,
        )
        edit_banner = discord.ui.Button(
            label="Banner",
            style=discord.ButtonStyle.secondary,
        )
        edit_banner.callback = self._edit_banner
        add_action_row(self.container, edit_banner)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("Esse painel pertence a outra pessoa.", ephemeral=True)
        return False

    async def _edit_message(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        config = await _load_config(interaction.guild.id)
        await interaction.response.send_modal(DeliveryMessageModal(config))

    async def _edit_visual(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        config = await _load_config(interaction.guild.id)
        await interaction.response.send_modal(DeliveryVisualModal(config))

    async def _edit_banner(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        config = await _load_config(interaction.guild.id)
        await interaction.response.send_modal(DeliveryBannerModal(config))

    async def _edit_emojis(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        config = await _load_config(interaction.guild.id)
        await interaction.response.send_modal(DeliveryEmojiModal(config))

    async def _edit_product_emojis(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        config = await _load_config(interaction.guild.id)
        await interaction.response.send_modal(DeliveryProductEmojiModal(config))

    async def _preview(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        config = await _load_config(interaction.guild.id)
        item = SimpleNamespace(
            name_snapshot="VIP",
            quantity=1,
            unit_price=4.32,
            metadata_json={
                "game_name": "Dungeon Lootr › Morreti Gostoso",
                "robux_amount": 120,
                "discount_percent": "6",
                "original_total": "4.32",
                "discounted_total": "4.06",
            },
        )
        title, lines, footer, accent, _ = render_delivery(
            config,
            order_id="12345678-0000-0000-0000-000000000000",
            client_mention=interaction.user.mention,
            items=[item],
        )
        _, banner_url = _configured_delivery_banner(config)
        await interaction.response.send_message(
            view=CardLayout(
                title=None,
                lines=([title] if title else []) + lines,
                footer=footer or None,
                accent_colour=0x7B2CBF,
                image_url=banner_url,
            ),
            ephemeral=True,
        )


async def send_delivery_admin(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(
        view=DeliveryAdminView(owner_id=interaction.user.id),
        ephemeral=True,
    )
