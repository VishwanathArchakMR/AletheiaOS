from dataclasses import dataclass
from datetime import UTC, datetime

from app.graph.client import GraphClient
from app.graph.ingestion import (
    PROJECTED_PREDICATES,
    EntityRecord,
    FactRecord,
    extract_structured_graph,
    parse_source,
)
from app.graph.queries import GraphQueries
from app.graph.schema import GraphSchema


@dataclass(frozen=True)
class IngestionReceipt:
    source_id: str
    revision_id: str
    chunks_processed: int
    status: str


class RevisionConflictError(RuntimeError):
    pass


class GraphRepository:
    def __init__(self, client: GraphClient) -> None:
        self._client = client
        self._schema = GraphSchema(client)
        self._schema_initialized = False
        self._queries = GraphQueries(client)

    def initialize(self) -> None:
        if self._schema_initialized:
            return
        self._schema.initialize()
        self._schema_initialized = True

    def ingest_source(
        self,
        source_id: str,
        kind: str,
        canonical_url: str | None,
        file_format: str,
        content: str,
    ) -> IngestionReceipt:
        parsed = parse_source(source_id, content, file_format)
        if not parsed.sections:
            raise ValueError("Source contains no ingestible sections")
        structured = extract_structured_graph(content, parsed.sections)

        source_rows = _rows(
            self._client.query(
                "MATCH (source:SourceRecord {id: $source_id}) "
                "RETURN source.kind AS kind, source.status AS status, "
                "source.active_revision_id AS active_revision_id",
                {"source_id": source_id},
            )
        )
        if source_rows and source_rows[0].get("kind") != kind:
            raise ValueError("A source ID cannot change its source kind")
        expected_revision = source_rows[0].get("active_revision_id") if source_rows else None
        expected_status = source_rows[0].get("status") if source_rows else "processing"
        self._validate_existing_entities(structured.entities)
        self._validate_existing_facts(structured.facts)
        chunks = [
            {
                "id": section.chunk_id,
                "revision_id": parsed.revision_id,
                "section_key": section.section_key,
                "content_hash": section.content_hash,
                "content": section.content,
                "citation_locator": section.citation_locator,
            }
            for section in parsed.sections
        ]
        expected_supports = sum(
            1
            for fact in structured.facts
            for section in parsed.sections
            if fact.quote in section.content
        )
        prepared_chunks = self._client.query(
            """
            MERGE (source:SourceRecord {id: $source_id})
            ON CREATE SET source.status = 'processing',
                          source.kind = $kind,
                          source.canonical_url = $canonical_url,
                          source.updated_at = $updated_at
            ON MATCH SET source.updated_at = $updated_at
            WITH source
            WHERE source.kind = $kind
            UNWIND $chunks AS item
            MERGE (chunk:Chunk {id: item.id})
            ON CREATE SET chunk.source_id = $source_id,
                          chunk.revision_id = item.revision_id,
                          chunk.section_key = item.section_key,
                          chunk.content_hash = item.content_hash,
                          chunk.content = item.content,
                          chunk.citation_locator = item.citation_locator
            WITH source, chunk, item
            WHERE chunk.source_id = $source_id
              AND chunk.revision_id = item.revision_id
              AND chunk.section_key = item.section_key
              AND chunk.content_hash = item.content_hash
              AND chunk.content = item.content
              AND (chunk.citation_locator = item.citation_locator
                   OR (chunk.citation_locator IS NULL
                       AND item.citation_locator IS NULL))
            MERGE (source)-[:HAS_CHUNK {
                revision_id: item.revision_id,
                section_key: item.section_key
            }]->(chunk)
            RETURN count(chunk)
            """,
            {
                "source_id": source_id,
                "kind": kind,
                "canonical_url": canonical_url,
                "updated_at": _utc_now(),
                "chunks": chunks,
            },
        )
        if _single_value(prepared_chunks) != len(chunks):
            raise RuntimeError(
                "Prepared chunks have a conflicting source kind or stable chunk identity"
            )

        for entity in structured.entities:
            written = self._client.query(
                f"""
                MERGE (entity:Entity {{id: $id}})
                ON CREATE SET entity.name = $name,
                              entity.entity_type = $entity_type
                WITH entity
                WHERE entity.name = $name AND entity.entity_type = $entity_type
                SET entity:{entity.label}
                RETURN entity.id
                """,
                {
                    "id": entity.id,
                    "name": entity.name,
                    "entity_type": entity.label,
                },
            )
            if _single_value(written) != entity.id:
                raise ValueError(
                    f"Stable entity ID {entity.id} already exists with a different type or name"
                )

        for fact in structured.facts:
            fact_parameters = {
                "id": fact.id,
                "predicate": fact.predicate,
                "subject_id": fact.subject_id,
                "object_id": fact.object_id,
                "valid_from": fact.valid_from,
                "valid_to": fact.valid_to,
                "status": fact.status,
                "ownership_policy": fact.ownership_policy,
            }
            written = self._client.query(
                """
                MERGE (fact:Fact {id: $id})
                ON CREATE SET fact.predicate = $predicate,
                              fact.subject_id = $subject_id,
                              fact.object_id = $object_id,
                              fact.valid_from = $valid_from,
                              fact.valid_to = $valid_to,
                              fact.ownership_policy = $ownership_policy,
                              fact.status = $status
                WITH fact
                WHERE fact.predicate = $predicate
                  AND fact.subject_id = $subject_id
                  AND fact.object_id = $object_id
                  AND (fact.valid_from = $valid_from
                       OR (fact.valid_from IS NULL AND $valid_from IS NULL))
                  AND (fact.valid_to = $valid_to
                       OR (fact.valid_to IS NULL AND $valid_to IS NULL))
                  AND fact.ownership_policy = $ownership_policy
                SET fact.status = CASE WHEN $status = 'accepted'
                                       THEN 'accepted' ELSE fact.status END
                RETURN fact.id
                """,
                fact_parameters,
            )
            if _single_value(written) != fact.id:
                raise ValueError(
                    f"Stable fact ID {fact.id} already exists with a different assertion"
                )
            evidence_chunks = [
                section.chunk_id for section in parsed.sections if fact.quote in section.content
            ]
            for chunk_id in evidence_chunks:
                self._client.query(
                    """
                    MATCH (source:SourceRecord {id: $source_id})
                          -[:HAS_CHUNK {revision_id: $revision_id}]->
                          (chunk:Chunk {id: $chunk_id})
                    MATCH (fact:Fact {id: $fact_id})
                    MERGE (fact)-[support:SUPPORTED_BY]->(chunk)
                    SET support.status = $status,
                        support.quote = $quote
                    """,
                    {
                        "source_id": source_id,
                        "revision_id": parsed.revision_id,
                        "fact_id": fact.id,
                        "chunk_id": chunk_id,
                        "status": fact.status,
                        "quote": fact.quote,
                    },
                )

            relationship_type = PROJECTED_PREDICATES.get(fact.predicate)
            if fact.status == "accepted" and relationship_type:
                self._client.query(
                    f"""
                    MATCH (subject:{fact.subject_label} {{id: $subject_id}})
                    MATCH (object:{fact.object_label} {{id: $object_id}})
                    MATCH (fact:Fact {{id: $fact_id, status: 'accepted'}})
                    MERGE (subject)-[relationship:{relationship_type} {{fact_id: $fact_id}}]->(
                        object
                    )
                    SET relationship.fact_id = $fact_id
                    """,
                    {
                        "subject_id": fact.subject_id,
                        "object_id": fact.object_id,
                        "fact_id": fact.id,
                    },
                )

        verified = self._client.query(
            """
            MATCH (source:SourceRecord {id: $source_id})
                  -[:HAS_CHUNK {revision_id: $revision_id}]->(chunk:Chunk)
            WHERE chunk.revision_id = $revision_id
            RETURN count(chunk)
            """,
            {"source_id": source_id, "revision_id": parsed.revision_id},
        )
        if _single_value(verified) != len(chunks):
            raise RuntimeError("Prepared source revision failed verification")

        verified_entities = self._client.query(
            "MATCH (entity) WHERE entity.id IN $ids RETURN count(DISTINCT entity)",
            {"ids": [entity.id for entity in structured.entities]},
        )
        if structured.entities and _single_value(verified_entities) != len(structured.entities):
            raise RuntimeError("Prepared revision has a missing or duplicate entity")

        verified_facts = self._client.query(
            "MATCH (fact:Fact) WHERE fact.id IN $ids RETURN count(DISTINCT fact)",
            {"ids": [fact.id for fact in structured.facts]},
        )
        if structured.facts and _single_value(verified_facts) != len(structured.facts):
            raise RuntimeError("Prepared revision has a missing or duplicate fact")

        verified_supports = self._client.query(
            """
            MATCH (source:SourceRecord {id: $source_id})
                  -[:HAS_CHUNK {revision_id: $revision_id}]->(chunk:Chunk)
            MATCH (fact:Fact)-[support:SUPPORTED_BY]->(chunk)
            RETURN count(DISTINCT support)
            """,
            {"source_id": source_id, "revision_id": parsed.revision_id},
        )
        if expected_supports and _single_value(verified_supports) != expected_supports:
            raise RuntimeError("Prepared revision has missing or invalid fact evidence")

        switched = self._client.query(
            """
            MATCH (source:SourceRecord {id: $source_id})
            WHERE source.status = $expected_status
              AND (source.active_revision_id = $revision_id
                   OR (source.active_revision_id IS NULL AND $expected_revision IS NULL)
                   OR source.active_revision_id = $expected_revision)
            SET source.active_revision_id = $revision_id,
                source.status = 'active',
                source.canonical_url = $canonical_url,
                source.updated_at = $updated_at
            RETURN source.active_revision_id
            """,
            {
                "source_id": source_id,
                "expected_revision": expected_revision,
                "expected_status": expected_status,
                "revision_id": parsed.revision_id,
                "canonical_url": canonical_url,
                "updated_at": _utc_now(),
            },
        )
        if _single_value(switched) != parsed.revision_id:
            raise RevisionConflictError(
                "Source revision changed during ingestion; retry the update"
            )

        self._revalidate_source_claims(source_id, parsed.revision_id)

        return IngestionReceipt(
            source_id=source_id,
            revision_id=parsed.revision_id,
            chunks_processed=len(chunks),
            status="active",
        )

    def _revalidate_source_claims(self, source_id: str, active_revision: str | None) -> None:
        self._client.query(
            """
            MATCH (fact:Fact)-[:SUPPORTED_BY {status: 'accepted'}]->(old_chunk:Chunk)
            WHERE old_chunk.source_id = $source_id
              AND ($active_revision IS NULL OR old_chunk.revision_id <> $active_revision)
            WITH DISTINCT fact
            OPTIONAL MATCH (fact)-[:SUPPORTED_BY {status: 'accepted'}]->(evidence:Chunk)
                           <-[:HAS_CHUNK]-(source:SourceRecord)
            WHERE source.status = 'active'
              AND source.active_revision_id = evidence.revision_id
              AND source.id = evidence.source_id
            WITH fact, count(DISTINCT evidence) AS eligible_support_count
            WHERE fact.status IN ['accepted', 'stale']
            SET fact.status = CASE WHEN eligible_support_count > 0
                                   THEN 'accepted' ELSE 'stale' END
            """,
            {"source_id": source_id, "active_revision": active_revision},
        )

    def _validate_existing_entities(self, entities: tuple[EntityRecord, ...]) -> None:
        if not entities:
            return
        expected = {entity.id: entity for entity in entities}
        rows = _rows(
            self._client.query(
                "MATCH (entity) WHERE entity.id IN $ids "
                "RETURN entity.id AS id, labels(entity) AS labels, "
                "entity.entity_type AS entity_type, entity.name AS name",
                {"ids": list(expected)},
            )
        )
        for row in rows:
            entity = expected[str(row["id"])]
            if (
                set(row.get("labels", [])) != {"Entity", entity.label}
                or row.get("entity_type") != entity.label
                or row.get("name") != entity.name
            ):
                raise ValueError(
                    f"Stable entity ID {entity.id} already exists with a different type or name"
                )

    def _validate_existing_facts(self, facts: tuple[FactRecord, ...]) -> None:
        if not facts:
            return
        expected = {fact.id: fact for fact in facts}
        rows = _rows(
            self._client.query(
                "MATCH (fact:Fact) WHERE fact.id IN $ids "
                "RETURN fact.id AS id, fact.predicate AS predicate, "
                "fact.subject_id AS subject_id, fact.object_id AS object_id, "
                "fact.valid_from AS valid_from, fact.valid_to AS valid_to, "
                "fact.ownership_policy AS ownership_policy",
                {"ids": list(expected)},
            )
        )
        for row in rows:
            fact = expected[str(row["id"])]
            if any(
                row.get(key) != getattr(fact, key)
                for key in (
                    "predicate",
                    "subject_id",
                    "object_id",
                    "valid_from",
                    "valid_to",
                    "ownership_policy",
                )
            ):
                raise ValueError(
                    f"Stable fact ID {fact.id} already exists with a different assertion"
                )

    def delete_source(self, source_id: str) -> bool:
        result = self._client.query(
            "MATCH (source:SourceRecord {id: $source_id}) "
            "SET source.status = 'deleted', source.deleted_at = $deleted_at "
            "RETURN source.id",
            {"source_id": source_id, "deleted_at": _utc_now()},
        )
        if _single_value(result) != source_id:
            return False
        self._revalidate_source_claims(source_id, None)
        return True

    def _active_revision(self, source_id: str) -> str | None:
        result = self._client.query(
            "MATCH (source:SourceRecord {id: $source_id}) RETURN source.active_revision_id",
            {"source_id": source_id},
        )
        return _single_value(result)

    def is_ready(self) -> bool:
        self.initialize()
        self._client.ping()
        return True

    def search_evidence(self, query: str, limit: int = 10) -> list[dict[str, object]]:
        return self._queries.search_evidence({"query": query, "limit": limit})

    def run_tool(self, tool_name: str, arguments: dict[str, object]) -> list[dict[str, object]]:
        return self._queries.run(tool_name, arguments)

    def answer_question(self, question: str) -> dict[str, object]:
        return self._queries.answer_question(question)

    @property
    def tool_names(self) -> frozenset[str]:
        return self._queries.TOOL_NAMES


def _single_value(result: object) -> object | None:
    rows = getattr(result, "result_set", [])
    return rows[0][0] if rows else None


def _rows(result: object) -> list[dict[str, object]]:
    headers = [
        column[0] if isinstance(column, list | tuple) else column
        for column in getattr(result, "header", [])
    ]
    return [dict(zip(headers, row, strict=False)) for row in getattr(result, "result_set", [])]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
