# NEXTBUY

Loja automatizada para Discord com créditos internos, pagamentos Mercado Pago, tickets, transcripts, entregas, feedbacks, cargos e ranking.

## Stack
- Python 3.12+
- discord.py
- FastAPI
- PostgreSQL
- SQLAlchemy 2 + Alembic
- Mercado Pago

## Regras principais
- Cliente não usa slash commands: compra por embeds, botões, selects e modais.
- Slash commands são restritos à staff.
- 1 BRL = 1 crédito, com centavos.
- Dinheiro usa `Decimal`/`NUMERIC`, nunca `float`.
- Crédito só entra após webhook validado e idempotente.
- Segredos ficam fora do Git (`.env`).

Veja `docs/PROJECT_SPEC.md`.
