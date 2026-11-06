"""Generate and strictly validate untrusted pandas analysis code."""

from __future__ import annotations

from typing import Optional

from pydantic import ValidationError

from app.schemas.code_generation import (
    SUPPORTED_CODE_OPERATIONS,
    GeneratedCode,
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
    """A safely reportable failure to parse or validate generated code."""


def parse_code_generation_response(text: str) -> GeneratedCode:
    """Parse strict JSON code-generation data, accepting one JSON code fence."""
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
        return GeneratedCode.model_validate_json(payload)
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


def _cap_warnings(warnings: list[str]) -> list[str]:
    if len(warnings) <= _MAX_WARNINGS:
        return warnings
    return warnings[: _MAX_WARNINGS - 1] + [
        "Additional warnings were omitted."
    ]


def validate_generated_code(
    generated: GeneratedCode,
    interpretation: QuestionInterpretation,
    statistics: DatasetStatisticsResponse,
) -> GeneratedCode:
    """Validate generated-code metadata against the dataset and interpretation."""
    if interpretation.intent not in SUPPORTED_CODE_OPERATIONS:
        raise CodeGenerationError(
            "The model returned an unsupported code generation operation."
        )
    if generated.operation != interpretation.intent:
        raise CodeGenerationError(
            "The generated operation does not match the validated "
            "interpretation."
        )

    columns = {column.name for column in statistics.column_statistics}
    unknown_columns = [
        name for name in generated.referenced_columns if name not in columns
    ]
    if unknown_columns:
        raise CodeGenerationError(
            "The model referenced columns that are not present in this dataset."
        )

    warnings = list(generated.warnings)
    _add_warning(
        warnings,
        "Generated code is untrusted and has not been validated or "
        "executed.",
    )
    return generated.model_copy(
        update={
            "referenced_columns": _unique(generated.referenced_columns),
            "assumptions": _unique(generated.assumptions),
            "warnings": _cap_warnings(warnings),
        }
    )


async def generate_pandas_code(
    llm_client: LLMClient,
    question: str,
    interpretation: QuestionInterpretation,
    statistics: DatasetStatisticsResponse,
    quality: Optional[DatasetQualityResponse] = None,
) -> GeneratedCode:
    """Generate untrusted pandas code and validate its structured metadata."""
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
    generated = parse_code_generation_response(response.text)
    return validate_generated_code(generated, interpretation, statistics)
