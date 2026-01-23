"""
Daft Service Configuration
"""
import os
from typing import Optional
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Daft Service Settings"""

    # Service
    APP_NAME: str = "DIntelliHub Daft Service"
    APP_VERSION: str = "0.1.0"
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    DEBUG: bool = False

    # MinIO/S3
    MINIO_ENDPOINT: str = "minio:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin123"
    MINIO_USE_SSL: bool = False

    # PostgreSQL
    POSTGRES_HOST: str = "postgres"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "admin"
    POSTGRES_PASSWORD: str = "admin123"
    POSTGRES_DB: str = "gravitino"

    # LanceDB Service
    LANCEDB_SERVICE_URL: str = "http://lancedb-service:8765"

    # Daft/Ray
    RAY_ADDRESS: Optional[str] = None  # None for local, "ray://head:10001" for cluster
    DAFT_WORKERS: int = 4
    DAFT_MEMORY_LIMIT: str = "16GB"

    # Processing
    BATCH_SIZE: int = 1000
    MAX_WORKERS: int = 4
    PROCESSING_TIMEOUT: int = 3600

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
