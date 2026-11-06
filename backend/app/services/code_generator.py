"""Generate untrusted pandas analysis code from validated interpretations."""

from __future__ import annotations

from typing import Optional

from pydantic import ValidationError

from app.schemas.code_generation import (
    CodeGenerationResult,
    GeneratedCodeDraft,
)
from app.schemas.dataset import (
    DatasetQualityResponse,
    DatasetStatisticsResponse,
)
from app.schemas.questions import QuestionInterpretation
from app.services.code_generation_prompt import (
    CODE_GENERATION_SYSTEM_INSTRUCTION,
    build_code_generation_prompt,
)
from app.services.llm.base import LLMClient, LLMMessage
from app.services.question_context import build_dataset_context

_MAX_WARNINGS = 20


class CodeGenerationError(ValueError):
    """A safely reportable failure to parse generated code."""


def parse_code_generation_response(text: str) -> GeneratedCodeDraft:
    """Parse a JSON code-generation object, accepting one JSON code fence."""
    if not isinstance(text, str) or not text.strip():
        raise CodeGenerationError(
            "The model returned an empty code generation response."
        )

    payload = text.strip()
    if payload.startswith("```"):
        lines = payload.splitlines()
        if (
            len(lines) < 3
            or lines[0].strip().lower() not in {"```", "```json"}
            or lines[-1].strip() != "```"
        ):
            raise CodeGenerationError(
                "The model returned an invalid code generation response."
            )
        payload = "\n".join(lines[1:-1]).strip()

    try:
        return GeneratedCodeDraft.model_validate_json(payload)
    except (ValidationError, TypeError, ValueError):
        raise CodeGenerationError(
            "The model returned an invalid code generation response."
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


def _strip_code_fence(code: str) -> str:
    text = code.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if len(lines) < 3 or lines[-1].strip() != "```":
        return text
    return "\n".join(lines[1:-1]).strip()


def _cap_warnings(warnings: list[str]) -> list[str]:
    if len(warnings) <= _MAX_WARNINGS:
        return warnings
    return warnings[: _MAX_WARNINGS - 1] + [
        "Additional warnings were omitted."
    ]


def _normalize_code_result(
    draft: GeneratedCodeDraft,
    interpretation: QuestionInterpretation,
    statistics: DatasetStatisticsResponse,
) -> CodeGenerationResult:
    columns = {column.name for column in statistics.column_statistics}
    warnings = list(draft.warnings)
    assumptions = _unique(draft.assumptions)
    referenced_columns = _unique(draft.referenced_columns)
    code = _strip_code_fence(draft.code) if draft.code is not None else None

    if not code:
        if draft.explanation is None and not warnings:
            raise CodeGenerationError(
                "The model did not return generated code or an explanation."
            )
        explanation = draft.explanation or (
            "The model did not generate code for this question."
        )
        _add_warning(
            warnings,
            "No analysis code was generated; the question may require "
            "clarification or a different dataset.",
        )
        return CodeGenerationResult(
            code=None,
            explanation=explanation,
            operation=draft.operation,
            referenced_columns=referenced_columns,
            assumptions=assumptions,
            warnings=_cap_warnings(warnings),
        )

    warnings = _unique(
        [
            "Generated code is untrusted and has not been validated or "
            "executed.",
            *draft.warnings,
        ]
    )
    unknown_columns = [
        name for name in referenced_columns if name not in columns
    ]
    for name in unknown_columns[:5]:
        _add_warning(
            warnings,
            f"Generated code reports unknown column '{name[:120]}'.",
        )
    if len(unknown_columns) > 5:
        _add_warning(
            warnings,
            "Generated code reports additional columns that are not in the "
            "dataset.",
        )

    if not referenced_columns:
        referenced_columns = _unique(interpretation.referenced_columns)
        _add_warning(
            warnings,
            "The model did not report referenced columns; using the columns "
            "from the validated interpretation.",
        )

    explanation = draft.explanation
    if explanation is None:
        explanation = "The model did not provide an explanation for the code."
        _add_warning(
            warnings,
            "The model did not provide an explanation for the generated code.",
        )

    return CodeGenerationResult(
        code=code,
        explanation=explanation,
        operation=draft.operation or interpretation.intent,
        referenced_columns=referenced_columns,
        assumptions=assumptions,
        warnings=_cap_warnings(warnings),
    )


async def generate_pandas_code(
    llm_client: LLMClient,
    question: str,
    interpretation: QuestionInterpretation,
    statistics: DatasetStatisticsResponse,
    quality: Optional[DatasetQualityResponse] = None,
) -> CodeGenerationResult:
    """Generate untrusted pandas code from a validated interpretation."""
    context_json = build_dataset_context(statistics, quality)
    prompt = build_code_generation_prompt(
        context_json,
        interpretation,
        question,
    )
    response = await llm_client.generate(
        [LLMMessage("user", prompt)],
        system_instruction=CODE_GENERATION_SYSTEM_INSTRUCTION,
    )
    draft = parse_code_generation_response(response.text)
    return _normalize_code_result(draft, interpretation, statistics)
