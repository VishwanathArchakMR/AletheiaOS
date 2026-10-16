from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    backend_host: str = "0.0.0.0"
    backend_port: int = Field(default=8000, ge=1, le=65535)
    frontend_origin: str = "http://localhost:5173"
    falkordb_host: str = "localhost"
    falkordb_port: int = Field(default=6379, ge=1, le=65535)
    falkordb_graph_name: str = "aletheia_os"
    falkordb_password: str | None = None
    cors_allowed_origins: list[str] = ["http://localhost:5173"]
    log_level: str = "INFO"

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def parse_origins(cls, value: str | list[str]) -> list[str]:
        return [v.strip() for v in value.split(",") if v.strip()] if isinstance(value, str) else value

    @field_validator("falkordb_graph_name")
    @classmethod
    def graph_name_is_safe(cls, value: str) -> str:
        if not value.replace("_", "").isalnum():
            raise ValueError("FALKORDB_GRAPH_NAME may contain only letters, numbers, and underscores")
        return value

    @property
    def graph_password(self) -> str | None:
        return self.falkordb_password or None


@lru_cache
def get_settings() -> Settings:
    return Settings()

