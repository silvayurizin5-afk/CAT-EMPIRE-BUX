# Tickets NEXTBUY

Sistema de atendimento privado integrado ao bot existente, exclusivo do servidor
`1549240664149991424`. Tickets de suporte não alteram pagamentos, estoque ou entregas.
Os tickets de compra continuam no fluxo existente da loja.

## Atualizar

Pare o bot, ative o ambiente virtual e execute na pasta do projeto:

```bat
git pull
python -m pip install -e ".[dev]"
python -m alembic upgrade head
python -m app.bot.main
```

A migration `0015_support_tickets` acrescenta opções à configuração existente e uma
nova tabela para atendimentos. Não exclui pedidos nem tickets antigos. O bot precisa
estar parado durante a migration. Use o DATABASE_URL habitual no `.env`.

## Configurar e publicar

1. `/admin → Configurar tickets` ou `/tickets configurar`.
2. **Cargos:** defina Administrador e/ou Atendente. Não use `@everyone`.
3. **Canais:** escolha Categoria de tickets, Transcripts e Logs.
4. **Textos:** título/descrição do painel, texto do botão e boas-vindas. A variável
   `{customer}` nas boas-vindas identifica o cliente.
5. **Visual:** cor hexadecimal, emoji Unicode/customizado e URL HTTPS de banner.
6. **Regras:** ativar aberturas, limite por cliente (1–10), intervalo (0–86400s),
   transcript obrigatório e permissão para o cliente fechar.
7. Escolha opcionalmente a categoria para tickets fechados. O movimento conserva
   as permissões privadas do canal, sem sincronizar permissões da categoria.
8. Confira **Prévia** e use `/tickets publicar canal:#atendimento`.

Padrão: 1 ticket aberto por cliente, intervalo de 60 segundos, transcript obrigatório,
e cliente autorizado a fechar o próprio atendimento. Regras novas valem inclusive
para painéis antigos. Mudanças visuais exigem republicação; a publicação cria uma nova
mensagem, portanto remova painéis visuais antigos se não forem mais necessários.

O bot precisa de Ver canais, Enviar mensagens, Ler histórico, Anexar arquivos,
Gerenciar canais e Gerenciar permissões. Mantenha o canal de transcripts e o de logs
restritos à equipe, pois armazenam conversas de atendimento. Ative os intents de
membros e conteúdo de mensagens, usados pelo bot e pelo histórico.

## Operação

| Ação | Como usar |
|---|---|
| Abrir | Botão público + formulário de assunto e descrição |
| Assumir / Liberar | Botões no ticket; um atendente não toma o ticket já assumido por outro |
| Fechar | Botão + motivo; exporta o histórico e bloqueia novas mensagens do cliente/participantes |
| Reabrir | Equipe usa o botão; valida novamente o limite do cliente |
| Transcript avulso | Equipe usa **Transcript**, sem fechar |
| Excluir | Motivo + confirmação; exporta antes de remover permanentemente o canal |
| Gerenciar fora do ticket | `/tickets listar` ou `/admin → Gerenciar suporte`, com paginação |
| Selecionar canal diretamente | `/tickets acao acao:... canal:#suporte-cliente` |
| Adicionar/remover membro | `/tickets participante acao:... membro:@pessoa canal:#suporte-cliente` |
| Gerenciar tickets de pedidos | `/admin → Gerenciar tickets` (fluxo de compras existente) |
| Mensagens de pedidos | `/admin → Mensagens dos tickets` |

Administradores e Atendentes podem gerenciar suporte. Clientes podem apenas abrir e,
se permitido, fechar seu próprio ticket. Participantes adicionais não recebem poder
de staff. O botão de exclusão sempre exige confirmação. Botões públicos e controles
são restaurados após reiniciar o bot.

## Transcripts e logs

O HTML contém mensagens, conteúdo textual de Components V2, imagens dos componentes,
embeds, anexos, emojis, autores e horários BRT. Atendimentos incluem assunto, cliente,
atendente e motivo da exportação. Não mostra UUIDs de pedidos como títulos.
HTML recebido de usuários é escapado e o documento tem política que bloqueia scripts.

Arquivos grandes são compactados como `.html.gz`; descompacte para abrir no navegador.
Se mesmo assim ultrapassarem o limite do Discord, a operação falha e preserva o canal.
Com transcript obrigatório, configuração ausente ou upload recusado bloqueia fechamento
ou exclusão. Mesmo com a obrigação desligada, um destino configurado que falha não é
ignorado. Desligar a obrigação e deixar o destino ausente permite encerrar sem histórico.
Esta regra também protege fechamento/exclusão dos tickets de compra existentes.

O transcript é um retrato das mensagens disponíveis no canal no momento da exportação:
não recupera mensagens apagadas, versões anteriores de edições nem históricos de threads.
Imagens e anexos são referências externas que podem expirar; não é um backup binário
permanente dos anexos. Exportação e upload precisam terminar para a exclusão prosseguir.

Logs registram abertura, responsável, liberação, fechamento/motivo, reabertura,
participantes, exportação, exclusão e alterações de configuração. A entrega usa a fila
persistente de logs existente, com novas tentativas se o envio falhar. UUIDs continuam
internos. Logs de suporte apresentam cliente, assunto, atendente e canal.

## Confiabilidade e limites

- Locks PostgreSQL serializam aberturas do mesmo cliente e alterações no mesmo ticket.
- Configurações são salvas por seção, sem sobrescrever alterações concorrentes de outra seção.
- Falha de exportação restaura as permissões alteradas durante fechamento/exclusão.
- Canal excluído manualmente enquanto o bot está online é marcado como excluído e gera log.
- Remover canal diretamente no Discord não permite ao bot exportar o histórico apagado.
- Discord e PostgreSQL não compartilham uma transação: falhas de processo/rede no momento
  exato da criação/exclusão podem exigir reconciliação administrativa. Não apague canais
  manualmente com o bot offline se precisar preservar o histórico.
- Validação automatizada usa Discord simulado e PostgreSQL no CI; faça uma abertura,
  fechamento, reabertura e exclusão de teste no servidor após atualizar para validar
  permissões reais e upload.

## Testes

```sh
python -m ruff check .
python -m alembic upgrade head --sql
python -m pytest -q
```

No CI: PostgreSQL, aplicação de todas as migrations, `alembic check` e testes de
concorrência com `RUN_DB_TESTS=1`.
