"""Tests for Phase 4.4 strict structured-output validation."""

import csv
import datetime as dt
import io
import json
from pathlib import Path
from typing import Any, Iterator, Optional
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import app.api.routes.questions as questions_route
from app.core.config import Settings, get_settings
from app.main import app
from app.schemas.code_generation import GeneratedCode
from app.schemas.dataset import (
    DatasetColumnStatistics,
    DatasetStatisticsResponse,
)
from app.schemas.final_response import FinalResponse
from app.schemas.questions import QuestionInterpretation
from app.schemas.tools import ToolRequest
from app.services.code_generator import (
    CodeGenerationError,
    parse_code_generation_response,
    validate_generated_code,
)
from app.services.llm import LLMResponse

client = TestClient(app)

HEADERS = ["region", "amount"]
ROWS = [
    ["North", "10"],
    ["South", "20"],
]


class FakeLLMClient:
    provider = "gemini"
    model = "fake-gemini-model"

    def __init__(self) -> None:
        self.responses: list[Any] = []
        self.calls: list[dict[str, Any]] = []

    async def generate(
        self,
        messages: Any,
        *,
        system_instruction: Optional[str] = None,
    ) -> LLMResponse:
        self.calls.append(
            {
                "messages": list(messages),
                "system_instruction": system_instruction,
            }
        )
        if not self.responses:
            raise AssertionError("Unexpected language model call.")
        outcome = self.responses.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return LLMResponse(
            text=str(outcome),
            provider=self.provider,
            model=self.model,
        )


def make_csv() -> bytes:
    contents = io.StringIO(newline="")
    writer = csv.writer(contents, lineterminator="\n")
    writer.writerow(HEADERS)
    writer.writerows(ROWS)
    return contents.getvalue().encode("utf-8")


@pytest.fixture
def structured_settings(tmp_path: Path) -> Iterator[Settings]:
    settings = Settings(
        _env_file=None,
        dataset_storage_dir=tmp_path / "structured-datasets",
        dataset_ttl_hours=1,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    yield settings
    app.dependency_overrides.pop(get_settings, None)


@pytest.fixture
def stored_dataset(structured_settings: Settings) -> str:
    response = client.post(
        "/api/v1/datasets/upload",
        files={"file": ("sales.csv", make_csv(), "text/csv")},
    )
    assert response.status_code == 201, response.text
    return response.json()["dataset_id"]


@pytest.fixture
def fake_llm(monkeypatch: pytest.MonkeyPatch) -> FakeLLMClient:
    fake = FakeLLMClient()
    monkeypatch.setattr(
        questions_route,
        "create_llm_client",
        lambda settings: fake,
    )
    return fake


def interpretation_json(**overrides: Any) -> str:
    payload: dict[str, Any] = {
        "intent": "summarize",
        "referenced_columns": [],
        "requested_metrics": [],
        "group_by": [],
        "filters": None,
        "sort": None,
        "limit": None,
        "clarification_question": None,
        "assumptions": [],
    }
    payload.update(overrides)
    return json.dumps(payload)


def code_json(**overrides: Any) -> str:
    payload: dict[str, Any] = {
        "code": "result = df.shape",
        "explanation": "Returns the dataset shape.",
        "operation": "summarize",
        "referenced_columns": [],
        "assumptions": [],
        "warnings": [],
    }
    payload.update(overrides)
    return json.dumps(payload)


def generate_code(dataset_id: str) -> Any:
    return client.post(
        f"/api/v1/datasets/{dataset_id}/generate-code",
        json={"question": "Summarize this dataset."},
    )


def statistics_for(*columns: str) -> DatasetStatisticsResponse:
    return DatasetStatisticsResponse(
        dataset_id=uuid4(),
        filename="sales.csv",
        row_count=2,
        column_count=len(columns),
        column_statistics=[
            DatasetColumnStatistics(
                name=name,
                data_type="string",
                non_null_count=2,
                null_count=0,
            )
            for name in columns
        ],
    )


def summarize_interpretation() -> QuestionInterpretation:
    return QuestionInterpretation(
        question="Summarize this dataset.",
        intent="summarize",
    )


def valid_generated_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "code": "result = df.shape",
        "explanation": "Returns the dataset shape.",
        "operation": "summarize",
        "referenced_columns": [],
        "assumptions": [],
        "warnings": [],
    }
    payload.update(overrides)
    return payload


# Generated code schema


def test_valid_generated_code_is_accepted() -> None:
    generated = GeneratedCode.model_validate(valid_generated_payload())

    assert generated.code == "result = df.shape"
    assert generated.operation == "summarize"
    assert generated.referenced_columns == []


