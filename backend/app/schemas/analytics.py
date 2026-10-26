"""Typed request and response models for dataset analytics."""

from typing import Annotated, Literal, Optional, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.dataset import DatasetLogicalType

AnalyticsOperation = Literal["filter", "group", "aggregate", "sort", "summarize"]
FilterOperator = Literal[
    "equals",
    "not_equals",
    "greater_than",
    "greater_than_or_equal",
    "less_than",
    "less_than_or_equal",
    "contains",
    "starts_with",
    "ends_with",
    "is_null",
    "is_not_null",
]
FilterCombine = Literal["and", "or"]
AggregationFunction = Literal[
    "count",
    "sum",
    "mean",
    "median",
    "min",
    "max",
    "std",
    "nunique",
]
SortDirection = Literal["asc", "desc"]

ScalarValue = Union[bool, int, float, str]
AnalyticsValue = Optional[Union[bool, int, float, str]]

_MAX_FILTER_CONDITIONS = 50
_MAX_SORT_COLUMNS = 10
_MAX_GROUP_COLUMNS = 10
_MAX_AGGREGATIONS = 20


class FilterCondition(BaseModel):
    """One validated filter condition."""

    model_config = ConfigDict(extra="forbid")

    column: str = Field(min_length=1, max_length=256)
    operator: FilterOperator
    value: Optional[ScalarValue] = None


class FilterGroup(BaseModel):
    """Conditions combined with a single explicit AND or OR rule."""

    model_config = ConfigDict(extra="forbid")

    combine: FilterCombine = "and"
    conditions: list[FilterCondition] = Field(
        min_length=1,
        max_length=_MAX_FILTER_CONDITIONS,
    )


class SortSpec(BaseModel):
    """One sort column and direction."""

    model_config = ConfigDict(extra="forbid")

    column: str = Field(min_length=1, max_length=256)
    direction: SortDirection = "asc"


class AggregationSpec(BaseModel):
    """One aggregation function applied to an optional column."""

    model_config = ConfigDict(extra="forbid")

    column: Optional[str] = Field(default=None, max_length=256)
    function: AggregationFunction
    alias: Optional[str] = Field(default=None, max_length=256)


class _AnalyticsRequestBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filters: Optional[FilterGroup] = None


class FilterRequest(_AnalyticsRequestBase):
    """Return rows matching a filter expression."""

    operation: Literal["filter"]
    filters: FilterGroup
    columns: Optional[list[str]] = Field(default=None, min_length=1)
    sort_by: list[SortSpec] = Field(
        default_factory=list,
        max_length=_MAX_SORT_COLUMNS,
    )
    limit: Optional[int] = Field(default=None, ge=1)


class SortRequest(_AnalyticsRequestBase):
    """Return rows ordered by one or more columns."""

    operation: Literal["sort"]
    sort_by: list[SortSpec] = Field(
        min_length=1,
        max_length=_MAX_SORT_COLUMNS,
    )
    columns: Optional[list[str]] = Field(default=None, min_length=1)
    limit: Optional[int] = Field(default=None, ge=1)


class GroupRequest(_AnalyticsRequestBase):
    """Group rows by one or more columns and optionally aggregate."""

    operation: Literal["group"]
    group_by: list[str] = Field(
        min_length=1,
        max_length=_MAX_GROUP_COLUMNS,
    )
    aggregations: list[AggregationSpec] = Field(
        default_factory=list,
        max_length=_MAX_AGGREGATIONS,
    )
    limit: Optional[int] = Field(default=None, ge=1)


class AggregateRequest(_AnalyticsRequestBase):
    """Aggregate rows, optionally grouped by one or more columns."""

    operation: Literal["aggregate"]
    group_by: list[str] = Field(
        default_factory=list,
        max_length=_MAX_GROUP_COLUMNS,
    )
    aggregations: list[AggregationSpec] = Field(
        min_length=1,
        max_length=_MAX_AGGREGATIONS,
    )
    limit: Optional[int] = Field(default=None, ge=1)


class SummarizeRequest(_AnalyticsRequestBase):
    """Summarize a dataset, optionally with grouped aggregations."""

    operation: Literal["summarize"]
    group_by: list[str] = Field(
        default_factory=list,
        max_length=_MAX_GROUP_COLUMNS,
    )
    aggregations: list[AggregationSpec] = Field(
        default_factory=list,
        max_length=_MAX_AGGREGATIONS,
    )
    top_values_limit: Optional[int] = Field(default=None, ge=1)


AnalyticsRequest = Annotated[
    Union[
        FilterRequest,
        SortRequest,
        GroupRequest,
        AggregateRequest,
        SummarizeRequest,
    ],
    Field(discriminator="operation"),
]


class AnalyticsColumnSummary(BaseModel):
    """One column name and its inferred logical type."""

    name: str
    data_type: DatasetLogicalType


class AnalyticsNumericSummary(BaseModel):
    """Bounded numeric summary for one numeric column."""

    column: str
    count: int
    mean: Optional[float] = None
    median: Optional[float] = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    standard_deviation: Optional[float] = None


class AnalyticsFrequency(BaseModel):
    """One observed category value and its count."""

    value: str
    count: int


class AnalyticsCategoricalSummary(BaseModel):
    """Bounded categorical summary for one categorical column."""

    column: str
    distinct_count: int
    top_values: list[AnalyticsFrequency] = Field(default_factory=list)
    truncated: bool = False


class AnalyticsMissingSummary(BaseModel):
    """Missing-value counts for one column."""

    column: str
    missing_count: int
    missing_percentage: float


class AnalyticsSummary(BaseModel):
    """Dataset-wide and optional grouped summary information."""

    row_count: int
    column_count: int
    columns: list[AnalyticsColumnSummary] = Field(default_factory=list)
    numeric: list[AnalyticsNumericSummary] = Field(default_factory=list)
    categorical: list[AnalyticsCategoricalSummary] = Field(default_factory=list)
    missing: list[AnalyticsMissingSummary] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    groups: list[dict[str, AnalyticsValue]] = Field(default_factory=list)
    groups_truncated: bool = False
    total_groups: Optional[int] = None


class AnalyticsResponse(BaseModel):
    """Structured, JSON-serializable analytics result."""

    dataset_id: UUID
    operation: AnalyticsOperation
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, AnalyticsValue]] = Field(default_factory=list)
    row_count: int
    result_count: int
    truncated: bool = False
    matched_count: Optional[int] = None
    total_groups: Optional[int] = None
    summary: Optional[AnalyticsSummary] = None
