"""Dataset upload endpoints."""

from typing import Optional

import anyio

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status

from app.core.config import Settings, get_settings
from app.schemas.analytics import AnalyticsRequest, AnalyticsResponse
from app.schemas.dataset import (
    DatasetMetadataResponse,
    DatasetPreviewResponse,
    DatasetQualityResponse,
    DatasetStatisticsResponse,
    DatasetUploadResponse,
)
from app.services.analytics_service import (
    AnalyticsValidationError,
    analyze_dataset,
)
from app.services.csv_validator import CsvValidationError, validate_csv
from app.services.dataset_storage import (
    DatasetStorage,
    DatasetNotFoundError,
    DatasetStorageError,
    InvalidDatasetIdError,
    StagedDataset,
)
from app.services.dataset_preview import create_dataset_preview
from app.services.dataset_profiler import create_dataset_metadata
from app.services.statistical_profiler import create_dataset_statistics
from app.services.data_quality_analyzer import analyze_dataset_quality
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
                filename,
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


@router.get(
    "/{dataset_id}/preview",
    response_model=DatasetPreviewResponse,
    tags=["datasets"],
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "The dataset does not exist or has expired."
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": "The dataset identifier or pagination values are invalid."
        },
        status.HTTP_500_INTERNAL_SERVER_ERROR: {
            "description": "The stored dataset is unreadable or corrupted."
        },
    },
)
async def preview_dataset(
    dataset_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    settings: Settings = Depends(get_settings),
) -> DatasetPreviewResponse:
    """Return one validated page of rows from a stored CSV dataset."""
    storage = DatasetStorage(
        settings.dataset_storage_dir,
        ttl_hours=settings.dataset_ttl_hours,
    )
    try:
        return await anyio.to_thread.run_sync(
            create_dataset_preview,
            storage,
            settings,
            dataset_id,
            page,
            page_size,
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
    except (CsvValidationError, DatasetStorageError, OSError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The stored dataset could not be read safely.",
        ) from exc


@router.get(
    "/{dataset_id}/metadata",
    response_model=DatasetMetadataResponse,
    tags=["datasets"],
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "The dataset does not exist or has expired."
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": "The dataset identifier is invalid."
        },
        status.HTTP_500_INTERNAL_SERVER_ERROR: {
            "description": "The stored dataset is unreadable or corrupted."
        },
    },
)
async def metadata_dataset(
    dataset_id: str,
    settings: Settings = Depends(get_settings),
) -> DatasetMetadataResponse:
    """Return dataset shape and inferred logical column types."""
    storage = DatasetStorage(
        settings.dataset_storage_dir,
        ttl_hours=settings.dataset_ttl_hours,
    )
    try:
        return await anyio.to_thread.run_sync(
            create_dataset_metadata,
            storage,
            settings,
            dataset_id,
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
    except (CsvValidationError, DatasetStorageError, OSError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The stored dataset could not be read safely.",
        ) from exc


@router.get(
    "/{dataset_id}/statistics",
    response_model=DatasetStatisticsResponse,
    tags=["datasets"],
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "The dataset does not exist or has expired."
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": "The dataset identifier is invalid."
        },
        status.HTTP_500_INTERNAL_SERVER_ERROR: {
            "description": "The stored dataset is unreadable or corrupted."
        },
    },
)
async def statistics_dataset(
    dataset_id: str,
    settings: Settings = Depends(get_settings),
) -> DatasetStatisticsResponse:
    """Return descriptive statistics for a stored CSV dataset."""
    storage = DatasetStorage(
        settings.dataset_storage_dir,
        ttl_hours=settings.dataset_ttl_hours,
    )
    try:
        return await anyio.to_thread.run_sync(
            create_dataset_statistics,
            storage,
            settings,
            dataset_id,
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
    except (CsvValidationError, DatasetStorageError, OSError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The stored dataset could not be read safely.",
        ) from exc


@router.get(
    "/{dataset_id}/quality",
    response_model=DatasetQualityResponse,
    tags=["datasets"],
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "The dataset does not exist or has expired."
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": "The dataset identifier is invalid."
        },
        status.HTTP_500_INTERNAL_SERVER_ERROR: {
            "description": "The stored dataset is unreadable or corrupted."
        },
    },
)
async def quality_dataset(
    dataset_id: str,
    settings: Settings = Depends(get_settings),
) -> DatasetQualityResponse:
    """Return read-only quality findings for a stored CSV dataset."""
    storage = DatasetStorage(
        settings.dataset_storage_dir,
        ttl_hours=settings.dataset_ttl_hours,
    )
    try:
        return await anyio.to_thread.run_sync(
            analyze_dataset_quality,
            storage,
            settings,
            dataset_id,
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
    except (CsvValidationError, DatasetStorageError, OSError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The stored dataset could not be read safely.",
        ) from exc


@router.post(
    "/{dataset_id}/analytics",
    response_model=AnalyticsResponse,
    tags=["datasets"],
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "The dataset does not exist or has expired."
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": (
                "The dataset identifier, operation parameters, columns, or "
                "comparison values are invalid."
            )
        },
        status.HTTP_500_INTERNAL_SERVER_ERROR: {
            "description": "The stored dataset is unreadable or corrupted."
        },
    },
)
async def dataset_analytics(
    dataset_id: str,
    request: AnalyticsRequest,
    settings: Settings = Depends(get_settings),
) -> AnalyticsResponse:
    """Run a validated analytics operation against a stored CSV dataset."""
    storage = DatasetStorage(
        settings.dataset_storage_dir,
        ttl_hours=settings.dataset_ttl_hours,
    )
    try:
        return await anyio.to_thread.run_sync(
            analyze_dataset,
            storage,
            settings,
            dataset_id,
            request,
        )
    except AnalyticsValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
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
    except (CsvValidationError, DatasetStorageError, OSError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The stored dataset could not be read safely.",
        ) from exc
