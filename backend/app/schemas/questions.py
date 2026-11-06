"""Typed models for natural-language dataset question interpretation."""

from typing import Annotated, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.analytics import (
    AggregationFunction,
    FilterGroup,
    SortDirection,
)
from app.schemas.llm_usage import LLMUsage

QuestionIntent = Literal[
    "filter",
    "group_and_aggregate",
    "aggregate",
    "sort",
    "compare",
    "summarize",
    "clarification_needed",
    "unsupported",
]
QuestionStatus = Literal["interpreted", "clarification_needed"]

_MAX_QUESTION_LENGTH = 2000
_MAX_REFERENCED_COLUMNS = 50
_MAX_GROUP_COLUMNS = 10
_MAX_METRICS = 10
_MAX_ASSUMPTIONS = 10
_MAX_LIMIT = 1000
_MAX_FILTER_VALUE_LENGTH = 1000

ColumnName = Annotated[str, Field(min_length=1, max_length=256)]
MetricName = Annotated[str, Field(min_length=1, max_length=128)]
AssumptionText = Annotated[str, Field(min_length=1, max_length=500)]
WarningText = Annotated[str, Field(min_length=1, max_length=600)]


class QuestionRequest(BaseModel):
    """A natural-language question about one stored dataset."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    question: str = Field(
        min_length=1,
        max_length=_MAX_QUESTION_LENGTH,
        description="Question to interpret using the dataset schema.",
    )


class _InterpretationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProposedMetric(_InterpretationModel):
    """A metric proposal from the model before schema validation."""

    name: MetricName
    column: Optional[ColumnName] = None
    aggregation: Optional[AggregationFunction] = None


class QuestionSort(_InterpretationModel):
    """A requested sort field, which may name a column or a requested metric."""

    column: str = Field(min_length=1, max_length=256)
    direction: SortDirection = "asc"


class QuestionInterpretationDraft(_InterpretationModel):
    """The untrusted structured output requested from the language model."""

    intent: QuestionIntent
    referenced_columns: list[ColumnName] = Field(
        default_factory=list,
        max_length=_MAX_REFERENCED_COLUMNS,
    )
    requested_metrics: list[ProposedMetric] = Field(
        default_factory=list,
        max_length=_MAX_METRICS,
    )
    group_by: list[ColumnName] = Field(
        default_factory=list,
        max_length=_MAX_GROUP_COLUMNS,
    )
    filters: Optional[FilterGroup] = None
    sort: Optional[QuestionSort] = None
    limit: Optional[int] = Field(default=None, ge=1, le=_MAX_LIMIT)
    clarification_question: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=500,
    )
    assumptions: list[AssumptionText] = Field(
        default_factory=list,
        max_length=_MAX_ASSUMPTIONS,
    )

    @field_validator("filters")
    @classmethod
    def validate_filter_value_lengths(
        cls,
        filters: Optional[FilterGroup],
    ) -> Optional[FilterGroup]:
        if filters is None:
            return None
        for condition in filters.conditions:
            if (
                isinstance(condition.value, str)
                and len(condition.value) > _MAX_FILTER_VALUE_LENGTH
            ):
                raise ValueError(
                    "Filter values must be at most "
                    f"{_MAX_FILTER_VALUE_LENGTH} characters."
                )
        return filters


class QuestionMetric(BaseModel):
    """A requested metric after schema-aware support validation."""

    name: MetricName
    column: Optional[ColumnName] = None
    aggregation: Optional[AggregationFunction] = None
    supported: bool = True
    limitation: Optional[WarningText] = None


class QuestionInterpretation(BaseModel):
    """Validated interpretation suitable for a later planning phase."""

    question: str = Field(min_length=1, max_length=_MAX_QUESTION_LENGTH)
    intent: QuestionIntent
    referenced_columns: list[ColumnName] = Field(default_factory=list)
    requested_metrics: list[QuestionMetric] = Field(default_factory=list)
    group_by: list[ColumnName] = Field(default_factory=list)
    filters: Optional[FilterGroup] = None
    sort: Optional[QuestionSort] = None
    limit: Optional[int] = Field(default=None, ge=1, le=_MAX_LIMIT)
    clarification_question: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=500,
    )
    assumptions: list[AssumptionText] = Field(default_factory=list)


class QuestionProcessingResponse(BaseModel):
    """The interpreted question and any limitations or clarification request."""

    dataset_id: UUID
    question: str = Field(min_length=1, max_length=_MAX_QUESTION_LENGTH)
    status: QuestionStatus
    interpretation: QuestionInterpretation
    clarification_question: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=500,
    )
    warnings: list[WarningText] = Field(default_factory=list)
    usage: Optional[LLMUsage] = None
