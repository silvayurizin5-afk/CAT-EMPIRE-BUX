from __future__ import annotations

from types import SimpleNamespace

import discord

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
            label="Título",
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
            label="Linha de cada produto",
            max_length=500,
            default=str(config["product_template"])[:500],
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
            label="Mostrar imagem do produto? sim/não",
            max_length=3,
            default="sim" if bool(config["show_image"]) else "não",
        )
        self.verify = discord.ui.TextInput(
            label="Emoji de verificação",
            required=False,
            max_length=128,
            default=str(config["verify_emoji"])[:128],
        )
        self.member = discord.ui.TextInput(
            label="Emoji de cliente",
            required=False,
            max_length=128,
            default=str(config["member_emoji"])[:128],
        )
        self.box = discord.ui.TextInput(
            label="Emoji de produtos",
            required=False,
            max_length=128,
            default=str(config["box_emoji"])[:128],
        )
        for item in (self.accent, self.show_image, self.verify, self.member, self.box):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        answer = str(self.show_image).strip().casefold()
        if answer not in {"sim", "nao", "não"}:
            await interaction.response.send_message(
                "Em mostrar imagem, use apenas `sim` ou `não`.",
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
                "verify_emoji": str(self.verify).strip(),
                "member_emoji": str(self.member).strip(),
                "box_emoji": str(self.box).strip(),
            }
        )
        try:
            await _save_config(interaction.guild.id, config)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.send_message("Visual de entrega atualizado.", ephemeral=True)


class DeliveryAdminView(discord.ui.LayoutView):
    def __init__(self, *, owner_id: int) -> None:
        super().__init__(timeout=900)
        self.owner_id = owner_id
        card = CardLayout(
            title="NEXTBUY • Entregas",
            description="Configure livremente a mensagem publicada quando um pedido é entregue.",
            lines=[
                "**Placeholders gerais:** `{client}`, `{order}`, `{order_short}`, `{verify}`, `{member}`, `{box}`, `{products}`",
                "**Na linha do produto:** `{product}`, `{game}`, `{game_part}`, `{quantity}`, `{quantity_part}`, `{unit_price}`, `{line_total}`",
                "Você pode colocar texto, markdown e emojis customizados do servidor diretamente nos templates.",
            ],
            footer="A imagem usa a foto salva no pedido; se não houver snapshot, tenta a imagem atual do produto.",
            timeout=900,
        )
        self.container = card.container
        card.remove_item(card.container)
        self.add_item(self.container)

        edit_message = discord.ui.Button(
            label="Editar mensagem",
            style=discord.ButtonStyle.primary,
        )
        edit_message.callback = self._edit_message
        edit_visual = discord.ui.Button(
            label="Editar visual",
            style=discord.ButtonStyle.secondary,
        )
        edit_visual.callback = self._edit_visual
        preview = discord.ui.Button(
            label="Prévia",
            style=discord.ButtonStyle.secondary,
        )
        preview.callback = self._preview
        add_action_row(self.container, edit_message, edit_visual, preview)

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

    async def _preview(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        config = await _load_config(interaction.guild.id)
        item = SimpleNamespace(
            name_snapshot="Notifier",
            quantity=1,
            unit_price=22,
            metadata_json={"game_name": "BLOX FRUITS"},
        )
        title, lines, footer, accent, _ = render_delivery(
            config,
            order_id="12345678-0000-0000-0000-000000000000",
            client_mention=interaction.user.mention,
            items=[item],
        )
        await interaction.response.send_message(
            view=CardLayout(
                title=title,
                lines=lines,
                footer=footer,
                accent_colour=accent,
            ),
            ephemeral=True,
        )


async def send_delivery_admin(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(
        view=DeliveryAdminView(owner_id=interaction.user.id),
        ephemeral=True,
    )
