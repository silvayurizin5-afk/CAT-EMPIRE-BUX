import discord

from app.bot.components_v2 import CardLayout, add_action_row, add_select_row
from app.bot.views.embed_builder import (
    AuthorModal,
    ColorModal,
    DescriptionModal,
    EmbedDraft,
    FieldModal,
    FooterModal,
    ImagesModal,
    LinkButtonModal,
    TitleModal,
)


def _draft_lines(draft: EmbedDraft) -> list[str]:
    lines: list[str] = []
    if draft.author_name:
        lines.append(f"-# {draft.author_name}")
    for name, value, _inline in draft.fields:
        lines.append(f"**{name}**\n{value}")
    return lines


def build_public_message_view(
    draft: EmbedDraft,
    *,
    timeout: float | None = None,
) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=timeout)
    card = CardLayout(
        title=draft.title or None,
        description=draft.description or None,
        lines=_draft_lines(draft),
        footer=draft.footer_text or None,
        accent_colour=discord.Colour(draft.color),
        image_url=draft.image_url or None,
        thumbnail_url=draft.thumbnail_url or None,
        timeout=timeout,
    )
    container = card.container
    card.remove_item(container)
    view.add_item(container)

    if draft.buttons:
        buttons = [
            discord.ui.Button(label=label[:80], url=url)
            for label, url in draft.buttons[:5]
        ]
        add_action_row(container, *buttons)
    return view


class _CompactRefreshMixin:
    async def refresh(self, interaction: discord.Interaction) -> None:
        builder = self.builder
        if isinstance(builder, CompactEmbedBuilderView):
            builder.rebuild()
            await interaction.response.edit_message(
                content=None,
                embeds=[],
                view=builder,
            )
            return
        await super().refresh(interaction)


class CompactTitleModal(_CompactRefreshMixin, TitleModal):
    pass


class CompactDescriptionModal(_CompactRefreshMixin, DescriptionModal):
    pass


class CompactColorModal(_CompactRefreshMixin, ColorModal):
    pass


class CompactAuthorModal(_CompactRefreshMixin, AuthorModal):
    pass


class CompactFieldModal(_CompactRefreshMixin, FieldModal):
    pass


class CompactImagesModal(_CompactRefreshMixin, ImagesModal):
    pass


class CompactFooterModal(_CompactRefreshMixin, FooterModal):
    pass


class CompactLinkButtonModal(_CompactRefreshMixin, LinkButtonModal):
    pass


