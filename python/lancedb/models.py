"""
LanceDB Service Data Models
"""
from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field
from datetime import datetime


class HealthResponse(BaseModel):
    """Health check response"""

    status: str
    service: str
    version: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class SearchRequest(BaseModel):
    """Semantic search request"""

    collection: str = Field(..., description="Collection/table name")
    vector: List[float] = Field(..., description="Query vector")
    limit: int = Field(default=10, ge=1, le=100, description="Max results")
    metric: str = Field(default="L2", description="Distance metric: L2 or Cosine")
    filter: Optional[Dict[str, Any]] = Field(default=None, description="Filter conditions")


class SearchResult(BaseModel):
    """Single search result"""

    id: str
    score: float
    data: Dict[str, Any]


class SearchResponse(BaseModel):
    """Semantic search response"""

    items: List[SearchResult]
    total: int
    latency_ms: float


class UpsertItem(BaseModel):
    """Single upsert item"""

    id: str
    vector: List[float]
    metadata: Dict[str, Any]


class UpsertRequest(BaseModel):
    """Upsert data request"""

    collection: str = Field(..., description="Collection/table name")
    items: List[UpsertItem] = Field(..., description="Items to upsert")
    mode: str = Field(default="append", description="Write mode: append or overwrite")


class UpsertResponse(BaseModel):
    """Upsert response"""

    success: bool
    count: int
    message: str


class RecentRequest(BaseModel):
    """Get recent records request"""

    collection: str = Field(..., description="Collection/table name")
    limit: int = Field(default=10, ge=1, le=100, description="Max records")


class RecentResponse(BaseModel):
    """Recent records response"""

    items: List[Dict[str, Any]]
    total: int


class DeleteRequest(BaseModel):
    """Delete records request"""

    collection: str = Field(..., description="Collection/table name")
    ids: List[str] = Field(..., description="IDs to delete")


class DeleteResponse(BaseModel):
    """Delete response"""

    success: bool
    count: int
    message: str
