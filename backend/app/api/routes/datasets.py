"""Dataset upload endpoints."""

import anyio
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.core.config import Settings, get_settings
from app.schemas.dataset import DatasetUploadResponse
from app.services.csv_validator import CsvValidationError, validate_csv
from app.services.uploads import (
    UploadReadError,
    UploadTooLargeError,
    count_upload_bytes,
)

router = APIRouter()


@router.post(
    "/upload",
    response_model=DatasetUploadResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["datasets"],
    responses={
        status.HTTP_400_BAD_REQUEST: {
            "description": "The filename or CSV content is invalid, or the upload could not be read."
        },
        status.HTTP_413_CONTENT_TOO_LARGE: {
            "description": "The upload or CSV validation exceeds a configured resource limit."
        },
    },
)
async def upload_dataset(
    file: UploadFile = File(..., description="A CSV file to accept for analysis."),
    settings: Settings = Depends(get_settings),
) -> DatasetUploadResponse:
    """Validate and acknowledge a CSV upload without retaining its contents."""
    try:
        filename = file.filename
        if filename is None or not filename.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A non-empty CSV filename is required.",
            )
        if not filename.lower().endswith(".csv"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only files with a .csv filename extension are accepted.",
            )

        try:
            size_bytes = await count_upload_bytes(
                file,
                settings.max_upload_size_bytes,
            )
            await file.seek(0)
            await anyio.to_thread.run_sync(
                validate_csv,
                file.file,
                settings,
            )
        except UploadTooLargeError as exc:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=(
                    "The uploaded file exceeds the maximum size of "
                    f"{settings.max_upload_size_bytes} bytes."
                ),
            ) from exc
        except UploadReadError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The uploaded file could not be read.",
            ) from exc
        except CsvValidationError as exc:
            raise HTTPException(
                status_code=(
                    status.HTTP_413_CONTENT_TOO_LARGE
                    if exc.resource_limit
                    else status.HTTP_400_BAD_REQUEST
                ),
                detail=str(exc),
            ) from exc
        except (OSError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The uploaded file could not be read.",
            ) from exc

        return DatasetUploadResponse(
            dataset_id=uuid4(),
            filename=filename,
            size_bytes=size_bytes,
            content_type=file.content_type,
            status="uploaded",
        )
    finally:
        try:
            await file.close()
        except (OSError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The uploaded file could not be closed safely.",
            ) from exc
