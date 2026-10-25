"""Dataset API response schemas."""

from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel


class DatasetUploadResponse(BaseModel):
    """Acknowledgement for a validated, temporarily stored upload."""

    dataset_id: UUID
    filename: str
    size_bytes: int
    content_type: Optional[str]
    status: Literal["uploaded"]


class DatasetPreviewResponse(BaseModel):
    """One page of records and metadata from a stored CSV dataset."""

    dataset_id: UUID
    filename: str
    row_count: int
    column_count: int
    columns: list[str]
    rows: list[dict[str, Optional[str]]]
    page: int
    page_size: int
    total_pages: int
