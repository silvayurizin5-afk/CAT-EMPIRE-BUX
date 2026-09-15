# NEXTBUY — Especificação do Projeto

## Visão geral
NEXTBUY é uma loja automatizada dentro do Discord. A experiência do cliente é guiada por embeds e componentes; comandos slash ficam para a staff.

## Perfis de staff
- Administrador: acesso total.
- Atendente: tickets e suporte.
- Entregador: fluxos e comandos de entrega.
- Cargos e permissões são configuráveis pelo bot.

## Créditos e carteira
- 1 BRL = 1 crédito.
- Suporte a centavos.
- Nunca usar float em dinheiro.
- Toda alteração de saldo gera transação de ledger auditável.
- Compra só debita se houver saldo suficiente.
- Recarga só credita após confirmação real do gateway.

## Pagamentos
- Stripe é o gateway principal.
- Checkout hospedado pela Stripe; a NEXTBUY não coleta nem armazena dados de cartão.
- Métodos de pagamento são dinâmicos e dependem do que estiver habilitado/disponível na conta Stripe e na região.
- O bot cria Checkout Sessions de pagamento único para recargas de valor variável.
- O saldo só é creditado após webhook Stripe autenticado e idempotente.
- Eventos de pagamento assíncrono também precisam ser tratados antes de liberar saldo.
- Reembolso ou contestação depois de uma recarga creditada bloqueia novas compras para revisão manual; o sistema não força saldo negativo automaticamente.
- O endpoint do Mercado Pago fica temporariamente apenas para concluir recargas legadas criadas antes da migração para Stripe.

## Loja
- Painéis enviados pela staff.
- Itens, Robux e Game Pass.
- Produtos com nome, descrição, preço, jogo, imagem/banner, estoque/status e configurações próprias.
- Robux e Game Pass podem compartilhar o mesmo fluxo base.
- Tudo editável por comandos de administração.

## Calculadora
- Canal configurável.
- Se o cliente mandar apenas um valor como `10,80`, interpretar como BRL.
- Também reconhecer mensagens envolvendo Robux.
- Retornar opções configuráveis: Game Pass/gifts, Robux com prazo, Robux instantâneo etc.
- Taxas e fórmulas ficam no banco/configuração, não hardcoded.

## Tickets
- Compra abre ticket privado quando o fluxo exigir atendimento/entrega.
- Mensagens automáticas configuráveis.
- Status do pedido visível e limpo.
- Transcripts HTML no fechamento.
- Canal de logs/transcripts configurável.

## Termos
- Painel público com select/dropdown.
- Cada opção exibe embed efêmero com o termo escolhido.
- Seções previstas: segurança; suporte/comunicação; garantia/suporte; responsabilidade do cliente; má-fé/fraudes; preço regional; métodos/condições de entrega; reembolsos; créditos internos.
- Registrar aceite quando o fluxo de compra exigir.

## Auto-respostas/FAQ
- Detectar intenções por palavras-chave e variações em canais configuráveis.
- Exemplos: Pix, pagamento, saldo, Robux.
- Respostas em embeds com emoji/título/texto/botões configuráveis.
- Aplicar cooldown para evitar spam.

## Entregas
- Ao concluir pedido, publicar no canal de entregas.
- Embed/card pode usar imagem/banner do produto e dados essenciais da compra.
- Não expor dados sensíveis.

## Feedbacks
- Feedback sempre vinculado a um pedido real.
- Modal imediato opcional no fluxo de conclusão.
- Se adiado, agendar lembrete persistente no banco; não depender de `sleep` em memória.
- Após cerca de 3–5 minutos (configurável), mencionar o cliente no canal público de feedbacks.
- Canal é visível publicamente; permissão de escrita depende dos cargos configurados.
- Ao detectar o feedback, salvar no perfil/pedido e reagir com emoji de gato configurável, sem resposta extra.
- Se continuar pendente, DM com cooldown configurável.
- Nova compra pode gerar novo lembrete mesmo que haja feedback antigo pendente.
- Feedback via modal pode gerar um card/imagem padronizado para publicação.

## Perfil, gastos e ranking
- Perfil do cliente guarda total gasto, histórico e produtos/jogos comprados.
- Botão em painel abre perfil efêmero.
- Se nunca comprou, informar isso.
- Leaderboard público Top 20 por gasto confirmado.
- Usuário fora do Top 20 pode ver posição/total no perfil.

## Cargos automáticos
- Faixas por total gasto configuráveis.
- Após compra confirmada, recalcular nível/cargo.
- Adicionar/remover cargos conforme regra configurada.
- Enviar DM ao atingir nova faixa.

## Segurança e arquitetura
- Repositório privado.
- Segredos somente via variáveis de ambiente.
- PostgreSQL em produção.
- SQLAlchemy 2 assíncrono e Alembic.
- Operações de carteira e compra devem ser transacionais.
- Webhooks Stripe devem validar assinatura sobre o corpo bruto da requisição.
- IDs de Stripe e referências de pagamento precisam de idempotência e vínculo inequívoco com a recarga.
- IDs de Discord, emojis, textos, canais, cargos, taxas e regras devem ser configuráveis.
- Código modular; evitar `main.py` gigante.

## Módulos principais
- bot/admin
- bot/store
- bot/tickets
- bot/delivery
- bot/feedback
- bot/calculator
- bot/faq
- bot/terms
- bot/profile
- bot/leaderboard
- services/stripe_topups
- services/wallet
- services/orders
- services/transcripts
- api/webhooks
- db/models + migrations

## Validação
- Testes de concorrência da carteira.
- Idempotência de confirmação de recarga.
- Proteção contra duplicidade de pagamento.
- Testes de permissões e RBAC.
- Alembic aplicado em PostgreSQL no CI e verificação de schema drift.
