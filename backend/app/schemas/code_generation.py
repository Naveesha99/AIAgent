"""Typed models for strictly validated untrusted code generation."""

from typing import Literal, Optional
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

from app.schemas.questions import (
    AssumptionText,
    ColumnName,
    QuestionInterpretation,
    QuestionRequest,
    WarningText,
)

CodeGenerationStatus = Literal["generated", "clarification_needed"]
CodeOperation = Literal[
    "filter",
    "group_and_aggregate",
    "aggregate",
    "sort",
    "compare",
    "summarize",
]
SUPPORTED_CODE_OPERATIONS = frozenset(
    {
        "filter",
        "group_and_aggregate",
        "aggregate",
        "sort",
        "compare",
        "summarize",
    }
)

_MAX_CODE_LENGTH = 20_000
_MAX_EXPLANATION_LENGTH = 2_000
_MAX_REFERENCED_COLUMNS = 50
_MAX_ASSUMPTIONS = 10
_MAX_WARNINGS = 20


def _strip_code_fence(code: str) -> str:
    text = code.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if len(lines) < 3 or lines[-1].strip() != "```":
        return text
    return "\n".join(lines[1:-1]).strip()


class GenerateCodeRequest(QuestionRequest):
    """A natural-language question requesting pandas analysis code."""


class GeneratedCode(BaseModel):
    """Strict, untrusted generated-code object returned by the model.

    This model validates data shape only. It is not a Python security boundary
    and its acceptance must never be treated as proof that code is safe.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        strict=True,
    )

    code: str = Field(
        min_length=1,
        max_length=_MAX_CODE_LENGTH,
        description="Non-empty Python analysis source code.",
    )
    explanation: str = Field(
        min_length=1,
        max_length=_MAX_EXPLANATION_LENGTH,
        description="Short explanation of the generated analysis.",
    )
    operation: CodeOperation
    referenced_columns: list[ColumnName] = Field(
        max_length=_MAX_REFERENCED_COLUMNS,
        description="Exact dataset column names used by the code.",
    )
    assumptions: list[AssumptionText] = Field(
        default_factory=list,
        max_length=_MAX_ASSUMPTIONS,
    )
    warnings: list[WarningText] = Field(
        default_factory=list,
        max_length=_MAX_WARNINGS,
    )

    @field_validator("code")
    @classmethod
    def validate_code_body(cls, value: str) -> str:
        """Accept one surrounding code fence, then reject empty code."""
        cleaned = _strip_code_fence(value)
        if not cleaned:
            raise ValueError("Generated code must not be empty.")
        if len(cleaned) > _MAX_CODE_LENGTH:
            raise ValueError(
                f"Generated code must be at most {_MAX_CODE_LENGTH} characters."
            )
        return cleaned


GeneratedCodeDraft = GeneratedCode
CodeGenerationResult = GeneratedCode


class CodeGenerationResponse(BaseModel):
    """A validated question interpretation and strictly validated code."""

    dataset_id: UUID
    question: str = Field(min_length=1, max_length=2_000)
    status: CodeGenerationStatus
    interpretation: QuestionInterpretation
    generated_code: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=_MAX_CODE_LENGTH,
    )
    explanation: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=_MAX_EXPLANATION_LENGTH,
    )
    operation: Optional[CodeOperation] = Field(
        default=None,
    )
    referenced_columns: list[ColumnName] = Field(
        default_factory=list,
        max_length=_MAX_REFERENCED_COLUMNS,
    )
    assumptions: list[AssumptionText] = Field(
        default_factory=list,
        max_length=_MAX_ASSUMPTIONS,
    )
    warnings: list[WarningText] = Field(
        default_factory=list,
        max_length=_MAX_WARNINGS,
    )
    clarification_question: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=500,
    )