def test_generated_code_accepts_a_single_code_fence() -> None:
    generated = GeneratedCode.model_validate(
        valid_generated_payload(
            code="```python\nresult = df.shape\n```",
        )
    )

    assert generated.code == "result = df.shape"


@pytest.mark.parametrize(
    "field",
    ["code", "explanation", "operation", "referenced_columns"],
)
def test_generated_code_rejects_missing_required_fields(field: str) -> None:
    payload = valid_generated_payload()
    payload.pop(field)

    with pytest.raises(ValidationError):
        GeneratedCode.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("code", ["result = df.shape"]),
        ("explanation", 42),
        ("operation", ["summarize"]),
        ("referenced_columns", "region"),
        ("warnings", "warning"),
    ],
)
def test_generated_code_rejects_wrong_field_types(
    field: str,
    value: Any,
) -> None:
    payload = valid_generated_payload()
    payload[field] = value

    with pytest.raises(ValidationError):
        GeneratedCode.model_validate(payload)


@pytest.mark.parametrize(
    "code",
    ["", "   ", "x" * 20_001],
)
def test_generated_code_rejects_empty_or_oversized_code(code: str) -> None:
    with pytest.raises(ValidationError):
        GeneratedCode.model_validate(valid_generated_payload(code=code))


@pytest.mark.parametrize(
    "operation",
    ["execute", "shell", "clarification_needed", "unsupported", ""],
)
def test_generated_code_rejects_unsupported_operations(
    operation: str,
) -> None:
    with pytest.raises(ValidationError):
        GeneratedCode.model_validate(
            valid_generated_payload(operation=operation)
        )


def test_generated_code_rejects_unexpected_fields() -> None:
    payload = valid_generated_payload()
    payload["sql"] = "SELECT * FROM sales"

    with pytest.raises(ValidationError):
        GeneratedCode.model_validate(payload)


def test_generated_code_rejects_unknown_dataset_columns() -> None:
    generated = GeneratedCode.model_validate(
        valid_generated_payload(referenced_columns=["region", "revenue"])
    )

    with pytest.raises(CodeGenerationError):
        validate_generated_code(
            generated,
            summarize_interpretation(),
            statistics_for("region", "amount"),
        )


def test_valid_generated_code_is_validated_against_dataset_columns() -> None:
    generated = GeneratedCode.model_validate(
        valid_generated_payload(referenced_columns=["region", "amount"])
    )

    validated = validate_generated_code(
        generated,
        summarize_interpretation(),
        statistics_for("region", "amount"),
    )

    assert validated.referenced_columns == ["region", "amount"]
    assert any(
        "untrusted" in warning for warning in validated.warnings
    )


def test_generated_operation_must_match_interpretation() -> None:
    generated = GeneratedCode.model_validate(
        valid_generated_payload(operation="aggregate")
    )

    with pytest.raises(CodeGenerationError):
        validate_generated_code(
            generated,
            summarize_interpretation(),
            statistics_for("region", "amount"),
        )


@pytest.mark.parametrize(
    "text",
    [
        "not-json",
        "```json\n{}\n```",
        json.dumps({"code": "result = df.shape"}),
    ],
)
def test_parse_generated_code_rejects_malformed_json(text: str) -> None:
    with pytest.raises(CodeGenerationError):
        parse_code_generation_response(text)


# Tool request schema


def test_valid_analytics_tool_request_is_accepted() -> None:
    request = ToolRequest.model_validate(
        {
            "dataset_id": str(uuid4()),
            "tool_name": "analytics",
            "operation": "filter",
            "arguments": {
                "filters": {
                    "combine": "and",
                    "conditions": [
                        {
                            "column": "region",
                            "operator": "equals",
                            "value": "North",
                        }
                    ],
                },
                "limit": 10,
            },
        }
    )

    assert request.tool_name == "analytics"
    assert request.validated_arguments().operation == "filter"


def test_valid_summarize_tool_request_accepts_empty_arguments() -> None:
    request = ToolRequest.model_validate(
        {
            "dataset_id": str(uuid4()),
            "tool_name": "analytics",
            "operation": "summarize",
            "arguments": {},
        }
    )

    assert request.operation == "summarize"


def test_tool_request_rejects_unknown_tool_name() -> None:
    with pytest.raises(ValidationError):
        ToolRequest.model_validate(
            {
                "dataset_id": str(uuid4()),
                "tool_name": "shell",
                "operation": "summarize",
                "arguments": {},
            }
        )


