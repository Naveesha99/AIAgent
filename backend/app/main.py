"""FastAPI application entry point."""

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes.datasets import router as datasets_router
from app.api.routes.health import router as health_router
from app.api.routes.questions import router as questions_router
from app.core.config import get_settings

settings = get_settings()

app = FastAPI(
    title="AI Data Analyst API",
    version="0.1.0",
    debug=settings.app_env == "development",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Accept", "Content-Type"],
)
app.include_router(health_router, prefix="/api/v1")
app.include_router(datasets_router, prefix="/api/v1/datasets")
app.include_router(questions_router, prefix="/api/v1/datasets")


@app.exception_handler(RequestValidationError)
async def handle_request_validation_error(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """Avoid echoing uploaded form values in upload validation errors."""
    if request.url.path != "/api/v1/datasets/upload":
        return await request_validation_exception_handler(request, exc)

    sanitized_errors = [
        {
            key: value
            for key, value in error.items()
            if key not in {"input", "ctx"}
        }
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={"detail": sanitized_errors},
    )
