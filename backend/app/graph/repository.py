from dataclasses import dataclass
from datetime import UTC, datetime

from app.graph.client import GraphClient
from app.graph.ingestion import (
    PROJECTED_PREDICATES,
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

        expected_revision = self._active_revision(source_id)
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
        self._client.query(
            """
            MERGE (source:SourceRecord {id: $source_id})
            ON CREATE SET source.status = 'processing'
            SET source.kind = $kind,
                source.canonical_url = $canonical_url,
                source.updated_at = $updated_at
            WITH source
            UNWIND $chunks AS item
            MERGE (chunk:Chunk {id: item.id})
            ON CREATE SET chunk.source_id = $source_id,
                          chunk.revision_id = item.revision_id,
                          chunk.section_key = item.section_key,
                          chunk.content_hash = item.content_hash,
                          chunk.content = item.content,
                          chunk.citation_locator = item.citation_locator
            WITH source, chunk, item
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

        for entity in structured.entities:
            self._client.query(
                f"MERGE (entity:{entity.label} {{id: $id}}) SET entity.name = $name",
                {"id": entity.id, "name": entity.name},
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
            }
            self._client.query(
                """
                MERGE (fact:Fact {id: $id})
                ON CREATE SET fact.status = $status
                SET fact.predicate = $predicate,
                    fact.subject_id = $subject_id,
                    fact.object_id = $object_id,
                    fact.valid_from = $valid_from,
                    fact.valid_to = $valid_to,
                    fact.status = CASE WHEN $status = 'accepted'
                                       THEN 'accepted' ELSE fact.status END
                """,
                fact_parameters,
            )
            evidence_chunks = [
                section.chunk_id for section in parsed.sections if fact.quote in section.content
            ]
            for chunk_id in evidence_chunks:
                self._client.query(
                    "MATCH (fact:Fact {id: $fact_id}), (chunk:Chunk {id: $chunk_id}) "
                    "MERGE (fact)-[:SUPPORTED_BY]->(chunk)",
                    {"fact_id": fact.id, "chunk_id": chunk_id},
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

        switched = self._client.query(
            """
            MATCH (source:SourceRecord {id: $source_id})
            WHERE (source.active_revision_id IS NULL AND $expected_revision IS NULL)
               OR source.active_revision_id = $expected_revision
            SET source.active_revision_id = $revision_id,
                source.status = 'active',
                source.updated_at = $updated_at
            RETURN source.active_revision_id
            """,
            {
                "source_id": source_id,
                "expected_revision": expected_revision,
                "revision_id": parsed.revision_id,
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

    def _revalidate_source_claims(self, source_id: str, active_revision: str) -> None:
        self._client.query(
            """
            MATCH (fact:Fact)-[:SUPPORTED_BY]->(old_chunk:Chunk)
            WHERE old_chunk.source_id = $source_id
                            AND ($active_revision IS NULL OR old_chunk.revision_id <> $active_revision)
            WITH DISTINCT fact
            OPTIONAL MATCH (fact)-[:SUPPORTED_BY]->(evidence:Chunk)
                           <-[:HAS_CHUNK]-(source:SourceRecord)
            WHERE source.status = 'active'
              AND source.active_revision_id = evidence.revision_id
            WITH fact, count(evidence) AS eligible_support_count
            WHERE eligible_support_count = 0 AND fact.status = 'accepted'
            SET fact.status = 'stale'
            """,
            {"source_id": source_id, "active_revision": active_revision},
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


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
