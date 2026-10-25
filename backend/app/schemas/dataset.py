"""Dataset API response schemas."""

from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field

DatasetLogicalType = Literal[
    "integer",
    "float",
    "boolean",
    "date",
    "datetime",
    "categorical",
    "string",
]


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
    data_type: DatasetLogicalType
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


class CategoryFrequency(BaseModel):
    """One non-null category and its observed frequency."""

    value: str
    count: int


class NumericColumnStatistics(BaseModel):
    """Descriptive statistics for values inferred as numeric."""

    count: int
    mean: Optional[float]
    median: Optional[float]
    standard_deviation: Optional[float]
    variance: Optional[float]
    minimum: Optional[float]
    maximum: Optional[float]
    q1: Optional[float]
    q3: Optional[float]
    iqr: Optional[float]
    sum: Optional[float]


class DatasetColumnStatistics(BaseModel):
    """Counts and applicable descriptive statistics for one dataset column."""

    name: str
    data_type: DatasetLogicalType
    non_null_count: int
    null_count: int
    numeric: Optional[NumericColumnStatistics] = None
    distinct_count: Optional[int] = None
    mode: Optional[str] = None
    mode_frequency: Optional[int] = None
    value_frequencies: list[CategoryFrequency] = Field(default_factory=list)
    value_frequencies_truncated: bool = False
    earliest: Optional[str] = None
    latest: Optional[str] = None


class DatasetStatisticsResponse(BaseModel):
    """Descriptive statistics for a stored CSV dataset."""

    dataset_id: UUID
    filename: str
    row_count: int
    column_count: int
    column_statistics: list[DatasetColumnStatistics]