def test_tool_request_rejects_unsupported_operation() -> None:
    with pytest.raises(ValidationError):
        ToolRequest.model_validate(
            {
                "dataset_id": str(uuid4()),
                "tool_name": "analytics",
                "operation": "execute",
                "arguments": {},
            }
        )


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"unknown": 1},
        {"limit": 10},
    ],
)
def test_tool_request_rejects_missing_or_unexpected_arguments(
    arguments: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        ToolRequest.model_validate(
            {
                "dataset_id": str(uuid4()),
                "tool_name": "analytics",
                "operation": "filter",
                "arguments": arguments,
            }
        )


@pytest.mark.parametrize(
    "dataset_id",
    [
        "not-a-uuid",
        "A9C162D4-15B5-4B02-A062-0D1A9AD44247",
        "{a9c162d4-15b5-4b02-a062-0d1a9ad44247}",
    ],
)
def test_tool_request_rejects_invalid_dataset_ids(dataset_id: str) -> None:
    with pytest.raises(ValidationError):
        ToolRequest.model_validate(
            {
                "dataset_id": dataset_id,
                "tool_name": "analytics",
                "operation": "summarize",
                "arguments": {},
            }
        )


@pytest.mark.parametrize(
    "arguments",
    [
        {"code": "print(1)"},
        {"command": "rm -rf /"},
        {"path": "/etc/passwd"},
        {"sql": "SELECT * FROM sales"},
        {"nested": {"exec": "print(1)"}},
    ],
)
def test_tool_request_rejects_arbitrary_execution_parameters(
    arguments: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        ToolRequest.model_validate(
            {
                "dataset_id": str(uuid4()),
                "tool_name": "analytics",
                "operation": "summarize",
                "arguments": arguments,
            }
        )


def test_tool_request_rejects_extra_top_level_fields() -> None:
    with pytest.raises(ValidationError):
        ToolRequest.model_validate(
            {
                "dataset_id": str(uuid4()),
                "tool_name": "analytics",
                "operation": "summarize",
                "arguments": {},
                "code": "print(1)",
            }
        )


# Final response schema


def valid_final_response_payload() -> dict[str, Any]:
    return {
        "dataset_id": str(uuid4()),
        "question": "Which region has the most sales?",
        "status": "success",
        "answer": "North has the highest total.",
        "explanation": "The table groups sales by region.",
        "tables": [
            {
                "name": "sales_by_region",
                "columns": ["region", "total"],
                "rows": [
                    {"region": "North", "total": 10.0},
                    {"region": "South", "total": 20.0},
                ],
            }
        ],
        "charts": [
            {
                "type": "bar",
                "title": "Sales by region",
                "x": "region",
                "y": "total",
                "data": [
                    {"label": "North", "value": 10.0},
                    {"label": "South", "value": 20.0},
                ],
            }
        ],
        "warnings": [],
        "assumptions": ["Total sales are already aggregated."],
    }


def test_valid_success_response_is_accepted_and_serializable() -> None:
    response = FinalResponse.model_validate(valid_final_response_payload())

    assert response.status == "success"
    assert response.tables[0].column_names() == ["region", "total"]
    assert response.model_dump(mode="json")["tables"][0]["rows"][0] == {
        "region": "North",
        "total": 10.0,
    }
    json.dumps(response.model_dump(mode="json"), allow_nan=False)


def test_clarification_response_is_accepted() -> None:
    payload = {
        "dataset_id": str(uuid4()),
        "question": "Show the best ones.",
        "status": "clarification_needed",
        "answer": None,
        "explanation": None,
        "tables": [],
        "charts": [],
        "warnings": ["The question is ambiguous."],
        "assumptions": [],
        "clarification_question": "Which metric and group do you mean?",
    }

    response = FinalResponse.model_validate(payload)

    assert response.status == "clarification_needed"
    assert response.answer is None
    assert response.clarification_question is not None


def test_error_response_is_accepted() -> None:
    payload = {
        "dataset_id": str(uuid4()),
        "question": "Summarize this dataset.",
        "status": "error",
        "answer": None,
        "explanation": None,
        "tables": [],
        "charts": [],
        "warnings": ["The analysis could not be completed."],
        "assumptions": [],
    }

    response = FinalResponse.model_validate(payload)

    assert response.status == "error"
    assert response.tables == []
    assert response.charts == []


def test_final_response_rejects_invalid_status() -> None:
    payload = valid_final_response_payload()
    payload["status"] = "completed"

    with pytest.raises(ValidationError):
        FinalResponse.model_validate(payload)


@pytest.mark.parametrize(
    "table",
    [
        {"columns": ["region", "total"], "rows": [{"region": "North"}]},
        {
            "columns": ["region", "total"],
            "rows": [{"region": "North", "total": 10, "extra": 1}],
        },
        {"columns": ["region", "total"], "rows": [{"region": "North", "TOTAL": 10}]},
        {"columns": ["region", "region"], "rows": [{"region": "North"}]},
    ],
)
def test_final_response_rejects_invalid_table_structure(
    table: dict[str, Any],
) -> None:
    payload = valid_final_response_payload()
    payload["tables"] = [table]

    with pytest.raises(ValidationError):
        FinalResponse.model_validate(payload)


@pytest.mark.parametrize(
    "chart",
    [
        {"type": "radar", "title": "Unsupported"},
        {"type": "bar", "series": "not-a-list"},
        {
            "type": "bar",
            "data": [{"label": "North", "value": {1, 2}}],
        },
    ],
)
def test_final_response_rejects_invalid_chart_specification(
    chart: dict[str, Any],
) -> None:
    payload = valid_final_response_payload()
    payload["charts"] = [chart]

    with pytest.raises(ValidationError):
        FinalResponse.model_validate(payload)


def test_final_response_rejects_oversized_data() -> None:
    payload = valid_final_response_payload()
    payload["answer"] = "x" * 20_001

    with pytest.raises(ValidationError):
        FinalResponse.model_validate(payload)


def test_final_response_rejects_excessively_large_tables() -> None:
    payload = valid_final_response_payload()
    payload["tables"] = [
        {
            "columns": ["value"],
            "rows": [{"value": index} for index in range(1_001)],
        }
    ]

    with pytest.raises(ValidationError):
        FinalResponse.model_validate(payload)


@pytest.mark.parametrize(
    "value",
    [
        {1, 2},
        dt.datetime(2025, 1, 1),
        object(),
    ],
)
def test_final_response_rejects_non_json_serializable_values(
    value: Any,
) -> None:
    payload = valid_final_response_payload()
    payload["tables"] = [
        {"columns": ["value"], "rows": [{"value": value}]}
    ]

    with pytest.raises(ValidationError):
        FinalResponse.model_validate(payload)


def test_final_response_rejects_unexpected_fields() -> None:
    payload = valid_final_response_payload()
    payload["stack_trace"] = "internal trace"

    with pytest.raises(ValidationError):
        FinalResponse.model_validate(payload)


# Integration with the Phase 4.3 code-generation flow


def test_valid_gemini_output_passes_validation(
    stored_dataset: str,
    fake_llm: FakeLLMClient,
) -> None:
    fake_llm.responses.extend(
        [interpretation_json(), code_json()]
    )

    response = generate_code(stored_dataset)

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "generated"
    assert result["generated_code"] == "result = df.shape"
    assert any(
        "untrusted" in warning for warning in result["warnings"]
    )


@pytest.mark.parametrize(
    "model_text",
    [
        "not-json",
        json.dumps({"code": "result = df.shape"}),
        code_json(code=42),
        code_json(operation="execute"),
        code_json(referenced_columns=["unknown column"]),
        code_json(sql="SELECT * FROM sales"),
    ],
)
def test_invalid_gemini_output_is_rejected(
    stored_dataset: str,
    fake_llm: FakeLLMClient,
    model_text: str,
) -> None:
    fake_llm.responses.extend(
        [interpretation_json(), model_text]
    )

    response = generate_code(stored_dataset)

    assert response.status_code == 502
    assert response.json()["detail"] == (
        "The model returned an invalid code generation response."
    )
    assert model_text not in response.text


def test_existing_dataset_endpoints_remain_unaffected(
    stored_dataset: str,
    fake_llm: FakeLLMClient,
) -> None:
    fake_llm.responses.extend(
        [interpretation_json(), code_json()]
    )
    assert generate_code(stored_dataset).status_code == 200

    assert client.get("/api/v1/health").status_code == 200
    assert (
        client.get(f"/api/v1/datasets/{stored_dataset}/preview").status_code
        == 200
    )
    assert (
        client.get(f"/api/v1/datasets/{stored_dataset}/metadata").status_code
        == 200
    )
    assert (
        client.get(f"/api/v1/datasets/{stored_dataset}/statistics").status_code
        == 200
    )
    assert (
        client.get(f"/api/v1/datasets/{stored_dataset}/quality").status_code
        == 200
    )
    analytics = client.post(
        f"/api/v1/datasets/{stored_dataset}/analytics",
        json={"operation": "summarize"},
    )
    assert analytics.status_code == 200
    assert analytics.json()["summary"]["row_count"] == 2
