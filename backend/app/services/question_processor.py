"""Natural-language dataset question interpretation."""

from __future__ import annotations

import re
from typing import Optional

import anyio

from pydantic import ValidationError

from app.core.config import Settings
from app.schemas.dataset import (
    DatasetColumnStatistics,
    DatasetStatisticsResponse,
)
from app.schemas.questions import (
    ProposedMetric,
    QuestionInterpretation,
    QuestionInterpretationDraft,
    QuestionMetric,
    QuestionProcessingResponse,
)
from app.services.dataset_storage import DatasetStorage
from app.services.llm.base import LLMClient, LLMMessage
from app.services.question_context import build_dataset_context
from app.services.question_prompt import (
    QUESTION_INTERPRETATION_SYSTEM_INSTRUCTION,
    build_question_prompt,
)
from app.services.statistical_profiler import create_dataset_statistics

_STRING_TYPES = {"string", "categorical"}
_NUMERIC_TYPES = {"integer", "float"}
_DATE_TYPES = {"date", "datetime"}
_NUMERIC_AGGREGATIONS = {"sum", "mean", "median", "std"}
_STRING_OPERATORS = {"contains", "starts_with", "ends_with"}
_ORDERING_OPERATORS = {
    "greater_than",
    "greater_than_or_equal",
    "less_than",
    "less_than_or_equal",
}
_NULL_OPERATORS = {"is_null", "is_not_null"}
_COUNT_METRIC_EXCLUSIONS = {
    "duplicate",
    "empty",
    "invalid",
    "missing",
    "null",
    "unique",
}
_MAX_WARNINGS = 20


class QuestionInterpretationError(ValueError):
    """A safely reportable failure to validate the model interpretation."""


def parse_interpretation_response(text: str) -> QuestionInterpretationDraft:
    """Parse model text as a strict JSON draft, accepting a JSON code fence."""
    if not isinstance(text, str) or not text.strip():
        raise QuestionInterpretationError(
            "The model returned an empty question interpretation."
        )

    payload = text.strip()
    if payload.startswith("```"):
        lines = payload.splitlines()
        if (
            len(lines) < 3
            or lines[0].strip().lower() not in {"```", "```json"}
            or lines[-1].strip() != "```"
        ):
            raise QuestionInterpretationError(
                "The model returned an invalid question interpretation."
            )
        payload = "\n".join(lines[1:-1]).strip()

    try:
        return QuestionInterpretationDraft.model_validate_json(payload)
    except (ValidationError, TypeError, ValueError):
        raise QuestionInterpretationError(
            "The model returned an invalid question interpretation."
        ) from None


