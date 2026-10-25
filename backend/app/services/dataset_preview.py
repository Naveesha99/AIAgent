"""Bounded CSV preview generation for stored datasets."""

from __future__ import annotations

import math
from uuid import UUID

from app.core.config import Settings
from app.schemas.dataset import DatasetPreviewResponse
from app.services.csv_validator import validate_csv
from app.services.dataset_storage import DatasetStorage


def create_dataset_preview(
    storage: DatasetStorage,
    settings: Settings,
    dataset_id: str,
    page: int,
    page_size: int,
) -> DatasetPreviewResponse:
    """Validate the stored CSV and return only the requested page of rows."""
    with storage.open_dataset(dataset_id) as dataset_file:
        filename = storage.get_filename(dataset_id)
        result = validate_csv(
            dataset_file,
            settings,
            preview_page=page,
            preview_page_size=page_size,
        )

    rows = [
        dict(zip(result.column_names, row))
        for row in result.preview_rows
    ]
    return DatasetPreviewResponse(
        dataset_id=UUID(dataset_id),
        filename=filename,
        row_count=result.data_row_count,
        column_count=result.column_count,
        columns=list(result.column_names),
        rows=rows,
        page=page,
        page_size=page_size,
        total_pages=math.ceil(result.data_row_count / page_size),
    )
