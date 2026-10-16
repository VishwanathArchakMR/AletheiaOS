import pytest

from app.graph.queries import GraphQueries


class FakeQueryResult:
    header = ["entity_id", "fact_id"]
    result_set = [["project:launch", "fact:ownership"]]


class EmptyQueryResult:
    header: list[str] = []
    result_set: list[list[object]] = []


class FakeGraphClient:
    def __init__(self) -> None:
        self.call: tuple[str, dict[str, object], int] | None = None

    def query(
        self,
        cypher: str,
        params: dict[str, object] | None = None,
        timeout_ms: int = 5000,
    ) -> FakeQueryResult:
        self.call = (cypher, params or {}, timeout_ms)
        if "UNION ALL" in cypher:
            return EmptyQueryResult()
        return FakeQueryResult()


def test_ownership_tool_uses_parameterized_current_fact_query() -> None:
    client = FakeGraphClient()
    tools = GraphQueries(client)

    rows = tools.run("who_owns", {"entity_id": "project:launch", "limit": 4})

    assert rows == [{"entity_id": "project:launch", "fact_id": "fact:ownership"}]
    assert client.call is not None
    cypher, parameters, timeout_ms = client.call
    assert "$entity_id" in cypher
    assert "source.active_revision_id = chunk.revision_id" in cypher
    assert parameters["entity_id"] == "project:launch"
    assert parameters["limit"] == 4
    assert timeout_ms == 5000


def test_query_values_are_never_interpolated_and_limits_are_bounded() -> None:
    client = FakeGraphClient()
    tools = GraphQueries(client)
    injected = "x' MATCH (n) DETACH DELETE n //"

    tools.run("search_evidence", {"query": injected, "limit": 20})

    assert client.call is not None
    cypher, parameters, _ = client.call
    assert injected not in cypher
    assert parameters["search_query"] == "x MATCH n DETACH DELETE n"
    assert "db.idx.fulltext.queryNodes('Chunk', $search_query)" in cypher
    with pytest.raises(ValueError, match="limit"):
        tools.run("find_stale_claims", {"limit": 21})


def test_unknown_tools_are_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        GraphQueries(FakeGraphClient()).run("raw_cypher", {"query": "RETURN 1"})


def test_related_query_is_a_bounded_evidence_checked_multi_hop_path() -> None:
    client = FakeGraphClient()
    tools = GraphQueries(client)

    tools.run("how_related", {"from_id": "person:maya", "to_id": "project:launch"})

    assert client.call is not None
    cypher, parameters, _ = client.call
    assert "*1..5" in cypher
    assert "all(projection IN projections" in cypher
    assert "verified_edges = size(projections)" in cypher
    assert "SUPPORTED_BY {status: 'accepted'}" in cypher
    assert "source.active_revision_id = chunk.revision_id" in cypher
    assert "REFERENCES" not in parameters["relationship_types"]
    assert parameters["from_id"] == "person:maya"
    assert parameters["to_id"] == "project:launch"


def test_impact_traverses_from_dependents_toward_changed_dependency() -> None:
    client = FakeGraphClient()
    tools = GraphQueries(client)

    tools.run("impact", {"entity_id": "project:auth"})

    assert client.call is not None
    cypher, parameters, _ = client.call
    assert "(affected)-[projections:DEPENDS_ON*1..5]->(changed)" in cypher
    assert "fact.subject_id = startNode(projection).id" in cypher
    assert parameters["entity_id"] == "project:auth"


def test_conflicts_respect_exclusive_policy_and_temporal_overlap() -> None:
    client = FakeGraphClient()
    tools = GraphQueries(client)

    tools.run("find_conflicts", {"entity_id": "project:launch"})

    assert client.call is not None
    cypher, parameters, _ = client.call
    assert "first.ownership_policy = 'exclusive'" in cypher
    assert "second.ownership_policy = 'exclusive'" in cypher
    assert "first.valid_from <= $as_of" in cypher
    assert "second.valid_to > $as_of" in cypher
    assert parameters["entity_id"] == "project:launch"


def test_lineage_query_takes_direction_and_enforces_current_cited_support() -> None:
    client = FakeGraphClient()
    tools = GraphQueries(client)

    tools.run(
        "decision_lineage",
        {"decision_id": "decision:rollout-old", "direction": "newer"},
    )

    assert client.call is not None
    cypher, parameters, _ = client.call
    assert "$direction = 'newer'" in cypher
    assert "SUPPORTED_BY {status: 'accepted'}" in cypher
    assert parameters["direction"] == "newer"
    with pytest.raises(ValueError, match="direction"):
        tools.run("decision_lineage", {"decision_id": "decision:x", "direction": "sideways"})


def test_question_handler_abstains_when_entity_is_not_resolved() -> None:
    client = FakeGraphClient()
    answer = GraphQueries(client).answer_question("Who owns the launch?")

    assert answer["status"] == "insufficient_evidence"
    assert answer["claims"] == []
    assert client.call is not None
    assert client.call[1]["query"] == "the launch"
