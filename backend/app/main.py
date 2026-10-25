"""FastAPI application entry point."""

from fastapi import FastAPI

from app.api.routes.health import router as health_router
from app.core.config import get_settings

settings = get_settings()

app = FastAPI(
    title="AI Data Analyst API",
    version="0.1.0",
    debug=settings.app_env == "development",
)
app.include_router(health_router, prefix="/api/v1")