def _unique(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            cleaned.append(value)
    return cleaned


def _add_warning(warnings: list[str], message: str) -> None:
    if message not in warnings:
        warnings.append(message)


def _count_metric_has_exclusions(metric: ProposedMetric) -> bool:
    if metric.aggregation != "count":
        return False
    words = set(re.findall(r"[a-z0-9]+", metric.name.casefold()))
    return bool(words & _COUNT_METRIC_EXCLUSIONS)


def _derived_metric_limitation(metric: ProposedMetric) -> str:
    return (
        f"Metric '{metric.name}' requires a derived calculation that "
        "is not supported by the current analytics operations."
    )


def _validate_metric(
    metric: ProposedMetric,
    columns: dict[str, DatasetColumnStatistics],
    warnings: list[str],
    *,
    allow_derived_metrics: bool = False,
) -> tuple[QuestionMetric, bool]:
    """Return a normalized metric and whether the proposal is blocked."""
    blocked = False
    column_name = metric.column
    supported = True
    limitation: Optional[str] = None
    column: Optional[DatasetColumnStatistics] = None

    if column_name is None:
        direct_column = columns.get(metric.name)
        if direct_column is not None:
            column_name = direct_column.name
            column = direct_column
        elif _count_metric_has_exclusions(metric) or (
            metric.aggregation != "count"
        ):
            supported = False
            blocked = not allow_derived_metrics
            limitation = _derived_metric_limitation(metric)
            _add_warning(warnings, limitation)
    else:
        column = columns.get(column_name)
        if column is None:
            supported = False
            blocked = True
            limitation = (
                f"Metric '{metric.name}' references unknown column "
                f"'{column_name}'."
            )
            _add_warning(
                warnings,
                f"Referenced column '{column_name}' is not present in this "
                "dataset.",
            )
        elif _count_metric_has_exclusions(metric):
            supported = False
            blocked = not allow_derived_metrics
            limitation = _derived_metric_limitation(metric)
            _add_warning(warnings, limitation)

    if (
        column is not None
        and supported
        and metric.aggregation in _NUMERIC_AGGREGATIONS
        and column.data_type not in _NUMERIC_TYPES
    ):
        supported = False
        blocked = True
        limitation = (
            f"Metric '{metric.name}' uses aggregation "
            f"'{metric.aggregation}' on non-numeric column "
            f"'{column_name}'."
        )
        _add_warning(warnings, limitation)

    return (
        QuestionMetric(
            name=metric.name,
            column=column_name,
            aggregation=metric.aggregation,
            supported=supported,
            limitation=limitation,
        ),
        blocked,
    )


def _validate_filter_columns(
    draft: QuestionInterpretationDraft,
    columns: dict[str, DatasetColumnStatistics],
    warnings: list[str],
) -> bool:
    if draft.filters is None:
        return False

    blocked = False
    for condition in draft.filters.conditions:
        column = columns.get(condition.column)
        if column is None:
            blocked = True
            _add_warning(
                warnings,
                f"Referenced column '{condition.column}' is not present in "
                "this dataset.",
            )
            continue

        if condition.operator in _NULL_OPERATORS:
            if condition.value is not None:
                blocked = True
                _add_warning(
                    warnings,
                    f"Filter operator '{condition.operator}' on column "
                    f"'{condition.column}' does not accept a value.",
                )
            continue

        if condition.value is None:
            blocked = True
            _add_warning(
                warnings,
                f"Filter operator '{condition.operator}' on column "
                f"'{condition.column}' requires a value.",
            )
            continue

        if (
            condition.operator in _STRING_OPERATORS
            and column.data_type not in _STRING_TYPES
        ):
            blocked = True
            _add_warning(
                warnings,
                f"Filter operator '{condition.operator}' is not supported for "
                f"{column.data_type} column '{condition.column}'.",
            )
        elif (
            condition.operator in _ORDERING_OPERATORS
            and column.data_type not in _NUMERIC_TYPES | _DATE_TYPES
        ):
            blocked = True
            _add_warning(
                warnings,
                f"Filter operator '{condition.operator}' is not supported for "
                f"{column.data_type} column '{condition.column}'.",
            )
    return blocked


def _default_clarification(
    intent: str,
    warnings: list[str],
) -> str:
    if intent == "unsupported":
        return (
            "This question does not appear to be answerable from this dataset. "
            "Please ask about its columns or values."
        )
    if any("requires a derived calculation" in warning for warning in warnings):
        return (
            "The requested metric needs a derived calculation that is not "
            "supported yet. Please choose a supported metric or clarify the "
            "calculation."
        )
    if any("is not present in this dataset" in warning for warning in warnings):
        return (
            "Some referenced columns are not present in this dataset. Please "
            "choose from the available columns."
        )
    return (
        "Could you clarify which columns, metric, or conditions you mean for "
        "this dataset?"
    )


def normalize_interpretation(
    draft: QuestionInterpretationDraft,
    question: str,
    statistics: DatasetStatisticsResponse,
    *,
    allow_derived_metrics: bool = False,
) -> tuple[QuestionInterpretation, list[str]]:
    """Validate a model proposal against the actual dataset schema."""
    columns = {
        column.name: column for column in statistics.column_statistics
    }
    warnings: list[str] = []
    blocked = False

    referenced_columns = _unique(draft.referenced_columns)
    for name in referenced_columns:
        if name not in columns:
            blocked = True
            _add_warning(
                warnings,
                f"Referenced column '{name}' is not present in this dataset.",
            )

    group_by = _unique(draft.group_by)
    for name in group_by:
        if name not in columns:
            blocked = True
            _add_warning(
                warnings,
                f"Referenced column '{name}' is not present in this dataset.",
            )

    metrics: list[QuestionMetric] = []
    for proposed in draft.requested_metrics:
        metric, metric_blocked = _validate_metric(
            proposed,
            columns,
            warnings,
            allow_derived_metrics=allow_derived_metrics,
        )
        metrics.append(metric)
        blocked = blocked or metric_blocked
    metric_names = {metric.name for metric in metrics}

    if draft.sort is not None and (
        draft.sort.column not in columns
        and draft.sort.column not in metric_names
    ):
        blocked = True
        _add_warning(
            warnings,
            f"Sort column '{draft.sort.column}' is not present in this dataset "
            "or among the requested metrics.",
        )

    blocked = _validate_filter_columns(draft, columns, warnings) or blocked

    if draft.intent == "filter" and draft.filters is None:
        blocked = True
        _add_warning(warnings, "The filter intent requires filter conditions.")
    elif draft.intent == "group_and_aggregate":
        if not group_by:
            blocked = True
            _add_warning(
                warnings,
                "The group-and-aggregate intent requires at least one group "
                "column.",
            )
        if not metrics:
            blocked = True
            _add_warning(
                warnings,
                "The group-and-aggregate intent requires at least one metric.",
            )
    elif draft.intent == "aggregate" and not metrics:
        blocked = True
        _add_warning(
            warnings,
            "The aggregate intent requires at least one metric.",
        )
    elif draft.intent == "sort" and draft.sort is None:
        blocked = True
        _add_warning(warnings, "The sort intent requires a sort field.")
    elif draft.intent == "compare" and not (
        group_by or metrics or draft.filters
    ):
        blocked = True
        _add_warning(
            warnings,
            "The compare intent requires grouping, metrics, or filters.",
        )
    elif draft.intent in {"clarification_needed", "unsupported"}:
        blocked = True
        if draft.intent == "unsupported":
            _add_warning(
                warnings,
                "The question does not appear to be answerable from this "
                "dataset.",
            )

    clarification_question = draft.clarification_question
    if clarification_question is not None:
        blocked = True
    if blocked and clarification_question is None:
        clarification_question = _default_clarification(
            draft.intent,
            warnings,
        )

    assumptions = _unique(draft.assumptions)
    for assumption in assumptions:
        _add_warning(warnings, f"Assumption: {assumption}")

    if len(warnings) > _MAX_WARNINGS:
        warnings = warnings[: _MAX_WARNINGS - 1] + [
            "Additional warnings were omitted."
        ]

    interpretation = QuestionInterpretation(
        question=question,
        intent=draft.intent,
        referenced_columns=referenced_columns,
        requested_metrics=metrics,
        group_by=group_by,
        filters=draft.filters,
        sort=draft.sort,
        limit=draft.limit,
        clarification_question=clarification_question,
        assumptions=assumptions,
    )
    return interpretation, warnings


async def interpret_question(
    storage: DatasetStorage,
    settings: Settings,
    llm_client: LLMClient,
    dataset_id: str,
    question: str,
    *,
    allow_derived_metrics: bool = False,
) -> tuple[
    QuestionInterpretation,
    DatasetStatisticsResponse,
    list[str],
]:
    """Interpret a question and return the statistics used for the decision."""
    statistics = await anyio.to_thread.run_sync(
        create_dataset_statistics,
        storage,
        settings,
        dataset_id,
    )
    context_json = build_dataset_context(statistics)
    prompt = build_question_prompt(context_json, question)
    response = await llm_client.generate(
        [LLMMessage("user", prompt)],
        system_instruction=QUESTION_INTERPRETATION_SYSTEM_INSTRUCTION,
    )
    draft = parse_interpretation_response(response.text)
    interpretation, warnings = normalize_interpretation(
        draft,
        question,
        statistics,
        allow_derived_metrics=allow_derived_metrics,
    )
    return interpretation, statistics, warnings


async def process_question(
    storage: DatasetStorage,
    settings: Settings,
    llm_client: LLMClient,
    dataset_id: str,
    question: str,
) -> QuestionProcessingResponse:
    """Retrieve bounded metadata, call the model, and validate its proposal."""
    interpretation, statistics, warnings = await interpret_question(
        storage,
        settings,
        llm_client,
        dataset_id,
        question,
    )
    status = (
        "clarification_needed"
        if interpretation.clarification_question is not None
        else "interpreted"
    )
    return QuestionProcessingResponse(
        dataset_id=statistics.dataset_id,
        question=question,
        status=status,
        interpretation=interpretation,
        clarification_question=interpretation.clarification_question,
        warnings=warnings,
    )
