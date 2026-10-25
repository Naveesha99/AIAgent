"""Health-check endpoint."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class HealthResponse(BaseModel):
    """Response indicating that the API process is responsive."""

    status: Literal["ok"]
    service: Literal["ai-data-analyst-api"]


@router.get("/health", response_model=HealthResponse, tags=["health"])
def get_health() -> HealthResponse:
    """Return the process health without checking external services."""
    return HealthResponse(status="ok", service="ai-data-analyst-api")
