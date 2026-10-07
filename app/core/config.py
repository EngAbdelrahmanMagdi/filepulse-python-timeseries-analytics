from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    watch_root: Path = Path("watched_data")
    udp_host: str = "127.0.0.1"
    udp_port: int = Field(default=9999, ge=1, le=65535)
    udp_max_payload: int = Field(default=8192, ge=256, le=8192)
    observer_mode: Literal["polling", "native"] = "polling"
    poll_interval: float = Field(default=1.0, ge=0.1, le=60)
    postgres_host: str = "localhost"
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_db: str = "filepulse"
    postgres_user: str = "filepulse"
    postgres_password: SecretStr | None = None
    mongo_uri: SecretStr | None = None
    mongo_database: str = "filepulse"
    db_timeout_seconds: int = Field(default=3, ge=1, le=30)
    api_base_url: str = "http://localhost:8000"
    api_timeout_seconds: int = Field(default=30, ge=1, le=120)
    scan_timeout_seconds: int = Field(default=90, ge=1, le=180)

    def require_databases(self) -> None:
        if not self.postgres_password or not self.postgres_password.get_secret_value():
            raise ValueError("POSTGRES_PASSWORD is required")
        if not self.mongo_uri or not self.mongo_uri.get_secret_value():
            raise ValueError("MONGO_URI is required")
