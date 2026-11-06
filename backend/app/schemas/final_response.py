"""Strict, bounded schema for the final structured agent response."""

from __future__ import annotations

import json
from typing import Literal, Optional, Union

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    model_validator,
)

from app.schemas.common import DatasetId
from app.schemas.dataset import DatasetLogicalType
from app.schemas.questions import AssumptionText, WarningText

ResponseStatus = Literal["success", "clarification_needed", "error"]
ChartType = Literal["bar", "line", "pie", "scatter", "area", "histogram"]

_MAX_QUESTION_LENGTH = 2_000
_MAX_ANSWER_LENGTH = 20_000
_MAX_EXPLANATION_LENGTH = 4_000
_MAX_CLARIFICATION_LENGTH = 500
_MAX_TABLE_COUNT = 10
_MAX_TABLE_COLUMNS = 100
_MAX_TABLE_ROWS = 1_000
_MAX_TABLE_CELLS = 50_000
_MAX_TOTAL_TABLE_CELLS = 100_000
_MAX_CHART_COUNT = 10
_MAX_SERIES_PER_CHART = 20
_MAX_POINTS_PER_SERIES = 1_000
_MAX_POINTS_PER_CHART = 2_000
_MAX_TOTAL_CHART_POINTS = 10_000
_MAX_RESULT_ITEMS = 20
_MAX_WARNINGS = 20
_MAX_ASSUMPTIONS = 10
_MAX_RESPONSE_BYTES = 2_000_000


class TableColumnDefinition(BaseModel):
    """Optional typed column metadata for a returned table."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        populate_by_name=True,
    )

    name: str = Field(min_length=1, max_length=256)
    data_type: Optional[DatasetLogicalType] = Field(
        default=None,
        validation_alias=AliasChoices("data_type", "type"),
    )


TableColumn = Union[str, TableColumnDefinition]


class ResultTable(BaseModel):
    """A bounded table whose row keys must exactly match its columns."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        populate_by_name=True,
    )

    name: Optional[str] = Field(default=None, min_length=1, max_length=256)
    title: Optional[str] = Field(default=None, min_length=1, max_length=256)
    columns: list[TableColumn] = Field(
        min_length=1,
        max_length=_MAX_TABLE_COLUMNS,
    )
    rows: list[dict[str, JsonValue]] = Field(
        default_factory=list,
        max_length=_MAX_TABLE_ROWS,
        validation_alias=AliasChoices("rows", "data"),
    )
    truncated: bool = False

    def column_names(self) -> list[str]:
        """Return the ordered column names, accepting names or definitions."""
        return [
            column if isinstance(column, str) else column.name
            for column in self.columns
        ]

    @model_validator(mode="after")
    def validate_table_rows(self) -> "ResultTable":
        names = self.column_names()
        if len(set(names)) != len(names):
            raise ValueError("Table column names must be unique.")
        expected = set(names)
        for row in self.rows:
            if set(row) != expected:
                raise ValueError(
                    "Table row keys must exactly match the table columns."
                )
        if len(self.rows) * len(names) > _MAX_TABLE_CELLS:
            raise ValueError(
                f"A table may contain at most {_MAX_TABLE_CELLS} cells."
            )
        return self


class ChartSeries(BaseModel):
    """One bounded chart data series."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        populate_by_name=True,
    )

    name: str = Field(min_length=1, max_length=256)
    data: list[JsonValue] = Field(
        default_factory=list,
        max_length=_MAX_POINTS_PER_SERIES,
        validation_alias=AliasChoices("data", "values", "points"),
    )


class ChartSpec(BaseModel):
    """A structured, bounded chart specification without rendering logic."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        populate_by_name=True,
    )

    chart_type: ChartType = Field(
        validation_alias=AliasChoices("chart_type", "type"),
    )
    title: Optional[str] = Field(default=None, min_length=1, max_length=500)
    x: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=256,
        validation_alias=AliasChoices(
            "x",
            "x_axis",
            "x_field",
            "x_column",
        ),
    )
    y: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=256,
        validation_alias=AliasChoices(
            "y",
            "y_axis",
            "y_field",
            "y_column",
        ),
    )
    series: list[ChartSeries] = Field(
        default_factory=list,
        max_length=_MAX_SERIES_PER_CHART,
    )
    data: Optional[list[JsonValue]] = Field(
        default=None,
        max_length=_MAX_POINTS_PER_SERIES,
    )

    @model_validator(mode="after")
    def validate_chart_size(self) -> "ChartSpec":
        point_count = len(self.data or []) + sum(
            len(series.data) for series in self.series
        )
        if point_count > _MAX_POINTS_PER_CHART:
            raise ValueError(
                f"A chart may contain at most {_MAX_POINTS_PER_CHART} points."
            )
        return self


class FinalResponse(BaseModel):
    """The structured response returned by the agent to the frontend.

    The schema deliberately has no fields for filesystem paths, credentials,
    provider metadata, stack traces, or execution internals.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    dataset_id: DatasetId
    question: str = Field(
        min_length=1,
        max_length=_MAX_QUESTION_LENGTH,
    )
    status: ResponseStatus
    answer: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=_MAX_ANSWER_LENGTH,
    )
    explanation: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=_MAX_EXPLANATION_LENGTH,
    )
    tables: list[ResultTable] = Field(
        default_factory=list,
        max_length=_MAX_TABLE_COUNT,
    )
    charts: list[ChartSpec] = Field(
        default_factory=list,
        max_length=_MAX_CHART_COUNT,
    )
    warnings: list[WarningText] = Field(
        default_factory=list,
        max_length=_MAX_WARNINGS,
    )
    assumptions: list[AssumptionText] = Field(
        default_factory=list,
        max_length=_MAX_ASSUMPTIONS,
    )
    clarification_question: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=_MAX_CLARIFICATION_LENGTH,
    )

    @model_validator(mode="after")
    def validate_bounded_response(self) -> "FinalResponse":
        if len(self.tables) + len(self.charts) > _MAX_RESULT_ITEMS:
            raise ValueError(
                f"A response may contain at most {_MAX_RESULT_ITEMS} result "
                "items."
            )

        total_cells = sum(
            len(table.rows) * len(table.column_names())
            for table in self.tables
        )
        if total_cells > _MAX_TOTAL_TABLE_CELLS:
            raise ValueError(
                "The response contains too many table cells."
            )

        total_points = sum(
            len(chart.data or [])
            + sum(len(series.data) for series in chart.series)
            for chart in self.charts
        )
        if total_points > _MAX_TOTAL_CHART_POINTS:
            raise ValueError(
                "The response contains too many chart points."
            )

        payload = self.model_dump(mode="json")
        try:
            serialized = json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
        except (TypeError, ValueError):
            raise ValueError(
                "The response must contain only JSON-serializable values."
            ) from None
        if len(serialized.encode("utf-8")) > _MAX_RESPONSE_BYTES:
            raise ValueError("The response is too large.")
        return self


AgentResponse = FinalResponse
