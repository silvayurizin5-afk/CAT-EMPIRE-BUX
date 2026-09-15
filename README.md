# NEXTBUY

Loja automatizada para Discord com créditos internos, Stripe, tickets, transcripts, entregas, feedbacks, cargos, ranking, calculadora de Robux e FAQ automático.

## Stack
- Python 3.12+
- discord.py
- FastAPI
- PostgreSQL
- SQLAlchemy 2 + Alembic
- Stripe Checkout + webhooks

## Regras principais
- Cliente não usa slash commands: compra por embeds, botões, selects e modais.
- Slash commands são restritos à staff.
- 1 BRL = 1 crédito, com suporte a centavos.
- Dinheiro usa `Decimal`/`NUMERIC`, nunca `float`.
- Recarga só credita depois de webhook Stripe autenticado e idempotente.
- Dados de cartão não passam pelo Discord nem pelo backend da NEXTBUY; o checkout é hospedado pela Stripe.
- Segredos ficam fora do Git em variáveis de ambiente.
- Ranking e cargos de cliente usam apenas compras confirmadas do servidor atual.
- O endpoint legado do Mercado Pago permanece apenas para finalizar recargas antigas já criadas antes da migração.

## Desenvolvimento local

```bash
python -m venv .venv
# ative o ambiente virtual
pip install -e ".[dev]"
cp .env.example .env
alembic upgrade head
```

API:

```bash
uvicorn app.api.main:app --reload
```

Bot:

```bash
python -m app.bot.main
```

## Stripe
Configure no ambiente:

```text
STRIPE_SECRET_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_CREDITS_PRODUCT_ID=prod_...
PUBLIC_BASE_URL=https://seu-dominio-publico
```

Webhook esperado: `POST /webhooks/stripe`.

Eventos usados pelo sistema:
- `checkout.session.completed`
- `checkout.session.async_payment_succeeded`
- `checkout.session.async_payment_failed`
- `checkout.session.expired`
- `charge.refunded`
- `charge.dispute.created`

O Checkout usa métodos de pagamento dinâmicos da Stripe. Os métodos realmente exibidos dependem do que estiver habilitado e disponível para a conta/região.

## Configuração pelo Discord
Use `/admin`. O painel concentra configuração de cargos, canais, produtos, cotações de Robux, termos, respostas automáticas, faixas de cliente, publicação da loja e publicação do ranking.

O canal de calculadora entende valores como `10,80` como reais/créditos e mensagens como `380 Robux` como quantidade de Robux. O canal de FAQ responde por palavras-chave configuradas pela staff.

Veja `docs/PROJECT_SPEC.md` para o escopo completo.
