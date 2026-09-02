from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_INSECURE_JWT_SECRETS = {"change-me", "change-me-to-a-long-random-string"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"  # dev | test | prod
    app_version: str = "2.0.0"
    database_url: str = "sqlite+aiosqlite:///./data/dev.db"
    jwt_secret: str = "change-me"
    access_token_minutes: int = 30
    refresh_token_days: int = 14
    stream_token_minutes: int = 10
    cors_origins: str = "http://localhost:5173"
    front_base_url: str = "http://localhost:5173"
    agent_impl: str = "app.agent.mock:MockAgent"
    storage_backend: str = "local"  # local | s3
    storage_local_dir: str = "./data"
    s3_bucket: str | None = None
    s3_region: str | None = None
    mail_backend: str = "mock"  # mock | smtp
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_starttls: bool = True
    mail_from: str = "no-reply@cardefender.kr"
    ffprobe_bin: str = "ffprobe"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_prod(self) -> bool:
        return self.app_env == "prod"

    @model_validator(mode="after")
    def _check_jwt_secret(self) -> "Settings":
        if self.is_prod and (self.jwt_secret in _INSECURE_JWT_SECRETS or len(self.jwt_secret) < 32):
            raise ValueError("JWT_SECRET must be a long random value in prod (32자 이상, 기본값 금지)")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
