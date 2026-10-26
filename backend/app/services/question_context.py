"""Deterministic, bounded dataset context for question interpretation."""

from __future__ import annotations

import json
from typing import Any

from app.schemas.dataset import DatasetStatisticsResponse

MAX_CONTEXT_CHARS = 48_000
_MAX_QUALITY_WARNINGS = 10


class QuestionContextError(ValueError):
    """The bounded model context cannot represent the dataset safely."""


def _column_classification(data_type: str) -> str:
    if data_type in {"integer", "float"}:
        return "numeric"
    if data_type in {"date", "datetime"}:
        return "date"
    if data_type == "categorical":
        return "categorical"
    if data_type == "boolean":
        return "boolean"
    return "text"


def _missing_percentage(missing_count: int, row_count: int) -> float:
    if row_count <= 0:
        return 0.0
    return round(missing_count / row_count * 100, 2)


def build_dataset_context(statistics: DatasetStatisticsResponse) -> str:
    """Serialize schema-level statistics without row values or storage paths."""
    rows: list[dict[str, Any]] = []
    quality_warnings: list[dict[str, Any]] = []

    for column in statistics.column_statistics:
        classification = _column_classification(column.data_type)
        column_context: dict[str, Any] = {
            "name": column.name,
            "data_type": column.data_type,
            "classification": classification,
            "non_null_count": column.non_null_count,
            "missing_count": column.null_count,
            "missing_percentage": _missing_percentage(
                column.null_count,
                statistics.row_count,
            ),
        }
        if column.earliest is not None or column.latest is not None:
            column_context["date_range"] = {
                "earliest": column.earliest,
                "latest": column.latest,
            }
        rows.append(column_context)

        if column.null_count:
            quality_warnings.append(
                {
                    "category": "missing_values",
                    "column": column.name,
                    "count": column.null_count,
                    "percentage": column_context["missing_percentage"],
                }
            )

    context = {
        "row_count": statistics.row_count,
        "column_count": statistics.column_count,
        "columns": rows,
        "numeric_columns": [
            column.name
            for column in statistics.column_statistics
            if _column_classification(column.data_type) == "numeric"
        ],
        "date_columns": [
            column.name
            for column in statistics.column_statistics
            if _column_classification(column.data_type) == "date"
        ],
        "categorical_columns": [
            column.name
            for column in statistics.column_statistics
            if _column_classification(column.data_type) == "categorical"
        ],
        "quality_warnings": quality_warnings[:_MAX_QUALITY_WARNINGS],
        "quality_warnings_truncated": (
            len(quality_warnings) > _MAX_QUALITY_WARNINGS
        ),
    }
    serialized = json.dumps(
        context,
        ensure_ascii=True,
        separators=(",", ":"),
    )
    if len(serialized) > MAX_CONTEXT_CHARS:
        raise QuestionContextError(
            "The dataset metadata is too large to interpret safely."
        )
    return serialized
