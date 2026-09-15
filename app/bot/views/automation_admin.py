from decimal import Decimal, InvalidOperation

import discord

from app.db.models import AutoReply, RobuxRate
from app.db.session import SessionLocal
from app.services.audit import write_audit_log
from app.services.catalog import upsert_robux_rate
from app.services.faq import list_auto_replies, set_auto_reply_active, upsert_auto_reply
from app.services.faq_buttons import (
    list_auto_reply_buttons,
    parse_auto_reply_buttons,
    serialize_auto_reply_buttons,
    set_auto_reply_buttons,
)
from app.services.quotes import list_robux_rates, set_robux_rate_active


def build_robux_rate_embed(rate: RobuxRate) -> discord.Embed:
    embed = discord.Embed(
        title=f"Cotação • {rate.label}",
        description="Ativa" if rate.active else "Desativada",
    )
    embed.add_field(name="Código", value=f"`{rate.code}`")
    embed.add_field(name="Preço por Robux", value=f"{rate.price_per_robux:.6f} créditos")
    embed.add_field(name="Entrega", value=rate.delivery_label or "—", inline=False)
    return embed


class RobuxRateEditModal(discord.ui.Modal):
    def __init__(self, rate: RobuxRate) -> None:
        super().__init__(title=f"Editar {rate.label[:35]}")
        self.rate_id = rate.id
        self.label_input = discord.ui.TextInput(
            label="Nome exibido",
            max_length=80,
            default=rate.label[:80],
        )
        self.price_input = discord.ui.TextInput(
            label="Créditos por 1 Robux",
            max_length=24,
            default=f"{rate.price_per_robux:.6f}",
        )
        self.delivery_input = discord.ui.TextInput(
            label="Prazo/descrição",
            required=False,
            max_length=120,
            default=(rate.delivery_label or "")[:120],
        )
        self.add_item(self.label_input)
        self.add_item(self.price_input)
        self.add_item(self.delivery_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        label = str(self.label_input).strip()
        try:
            price = Decimal(str(self.price_input).strip().replace(",", "."))
            if price <= 0:
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            await interaction.response.send_message("Preço inválido.", ephemeral=True)
            return
        if not label:
            await interaction.response.send_message("O nome da cotação é obrigatório.", ephemeral=True)
            return

        async with SessionLocal() as session, session.begin():
            rate = await session.get(RobuxRate, self.rate_id)
            if rate is None or rate.guild_id != interaction.guild.id:
                await interaction.response.send_message("Cotação não encontrada.", ephemeral=True)
                return
            was_active = rate.active
            updated = await upsert_robux_rate(
                session,
                guild_id=interaction.guild.id,
                code=rate.code,
                label=label,
                price_per_robux=price,
                delivery_label=str(self.delivery_input),
            )
            updated.active = was_active
            await write_audit_log(
                session,
                guild_id=interaction.guild.id,
                actor_discord_id=interaction.user.id,
                action="robux_rate.update",
                target_type="robux_rate",
                target_id=str(rate.id),
                details={"code": rate.code, "price_per_robux": str(price)},
            )

        await interaction.response.send_message(
            "Cotação atualizada. Reabra **Cotações** para conferir.",
            ephemeral=True,
        )


class RobuxRateActionsView(discord.ui.View):
    def __init__(self, rate_id: int) -> None:
        super().__init__(timeout=180)
        self.rate_id = rate_id

    @discord.ui.button(label="Editar", style=discord.ButtonStyle.primary)
    async def edit(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if interaction.guild is None:
            return
        async with SessionLocal() as session:
            rate = await session.get(RobuxRate, self.rate_id)
        if rate is None or rate.guild_id != interaction.guild.id:
            await interaction.response.send_message("Cotação não encontrada.", ephemeral=True)
            return
        await interaction.response.send_modal(RobuxRateEditModal(rate))

    @discord.ui.button(label="Ativar/Desativar", style=discord.ButtonStyle.secondary)
    async def toggle(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if interaction.guild is None:
            return
        async with SessionLocal() as session, session.begin():
            rate = await session.get(RobuxRate, self.rate_id)
            if rate is None or rate.guild_id != interaction.guild.id:
                await interaction.response.send_message("Cotação não encontrada.", ephemeral=True)
                return
            await set_robux_rate_active(session, rate=rate, active=not rate.active)
            await write_audit_log(
                session,
                guild_id=interaction.guild.id,
                actor_discord_id=interaction.user.id,
                action="robux_rate.toggle",
                target_type="robux_rate",
                target_id=str(rate.id),
                details={"active": rate.active},
            )
        await interaction.response.edit_message(
            embed=build_robux_rate_embed(rate),
            view=RobuxRateActionsView(rate.id),
        )


class RobuxRateManageSelect(discord.ui.Select):
    def __init__(self, rates: list[RobuxRate]) -> None:
        options = [
            discord.SelectOption(
                label=rate.label[:100],
                value=str(rate.id),
                description=(f"{rate.code} • {'ativa' if rate.active else 'desativada'}")[:100],
            )
            for rate in rates[:25]
        ]
        super().__init__(placeholder="Escolha a cotação", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        rate_id = int(self.values[0])
        async with SessionLocal() as session:
            rate = await session.get(RobuxRate, rate_id)
        if rate is None or rate.guild_id != interaction.guild.id:
            await interaction.response.edit_message(content="Cotação não encontrada.", view=None)
            return
        await interaction.response.edit_message(
            content=None,
            embed=build_robux_rate_embed(rate),
            view=RobuxRateActionsView(rate.id),
        )


class RobuxRateManagementView(discord.ui.View):
    def __init__(self, rates: list[RobuxRate]) -> None:
        super().__init__(timeout=180)
        self.add_item(RobuxRateManageSelect(rates))


async def send_robux_rate_management(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        return
    async with SessionLocal() as session:
        rates = await list_robux_rates(session, guild_id=interaction.guild.id)
    if not rates:
        await interaction.response.send_message(
            "Nenhuma cotação cadastrada. Use **Cotação Robux** primeiro.",
            ephemeral=True,
        )
        return
    await interaction.response.send_message(
        "Escolha uma cotação para editar ou ativar/desativar:",
        view=RobuxRateManagementView(rates),
        ephemeral=True,
    )


def build_auto_reply_embed(reply: AutoReply) -> discord.Embed:
    embed = discord.Embed(
        title=f"FAQ • {reply.name}",
        description=reply.content[:4000],
    )
    embed.add_field(name="Status", value="Ativa" if reply.active else "Desativada")
    embed.add_field(name="Cooldown", value=f"{reply.cooldown_seconds}s")
    embed.add_field(name="Título", value=reply.title[:1024], inline=False)
    keywords = ", ".join(str(item) for item in (reply.keywords or []))
    embed.add_field(name="Palavras-chave", value=keywords[:1024] or "—", inline=False)
    embed.add_field(
        name="Botões",
        value="Use **Botões** para configurar até 5 links HTTPS.",
        inline=False,
    )
    if reply.emoji:
        embed.set_footer(text=f"Emoji: {reply.emoji}")
    return embed


class AutoReplyEditModal(discord.ui.Modal):
    def __init__(self, reply: AutoReply) -> None:
        super().__init__(title=f"Editar {reply.name[:35]}")
        self.reply_id = reply.id
        self.keywords_input = discord.ui.TextInput(
            label="Palavras-chave",
            max_length=500,
            default=", ".join(str(item) for item in (reply.keywords or []))[:500],
        )
        self.title_input = discord.ui.TextInput(
            label="Título do embed",
            max_length=160,
            default=reply.title[:160],
        )
        self.content_input = discord.ui.TextInput(
            label="Resposta",
            style=discord.TextStyle.paragraph,
            max_length=4000,
            default=reply.content[:4000],
        )
        self.emoji_input = discord.ui.TextInput(
            label="Emoji",
            required=False,
            max_length=128,
            default=(reply.emoji or "")[:128],
        )
        self.cooldown_input = discord.ui.TextInput(
            label="Cooldown em segundos",
            max_length=6,
            default=str(reply.cooldown_seconds),
        )
        for item in (
            self.keywords_input,
            self.title_input,
            self.content_input,
            self.emoji_input,
            self.cooldown_input,
        ):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        try:
            cooldown = int(str(self.cooldown_input).strip())
            if cooldown < 0 or cooldown > 86400:
                raise ValueError
        except ValueError:
            await interaction.response.send_message(
                "Cooldown inválido. Use um valor entre 0 e 86400 segundos.",
                ephemeral=True,
            )
            return
        keywords = [
            item.strip()
            for item in str(self.keywords_input).replace("\n", ",").split(",")
            if item.strip()
        ]

        try:
            async with SessionLocal() as session, session.begin():
                reply = await session.get(AutoReply, self.reply_id)
                if reply is None or reply.guild_id != interaction.guild.id:
                    await interaction.response.send_message(
                        "Resposta automática não encontrada.", ephemeral=True
                    )
                    return
                was_active = reply.active
                updated = await upsert_auto_reply(
                    session,
                    guild_id=interaction.guild.id,
                    name=reply.name,
                    keywords=keywords,
                    title=str(self.title_input),
                    content=str(self.content_input),
                    emoji=str(self.emoji_input),
                    cooldown_seconds=cooldown,
                )
                updated.active = was_active
                await write_audit_log(
                    session,
                    guild_id=interaction.guild.id,
                    actor_discord_id=interaction.user.id,
                    action="faq.update",
                    target_type="auto_reply",
                    target_id=str(reply.id),
                    details={"name": reply.name, "cooldown_seconds": cooldown},
                )
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        await interaction.response.send_message(
            "Resposta automática atualizada. Reabra **FAQ** para conferir.",
            ephemeral=True,
        )


class AutoReplyButtonsModal(discord.ui.Modal, title="Botões da resposta automática"):
    buttons_input = discord.ui.TextInput(
        label="Botões (um por linha)",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=3000,
        placeholder="Suporte | https://exemplo.com/suporte | 🎫",
    )

    def __init__(self, reply_id: int, current_value: str) -> None:
        super().__init__()
        self.reply_id = reply_id
        self.buttons_input.default = current_value[:3000]

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        try:
            parsed = parse_auto_reply_buttons(str(self.buttons_input))
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        async with SessionLocal() as session, session.begin():
            reply = await session.get(AutoReply, self.reply_id)
            if reply is None or reply.guild_id != interaction.guild.id:
                await interaction.response.send_message(
                    "Resposta automática não encontrada.", ephemeral=True
                )
                return
            saved = await set_auto_reply_buttons(
                session,
                auto_reply_id=reply.id,
                buttons=parsed,
            )
            await write_audit_log(
                session,
                guild_id=interaction.guild.id,
                actor_discord_id=interaction.user.id,
                action="faq.buttons.update",
                target_type="auto_reply",
                target_id=str(reply.id),
                details={"name": reply.name, "button_count": len(saved)},
            )

        await interaction.response.send_message(
            f"Botões atualizados: **{len(saved)}**. "
            "Formato: `Nome | https://link | emoji opcional`.",
            ephemeral=True,
        )


class AutoReplyActionsView(discord.ui.View):
    def __init__(self, reply_id: int) -> None:
        super().__init__(timeout=180)
        self.reply_id = reply_id

    @discord.ui.button(label="Editar", style=discord.ButtonStyle.primary)
    async def edit(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if interaction.guild is None:
            return
        async with SessionLocal() as session:
            reply = await session.get(AutoReply, self.reply_id)
        if reply is None or reply.guild_id != interaction.guild.id:
            await interaction.response.send_message(
                "Resposta automática não encontrada.", ephemeral=True
            )
            return
        await interaction.response.send_modal(AutoReplyEditModal(reply))

    @discord.ui.button(label="Botões", style=discord.ButtonStyle.secondary)
    async def buttons(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if interaction.guild is None:
            return
        async with SessionLocal() as session:
            reply = await session.get(AutoReply, self.reply_id)
            if reply is None or reply.guild_id != interaction.guild.id:
                await interaction.response.send_message(
                    "Resposta automática não encontrada.", ephemeral=True
                )
                return
            current = await list_auto_reply_buttons(session, auto_reply_id=reply.id)
        await interaction.response.send_modal(
            AutoReplyButtonsModal(reply.id, serialize_auto_reply_buttons(current))
        )

    @discord.ui.button(label="Ativar/Desativar", style=discord.ButtonStyle.secondary)
    async def toggle(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if interaction.guild is None:
            return
        async with SessionLocal() as session, session.begin():
            reply = await session.get(AutoReply, self.reply_id)
            if reply is None or reply.guild_id != interaction.guild.id:
                await interaction.response.send_message(
                    "Resposta automática não encontrada.", ephemeral=True
                )
                return
            await set_auto_reply_active(session, reply=reply, active=not reply.active)
            await write_audit_log(
                session,
                guild_id=interaction.guild.id,
                actor_discord_id=interaction.user.id,
                action="faq.toggle",
                target_type="auto_reply",
                target_id=str(reply.id),
                details={"active": reply.active},
            )
        await interaction.response.edit_message(
            embed=build_auto_reply_embed(reply),
            view=AutoReplyActionsView(reply.id),
        )


class AutoReplyManageSelect(discord.ui.Select):
    def __init__(self, replies: list[AutoReply]) -> None:
        options = [
            discord.SelectOption(
                label=reply.name[:100],
                value=str(reply.id),
                description=(
                    f"{'ativa' if reply.active else 'desativada'} • "
                    f"cooldown {reply.cooldown_seconds}s"
                )[:100],
            )
            for reply in replies[:25]
        ]
        super().__init__(placeholder="Escolha a resposta automática", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        reply_id = int(self.values[0])
        async with SessionLocal() as session:
            reply = await session.get(AutoReply, reply_id)
        if reply is None or reply.guild_id != interaction.guild.id:
            await interaction.response.edit_message(
                content="Resposta automática não encontrada.", view=None
            )
            return
        await interaction.response.edit_message(
            content=None,
            embed=build_auto_reply_embed(reply),
            view=AutoReplyActionsView(reply.id),
        )


class AutoReplyManagementView(discord.ui.View):
    def __init__(self, replies: list[AutoReply]) -> None:
        super().__init__(timeout=180)
        self.add_item(AutoReplyManageSelect(replies))


async def send_auto_reply_management(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        return
    async with SessionLocal() as session:
        replies = await list_auto_replies(session, guild_id=interaction.guild.id)
    if not replies:
        await interaction.response.send_message(
            "Nenhuma resposta automática cadastrada. Use **Auto-resposta** primeiro.",
            ephemeral=True,
        )
        return
    await interaction.response.send_message(
        "Escolha uma resposta automática para editar, configurar botões ou ativar/desativar:",
        view=AutoReplyManagementView(replies),
        ephemeral=True,
    )
