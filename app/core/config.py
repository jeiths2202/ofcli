"""
OFKMS v2.0 Configuration (Pydantic Settings)
"""
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # LLM (Qwen3 32B)
    LLM_BASE_URL: str = "http://192.168.8.11:12810/v1"
    LLM_MODEL: str = "/opt/models/qwen3-32b"
    LLM_TIMEOUT: int = 60
    LLM_TEMPERATURE: float = 0.3
    LLM_MAX_TOKENS: int = 2048

    # BGE-M3
    BGE_M3_BASE_URL: str = "http://192.168.8.11:12801"
    BGE_M3_TIMEOUT: int = 10

    # OFCode
    OFCODE_BASE_URL: str = "http://192.168.8.11:12820"
    OFCODE_TIMEOUT: int = 10

    # Neo4j
    NEO4J_URI: str = "bolt://192.168.8.11:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "graphrag2024"

    # PostgreSQL
    POSTGRES_HOST: str = "192.168.8.11"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "ragdb"
    POSTGRES_USER: str = "raguser"
    POSTGRES_PASSWORD: str = "ragpassword123"

    # Pipeline
    FALLBACK_THRESHOLD: float = 0.3
    SEARCH_TOP_K: int = 10

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
