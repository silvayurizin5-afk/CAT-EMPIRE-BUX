from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    discord_token: str
    discord_guild_id: int | None = None
    database_url: str
    mercado_pago_access_token: str = ""
    mercado_pago_webhook_secret: str = ""
    public_base_url: str = "http://localhost:8000"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
