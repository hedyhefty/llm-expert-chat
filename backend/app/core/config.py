from functools import cached_property

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    secret_key: str = "change-me"
    api_key_master_secret: str = "change-me-32-byte-minimum-secret"
    access_token_expire_minutes: int = 60 * 24 * 7
    llm_request_timeout_seconds: float = 120
    cors_origins_raw: str = Field(default="http://localhost:5173", alias="CORS_ORIGINS")

    mysql_host: str = "localhost"
    mysql_port: int = 3306
    mysql_database: str = "llm_expert_chat"
    mysql_user: str = "llm_user"
    mysql_password: str = "llm_password"

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins_raw.split(",") if origin.strip()]

    @cached_property
    def database_url(self) -> str:
        return (
            f"mysql+pymysql://{self.mysql_user}:{self.mysql_password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}"
        )


settings = Settings()
