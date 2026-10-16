import re
from datetime import UTC, datetime
from typing import Any

from app.graph.client import GraphClient

MAX_RESULTS = 20
MAX_DEPTH = 5


class GraphQueries:
    TOOL_NAMES = frozenset(
        {
            "search_evidence",
            "resolve_entities",
            "who_owns",
            "who_knows",
            "why_decision",
            "decision_lineage",
            "impact",
            "find_conflicts",
            "find_stale_claims",
            "how_related",
        }
    )

    def __init__(self, client: GraphClient) -> None:
        self._client = client

    def run(self, tool_name: str, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        tools = {
            "search_evidence": self.search_evidence,
            "resolve_entities": self.resolve_entities,
            "who_owns": self.who_owns,
            "who_knows": self.who_knows,
            "why_decision": self.why_decision,
            "decision_lineage": self.decision_lineage,
            "impact": self.impact,
            "find_conflicts": self.find_conflicts,
            "find_stale_claims": self.find_stale_claims,
            "how_related": self.how_related,
        }
        tool = tools.get(tool_name)
        if tool is None:
            raise ValueError(f"Unsupported graph tool: {tool_name}")
        return tool(arguments)

    def answer_question(self, question: str) -> dict[str, Any]:
        patterns = (
            ("who_owns", re.compile(r"^who owns (.+?)\??$", re.IGNORECASE), "entity_id", None),
            ("who_knows", re.compile(r"^who knows (.+?)\??$", re.IGNORECASE), "skill_id", "Skill"),
            (
                "why_decision",
                re.compile(r"^why (?:was )?(?:the )?decision (.+?)(?: made)?\??$", re.IGNORECASE),
                "decision_id",
                "Decision",
            ),
            (
                "decision_lineage",
                re.compile(r"^(?:show )?decision lineage(?: for)? (.+?)\??$", re.IGNORECASE),
                "decision_id",
                "Decision",
            ),
            (
                "impact",
                re.compile(r"^(?:show )?impact(?: of| from)? (.+?)\??$", re.IGNORECASE),
                "entity_id",
                None,
            ),
        )
        normalized_question = question.strip()
        for tool_name, pattern, identifier_name, required_type in patterns:
            match = pattern.fullmatch(normalized_question)
            if not match:
                continue
            entities = self.resolve_entities({"query": match.group(1), "limit": MAX_RESULTS})
            if required_type:
                entities = [row for row in entities if row.get("entity_type") == required_type]
            unique_entities = {row.get("entity_id"): row for row in entities}
            if len(unique_entities) > 1:
                return {
                    "status": "ambiguous",
                    "answer": "More than one exact entity match exists; choose a stable entity ID.",
                    "entities": list(unique_entities.values()),
                    "claims": [],
                }
            if not unique_entities:
                return _answer("insufficient_evidence", "No exact entity match was found.")

            entity_id = next(iter(unique_entities))
            results = self.run(tool_name, {identifier_name: entity_id, "limit": 10})
            if tool_name == "who_owns":
                owner_ids = {row.get("owner_id") for row in results}
                if len(owner_ids) > 1:
                    return _answer(
                        "conflicting",
                        "Multiple owners have current, accepted evidence.",
                        results,
                    )
            if results:
                if not _has_provenance(results):
                    return _answer(
                        "insufficient_evidence",
                        "The graph result did not include verifiable source provenance.",
                    )
                return _answer("supported", _answer_text(tool_name, results), results)

            stale = self.find_stale_claims({"entity_id": entity_id, "limit": 10})
            if stale:
                return _answer("stale", "Only stale evidence is available for this claim.", stale)
            return _answer("insufficient_evidence", "No current supporting evidence was found.")

        evidence = self.search_evidence({"query": normalized_question[:200], "limit": 10})
        return _answer(
            "insufficient_evidence",
            "No supported question pattern matched. Retrieved passages are shown as evidence only.",
            evidence,
        )

    def search_evidence(self, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        query = _required(arguments, "query")
        search_terms = re.findall(r"[^\W_]+", query, flags=re.UNICODE)
        if not search_terms:
            return []
        return self._query(
            """
            CALL db.idx.fulltext.queryNodes('Chunk', $search_query)
            YIELD node, score
            MATCH (source:SourceRecord)-[:HAS_CHUNK]->(node:Chunk)
            WHERE source.status = 'active'
              AND source.active_revision_id = node.revision_id
            RETURN source.id AS source_id,
                   source.kind AS kind,
                   source.canonical_url AS canonical_url,
               node.id AS chunk_id,
               node.revision_id AS revision_id,
               node.citation_locator AS citation_locator,
               node.content AS content
            ORDER BY score DESC
            LIMIT $limit
            """,
            {"search_query": " ".join(search_terms), "limit": _limit(arguments)},
        )

    def resolve_entities(self, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        query = _required(arguments, "query")
        labels = ("Person", "Team", "Project", "Task", "Decision", "Skill")
        branches = [
            f"MATCH (entity:{label}) "
            "WHERE entity.id = $query OR entity.name = $query "
            f"RETURN '{label}' AS entity_type, entity.id AS entity_id, "
            "entity.name AS name"
            for label in labels
        ]
        return self._query(
            " UNION ALL ".join(branches) + " LIMIT $limit",
            {"query": query, "limit": _limit(arguments)},
        )

    def who_owns(self, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        return self._query(
            """
            MATCH (owner)-[projection:OWNS]->(target)
            WHERE target.id = $entity_id
            MATCH (fact:Fact {id: projection.fact_id, status: 'accepted'})
                  -[:SUPPORTED_BY]->(chunk:Chunk)<-[:HAS_CHUNK]-(source:SourceRecord)
            WHERE source.status = 'active'
              AND source.active_revision_id = chunk.revision_id
              AND (fact.valid_from IS NULL OR fact.valid_from <= $as_of)
              AND (fact.valid_to IS NULL OR fact.valid_to > $as_of)
            RETURN DISTINCT owner.id AS owner_id,
                            owner.name AS owner_name,
                            target.id AS entity_id,
                            fact.id AS fact_id,
                            chunk.id AS chunk_id,
                            source.id AS source_id,
                            source.canonical_url AS canonical_url,
                            chunk.revision_id AS revision_id,
                            chunk.citation_locator AS citation_locator
            LIMIT $limit
            """,
            _entity_parameters(arguments),
        )

    def who_knows(self, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        return self._query(
            """
            MATCH (person:Person)-[projection:HAS_SKILL]->(skill:Skill)
            WHERE skill.id = $skill_id
            MATCH (fact:Fact {id: projection.fact_id, status: 'accepted'})
                  -[:SUPPORTED_BY]->(chunk:Chunk)<-[:HAS_CHUNK]-(source:SourceRecord)
            WHERE source.status = 'active'
              AND source.active_revision_id = chunk.revision_id
              AND (fact.valid_from IS NULL OR fact.valid_from <= $as_of)
              AND (fact.valid_to IS NULL OR fact.valid_to > $as_of)
            RETURN DISTINCT person.id AS person_id,
                            person.name AS person_name,
                            skill.id AS skill_id,
                            fact.id AS fact_id,
                            chunk.id AS chunk_id,
                            source.id AS source_id,
                            source.canonical_url AS canonical_url,
                            chunk.revision_id AS revision_id,
                            chunk.citation_locator AS citation_locator
            LIMIT $limit
            """,
            {
                "skill_id": _required(arguments, "skill_id"),
                "as_of": _as_of(arguments),
                "limit": _limit(arguments),
            },
        )

    def why_decision(self, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        return self._query(
            """
            MATCH (fact:Fact {predicate: 'DECISION_RATIONALE', status: 'accepted'})
            WHERE fact.subject_id = $decision_id
            MATCH (fact)-[:SUPPORTED_BY]->(chunk:Chunk)<-[:HAS_CHUNK]-(source:SourceRecord)
            WHERE source.status = 'active'
              AND source.active_revision_id = chunk.revision_id
            RETURN fact.id AS fact_id,
                   chunk.id AS chunk_id,
                   chunk.content AS rationale,
                   chunk.citation_locator AS citation_locator,
                   source.id AS source_id,
                   source.canonical_url AS canonical_url,
                   chunk.revision_id AS revision_id
            LIMIT $limit
            """,
            {
                "decision_id": _required(arguments, "decision_id"),
                "limit": _limit(arguments),
            },
        )

    def decision_lineage(self, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        frontier = [_required(arguments, "decision_id")]
        visited = set(frontier)
        lineage: list[dict[str, Any]] = []
        for _ in range(MAX_DEPTH):
            if not frontier or len(lineage) >= _limit(arguments):
                break
            rows = self._query(
                """
                MATCH (newer:Decision)-[projection:SUPERSEDES]->(older:Decision)
                WHERE newer.id IN $decision_ids
                MATCH (fact:Fact {id: projection.fact_id, status: 'accepted'})
                      -[:SUPPORTED_BY]->(chunk:Chunk)<-[:HAS_CHUNK]-(source:SourceRecord)
                WHERE source.status = 'active'
                  AND source.active_revision_id = chunk.revision_id
                RETURN DISTINCT newer.id AS newer_id,
                                older.id AS older_id,
                                fact.id AS fact_id,
                                source.id AS source_id,
                                source.canonical_url AS canonical_url,
                                chunk.id AS chunk_id,
                                chunk.revision_id AS revision_id,
                                chunk.citation_locator AS citation_locator
                LIMIT $limit
                """,
                {"decision_ids": frontier, "limit": _limit(arguments)},
            )
            frontier = []
            for row in rows:
                lineage.append(row)
                older_id = row.get("older_id")
                if isinstance(older_id, str) and older_id not in visited:
                    visited.add(older_id)
                    frontier.append(older_id)
                if len(lineage) >= _limit(arguments):
                    break
        return lineage

    def impact(self, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        return self._query(
            """
            MATCH (affected)-[projection:DEPENDS_ON]->(changed)
            WHERE changed.id = $entity_id
            MATCH (fact:Fact {id: projection.fact_id, status: 'accepted'})
                  -[:SUPPORTED_BY]->(chunk:Chunk)<-[:HAS_CHUNK]-(source:SourceRecord)
            WHERE source.status = 'active'
              AND source.active_revision_id = chunk.revision_id
            RETURN DISTINCT affected.id AS affected_id,
                            affected.name AS affected_name,
                            changed.id AS changed_id,
                            fact.id AS fact_id,
                            source.id AS source_id,
                            source.canonical_url AS canonical_url,
                            chunk.id AS chunk_id,
                            chunk.revision_id AS revision_id,
                            chunk.citation_locator AS citation_locator
            LIMIT $limit
            """,
            {
                "entity_id": _required(arguments, "entity_id"),
                "limit": _limit(arguments),
            },
        )

    def find_conflicts(self, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        return self._query(
            """
            MATCH (first:Fact {predicate: 'OWNS', status: 'accepted'})
                  -[:SUPPORTED_BY]->(first_chunk:Chunk)<-[:HAS_CHUNK]-(first_source:SourceRecord)
            MATCH (second:Fact {predicate: 'OWNS', status: 'accepted'})
                  -[:SUPPORTED_BY]->(second_chunk:Chunk)<-[:HAS_CHUNK]-(second_source:SourceRecord)
            WHERE first.object_id = second.object_id
              AND first.subject_id <> second.subject_id
              AND first.id < second.id
              AND first_source.status = 'active'
              AND first_source.active_revision_id = first_chunk.revision_id
              AND second_source.status = 'active'
              AND second_source.active_revision_id = second_chunk.revision_id
            RETURN DISTINCT first.object_id AS entity_id,
                            first.subject_id AS first_owner_id,
                            first.id AS first_fact_id,
                            first_source.id AS first_source_id,
                            first_source.canonical_url AS first_canonical_url,
                            first_chunk.id AS first_chunk_id,
                            first_chunk.revision_id AS first_revision_id,
                            first_chunk.citation_locator AS first_citation_locator,
                            second.subject_id AS second_owner_id,
                            second.id AS second_fact_id,
                            second_source.id AS second_source_id,
                            second_source.canonical_url AS second_canonical_url,
                            second_chunk.id AS second_chunk_id,
                            second_chunk.revision_id AS second_revision_id,
                            second_chunk.citation_locator AS second_citation_locator
            LIMIT $limit
            """,
            {"limit": _limit(arguments)},
        )

    def find_stale_claims(self, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        return self._query(
            """
            MATCH (fact:Fact {status: 'stale'})-[:SUPPORTED_BY]->(chunk:Chunk)
            MATCH (source:SourceRecord {id: chunk.source_id})
                WHERE $entity_id IS NULL
                    OR fact.subject_id = $entity_id
                    OR fact.object_id = $entity_id
            RETURN DISTINCT fact.id AS fact_id,
                            fact.predicate AS predicate,
                            fact.subject_id AS subject_id,
                            fact.object_id AS object_id,
                            chunk.id AS chunk_id,
                            chunk.source_id AS source_id,
                            source.canonical_url AS canonical_url,
                            chunk.revision_id AS revision_id,
                            chunk.citation_locator AS citation_locator
            LIMIT $limit
            """,
            {"entity_id": arguments.get("entity_id"), "limit": _limit(arguments)},
        )

    def how_related(self, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        return self._query(
            """
            MATCH (left)-[projection]->(right)
            WHERE left.id = $from_id
              AND right.id = $to_id
              AND projection.fact_id IS NOT NULL
            MATCH (fact:Fact {id: projection.fact_id, status: 'accepted'})
                  -[:SUPPORTED_BY]->(chunk:Chunk)<-[:HAS_CHUNK]-(source:SourceRecord)
            WHERE source.status = 'active'
              AND source.active_revision_id = chunk.revision_id
            RETURN left.id AS from_id,
                   type(projection) AS relationship_type,
                   right.id AS to_id,
                   fact.id AS fact_id,
                   source.id AS source_id,
                   source.canonical_url AS canonical_url,
                   chunk.id AS chunk_id,
                   chunk.revision_id AS revision_id,
                   chunk.citation_locator AS citation_locator
            LIMIT $limit
            """,
            {
                "from_id": _required(arguments, "from_id"),
                "to_id": _required(arguments, "to_id"),
                "limit": _limit(arguments),
            },
        )

    def _query(self, cypher: str, parameters: dict[str, Any]) -> list[dict[str, Any]]:
        result = self._client.query(cypher, params=parameters, timeout_ms=5000)
        headers = [
            column[0] if isinstance(column, list | tuple) else column for column in result.header
        ]
        return [dict(zip(headers, row, strict=False)) for row in result.result_set]


def _required(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise ValueError(f"{key} must be a non-empty string of at most 512 characters")
    return value.strip()


def _limit(arguments: dict[str, Any]) -> int:
    value = arguments.get("limit", 10)
    if type(value) is not int or not 1 <= value <= MAX_RESULTS:
        raise ValueError(f"limit must be between 1 and {MAX_RESULTS}")
    return value


def _entity_parameters(arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "entity_id": _required(arguments, "entity_id"),
        "as_of": _as_of(arguments),
        "limit": _limit(arguments),
    }


def _as_of(arguments: dict[str, Any]) -> str:
    value = arguments.get("as_of")
    if value is not None:
        return _required(arguments, "as_of")
    return datetime.now(UTC).date().isoformat()


def _has_provenance(rows: list[dict[str, Any]]) -> bool:
    required = {"fact_id", "chunk_id", "source_id", "revision_id", "citation_locator"}
    return all(required.issubset(row) and all(row.get(key) for key in required) for row in rows)


def _answer(
    state: str,
    text: str,
    claims: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {"status": state, "answer": text, "claims": claims or []}


def _answer_text(tool_name: str, results: list[dict[str, Any]]) -> str:
    if tool_name == "who_owns":
        name = results[0].get("owner_name") or results[0].get("owner_id")
        return f"{name} is supported as the current owner."
    if tool_name == "who_knows":
        names = sorted({str(row.get("person_name") or row.get("person_id")) for row in results})
        return "Current accepted evidence links this skill to: " + ", ".join(names) + "."
    if tool_name == "why_decision":
        return "A current rationale passage is attached to this decision."
    if tool_name == "decision_lineage":
        return "Current evidence links this decision to its superseded decisions."
    if tool_name == "impact":
        return "These current dependencies may be affected."
    return "The requested claim is supported by current evidence."
