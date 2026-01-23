"""
LanceDB Service Configuration
"""
import os
from typing import Optional
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """LanceDB Service Settings"""

    # Service
    APP_NAME: str = "DIntelliHub LanceDB Service"
    APP_VERSION: str = "0.1.0"
    HOST: str = "0.0.0.0"
    PORT: int = 8765
    DEBUG: bool = False

    # LanceDB
    LANCEDB_URI: str = "/data/lancedb"
    LANCEDB_DEFAULT_TABLE: str = "vectors"

    # Embedding
    EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"
    EMBEDDING_DIMENSION: int = 384

    # API
    API_V1_PREFIX: str = "/api/v1"

    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "json"

    class Config:
        env_file = ".env"
        case_sensitive = True


# Global settings instance
settings = Settings()
