from fastapi.testclient import TestClient

from app.graph.repository import IngestionReceipt
from app.main import app


class FakeKnowledgeRepository:
    def __init__(self) -> None:
        self.ingested: dict[str, object] | None = None

    def initialize(self) -> None:
        return None

    def ingest_source(self, **kwargs: object) -> IngestionReceipt:
        self.ingested = kwargs
        return IngestionReceipt("docs:handbook", "rev-123", 2, "active")

    def delete_source(self, source_id: str) -> bool:
        return source_id == "docs:handbook"

    def search_evidence(self, query: str, limit: int) -> list[dict[str, object]]:
        assert (query, limit) == ("release owner", 10)
        return [
            {
                "source_id": "docs:handbook",
                "kind": "document",
                "canonical_url": None,
                "chunk_id": "chunk-1",
                "revision_id": "rev-123",
                "citation_locator": "ownership, lines 4-6",
                "content": "The release owner is Jordan.",
            }
        ]

    @property
    def tool_names(self) -> frozenset[str]:
        return frozenset({"who_owns"})

    def run_tool(self, tool_name: str, arguments: dict[str, object]) -> list[dict[str, object]]:
        assert tool_name == "who_owns"
        assert arguments == {"entity_id": "project:launch", "limit": 10}
        return [{"entity_id": "project:launch", "fact_id": "fact:owner"}]


def test_source_ingestion_returns_active_revision() -> None:
    repository = FakeKnowledgeRepository()
    with TestClient(app) as client:
        app.state.graph_repository = repository
        response = client.post(
            "/api/v1/sources",
            json={
                "source_id": "docs:handbook",
                "kind": "document",
                "file_format": "md",
                "content": "# Ownership\nThe release owner is Jordan.",
                "canonical_url": "https://example.com/handbook",
            },
        )

    assert response.status_code == 201
    assert response.json() == {
        "source_id": "docs:handbook",
        "revision_id": "rev-123",
        "chunks_processed": 2,
        "status": "active",
    }
    assert repository.ingested is not None
    assert repository.ingested["canonical_url"] == "https://example.com/handbook"


def test_source_ingestion_rejects_unimplemented_format() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/sources",
            json={
                "source_id": "docs:pdf",
                "kind": "document",
                "file_format": "pdf",
                "content": "not parsed",
            },
        )

    assert response.status_code == 422


def test_evidence_search_returns_provenance_fields() -> None:
    repository = FakeKnowledgeRepository()
    with TestClient(app) as client:
        app.state.graph_repository = repository
        response = client.post(
            "/api/v1/evidence/search",
            json={"query": "release owner", "limit": 10},
        )

    assert response.status_code == 200
    match = response.json()["matches"][0]
    assert match["source_id"] == "docs:handbook"
    assert match["revision_id"] == "rev-123"
    assert match["citation_locator"] == "ownership, lines 4-6"


def test_answer_endpoint_returns_explicit_unavailable_state() -> None:
    class UnavailableRepository:
        def answer_question(self, question: str) -> dict[str, object]:
            raise OSError("connection refused")

    with TestClient(app) as client:
        app.state.graph_repository = UnavailableRepository()
        response = client.post("/api/v1/answers", json={"question": "Who owns Launch?"})

    assert response.status_code == 503
    assert response.json() == {
        "status": "temporarily_unavailable",
        "answer": "FalkorDB is temporarily unavailable.",
        "claims": [],
    }


def test_graph_tool_endpoint_dispatches_only_allowlisted_tools() -> None:
    repository = FakeKnowledgeRepository()
    with TestClient(app) as client:
        app.state.graph_repository = repository
        allowed = client.post(
            "/api/v1/tools/who_owns",
            json={"entity_id": "project:launch"},
        )
        rejected = client.post(
            "/api/v1/tools/raw_cypher",
            json={"query": "MATCH (n) DETACH DELETE n"},
        )

    assert allowed.status_code == 200
    assert allowed.json() == {
        "tool": "who_owns",
        "results": [{"entity_id": "project:launch", "fact_id": "fact:owner"}],
    }
    assert rejected.status_code == 404


def test_source_retirement_endpoint_returns_no_content() -> None:
    repository = FakeKnowledgeRepository()
    with TestClient(app) as client:
        app.state.graph_repository = repository
        response = client.delete("/api/v1/sources/docs%3Ahandbook")

    assert response.status_code == 204
