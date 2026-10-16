from time import monotonic, sleep
from typing import Any

from app.graph.client import GraphClient

NODE_LABELS = (
    "Entity",
    "Person",
    "Team",
    "Project",
    "Task",
    "Decision",
    "Skill",
    "Rationale",
    "SourceRecord",
    "Chunk",
    "Fact",
)

RELATIONSHIP_TYPES = (
    "MEMBER_OF",
    "HAS_SKILL",
    "OWNS",
    "ASSIGNED_TO",
    "PARTICIPATED_IN",
    "COMPLETED",
    "AUTHORED",
    "REVIEWED",
    "CONTAINS",
    "DEPENDS_ON",
    "REFERENCES",
    "MENTIONS",
    "FIXES",
    "SUPPORTED_BY",
    "DERIVED_FROM",
    "SUPERSEDES",
    "HAS_CHUNK",
)

INDEXES = tuple((label, "id") for label in NODE_LABELS) + (
    *(
        (label, "name")
        for label in (
            "Person",
            "Team",
            "Project",
            "Task",
            "Decision",
            "Skill",
            "Rationale",
        )
    ),
    ("SourceRecord", "active_revision_id"),
    ("SourceRecord", "kind"),
    ("Chunk", "source_id"),
    ("Chunk", "revision_id"),
    ("Fact", "predicate"),
    ("Fact", "status"),
)


class GraphSchema:
    def __init__(self, client: GraphClient, timeout_seconds: float = 10.0) -> None:
        self._client = client
        self._timeout_seconds = timeout_seconds
        self._entities_migrated = False
        self._fact_policies_migrated = False

    def initialize(self) -> None:
        index_result = self._client.query("CALL db.indexes()")
        existing_indexes = _index_keys(index_result)
        for label, property_name in INDEXES:
            if (label, (property_name,)) not in existing_indexes:
                self._client.query(f"CREATE INDEX FOR (n:{label}) ON (n.{property_name})")
        if not _has_full_text_index(index_result, "Chunk", "content"):
            self._client.query("CALL db.idx.fulltext.createNodeIndex('Chunk', 'content')")

        existing_constraints = _constraint_rows(self._client.query("CALL db.constraints()"))
        for label in NODE_LABELS:
            key = ("UNIQUE", "NODE", label, ("id",))
            if key not in existing_constraints:
                self._client.create_unique_constraint(label, "id")

        self._wait_for_constraints()
        self._migrate_typed_entities()
        self._migrate_fact_policies()

    def _migrate_typed_entities(self) -> None:
        if self._entities_migrated:
            return
        for label in ("Person", "Team", "Project", "Task", "Decision", "Skill", "Rationale"):
            self._client.query(
                f"MATCH (entity:{label}) "
                "WHERE entity.entity_type IS NULL "
                f"SET entity:Entity, entity.entity_type = '{label}'"
            )
        self._entities_migrated = True

    def _migrate_fact_policies(self) -> None:
        if self._fact_policies_migrated:
            return
        self._client.query(
            """
            MATCH (fact:Fact)
            WHERE fact.ownership_policy IS NULL
            SET fact.ownership_policy = CASE WHEN fact.predicate = 'OWNS'
                                             THEN 'exclusive'
                                             ELSE 'not_applicable' END
            """
        )
        self._fact_policies_migrated = True

    def _wait_for_constraints(self) -> None:
        deadline = monotonic() + self._timeout_seconds
        while True:
            constraints = _constraint_rows(self._client.query("CALL db.constraints()"))
            required = {("UNIQUE", "NODE", label, ("id",)) for label in NODE_LABELS}
            statuses = {key: status for key, status in constraints.items()}
            failed = [key for key in required if statuses.get(key) == "FAILED"]
            if failed:
                raise RuntimeError(f"FalkorDB schema constraints failed: {failed}")
            if all(statuses.get(key) == "OPERATIONAL" for key in required):
                return
            if monotonic() >= deadline:
                raise TimeoutError("FalkorDB schema constraints did not become operational")
            sleep(0.1)


def _rows_by_header(result: Any) -> list[dict[str, Any]]:
    headers = [
        column[0] if isinstance(column, list | tuple) else column for column in result.header
    ]
    return [dict(zip(headers, row, strict=False)) for row in result.result_set]


def _properties(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(sorted(str(property_name) for property_name in value))


def _index_keys(result: Any) -> set[tuple[str, tuple[str, ...]]]:
    return {
        (str(row.get("label", "")), _properties(row.get("properties")))
        for row in _rows_by_header(result)
    }


def _has_full_text_index(result: Any, label: str, property_name: str) -> bool:
    return any(
        row.get("label") == label
        and property_name in _properties(row.get("properties"))
        and "FULLTEXT" in {item.upper() for item in _properties(row.get("types"))}
        for row in _rows_by_header(result)
    )


def _constraint_rows(
    result: Any,
) -> dict[tuple[str, str, str, tuple[str, ...]], str]:
    return {
        (
            str(row.get("type", "")).upper(),
            str(row.get("entitytype", "")).upper(),
            str(row.get("label", "")),
            _properties(row.get("properties")),
        ): str(row.get("status", "")).upper()
        for row in _rows_by_header(result)
    }
