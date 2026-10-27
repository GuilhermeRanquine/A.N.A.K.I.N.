"""Configurações da aplicação A.N.A.K.I.N baseadas em pydantic-settings."""

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configurações do sistema carregadas do ambiente / arquivo .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Chaves e endpoints do Milvus
    milvus_api_token: str
    milvus_base_url: str = "https://apiintegracao.milvus.com.br/api"
    milvus_rate_limit_seconds: int = 62
    milvus_read_only: bool = True

    # Caminho do cache DuckDB
    duckdb_path: str = "data/anakin_readonly.duckdb"

    # Outras chaves opcionais
    nvidia_api_key: str | None = None
    gemini_api_key: str | None = None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Retorna uma instância singleton com cache das configurações."""
    return Settings()
