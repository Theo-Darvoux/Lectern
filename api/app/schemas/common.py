from typing import Any

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    detail: str


class PaginatedResponse[T](BaseModel):
    items: list[T]
    total: int
    page: int
    pages: int


class HealthResponse(BaseModel):
    status: str
    details: dict[str, str] | None = None


class ServiceStatus(BaseModel):
    status: str  # "healthy", "degraded", "unhealthy"
    message: str | None = None
    latency_ms: float | None = None
    metadata: dict[str, Any] | None = None


class DetailedHealthResponse(BaseModel):
    status: str
    timestamp: str
    services: dict[str, ServiceStatus]
    metrics: dict[str, Any]
