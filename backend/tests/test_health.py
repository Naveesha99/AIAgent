"""Tests for the API health endpoint."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_expected_response() -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "ai-data-analyst-api",
    }


def test_health_is_not_available_without_version_prefix() -> None:
    response = client.get("/health")

    assert response.status_code == 404


def test_unknown_route_returns_not_found() -> None:
    response = client.get("/api/v1/not-a-route")

    assert response.status_code == 404
