"""Módulo de integração com a API do Milvus (respeitando rate limit de 62s e trava read-only)."""

from anakin.milvus.readonly_client import MilvusReadOnlyClient, ReadOnlySafetyError

__all__ = ["MilvusReadOnlyClient", "ReadOnlySafetyError"]
