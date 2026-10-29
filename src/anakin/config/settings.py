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

    # Chave e modelo do Google Gemini
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash-lite"

    # Pesos do Algoritmo de Fila Ponderada por Carga (Score de Ocupação)
    peso_status_atendendo: int = 3
    peso_status_a_fazer: int = 2
    peso_status_pausado: int = 1
    peso_status_outros: int = 1

    # Outras chaves opcionais
    nvidia_api_key: str | None = None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Retorna uma instância singleton com cache das configurações."""
    return Settings()
