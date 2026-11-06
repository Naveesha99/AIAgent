"""Strict models for allowlisted analytics and future execution tool requests."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from app.schemas.analytics import AnalyticsOperation, AnalyticsRequest
from app.schemas.common import DatasetId

SUPPORTED_TOOL_NAMES = frozenset({"analytics"})
ToolName = Literal["analytics"]
_ANALYTICS_REQUEST_ADAPTER = TypeAdapter(AnalyticsRequest)

_FORBIDDEN_ARGUMENT_KEYS = frozenset(
    {
        "code",
        "command",
        "compile",
        "eval",
        "exec",
        "file",
        "filename",
        "filepath",
        "import",
        "module",
        "network",
        "os",
        "path",
        "python",
        "query",
        "script",
        "shell",
        "sql",
        "subprocess",
        "system",
        "url",
    }
)


def _contains_forbidden_key(value: Any) -> bool:
    """Return true when an argument tree contains an execution-oriented key."""
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and key.strip().lower() in (
                _FORBIDDEN_ARGUMENT_KEYS
            ):
                return True
            if _contains_forbidden_key(item):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_forbidden_key(item) for item in value)
    return False


class ToolRequest(BaseModel):
    """A request for one allowlisted tool with schema-validated arguments.

    This phase validates tool requests only. It does not execute them, load
    datasets, or permit arbitrary tool names or execution parameters.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    dataset_id: DatasetId
    tool_name: ToolName
    operation: AnalyticsOperation
    arguments: dict[str, Any]

    def validated_arguments(self) -> AnalyticsRequest:
        """Return the analytics request represented by these arguments."""
        return _ANALYTICS_REQUEST_ADAPTER.validate_python(
            {"operation": self.operation, **self.arguments}
        )

    @model_validator(mode="after")
    def validate_tool_arguments(self) -> "ToolRequest":
        if "operation" in self.arguments:
            raise ValueError(
                "Tool arguments must not include the top-level operation."
            )
        if _contains_forbidden_key(self.arguments):
            raise ValueError(
                "Tool arguments contain unsupported execution parameters."
            )
        try:
            self.validated_arguments()
        except ValidationError:
            raise ValueError(
                f"Invalid arguments for the '{self.tool_name}' tool."
            ) from None
        return self
