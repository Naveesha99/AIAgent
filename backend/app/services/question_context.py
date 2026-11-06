"""Deterministic, bounded dataset context for question interpretation."""

from __future__ import annotations

import json
from typing import Any

from app.schemas.dataset import (
    DatasetQualityResponse,
    DatasetStatisticsResponse,
)

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


def build_dataset_context(
    statistics: DatasetStatisticsResponse,
    quality: DatasetQualityResponse | None = None,
) -> str:
    """Serialize schema-level statistics without row values or storage paths."""
    rows: list[dict[str, Any]] = []
    missing_warnings: list[dict[str, Any]] = []
    quality_warnings: list[dict[str, Any]] = []
    quality_summary: dict[str, int] | None = None

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
            missing_warnings.append(
                {
                    "category": "missing_values",
                    "column": column.name,
                    "count": column.null_count,
                    "percentage": column_context["missing_percentage"],
                }
            )

    if quality is not None:
        quality_summary = quality.summary.model_dump()
        for issue in quality.issues:
            if issue.category == "missing_values":
                continue
            warning: dict[str, Any] = {
                "category": issue.category,
                "count": issue.count,
                "description": issue.description,
            }
            if issue.column is not None:
                warning["column"] = issue.column
            if issue.percentage is not None:
                warning["percentage"] = issue.percentage
            quality_warnings.append(warning)

    quality_warnings.extend(missing_warnings)

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
    if quality_summary is not None:
        context["quality_summary"] = quality_summary
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
