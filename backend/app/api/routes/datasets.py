"""Dataset upload endpoints."""

from typing import Optional

import anyio

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.core.config import Settings, get_settings
from app.schemas.dataset import DatasetUploadResponse
from app.services.csv_validator import CsvValidationError, validate_csv
from app.services.dataset_storage import (
    DatasetStorage,
    DatasetStorageError,
    StagedDataset,
)
from app.services.uploads import (
    UploadReadError,
    UploadTooLargeError,
)

router = APIRouter()


@router.post(
    "/upload",
    response_model=DatasetUploadResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["datasets"],
    responses={
        status.HTTP_400_BAD_REQUEST: {
            "description": (
                "The filename or CSV content is invalid, or the upload "
                "could not be read."
            )
        },
        status.HTTP_413_CONTENT_TOO_LARGE: {
            "description": "The upload or CSV validation exceeds a configured resource limit."
        },
        status.HTTP_500_INTERNAL_SERVER_ERROR: {
            "description": "The validated upload could not be stored safely."
        },
    },
)
async def upload_dataset(
    file: UploadFile = File(..., description="A CSV file to accept for analysis."),
    settings: Settings = Depends(get_settings),
) -> DatasetUploadResponse:
    """Validate and store a CSV upload in private temporary storage."""
    storage = DatasetStorage(
        settings.dataset_storage_dir,
        ttl_hours=settings.dataset_ttl_hours,
    )
    staged: Optional[StagedDataset] = None
    upload_closed = False
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
            await anyio.to_thread.run_sync(storage.cleanup_expired)
            await file.seek(0)
            staged_upload = await anyio.to_thread.run_sync(
                storage.stage_upload,
                file.file,
                settings.max_upload_size_bytes,
            )
            staged = staged_upload

            def validate_staged_upload(staged_upload: StagedDataset) -> None:
                with storage.open_staged(staged_upload) as staged_file:
                    validate_csv(staged_file, settings)

            await anyio.to_thread.run_sync(
                validate_staged_upload,
                staged_upload,
            )
            try:
                await file.close()
                upload_closed = True
            except (OSError, ValueError) as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="The uploaded file could not be closed safely.",
                ) from exc
            stored = await anyio.to_thread.run_sync(
                storage.finalize,
                staged_upload,
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
        except DatasetStorageError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="The uploaded dataset could not be stored safely.",
            ) from exc
        except (OSError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The uploaded file could not be read.",
            ) from exc

        return DatasetUploadResponse(
            dataset_id=stored.dataset_id,
            filename=filename,
            size_bytes=stored.size_bytes,
            content_type=file.content_type,
            status="uploaded",
        )
    finally:
        cleanup_failure: Optional[DatasetStorageError] = None
        if staged is not None:
            try:
                await anyio.to_thread.run_sync(storage.discard, staged)
            except DatasetStorageError as exc:
                cleanup_failure = exc
        if not upload_closed:
            try:
                await file.close()
            except (OSError, ValueError) as exc:
                if cleanup_failure is None:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="The uploaded file could not be closed safely.",
                    ) from exc
        if cleanup_failure is not None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Temporary upload data could not be cleaned up safely.",
            ) from cleanup_failure
