import pytest

from app.graph.ingestion import (
    IngestionFormatError,
    extract_structured_graph,
    parse_source,
)
from app.graph.repository import GraphRepository


def test_markdown_sections_and_chunk_ids_are_stable() -> None:
    content = "# Overview\nA proof graph.\n\n## Evidence\nCited text."

    first = parse_source("doc:architecture", content, "md")
    second = parse_source("doc:architecture", content, "markdown")

    assert first == second
    assert [section.section_key for section in first.sections] == ["overview", "overview/evidence"]
    assert first.sections[1].citation_locator.startswith("overview > evidence, lines")


def test_json_uses_escaped_pointer_locators() -> None:
    parsed = parse_source("doc:json", '{"team/name":{"owner":"Sam"}}', "json")

    assert parsed.sections[0].section_key == "json/team~1name"
    assert parsed.sections[0].citation_locator == "JSON Pointer /team~1name"


def test_csv_uses_stable_row_id_and_exact_line_locator() -> None:
    parsed = parse_source("ticket:export", "id,title\nA-1,Ship safely\n", "csv")

    assert parsed.sections[0].section_key == "csv/A-1"
    assert parsed.sections[0].citation_locator == "CSV row 2"


def test_text_revisions_change_when_content_changes() -> None:
    original = parse_source("doc:notes", "First paragraph.", "txt")
    updated = parse_source("doc:notes", "First paragraph updated.", "txt")

    assert original.revision_id != updated.revision_id
    assert original.sections[0].chunk_id != updated.sections[0].chunk_id


def test_invalid_or_unsupported_formats_are_rejected() -> None:
    with pytest.raises(IngestionFormatError, match="Invalid JSON"):
        parse_source("doc:bad", "{", "json")
    with pytest.raises(IngestionFormatError, match="Unsupported"):
        parse_source("doc:pdf", "%PDF", "pdf")


def test_structured_facts_require_stable_entities_and_exact_quotes() -> None:
    content = (
        '{"entities":['
        '{"id":"person:alice","type":"Person","name":"Alice"},'
        '{"id":"project:launch","type":"Project","name":"Launch"}],'
        '"facts":[{"predicate":"OWNS","subject_id":"person:alice",'
        '"object_id":"project:launch","quote":"Alice owns Launch."}],'
        '"evidence":"Alice owns Launch."}'
    )
    parsed = parse_source("doc:ownership", content, "json")

    structured = extract_structured_graph(content, parsed.sections)

    assert [entity.label for entity in structured.entities] == ["Person", "Project"]
    assert structured.facts[0].predicate == "OWNS"
    assert structured.facts[0].status == "accepted"
    assert structured.facts[0].id.startswith("fact:")


def test_structured_facts_reject_unresolved_entities_and_quotes() -> None:
    content = (
        '{"entities":[{"id":"person:alice","type":"Person","name":"Alice"}],'
        '"facts":[{"predicate":"OWNS","subject_id":"person:alice",'
        '"object_id":"project:missing","quote":"not in the source"}]}'
    )
    parsed = parse_source("doc:bad", content, "json")

    with pytest.raises(IngestionFormatError, match="stable entity IDs"):
        extract_structured_graph(content, parsed.sections)


def test_structured_facts_reject_invalid_relationship_types_and_dates() -> None:
    wrong_relation = (
        '{"entities":['
        '{"id":"person:alice","type":"Person","name":"Alice"},'
        '{"id":"team:platform","type":"Team","name":"Platform"}],'
        '"facts":[{"predicate":"DEPENDS_ON","subject_id":"person:alice",'
        '"object_id":"team:platform","quote":"Alice depends on Platform."}],'
        '"evidence":"Alice depends on Platform."}'
    )
    parsed = parse_source("doc:bad-relation", wrong_relation, "json")
    with pytest.raises(IngestionFormatError, match="does not support"):
        extract_structured_graph(wrong_relation, parsed.sections)

    invalid_dates = (
        '{"entities":['
        '{"id":"team:platform","type":"Team","name":"Platform"},'
        '{"id":"project:launch","type":"Project","name":"Launch"}],'
        '"facts":[{"predicate":"OWNS","subject_id":"team:platform",'
        '"object_id":"project:launch","valid_from":"2025-2-1",'
        '"quote":"Platform owns Launch."}],"evidence":"Platform owns Launch."}'
    )
    parsed = parse_source("doc:bad-date", invalid_dates, "json")
    with pytest.raises(IngestionFormatError, match="YYYY-MM-DD"):
        extract_structured_graph(invalid_dates, parsed.sections)


class FakeQueryResult:
    def __init__(self, value: object | None) -> None:
        self.result_set = [] if value is None else [[value]]


class FakeIngestionClient:
    def __init__(self, expected_count: int) -> None:
        self.expected_count = expected_count
        self.active_revision: str | None = None
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.retired = False

    def query(self, cypher: str, params: dict[str, object] | None = None) -> FakeQueryResult:
        parameters = params or {}
        self.calls.append((cypher, parameters))
        if "SET source.active_revision_id" in cypher:
            if parameters["expected_revision"] == self.active_revision:
                self.active_revision = str(parameters["revision_id"])
                return FakeQueryResult(self.active_revision)
            return FakeQueryResult(None)
        if "source.status = 'deleted'" in cypher:
            self.retired = True
            return FakeQueryResult(str(parameters["source_id"]))
        if "RETURN source.active_revision_id" in cypher:
            return FakeQueryResult(self.active_revision)
        if "RETURN count(chunk)" in cypher:
            value = (
                self.expected_count
                if "MATCH (source:SourceRecord" in cypher
                else len(parameters["chunks"])
            )
            return FakeQueryResult(value)
        return FakeQueryResult(None)


def test_ingestion_activates_only_after_revision_verification() -> None:
    content = "# Overview\nCurrent evidence."
    parsed = parse_source("doc:one", content, "md")
    client = FakeIngestionClient(expected_count=len(parsed.sections))
    repository = GraphRepository(client)

    receipt = repository.ingest_source("doc:one", "document", None, "md", content)

    assert receipt.revision_id == parsed.revision_id
    assert receipt.chunks_processed == len(parsed.sections)
    assert client.active_revision == parsed.revision_id
    queries = [query for query, _ in client.calls]
    verification_index = next(
        index
        for index, query in enumerate(queries)
        if "RETURN count(chunk)" in query and "MATCH (source:SourceRecord" in query
    )
    switch_index = next(
        index for index, query in enumerate(queries) if "SET source.active_revision_id" in query
    )
    revalidation_index = next(
        index for index, query in enumerate(queries) if "SET fact.status = CASE" in query
    )
    assert "HAS_CHUNK" in queries[verification_index]
    assert verification_index < switch_index < revalidation_index


def test_incomplete_revision_never_becomes_active() -> None:
    client = FakeIngestionClient(expected_count=0)
    repository = GraphRepository(client)

    with pytest.raises(RuntimeError, match="failed verification"):
        repository.ingest_source("doc:partial", "document", None, "txt", "evidence")

    assert client.active_revision is None
    assert not any("SET source.active_revision_id" in query for query, _ in client.calls)


def test_source_deletion_revalidates_facts_without_activating_revisions() -> None:
    client = FakeIngestionClient(expected_count=0)
    repository = GraphRepository(client)

    assert repository.delete_source("doc:one")

    assert client.retired
    assert client.active_revision is None
    assert "active_revision IS NULL" in client.calls[-1][0]
