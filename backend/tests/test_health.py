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


def test_cors_allows_local_frontend_preflight() -> None:
    response = client.options(
        "/api/v1/health",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "accept",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "GET" in response.headers["access-control-allow-methods"]
    assert "POST" in response.headers["access-control-allow-methods"]
    assert (
        "accept" in response.headers["access-control-allow-headers"].lower()
    )
    assert "access-control-allow-credentials" not in response.headers


def test_cors_allows_loopback_frontend_origin() -> None:
    response = client.get(
        "/api/v1/health",
        headers={"Origin": "http://127.0.0.1:3000"},
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:3000"


def test_cors_does_not_allow_unapproved_origin() -> None:
    response = client.options(
        "/api/v1/health",
        headers={
            "Origin": "http://example.com",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers
