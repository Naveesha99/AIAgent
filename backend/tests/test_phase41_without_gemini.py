"""Regression tests for existing features with no Gemini configuration."""

from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

import app.services.llm.gemini_client as gemini_module
from app.core.config import Settings, get_settings
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolate_storage_without_llm_key(
    tmp_path: Path,
) -> Iterator[None]:
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None,
        dataset_storage_dir=tmp_path / "datasets",
        gemini_api_key=None,
    )
    yield
    app.dependency_overrides.pop(get_settings, None)


def test_health_and_dataset_features_do_not_require_gemini(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_initialized(*args: object, **kwargs: object) -> None:
        raise AssertionError("Gemini must not be initialized for local endpoints.")

    monkeypatch.setattr(gemini_module.genai, "Client", fail_if_initialized)

    health = client.get("/api/v1/health")
    assert health.status_code == 200

    upload = client.post(
        "/api/v1/datasets/upload",
        files={
            "file": (
                "records.csv",
                b"name,amount,region\nAlpha,10,North\nBeta,20,South\n",
                "text/csv",
            )
        },
    )
    assert upload.status_code == 201
    dataset_id = upload.json()["dataset_id"]

    assert (
        client.get(f"/api/v1/datasets/{dataset_id}/preview").status_code
        == 200
    )
    assert (
        client.get(f"/api/v1/datasets/{dataset_id}/metadata").status_code
        == 200
    )
    assert (
        client.get(f"/api/v1/datasets/{dataset_id}/statistics").status_code
        == 200
    )
    assert (
        client.get(f"/api/v1/datasets/{dataset_id}/quality").status_code
        == 200
    )

    analytics = client.post(
        f"/api/v1/datasets/{dataset_id}/analytics",
        json={"operation": "summarize"},
    )
    assert analytics.status_code == 200
    assert analytics.json()["summary"]["row_count"] == 2
