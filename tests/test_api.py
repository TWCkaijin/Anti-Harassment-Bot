"""
測試：FastAPI Health Endpoint
"""

from fastapi.testclient import TestClient

from backend.app.main import app

client = TestClient(app)


def test_health_check():
    response = client.get("/api/v1/health/")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "timestamp" in data
    assert "version" in data


def test_root():
    response = client.get("/")
    assert response.status_code == 200
    assert "service" in response.json()
