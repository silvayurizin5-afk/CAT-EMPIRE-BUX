from fastapi import FastAPI

app = FastAPI(title="NEXTBUY API", version="0.1.0")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/webhooks/mercado-pago")
async def mercado_pago_webhook() -> dict[str, str]:
    # TODO: validar assinatura do Mercado Pago, buscar o pagamento na API,
    # aplicar idempotência no banco e creditar somente pagamentos aprovados.
    return {"status": "received"}
