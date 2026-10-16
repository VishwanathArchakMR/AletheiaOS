import os
import uuid

import pytest

from app.core.config import Settings
from app.demo_data import load_demo_data
from app.graph.client import GraphClient
from app.graph.repository import GraphRepository

pytestmark = pytest.mark.skipif(
    os.environ.get("ALETHEIA_RUN_FALKORDB_INTEGRATION") != "1",
    reason="Set ALETHEIA_RUN_FALKORDB_INTEGRATION=1 to use a live isolated FalkorDB graph.",
)


def test_company_brain_against_isolated_falkordb_graph() -> None:
    graph_name = f"aletheia_test_{uuid.uuid4().hex}"
    settings = Settings(falkordb_graph_name=graph_name)
    client = GraphClient(settings)
    repository = GraphRepository(client)
    try:
        repository.initialize()
        receipts = load_demo_data(repository)
        assert len(receipts) == 7

        owner = repository.answer_question("Who owns Project Launch?")
        assert owner["status"] == "supported"
        assert owner["claims"]
        assert all(
            claim["source_id"] and claim["chunk_id"] and claim["revision_id"]
            for claim in owner["claims"]
        )

        expertise = repository.answer_question("Who knows FalkorDB?")
        assert expertise["status"] == "supported"
        assert any(
            claim.get("expertise_basis") == "demonstrated_work" for claim in expertise["claims"]
        )

        rationale = repository.answer_question("Why did the company choose Graph architecture?")
        assert rationale["status"] == "supported"
        assert "low-latency graph traversal" in rationale["answer"]

        superseding = repository.answer_question(
            "Which decision superseded Blue-Green Rollout Decision?"
        )
        assert superseding["status"] == "supported"
        assert any(
            claim["fact_id"] == "fact:canary-supersedes-blue-green"
            for claim in superseding["claims"]
        )

        impact = repository.run_tool("impact", {"entity_id": "project:auth-service"})
        assert any(
            [node["id"] for node in row["path_nodes"]]
            == ["project:billing", "project:launch", "project:auth-service"]
            for row in impact
        )

        conflicts = repository.answer_question("Who owns Billing?")
        assert conflicts["status"] == "conflicting"
        ambiguous = repository.answer_question("Who owns Orion?")
        assert ambiguous["status"] == "ambiguous"

        repository.ingest_source(
            source_id="docs:company-handbook",
            kind="document",
            canonical_url="https://example.test/handbook",
            file_format="md",
            content="# Updated handbook\nMaya Chen remains on the Platform team.",
        )
        assert repository.answer_question("Who owns Project Launch?")["status"] == "supported"

        repository.ingest_source(
            source_id="docs:service-architecture",
            kind="document",
            canonical_url="https://example.test/architecture/services",
            file_format="md",
            content="# Updated architecture\nNo ownership record is present.",
        )
        repository.ingest_source(
            source_id="tickets:launch-owner-confirmation",
            kind="ticket",
            canonical_url="https://example.test/tickets/LAUNCH-18",
            file_format="md",
            content="# Resolved ticket\nThe ownership assertion was removed.",
        )
        stale = repository.answer_question("Who owns Project Launch?")
        assert stale["status"] == "stale"

        search = repository.search_evidence("Maya Chen Platform")
        assert search
        assert all(row["source_id"] and row["chunk_id"] and row["revision_id"] for row in search)

        client.close()
        client = GraphClient(settings)
        repository = GraphRepository(client)
        repository.initialize()
        assert repository.search_evidence("ownership assertion")
    finally:
        if client._client is not None:
            if graph_name in client._client.list_graphs():
                client._client.execute_command("GRAPH.DELETE", graph_name)
        client.close()
