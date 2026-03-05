from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Telegram
    telegram_bot_token: str
    telegram_webhook_url: str = ""
    manager_telegram_chat_id: str = ""

    # Zammad
    zammad_url: str
    zammad_admin_token: str = ""
    zammad_admin_user: str = ""
    zammad_admin_pass: str = ""

    # Security
    zammad_webhook_secret: str = "changeme"

    # DB
    database_url: str = "sqlite+aiosqlite:////data/gateway.db"

    # SLA alerts
    sla_alert_p2: bool = True

    @property
    def zammad_auth_headers(self) -> dict:
        if self.zammad_admin_token:
            return {"Authorization": f"Token token={self.zammad_admin_token}"}
        import base64
        creds = base64.b64encode(
            f"{self.zammad_admin_user}:{self.zammad_admin_pass}".encode()
        ).decode()
        return {"Authorization": f"Basic {creds}"}


settings = Settings()
