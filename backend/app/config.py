from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.1-flash-lite"
    openai_api_key: str = ""
    openai_model: str = "gpt-5-mini"

    redis_url: str = "redis://redis:6379/0"
    upload_dir: Path = Path("/data/uploads")
    max_upload_mb: int = 20
    document_ttl_hours: int = 24

    llm_max_attempts: int = 3
    llm_timeout_seconds: int = 60
    pages_per_chunk: int = 5
    claim_idle_seconds: int = 120
    max_deliveries: int = 3

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def document_ttl_seconds(self) -> int:
        return self.document_ttl_hours * 3600


@lru_cache
def get_settings() -> Settings:
    return Settings()
