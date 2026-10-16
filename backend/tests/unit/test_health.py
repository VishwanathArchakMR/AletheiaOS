from fastapi.testclient import TestClient

from app.main import app


class ReadyRepository:
    def is_ready(self) -> bool:
        return True


class UnavailableRepository:
    def is_ready(self) -> bool:
        raise OSError("database not reachable")


def test_live_does_not_need_graph() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "api"}


def test_ready_reports_available_graph() -> None:
    with TestClient(app) as client:
        app.state.graph_repository = ReadyRepository()
        response = client.get("/api/v1/health/ready")
    assert response.status_code == 200


def test_ready_hides_connection_details() -> None:
    with TestClient(app) as client:
        app.state.graph_repository = UnavailableRepository()
        response = client.get("/api/v1/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "service": "falkordb"}

