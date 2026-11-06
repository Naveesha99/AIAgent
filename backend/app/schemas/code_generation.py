"""Typed models for untrusted pandas code generation."""

from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.questions import (
    AssumptionText,
    ColumnName,
    QuestionInterpretation,
    QuestionRequest,
    WarningText,
)

CodeGenerationStatus = Literal["generated", "clarification_needed"]

_MAX_CODE_LENGTH = 20_000
_MAX_EXPLANATION_LENGTH = 2_000
_MAX_OPERATION_LENGTH = 120
_MAX_REFERENCED_COLUMNS = 50
_MAX_ASSUMPTIONS = 10
_MAX_WARNINGS = 20


class GenerateCodeRequest(QuestionRequest):
    """A natural-language question requesting pandas analysis code."""


class GeneratedCodeDraft(BaseModel):
    """Basic, untrusted code-generation object returned by the model."""

    model_config = ConfigDict(str_strip_whitespace=True)

    code: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=_MAX_CODE_LENGTH,
    )
    explanation: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=_MAX_EXPLANATION_LENGTH,
    )
    operation: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=_MAX_OPERATION_LENGTH,
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


class CodeGenerationResult(BaseModel):
    """Normalized, unexecuted pandas code and its supporting metadata."""

    code: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=_MAX_CODE_LENGTH,
    )
    explanation: str = Field(
        min_length=1,
        max_length=_MAX_EXPLANATION_LENGTH,
    )
    operation: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=_MAX_OPERATION_LENGTH,
    )
    referenced_columns: list[ColumnName] = Field(default_factory=list)
    assumptions: list[AssumptionText] = Field(default_factory=list)
    warnings: list[WarningText] = Field(default_factory=list)


class CodeGenerationResponse(BaseModel):
    """A validated question interpretation and untrusted generated code."""

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
    operation: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=_MAX_OPERATION_LENGTH,
    )
    referenced_columns: list[ColumnName] = Field(default_factory=list)
    assumptions: list[AssumptionText] = Field(default_factory=list)
    warnings: list[WarningText] = Field(default_factory=list)
    clarification_question: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=500,
    )