class EmbedEditorSelect(discord.ui.Select):
    def __init__(self) -> None:
        options = [
            discord.SelectOption(label="Título", value="title", description="Editar o título"),
            discord.SelectOption(
                label="Descrição", value="description", description="Editar o texto principal"
            ),
            discord.SelectOption(label="Cor", value="color", description="Alterar a cor lateral"),
            discord.SelectOption(label="Autor", value="author", description="Nome do autor"),
            discord.SelectOption(
                label="Adicionar campo", value="field", description="Adicionar informação ao painel"
            ),
            discord.SelectOption(
                label="Imagem / thumbnail",
                value="images",
                description="Configurar banner e miniatura",
            ),
            discord.SelectOption(label="Rodapé", value="footer", description="Texto do rodapé"),
            discord.SelectOption(
                label="Adicionar botão", value="link", description="Adicionar botão de link"
            ),
            discord.SelectOption(
                label="Remover último campo",
                value="remove_field",
                description="Remove a última informação adicionada",
            ),
            discord.SelectOption(
                label="Remover último botão",
                value="remove_button",
                description="Remove o último botão adicionado",
            ),
        ]
        super().__init__(
            placeholder="Selecione o que deseja editar",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if isinstance(self.view, CompactEmbedBuilderView):
            await self.view.handle_editor_action(interaction, self.values[0])


class PublishChannelSelectV2(discord.ui.ChannelSelect):
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
            await interaction.response.edit_message(content="Canal inválido.", view=None)
            return
        try:
            await channel.send(view=build_public_message_view(self.draft, timeout=None))
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


class PublishChannelLayoutV2(discord.ui.LayoutView):
    def __init__(self, *, draft: EmbedDraft) -> None:
        super().__init__(timeout=120)
        card = CardLayout(
            title="Publicar mensagem",
            description="Escolha o canal onde este Container V2 será enviado.",
            timeout=120,
        )
        self.container = card.container
        card.remove_item(card.container)
        self.add_item(self.container)
        add_select_row(self.container, PublishChannelSelectV2(draft=draft))


class CompactEmbedBuilderView(discord.ui.LayoutView):
    def __init__(self, *, owner_id: int, draft: EmbedDraft | None = None) -> None:
        super().__init__(timeout=900)
        self.owner_id = owner_id
        self.draft = draft or EmbedDraft()
        self.rebuild()

    def rebuild(self) -> None:
        self.clear_items()
        card = CardLayout(
            title=self.draft.title or None,
            description=self.draft.description or None,
            lines=_draft_lines(self.draft),
            footer=self.draft.footer_text or None,
            accent_colour=discord.Colour(self.draft.color),
            image_url=self.draft.image_url or None,
            thumbnail_url=self.draft.thumbnail_url or None,
            timeout=900,
        )
        self.container = card.container
        card.remove_item(card.container)
        self.add_item(self.container)

        if self.draft.buttons:
            links = [
                discord.ui.Button(label=label[:80], url=url)
                for label, url in self.draft.buttons[:5]
            ]
            add_action_row(self.container, *links)

        add_select_row(self.container, EmbedEditorSelect())

        publish_here = discord.ui.Button(
            label="Publicar aqui",
            style=discord.ButtonStyle.success,
            custom_id="nextbuy:builder:publish-here",
        )
        publish_other = discord.ui.Button(
            label="Outro canal",
            style=discord.ButtonStyle.secondary,
            custom_id="nextbuy:builder:publish-other",
        )
        reset = discord.ui.Button(
            label="Limpar",
            style=discord.ButtonStyle.secondary,
            custom_id="nextbuy:builder:reset",
        )
        publish_here.callback = self._publish_here
        publish_other.callback = self._publish_other
        reset.callback = self._reset
        add_action_row(self.container, publish_here, publish_other, reset)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "Esse editor pertence a outra pessoa.",
            ephemeral=True,
        )
        return False

    async def handle_editor_action(self, interaction: discord.Interaction, action: str) -> None:
        modal_by_action = {
            "title": CompactTitleModal,
            "description": CompactDescriptionModal,
            "color": CompactColorModal,
            "author": CompactAuthorModal,
            "field": CompactFieldModal,
            "images": CompactImagesModal,
            "footer": CompactFooterModal,
            "link": CompactLinkButtonModal,
        }
        modal = modal_by_action.get(action)
        if modal is not None:
            await interaction.response.send_modal(modal(self))
            return

        if action == "remove_field" and self.draft.fields:
            self.draft.fields.pop()
        elif action == "remove_button" and self.draft.buttons:
            self.draft.buttons.pop()
        self.rebuild()
        await interaction.response.edit_message(content=None, embeds=[], view=self)

    async def _publish_here(self, interaction: discord.Interaction) -> None:
        if interaction.channel is None:
            await interaction.response.send_message("Canal inválido.", ephemeral=True)
            return
        try:
            await interaction.channel.send(view=build_public_message_view(self.draft, timeout=None))
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message(
                "Não consegui publicar aqui. Revise as permissões do bot.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message("Mensagem publicada.", ephemeral=True)

    async def _publish_other(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            view=PublishChannelLayoutV2(draft=self.draft),
            ephemeral=True,
        )

    async def _reset(self, interaction: discord.Interaction) -> None:
        self.draft = EmbedDraft()
        self.rebuild()
        await interaction.response.edit_message(content=None, embeds=[], view=self)


async def send_compact_embed_builder(interaction: discord.Interaction) -> None:
    view = CompactEmbedBuilderView(owner_id=interaction.user.id)
    await interaction.response.send_message(
        view=view,
        ephemeral=True,
    )
