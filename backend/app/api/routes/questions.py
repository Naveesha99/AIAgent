"""Natural-language dataset question and code generation endpoints."""

import math
from typing import Optional

import anyio

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.config import Settings, get_settings
from app.schemas.code_generation import (
    CodeGenerationResponse,
    GenerateCodeRequest,
)
from app.schemas.questions import QuestionProcessingResponse, QuestionRequest
from app.services.code_generator import (
    CodeGenerationError,
    generate_pandas_code,
)
from app.services.csv_validator import CsvValidationError
from app.services.data_quality_analyzer import analyze_dataset_quality
from app.services.dataset_storage import (
    DatasetNotFoundError,
    DatasetStorage,
    DatasetStorageError,
    InvalidDatasetIdError,
)
from app.services.llm.base import (
    LLMAuthenticationError,
    LLMConfigurationError,
    LLMConnectionError,
    LLMInvalidRequestError,
    LLMModelUnavailableError,
    LLMPermissionError,
    LLMProviderError,
    LLMQuotaExceededError,
    LLMRateLimitError,
    LLMResponseError,
    LLMTimeoutError,
    LLMTransientError,
)
from app.services.llm.factory import create_llm_client
from app.services.question_context import QuestionContextError
from app.services.question_processor import (
    QuestionInterpretationError,
    interpret_question,
    process_question,
)

router = APIRouter()


def _merge_warnings(*groups: list[str]) -> list[str]:
    warnings: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for warning in group:
            if warning not in seen:
                seen.add(warning)
                warnings.append(warning)
    if len(warnings) > 20:
        return warnings[:19] + ["Additional warnings were omitted."]
    return warnings


def _http_error_for(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, InvalidDatasetIdError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid dataset identifier.",
        )
    if isinstance(exc, DatasetNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dataset not found or expired.",
        )
    if isinstance(exc, QuestionContextError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        )
    if isinstance(exc, QuestionInterpretationError):
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        )
    if isinstance(exc, CodeGenerationError):
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The model returned an invalid code generation response.",
        )
    if isinstance(exc, LLMConfigurationError):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        )
    if isinstance(exc, LLMTimeoutError):
        return HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="The language model request timed out.",
        )
    if isinstance(exc, LLMRateLimitError):
        headers: Optional[dict[str, str]] = None
        if exc.retry_after is not None and math.isfinite(exc.retry_after):
            headers = {
                "Retry-After": str(max(0, math.ceil(exc.retry_after)))
            }
        return HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="The language model rate limit was reached.",
            headers=headers,
        )
    if isinstance(exc, LLMQuotaExceededError):
        return HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="The language model quota is exhausted.",
        )
    if isinstance(
        exc,
        (
            LLMAuthenticationError,
            LLMPermissionError,
            LLMModelUnavailableError,
        ),
    ):
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The configured language model is unavailable.",
        )
    if isinstance(exc, (LLMConnectionError, LLMTransientError)):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The language model provider is temporarily unavailable.",
        )
    if isinstance(
        exc,
        (LLMInvalidRequestError, LLMProviderError, LLMResponseError),
    ):
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The language model returned an invalid response.",
        )
    if isinstance(
        exc,
        (CsvValidationError, DatasetStorageError, OSError, ValueError),
    ):
        return HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The stored dataset could not be read safely.",
        )
    raise exc


@router.post(
    "/{dataset_id}/questions",
    response_model=QuestionProcessingResponse,
    tags=["questions"],
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "The dataset does not exist or has expired."
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": "The dataset identifier or question is invalid."
        },
        status.HTTP_429_TOO_MANY_REQUESTS: {
            "description": "The provider rate limit or quota was reached."
        },
        status.HTTP_502_BAD_GATEWAY: {
            "description": "The provider returned an invalid interpretation."
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": (
                "The provider is not configured or is temporarily unavailable."
            )
        },
        status.HTTP_504_GATEWAY_TIMEOUT: {
            "description": "The provider did not respond before the timeout."
        },
        status.HTTP_500_INTERNAL_SERVER_ERROR: {
            "description": "The stored dataset could not be read safely."
        },
    },
)
async def ask_dataset_question(
    dataset_id: str,
    request: QuestionRequest,
    settings: Settings = Depends(get_settings),
) -> QuestionProcessingResponse:
    """Interpret a question using dataset metadata without calculating results."""
    storage = DatasetStorage(
        settings.dataset_storage_dir,
        ttl_hours=settings.dataset_ttl_hours,
    )
    try:
        storage.resolve(dataset_id)
        llm_client = create_llm_client(settings)
        return await process_question(
            storage,
            settings,
            llm_client,
            dataset_id,
            request.question,
        )
    except Exception as exc:
        raise _http_error_for(exc) from exc


@router.post(
    "/{dataset_id}/generate-code",
    response_model=CodeGenerationResponse,
    tags=["questions"],
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "The dataset does not exist or has expired."
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": "The dataset identifier or question is invalid."
        },
        status.HTTP_429_TOO_MANY_REQUESTS: {
            "description": "The provider rate limit or quota was reached."
        },
        status.HTTP_502_BAD_GATEWAY: {
            "description": (
                "The provider returned an invalid interpretation or code "
                "generation response."
            )
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": (
                "The provider is not configured or is temporarily unavailable."
            )
        },
        status.HTTP_504_GATEWAY_TIMEOUT: {
            "description": "The provider did not respond before the timeout."
        },
        status.HTTP_500_INTERNAL_SERVER_ERROR: {
            "description": "The stored dataset could not be read safely."
        },
    },
)
async def generate_dataset_code(
    dataset_id: str,
    request: GenerateCodeRequest,
    settings: Settings = Depends(get_settings),
) -> CodeGenerationResponse:
    """Generate unexecuted pandas code for a validated question."""
    storage = DatasetStorage(
        settings.dataset_storage_dir,
        ttl_hours=settings.dataset_ttl_hours,
    )
    try:
        storage.resolve(dataset_id)
        llm_client = create_llm_client(settings)
        interpretation, statistics, interpretation_warnings = (
            await interpret_question(
                storage,
                settings,
                llm_client,
                dataset_id,
                request.question,
                allow_derived_metrics=True,
            )
        )
        if interpretation.clarification_question is not None:
            return CodeGenerationResponse(
                dataset_id=statistics.dataset_id,
                question=request.question,
                status="clarification_needed",
                interpretation=interpretation,
                referenced_columns=[],
                assumptions=interpretation.assumptions,
                warnings=interpretation_warnings,
                clarification_question=(
                    interpretation.clarification_question
                ),
            )

        quality = await anyio.to_thread.run_sync(
            analyze_dataset_quality,
            storage,
            settings,
            dataset_id,
        )
        result = await generate_pandas_code(
            llm_client,
            request.question,
            interpretation,
            statistics,
            quality,
        )
        warnings = _merge_warnings(
            result.warnings,
            interpretation_warnings,
        )
        return CodeGenerationResponse(
            dataset_id=statistics.dataset_id,
            question=request.question,
            status=(
                "generated" if result.code is not None
                else "clarification_needed"
            ),
            interpretation=interpretation,
            generated_code=result.code,
            explanation=result.explanation,
            operation=result.operation,
            referenced_columns=result.referenced_columns,
            assumptions=result.assumptions,
            warnings=warnings,
            clarification_question=(
                interpretation.clarification_question
            ),
        )
    except Exception as exc:
        raise _http_error_for(exc) from exc
