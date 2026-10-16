import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.demo_data import load_demo_data
from app.graph.client import GraphClient
from app.graph.repository import GraphRepository


def main() -> int:
    repository_root = Path(__file__).resolve().parents[2]
    evaluation_path = repository_root / "data" / "evaluation" / "company_brain.json"
    questions = json.loads(evaluation_path.read_text(encoding="utf-8"))["questions"]
    settings = Settings(falkordb_graph_name=f"aletheia_eval_{uuid.uuid4().hex}")
    client = GraphClient(settings)
    repository = GraphRepository(client)
    report: list[dict[str, Any]] = []

    try:
        repository.initialize()
        load_demo_data(repository, settings.demo_data_directory)
        for item in questions:
            if item["scenario"] != "baseline":
                continue
            report.append(_evaluate_case(repository, item))

        for source_id, kind, canonical_url in (
            (
                "docs:company-handbook",
                "document",
                "https://example.test/handbook",
            ),
            (
                "docs:service-architecture",
                "document",
                "https://example.test/architecture/services",
            ),
            (
                "tickets:launch-owner-confirmation",
                "ticket",
                "https://example.test/tickets/LAUNCH-18",
            ),
        ):
            repository.ingest_source(
                source_id=source_id,
                kind=kind,
                canonical_url=canonical_url,
                file_format="md",
                content="# Updated record\nThe former ownership statement is no longer asserted.",
            )
        for item in questions:
            if item["scenario"] == "after_owner_support_retraction":
                report.append(_evaluate_case(repository, item))
    finally:
        _delete_isolated_graph(client, settings.falkordb_graph_name)
        client.close()

    passed = sum(result["passed"] for result in report)
    print(
        json.dumps(
            {"passed": passed, "total": len(report), "results": report},
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0 if passed == len(report) else 1


def _evaluate_case(repository: GraphRepository, item: dict[str, Any]) -> dict[str, Any]:
    answer = repository.answer_question(item["question"])
    records = list(_walk(answer))
    fact_ids = {
        record[key]
        for record in records
        for key in ("fact_id", "first_fact_id", "second_fact_id")
        if isinstance(record.get(key), str)
    }
    source_ids = {
        record[key]
        for record in records
        for key in ("source_id", "first_source_id", "second_source_id")
        if isinstance(record.get(key), str)
    }
    required_facts = set(item.get("expected_fact_ids", []))
    required_sources = set(item.get("expected_source_ids", []))
    minimum_citation_sources = item.get("minimum_citation_sources", 0)
    actual_path_depth = max(
        (
            len(record["path_nodes"]) - 1
            for record in records
            if isinstance(record.get("path_nodes"), list)
        ),
        default=0,
    )
    expected_path_depth = item.get("minimum_path_depth", 0)
    passed = (
        answer["status"] == item["expected_status"]
        and required_facts.issubset(fact_ids)
        and required_sources.issubset(source_ids)
        and len(source_ids) >= minimum_citation_sources
        and actual_path_depth >= expected_path_depth
    )
    return {
        "question": item["question"],
        "expected_status": item["expected_status"],
        "actual_status": answer["status"],
        "expected_fact_ids": sorted(required_facts),
        "missing_fact_ids": sorted(required_facts - fact_ids),
        "missing_source_ids": sorted(required_sources - source_ids),
        "minimum_path_depth": expected_path_depth,
        "actual_path_depth": actual_path_depth,
        "minimum_citation_sources": minimum_citation_sources,
        "actual_citation_sources": len(source_ids),
        "passed": passed,
    }


def _walk(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _walk(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk(nested)


def _delete_isolated_graph(client: GraphClient, graph_name: str) -> None:
    if client._client is None:
        return
    if graph_name in client._client.list_graphs():
        client._client.execute_command("GRAPH.DELETE", graph_name)


if __name__ == "__main__":
    raise SystemExit(main())
