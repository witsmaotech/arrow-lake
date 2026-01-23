"""
Daft Service Data Models
"""
from typing import List, Optional, Any, Dict, Union
from pydantic import BaseModel, Field
from datetime import datetime


class HealthResponse(BaseModel):
    """Health check response"""

    status: str
    service: str
    version: str
    ray_connected: bool = False
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class ProcessRequest(BaseModel):
    """Data processing request"""

    source: Dict[str, Any] = Field(..., description="Source configuration (MinIO, local, etc.)")
    destination: Dict[str, Any] = Field(..., description="Destination configuration")
    operations: List[Dict[str, Any]] = Field(..., description="Processing operations")
    batch_size: int = Field(default=1000, ge=1, description="Batch size for processing")
    embedding_model: Optional[str] = Field(
        default="sentence-transformers/all-MiniLM-L6-v2",
        description="Embedding model for vector operations",
    )


class ProcessResponse(BaseModel):
    """Data processing response"""

    success: bool
    records_processed: int
    execution_time_ms: float
    message: str
    output_location: Optional[str] = None


class ETLRequest(BaseModel):
    """ETL pipeline request"""

    name: str = Field(..., description="ETL pipeline name")
    extract: Dict[str, Any] = Field(..., description="Extract configuration")
    transform: List[Dict[str, Any]] = Field(default_factory=list, description="Transform operations")
    load: Dict[str, Any] = Field(..., description="Load configuration")
    schedule: Optional[str] = Field(default=None, description="Schedule (cron format)")


class ETLResponse(BaseModel):
    """ETL pipeline response"""

    success: bool
    pipeline_id: str
    status: str
    records_processed: int
    message: str


class QueryRequest(BaseModel):
    """Data query request"""

    source: Dict[str, Any] = Field(..., description="Data source")
    filters: Optional[Dict[str, Any]] = Field(default=None, description="Filter conditions")
    columns: Optional[List[str]] = Field(default=None, description="Columns to select")
    limit: int = Field(default=100, ge=1, le=10000, description="Max records")
    offset: int = Field(default=0, ge=0, description="Offset for pagination")


class QueryResponse(BaseModel):
    """Data query response"""

    success: bool
    total: int
    data: List[Dict[str, Any]]
    execution_time_ms: float


class EmbeddingRequest(BaseModel):
    """Text embedding request"""

    texts: List[str] = Field(..., description="Texts to embed")
    model: str = Field(
        default="sentence-transformers/all-MiniLM-L6-v2",
        description="Embedding model",
    )
    batch_size: int = Field(default=32, ge=1, description="Batch size for embedding")


class EmbeddingResponse(BaseModel):
    """Text embedding response"""

    success: bool
    embeddings: List[List[float]]
    dimension: int
    count: int
    execution_time_ms: float
