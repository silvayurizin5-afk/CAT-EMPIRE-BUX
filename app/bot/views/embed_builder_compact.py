import discord

from app.bot.views.embed_builder import (
    AuthorModal,
    ColorModal,
    DescriptionModal,
    EmbedDraft,
    FieldModal,
    FooterModal,
    ImagesModal,
    LinkButtonModal,
    PublishChannelView,
    TitleModal,
)


class EmbedEditorSelect(discord.ui.Select):
    def __init__(self) -> None:
        options = [
            discord.SelectOption(label="Título", value="title", description="Editar o título"),
            discord.SelectOption(
                label="Descrição", value="description", description="Editar o texto principal"
            ),
            discord.SelectOption(label="Cor", value="color", description="Alterar a cor lateral"),
            discord.SelectOption(label="Autor", value="author", description="Nome e ícone do autor"),
            discord.SelectOption(
                label="Adicionar campo", value="field", description="Adicionar informação à embed"
            ),
            discord.SelectOption(
                label="Imagem / thumbnail",
                value="images",
                description="Configurar banner e miniatura",
            ),
            discord.SelectOption(label="Rodapé", value="footer", description="Texto e ícone do rodapé"),
            discord.SelectOption(
                label="Adicionar botão", value="link", description="Adicionar um botão de link"
            ),
            discord.SelectOption(
                label="Remover último campo",
                value="remove_field",
                description="Remove o último campo adicionado",
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
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not isinstance(self.view, CompactEmbedBuilderView):
            return
        await self.view.handle_editor_action(interaction, self.values[0])


class CompactEmbedBuilderView(discord.ui.View):
    def __init__(self, *, owner_id: int, draft: EmbedDraft | None = None) -> None:
        super().__init__(timeout=900)
        self.owner_id = owner_id
        self.draft = draft or EmbedDraft()
        self.add_item(EmbedEditorSelect())

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
            "title": TitleModal,
            "description": DescriptionModal,
            "color": ColorModal,
            "author": AuthorModal,
            "field": FieldModal,
            "images": ImagesModal,
            "footer": FooterModal,
            "link": LinkButtonModal,
        }
        modal = modal_by_action.get(action)
        if modal is not None:
            await interaction.response.send_modal(modal(self))
            return

        if action == "remove_field":
            if self.draft.fields:
                self.draft.fields.pop()
            await interaction.response.edit_message(
                embed=self.draft.build_embed(),
                view=self,
            )
            return

        if action == "remove_button":
            if self.draft.buttons:
                self.draft.buttons.pop()
            await interaction.response.edit_message(
                embed=self.draft.build_embed(),
                view=self,
            )
            return

    @discord.ui.button(label="Publicar aqui", style=discord.ButtonStyle.success, row=1)
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
        await interaction.response.send_message("Embed publicada.", ephemeral=True)

    @discord.ui.button(label="Outro canal", style=discord.ButtonStyle.secondary, row=1)
    async def publish_other(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.send_message(
            "Escolha onde publicar:",
            view=PublishChannelView(draft=self.draft),
            ephemeral=True,
        )

    @discord.ui.button(label="Limpar", style=discord.ButtonStyle.secondary, row=1)
    async def reset_button(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        self.draft = EmbedDraft()
        await interaction.response.edit_message(
            embed=self.draft.build_embed(),
            view=self,
        )


async def send_compact_embed_builder(interaction: discord.Interaction) -> None:
    view = CompactEmbedBuilderView(owner_id=interaction.user.id)
    await interaction.response.send_message(
        embed=view.draft.build_embed(),
        view=view,
        ephemeral=True,
    )
