# NEXTBUY

Loja automatizada para Discord com créditos internos, Mercado Pago, tickets, transcripts, entregas, feedbacks, cargos, ranking, calculadora de Robux e FAQ automático.

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
- 1 BRL = 1 crédito, com suporte a centavos.
- Dinheiro usa `Decimal`/`NUMERIC`, nunca `float`.
- Recarga só credita depois de webhook autenticado e idempotente.
- Segredos ficam fora do Git em variáveis de ambiente.
- Ranking e cargos de cliente usam apenas compras confirmadas do servidor atual.

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

## Mercado Pago
As novas recargas usam **Checkout Pro via Orders API**. O backend cria uma order com chave de idempotência, recebe o `checkout_url` e só adiciona créditos depois de consultar a order autenticada e confirmar `processed/accredited` com valor e moeda esperados.

No painel do Mercado Pago, configure a URL HTTPS:

```text
https://SEU-DOMINIO/webhooks/mercado-pago
```

Ative o evento **Order (Mercado Pago)**. O endpoint ainda aceita o evento legado `payment` para recargas antigas criadas pelo fluxo de Preferences.

Se uma recarga já creditada depois receber reembolso total/parcial ou contestação, a NEXTBUY **não força um débito que poderia deixar a carteira negativa**. Em vez disso, bloqueia novas compras daquela conta no servidor, registra o incidente na auditoria e exige revisão manual de um administrador pelo `/staff` → **Revisar bloqueios**.

## Canal de feedbacks
O canal configurado como **Feedbacks** é gerenciado pela NEXTBUY: todos podem visualizar, mas somente o cargo de cliente, faixas de cliente ativas e cargos configurados da staff podem enviar mensagens. A sincronização acontece ao configurar cargos/canal, ao alterar faixas e na inicialização do bot. Como o canal é dedicado a feedbacks, a sincronização substitui os overwrites do canal para evitar permissões antigas deixando usuários indevidos escreverem.

## Configuração pelo Discord
Use `/admin`. O painel concentra configuração de cargos, canais, produtos, cotações de Robux, termos, respostas automáticas, faixas de cliente, publicação da loja e publicação do ranking.

O canal de calculadora entende valores como `10,80` como reais/créditos e mensagens como `380 Robux` como quantidade de Robux. O canal de FAQ responde por palavras-chave configuradas pela staff.

Veja `docs/PROJECT_SPEC.md` para o escopo completo.
