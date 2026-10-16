import json
from pathlib import Path
from typing import Any, Protocol

from app.graph.ingestion import extract_structured_graph, parse_source
from app.graph.repository import IngestionReceipt


class SourceIngestor(Protocol):
    def ingest_source(
        self,
        *,
        source_id: str,
        kind: str,
        canonical_url: str | None,
        file_format: str,
        content: str,
    ) -> IngestionReceipt: ...

    def delete_source(self, source_id: str) -> bool: ...


def default_demo_directory() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "demo"


def load_demo_data(
    repository: SourceIngestor, directory: Path | None = None
) -> list[IngestionReceipt]:
    return _ingest_fixtures(repository, _read_demo_fixtures(directory))


def reset_demo_data(
    repository: SourceIngestor, directory: Path | None = None
) -> list[IngestionReceipt]:
    fixtures = _read_demo_fixtures(directory)
    for source_id, _, _, _ in fixtures:
        repository.delete_source(source_id)
    return _ingest_fixtures(repository, fixtures)


def _read_demo_fixtures(
    directory: Path | None,
) -> list[tuple[str, str, str | None, str]]:
    fixture_directory = directory or default_demo_directory()
    if not fixture_directory.is_dir():
        raise FileNotFoundError(f"Demo dataset directory does not exist: {fixture_directory}")
    fixture_paths = sorted(fixture_directory.glob("*.json"))
    if not fixture_paths:
        raise FileNotFoundError(f"No JSON demo fixtures found in {fixture_directory}")

    fixtures: list[tuple[str, str, str | None, str]] = []
    source_ids: set[str] = set()
    for fixture_path in fixture_paths:
        content = fixture_path.read_text(encoding="utf-8")
        try:
            fixture = json.loads(content)
        except json.JSONDecodeError as error:
            raise ValueError(
                f"Invalid JSON demo fixture {fixture_path.name}: {error.msg}"
            ) from error
        if not isinstance(fixture, dict):
            raise ValueError(f"Demo fixture must be a JSON object: {fixture_path.name}")
        source_id = _required_string(fixture, "source_id", fixture_path.name)
        kind = _required_string(fixture, "kind", fixture_path.name)
        if kind not in {"document", "ticket", "chat", "pull_request", "decision"}:
            raise ValueError(f"Unsupported demo source kind in {fixture_path.name}: {kind}")
        if source_id in source_ids:
            raise ValueError(f"Duplicate demo source ID: {source_id}")
        source_ids.add(source_id)
        canonical_url = fixture.get("canonical_url")
        if canonical_url is not None and not isinstance(canonical_url, str):
            raise ValueError(f"canonical_url must be a string: {fixture_path.name}")
        parsed = parse_source(source_id, content, "json")
        extract_structured_graph(content, parsed.sections)
        fixtures.append((source_id, kind, canonical_url, content))
    return fixtures


def _ingest_fixtures(
    repository: SourceIngestor,
    fixtures: list[tuple[str, str, str | None, str]],
) -> list[IngestionReceipt]:
    return [
        repository.ingest_source(
            source_id=source_id,
            kind=kind,
            canonical_url=canonical_url,
            file_format="json",
            content=content,
        )
        for source_id, kind, canonical_url, content in fixtures
    ]


def _required_string(value: dict[str, Any], key: str, filename: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise ValueError(f"Demo fixture {filename} needs a non-empty {key}")
    return result.strip()
