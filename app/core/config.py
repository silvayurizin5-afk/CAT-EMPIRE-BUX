from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    app_name: str = "NEXTBUY"
    discord_token: SecretStr = SecretStr("")
    discord_guild_id: int | None = None
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/nextbuy"

    # Pagamento atual: PIX estático por pedido, confirmado manualmente pela equipe.
    pix_key: SecretStr = SecretStr("")
    pix_receiver_name: str = ""

    # IA: todos os provedores são opcionais. Um provedor só entra no fallback
    # quando chave e modelo estiverem configurados.
    ai_provider_order: str = "openai,anthropic,gemini,xai,mistral,groq,openrouter"
    ai_timeout_seconds: float = 18.0

    openai_api_key: SecretStr = SecretStr("")
    openai_model: str = ""
    anthropic_api_key: SecretStr = SecretStr("")
    anthropic_model: str = ""
    gemini_api_key: SecretStr = SecretStr("")
    gemini_model: str = ""
    xai_api_key: SecretStr = SecretStr("")
    xai_model: str = ""
    mistral_api_key: SecretStr = SecretStr("")
    mistral_model: str = ""
    groq_api_key: SecretStr = SecretStr("")
    groq_model: str = ""
    openrouter_api_key: SecretStr = SecretStr("")
    openrouter_model: str = ""

    # Integrações antigas mantidas somente para compatibilidade de histórico/webhooks legados.
    stripe_secret_key: SecretStr = SecretStr("")
    stripe_webhook_secret: SecretStr = SecretStr("")
    stripe_credits_product_id: str | None = None
    mercado_pago_access_token: SecretStr = SecretStr("")
    mercado_pago_webhook_secret: SecretStr = SecretStr("")

    public_base_url: str = "http://localhost:8000"
    webhook_signature_tolerance_seconds: int = 300

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def ai_providers(self) -> list[str]:
        return [
            item.strip().lower()
            for item in self.ai_provider_order.split(",")
            if item.strip()
        ]

    @property
    def stripe_webhook_url(self) -> str:
        return f"{self.public_base_url.rstrip('/')}/webhooks/stripe"

    @property
    def stripe_checkout_success_url(self) -> str:
        return f"{self.public_base_url.rstrip('/')}/payments/success?session_id={{CHECKOUT_SESSION_ID}}"

    @property
    def stripe_checkout_cancel_url(self) -> str:
        return f"{self.public_base_url.rstrip('/')}/payments/cancel"

    @property
    def mercado_pago_webhook_url(self) -> str:
        return f"{self.public_base_url.rstrip('/')}/webhooks/mercado-pago"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
