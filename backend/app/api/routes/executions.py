"""Submission and lifecycle endpoints for isolated Python executions."""

from typing import Union
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.schemas.execution import (
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatusResponse,
)
from app.services.execution_service import (
    ExecutionService,
    ExecutionServiceError,
)

router = APIRouter()


def get_execution_service(request: Request) -> ExecutionService:
    return request.app.state.execution_service


def _http_status_for_error(exc: ExecutionServiceError) -> int:
    code = exc.error.code.value
    if code == "execution_engine_unavailable":
        return status.HTTP_503_SERVICE_UNAVAILABLE
    if code == "invalid_execution_request":
        return status.HTTP_404_NOT_FOUND
    if code == "dataset_not_found":
        return status.HTTP_404_NOT_FOUND
    return status.HTTP_500_INTERNAL_SERVER_ERROR


def _error_response(exc: ExecutionServiceError) -> JSONResponse:
    return JSONResponse(
        status_code=_http_status_for_error(exc),
        content={"error": exc.error.model_dump(mode="json")},
    )


@router.post(
    "",
    response_model=ExecutionStatusResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["executions"],
    responses={
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": "The execution request is invalid."
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "Execution is disabled or Docker is unavailable."
        },
    },
)
async def submit_execution(
    request: ExecutionRequest,
    service: ExecutionService = Depends(get_execution_service),
) -> ExecutionStatusResponse:
    """Queue an execution and return its server-generated identifier."""
    if "execution_id" in request.model_fields_set:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="execution_id is assigned by the server.",
        )
    try:
        execution_id = await service.submit(request)
        execution_status = await service.get_status(execution_id)
    except ExecutionServiceError as exc:
        return _error_response(exc)
    return ExecutionStatusResponse(
        execution_id=execution_id,
        status=execution_status,
    )


@router.get(
    "/{execution_id}",
    response_model=ExecutionStatusResponse,
    tags=["executions"],
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "The execution identifier is unknown or expired."
        }
    },
)
async def get_execution_status(
    execution_id: UUID,
    service: ExecutionService = Depends(get_execution_service),
) -> ExecutionStatusResponse:
    """Return the latest known status for an execution."""
    try:
        execution_status = await service.get_status(execution_id)
    except ExecutionServiceError as exc:
        return _error_response(exc)
    return ExecutionStatusResponse(
        execution_id=execution_id,
        status=execution_status,
    )


@router.get(
    "/{execution_id}/result",
    response_model=Union[ExecutionResult, ExecutionStatusResponse],
    tags=["executions"],
    responses={
        status.HTTP_202_ACCEPTED: {
            "description": "Execution is still in progress; poll this endpoint again."
        },
        status.HTTP_404_NOT_FOUND: {
            "description": "The execution identifier is unknown or expired."
        },
    },
)
async def get_execution_result(
    execution_id: UUID,
    service: ExecutionService = Depends(get_execution_service),
) -> Union[ExecutionResult, ExecutionStatusResponse, JSONResponse]:
    """Return a terminal result, or the current status while it is running."""
    try:
        result = await service.get_result(execution_id)
        if result is not None:
            return result
        execution_status = await service.get_status(execution_id)
        if execution_status.terminal:
            result = await service.get_result(execution_id)
            if result is not None:
                return result
    except ExecutionServiceError as exc:
        return _error_response(exc)
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content=ExecutionStatusResponse(
            execution_id=execution_id,
            status=execution_status,
        ).model_dump(mode="json"),
    )


@router.post(
    "/{execution_id}/cancel",
    response_model=ExecutionResult,
    tags=["executions"],
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "The execution identifier is unknown or expired."
        }
    },
)
async def cancel_execution(
    execution_id: UUID,
    service: ExecutionService = Depends(get_execution_service),
) -> ExecutionResult:
    """Cancel a live execution or return its existing terminal result."""
    try:
        return await service.cancel(execution_id)
    except ExecutionServiceError as exc:
        return _error_response(exc)
