import json
from pathlib import Path

from app.demo_data import default_demo_directory, load_demo_data, reset_demo_data
from app.graph.ingestion import extract_structured_graph, parse_source
from app.graph.repository import IngestionReceipt


class RecordingRepository:
    def __init__(self) -> None:
        self.sources: list[dict[str, object]] = []
        self.deleted: list[str] = []

    def ingest_source(self, **source: object) -> IngestionReceipt:
        self.sources.append(source)
        parsed = parse_source(
            str(source["source_id"]), str(source["content"]), str(source["file_format"])
        )
        return IngestionReceipt(
            str(source["source_id"]), parsed.revision_id, len(parsed.sections), "active"
        )

    def delete_source(self, source_id: str) -> bool:
        self.deleted.append(source_id)
        return True


def test_company_demo_is_deterministic_connected_and_uses_all_source_kinds() -> None:
    repository = RecordingRepository()

    receipts = load_demo_data(repository)

    assert len(receipts) == 7
    assert {str(source["kind"]) for source in repository.sources} == {
        "document",
        "ticket",
        "chat",
        "pull_request",
        "decision",
    }
    assert len({receipt.source_id for receipt in receipts}) == len(receipts)

    facts_by_id: dict[str, set[str]] = {}
    for source in repository.sources:
        parsed = parse_source(
            str(source["source_id"]), str(source["content"]), str(source["file_format"])
        )
        structured = extract_structured_graph(str(source["content"]), parsed.sections)
        for fact in structured.facts:
            facts_by_id.setdefault(fact.id, set()).add(str(source["source_id"]))

    assert len(facts_by_id["fact:launch-owner"]) == 3
    assert facts_by_id["fact:canary-supersedes-blue-green"] == {
        "chat:architecture-review",
        "decisions:rollout-policy",
    }
    assert default_demo_directory().is_dir()


def test_demo_loader_is_idempotent_at_source_revision_level() -> None:
    first = RecordingRepository()
    second = RecordingRepository()

    first_receipts = load_demo_data(first)
    second_receipts = load_demo_data(second)

    assert [(item.source_id, item.revision_id) for item in first_receipts] == [
        (item.source_id, item.revision_id) for item in second_receipts
    ]


def test_explicit_demo_reset_only_retires_fixture_source_ids() -> None:
    repository = RecordingRepository()

    receipts = reset_demo_data(repository)

    assert len(receipts) == 7
    assert set(repository.deleted) == {str(source["source_id"]) for source in repository.sources}


def test_company_evaluation_has_thirty_cases_and_multi_hop_acceptance() -> None:
    evaluation_path = (
        Path(__file__).resolve().parents[3] / "data" / "evaluation" / "company_brain.json"
    )
    questions = json.loads(evaluation_path.read_text(encoding="utf-8"))["questions"]

    assert len(questions) >= 30
    assert {question["expected_status"] for question in questions} >= {
        "supported",
        "conflicting",
        "stale",
        "ambiguous",
        "insufficient_evidence",
    }
    assert (
        sum(
            question.get("minimum_path_depth", 0) >= 2
            or question.get("minimum_citation_sources", 0) >= 2
            for question in questions
        )
        >= 10
    )
