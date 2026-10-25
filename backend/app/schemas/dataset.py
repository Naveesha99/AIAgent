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


class DatasetColumnMetadata(BaseModel):
    """Inferred logical type and missing-value counts for one CSV column."""

    name: str
    data_type: Literal[
        "integer",
        "float",
        "boolean",
        "date",
        "datetime",
        "categorical",
        "string",
    ]
    non_null_count: int
    null_count: int
    is_numeric: bool
    is_date: bool
    is_categorical: bool


class DatasetMetadataResponse(BaseModel):
    """Dataset shape and ordered, inferred column metadata."""

    dataset_id: UUID
    filename: str
    row_count: int
    column_count: int
    columns: list[DatasetColumnMetadata]
    numeric_columns: list[str]
    date_columns: list[str]
    categorical_columns: list[str]
