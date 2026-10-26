"""Natural-language dataset question endpoint."""

import math

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.config import Settings, get_settings
from app.schemas.questions import QuestionProcessingResponse, QuestionRequest
from app.services.csv_validator import CsvValidationError
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
    process_question,
)

router = APIRouter()


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
    except InvalidDatasetIdError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid dataset identifier.",
        ) from exc
    except DatasetNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dataset not found or expired.",
        ) from exc
    except QuestionContextError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    except QuestionInterpretationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    except LLMConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except LLMTimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="The language model request timed out.",
        ) from exc
    except LLMRateLimitError as exc:
        headers = None
        if exc.retry_after is not None and math.isfinite(exc.retry_after):
            headers = {
                "Retry-After": str(max(0, math.ceil(exc.retry_after)))
            }
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="The language model rate limit was reached.",
            headers=headers,
        ) from exc
    except LLMQuotaExceededError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="The language model quota is exhausted.",
        ) from exc
    except (
        LLMAuthenticationError,
        LLMPermissionError,
        LLMModelUnavailableError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The configured language model is unavailable.",
        ) from exc
    except (LLMConnectionError, LLMTransientError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The language model provider is temporarily unavailable.",
        ) from exc
    except (
        LLMInvalidRequestError,
        LLMProviderError,
        LLMResponseError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The language model returned an invalid response.",
        ) from exc
    except (CsvValidationError, DatasetStorageError, OSError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The stored dataset could not be read safely.",
        ) from exc
