from fastapi.testclient import TestClient
from matescope.main import app


def test_health_contract() -> None:
    response = TestClient(app).get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_api_errors_are_not_hidden_by_spa() -> None:
    response = TestClient(app).get("/api/v1/does-not-exist")
    assert response.status_code == 404
