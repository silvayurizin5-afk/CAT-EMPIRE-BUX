from __future__ import annotations

from dataclasses import dataclass, field

import discord

DEFAULT_EMBED_COLOR = 0x5865F2


def parse_hex_color(value: str) -> int:
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
        color = int(cleaned, 16)
    except ValueError as exc:
        raise ValueError("Cor inválida") from exc
    if not 0 <= color <= 0xFFFFFF:
        raise ValueError("Cor inválida")
    return color


def normalize_http_url(value: str) -> str:
    url = value.strip()
    if not url:
        return ""
    if not url.lower().startswith(("https://", "http://")):
        raise ValueError("A URL precisa começar com http:// ou https://")
    return url


@dataclass(slots=True)
class EmbedDraft:
    title: str = "Título do Embed"
    description: str = (
        "✨ Monte o painel visualmente usando os botões abaixo. "
        "A prévia é atualizada a cada alteração."
    )
    color: int = DEFAULT_EMBED_COLOR
    author_name: str = ""
    author_icon_url: str = ""
    footer_text: str = "NEXTBUY • Editor visual"
    footer_icon_url: str = ""
    image_url: str = ""
    thumbnail_url: str = ""
    fields: list[tuple[str, str, bool]] = field(default_factory=list)
    buttons: list[tuple[str, str]] = field(default_factory=list)

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=self.title or None,
            description=self.description or None,
            color=self.color,
        )
        if self.author_name:
            if self.author_icon_url:
                embed.set_author(name=self.author_name, icon_url=self.author_icon_url)
            else:
                embed.set_author(name=self.author_name)
        for name, value, inline in self.fields:
            embed.add_field(name=name, value=value, inline=inline)
        if self.image_url:
            embed.set_image(url=self.image_url)
        if self.thumbnail_url:
            embed.set_thumbnail(url=self.thumbnail_url)
        if self.footer_text:
            if self.footer_icon_url:
                embed.set_footer(text=self.footer_text, icon_url=self.footer_icon_url)
            else:
                embed.set_footer(text=self.footer_text)
        return embed

    def build_link_view(self) -> discord.ui.View | None:
        if not self.buttons:
            return None
        view = discord.ui.View(timeout=None)
        for label, url in self.buttons[:5]:
            view.add_item(discord.ui.Button(label=label, url=url))
        return view


class _BuilderModal(discord.ui.Modal):
    def __init__(self, *, title: str, builder: EmbedBuilderView) -> None:
        super().__init__(title=title)
        self.builder = builder

    async def refresh(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(
            embed=self.builder.draft.build_embed(),
            view=self.builder,
        )


class TitleModal(_BuilderModal):
    def __init__(self, builder: EmbedBuilderView) -> None:
        super().__init__(title="Editar título", builder=builder)
        self.title_input = discord.ui.TextInput(
            label="Título",
            required=False,
            max_length=256,
            default=builder.draft.title[:256],
            placeholder="Título do Embed",
        )
        self.add_item(self.title_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.builder.draft.title = str(self.title_input).strip()
        await self.refresh(interaction)


class DescriptionModal(_BuilderModal):
    def __init__(self, builder: EmbedBuilderView) -> None:
        super().__init__(title="Editar descrição", builder=builder)
        self.description_input = discord.ui.TextInput(
            label="Descrição",
            required=False,
            max_length=4000,
            style=discord.TextStyle.paragraph,
            default=builder.draft.description[:4000],
            placeholder="Texto principal do embed",
        )
        self.add_item(self.description_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.builder.draft.description = str(self.description_input).strip()
        await self.refresh(interaction)


class ColorModal(_BuilderModal):
    def __init__(self, builder: EmbedBuilderView) -> None:
        super().__init__(title="Editar cor", builder=builder)
        self.color_input = discord.ui.TextInput(
            label="Cor hexadecimal",
            max_length=9,
            default=f"#{builder.draft.color:06X}",
            placeholder="#5865F2",
        )
        self.add_item(self.color_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            self.builder.draft.color = parse_hex_color(str(self.color_input))
        except ValueError:
            await interaction.response.send_message(
                "Cor inválida. Use algo como `#5865F2`.",
                ephemeral=True,
            )
            return
        await self.refresh(interaction)


class AuthorModal(_BuilderModal):
    def __init__(self, builder: EmbedBuilderView) -> None:
        super().__init__(title="Editar autor", builder=builder)
        self.name_input = discord.ui.TextInput(
            label="Nome do autor",
            required=False,
            max_length=256,
            default=builder.draft.author_name[:256],
        )
        self.icon_input = discord.ui.TextInput(
            label="URL do ícone",
            required=False,
            max_length=1000,
            default=builder.draft.author_icon_url[:1000],
            placeholder="https://...",
        )
        self.add_item(self.name_input)
        self.add_item(self.icon_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            icon_url = normalize_http_url(str(self.icon_input))
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        self.builder.draft.author_name = str(self.name_input).strip()
        self.builder.draft.author_icon_url = icon_url
        await self.refresh(interaction)


class FieldModal(_BuilderModal):
    def __init__(self, builder: EmbedBuilderView) -> None:
        super().__init__(title="Adicionar campo", builder=builder)
        self.name_input = discord.ui.TextInput(
            label="Nome do campo",
            max_length=256,
            placeholder="Ex: Preço",
        )
        self.value_input = discord.ui.TextInput(
            label="Conteúdo",
            max_length=1024,
            style=discord.TextStyle.paragraph,
            placeholder="Ex: R$ 10,00",
        )
        self.inline_input = discord.ui.TextInput(
            label="Lado a lado? (sim/não)",
            required=False,
            max_length=5,
            default="não",
        )
        self.add_item(self.name_input)
        self.add_item(self.value_input)
        self.add_item(self.inline_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if len(self.builder.draft.fields) >= 25:
            await interaction.response.send_message(
                "O Discord permite no máximo 25 campos por embed.",
                ephemeral=True,
            )
            return
        inline = str(self.inline_input).strip().lower() in {"sim", "s", "yes", "true", "1"}
        self.builder.draft.fields.append(
            (str(self.name_input).strip(), str(self.value_input).strip(), inline)
        )
        await self.refresh(interaction)


class ImagesModal(_BuilderModal):
    def __init__(self, builder: EmbedBuilderView) -> None:
        super().__init__(title="Imagem e thumbnail", builder=builder)
        self.image_input = discord.ui.TextInput(
            label="URL da imagem grande",
            required=False,
            max_length=1000,
            default=builder.draft.image_url[:1000],
            placeholder="https://...",
        )
        self.thumbnail_input = discord.ui.TextInput(
            label="URL da thumbnail",
            required=False,
            max_length=1000,
            default=builder.draft.thumbnail_url[:1000],
            placeholder="https://...",
        )
        self.add_item(self.image_input)
        self.add_item(self.thumbnail_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            image_url = normalize_http_url(str(self.image_input))
            thumbnail_url = normalize_http_url(str(self.thumbnail_input))
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        self.builder.draft.image_url = image_url
        self.builder.draft.thumbnail_url = thumbnail_url
        await self.refresh(interaction)


class FooterModal(_BuilderModal):
    def __init__(self, builder: EmbedBuilderView) -> None:
        super().__init__(title="Editar rodapé", builder=builder)
        self.text_input = discord.ui.TextInput(
            label="Texto do rodapé",
            required=False,
            max_length=2048,
            default=builder.draft.footer_text[:2048],
        )
        self.icon_input = discord.ui.TextInput(
            label="URL do ícone",
            required=False,
            max_length=1000,
            default=builder.draft.footer_icon_url[:1000],
            placeholder="https://...",
        )
        self.add_item(self.text_input)
        self.add_item(self.icon_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            icon_url = normalize_http_url(str(self.icon_input))
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        self.builder.draft.footer_text = str(self.text_input).strip()
        self.builder.draft.footer_icon_url = icon_url
        await self.refresh(interaction)


class LinkButtonModal(_BuilderModal):
    def __init__(self, builder: EmbedBuilderView) -> None:
        super().__init__(title="Adicionar botão", builder=builder)
        self.label_input = discord.ui.TextInput(
            label="Texto do botão",
            max_length=80,
            placeholder="Abrir loja",
        )
        self.url_input = discord.ui.TextInput(
            label="URL",
            max_length=1000,
            placeholder="https://...",
        )
        self.add_item(self.label_input)
        self.add_item(self.url_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if len(self.builder.draft.buttons) >= 5:
            await interaction.response.send_message(
                "Este editor permite até 5 botões de link por painel.",
                ephemeral=True,
            )
            return
        try:
            url = normalize_http_url(str(self.url_input))
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        self.builder.draft.buttons.append((str(self.label_input).strip(), url))
        await self.refresh(interaction)


class PublishChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, *, draft: EmbedDraft) -> None:
        super().__init__(
            placeholder="Escolha o canal para publicar",
            min_values=1,
            max_values=1,
            channel_types=[discord.ChannelType.text],
        )
        self.draft = draft

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        selected = self.values[0]
        channel = interaction.guild.get_channel(selected.id)
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.edit_message(
                content="Não consegui acessar esse canal.",
                view=None,
            )
            return
        try:
            await channel.send(
                embed=self.draft.build_embed(),
                view=self.draft.build_link_view(),
            )
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.edit_message(
                content="Não consegui publicar nesse canal. Revise as permissões do bot.",
                view=None,
            )
            return
        await interaction.response.edit_message(
            content=f"Publicado em {channel.mention}.",
            view=None,
        )


class PublishChannelView(discord.ui.View):
    def __init__(self, *, draft: EmbedDraft) -> None:
        super().__init__(timeout=120)
        self.add_item(PublishChannelSelect(draft=draft))


class EmbedBuilderView(discord.ui.View):
    def __init__(self, *, owner_id: int, draft: EmbedDraft | None = None) -> None:
        super().__init__(timeout=900)
        self.owner_id = owner_id
        self.draft = draft or EmbedDraft()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "Esse editor pertence a outra pessoa.",
            ephemeral=True,
        )
        return False

    @discord.ui.button(label="Título", emoji="📝", style=discord.ButtonStyle.secondary, row=0)
    async def title_button(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(TitleModal(self))

    @discord.ui.button(
        label="Descrição", emoji="📄", style=discord.ButtonStyle.secondary, row=0
    )
    async def description_button(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(DescriptionModal(self))

    @discord.ui.button(label="Cor", emoji="🎨", style=discord.ButtonStyle.secondary, row=0)
    async def color_button(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(ColorModal(self))

    @discord.ui.button(label="Autor", emoji="👤", style=discord.ButtonStyle.secondary, row=0)
    async def author_button(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(AuthorModal(self))

    @discord.ui.button(
        label="Adicionar campo", emoji="🧩", style=discord.ButtonStyle.secondary, row=1
    )
    async def field_button(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(FieldModal(self))

    @discord.ui.button(
        label="Imagem / Thumbnail", emoji="🖼️", style=discord.ButtonStyle.secondary, row=1
    )
    async def image_button(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(ImagesModal(self))

    @discord.ui.button(
        label="Rodapé", emoji="🏷️", style=discord.ButtonStyle.secondary, row=1
    )
    async def footer_button(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(FooterModal(self))

    @discord.ui.button(
        label="Adicionar botão", emoji="🔗", style=discord.ButtonStyle.secondary, row=1
    )
    async def link_button(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(LinkButtonModal(self))

    @discord.ui.button(
        label="Enviar aqui", emoji="🚀", style=discord.ButtonStyle.success, row=2
    )
    async def publish_here(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        if interaction.channel is None:
            await interaction.response.send_message("Canal inválido.", ephemeral=True)
            return
        try:
            await interaction.channel.send(
                embed=self.draft.build_embed(),
                view=self.draft.build_link_view(),
            )
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message(
                "Não consegui publicar aqui. Revise as permissões do bot.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message("Embed publicado.", ephemeral=True)

    @discord.ui.button(
        label="Outro canal", emoji="📨", style=discord.ButtonStyle.primary, row=2
    )
    async def publish_other(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.send_message(
            "Escolha onde publicar:",
            view=PublishChannelView(draft=self.draft),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Limpar", emoji="🧹", style=discord.ButtonStyle.danger, row=2
    )
    async def reset_button(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        self.draft = EmbedDraft()
        await interaction.response.edit_message(
            embed=self.draft.build_embed(),
            view=self,
        )


class EmbedBuilderLauncherButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Criar embed",
            emoji="✨",
            style=discord.ButtonStyle.success,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await send_embed_builder(interaction)


async def send_embed_builder(interaction: discord.Interaction) -> None:
    view = EmbedBuilderView(owner_id=interaction.user.id)
    await interaction.response.send_message(
        embed=view.draft.build_embed(),
        view=view,
        ephemeral=True,
    )
