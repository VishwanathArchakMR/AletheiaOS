import re
from datetime import UTC, datetime
from typing import Any

from app.graph.client import GraphClient

MAX_RESULTS = 20
MAX_DEPTH = 5
PATH_RELATIONSHIPS = (
    "OWNS",
    "MEMBER_OF",
    "HAS_SKILL",
    "ASSIGNED_TO",
    "PARTICIPATED_IN",
    "COMPLETED",
    "AUTHORED",
    "REVIEWED",
    "CONTAINS",
    "DEPENDS_ON",
    "FIXES",
    "SUPERSEDES",
)


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
            (
                "who_owns",
                re.compile(
                    r"^(?:who owns|who is responsible for|which team is responsible for) "
                    r"(.+?)\??$",
                    re.IGNORECASE,
                ),
                "entity_id",
                None,
            ),
            (
                "who_knows",
                re.compile(r"^(?:who knows|who has experience with) (.+?)\??$", re.IGNORECASE),
                "skill_id",
                "Skill",
            ),
            (
                "why_decision",
                re.compile(
                    r"^(?:why (?:was )?(?:the )?decision|why did (?:the )?company choose) "
                    r"(.+?)(?: made)?\??$",
                    re.IGNORECASE,
                ),
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
                re.compile(
                    r"^(?:(?:show )?impact(?: of| from)?|what depends on|what is affected by) "
                    r"(.+?)\??$",
                    re.IGNORECASE,
                ),
                "entity_id",
                None,
            ),
        )
        normalized_question = question.strip()
        related_match = re.fullmatch(
            r"how is (.+?) connected to (.+?)\??", normalized_question, re.IGNORECASE
        )
        if related_match:
            resolved: list[str] = []
            for name in related_match.groups():
                matches = self.resolve_entities({"query": name, "limit": MAX_RESULTS})
                if len(matches) != 1:
                    if len(matches) > 1:
                        return {
                            "status": "ambiguous",
                            "answer": "Choose a stable entity ID for each ambiguous name.",
                            "entities": matches,
                            "claims": [],
                        }
                    return _answer(
                        "insufficient_evidence",
                        "Both endpoints must resolve to one exact graph entity.",
                    )
                resolved.append(str(matches[0]["entity_id"]))
            results = self.how_related({"from_id": resolved[0], "to_id": resolved[1], "limit": 10})
            if results and _has_provenance(results):
                return _answer("supported", _answer_text("how_related", results), results)
            return _answer(
                "insufficient_evidence",
                "No verified, evidence-backed graph path connects those entities.",
            )

        if re.fullmatch(
            r"(?:which claims have only stale evidence|find stale claims)\??",
            normalized_question,
            re.IGNORECASE,
        ):
            stale = self.find_stale_claims({"limit": 10})
            return _answer(
                "stale" if stale else "insufficient_evidence",
                "Only historical evidence supports these claims."
                if stale
                else "No stale claims were found.",
                stale,
            )

        if re.fullmatch(
            r"(?:are there )?(?:conflicting owner records|conflicting owners)\??",
            normalized_question,
            re.IGNORECASE,
        ):
            conflicts = self.find_conflicts({"limit": 10})
            return _answer(
                "conflicting" if conflicts else "insufficient_evidence",
                "Incompatible current exclusive-owner claims were found."
                if conflicts
                else "No incompatible current exclusive-owner claims were found.",
                conflicts,
            )

        superseding_match = re.fullmatch(
            r"(?:which|what) decision superseded (.+?)\??",
            normalized_question,
            re.IGNORECASE,
        )
        if superseding_match:
            entities = self.resolve_entities(
                {"query": superseding_match.group(1), "limit": MAX_RESULTS}
            )
            decisions = [row for row in entities if row.get("entity_type") == "Decision"]
            if len(decisions) != 1:
                return {
                    "status": "ambiguous" if decisions else "insufficient_evidence",
                    "answer": "Resolve the previous decision by its exact name or stable ID.",
                    "entities": decisions,
                    "claims": [],
                }
            results = self.decision_lineage(
                {
                    "decision_id": decisions[0]["entity_id"],
                    "direction": "newer",
                    "limit": 10,
                }
            )
            if results and _has_provenance(results):
                return _answer("supported", _answer_text("decision_lineage", results), results)
            return _answer(
                "insufficient_evidence",
                "No validated superseding decision is supported by current evidence.",
            )

        experience_match = re.fullmatch(
            r"what work demonstrates (.+?)'s experience with (.+?)\??",
            normalized_question,
            re.IGNORECASE,
        )
        if experience_match:
            person_matches = [
                row
                for row in self.resolve_entities(
                    {"query": experience_match.group(1), "limit": MAX_RESULTS}
                )
                if row.get("entity_type") == "Person"
            ]
            skill_matches = [
                row
                for row in self.resolve_entities(
                    {"query": experience_match.group(2), "limit": MAX_RESULTS}
                )
                if row.get("entity_type") == "Skill"
            ]
            if len(person_matches) != 1 or len(skill_matches) != 1:
                candidates = person_matches + skill_matches
                status = (
                    "ambiguous"
                    if len(person_matches) > 1 or len(skill_matches) > 1
                    else "insufficient_evidence"
                )
                return {
                    "status": status,
                    "answer": "Resolve the person and skill by exact name or stable ID.",
                    "entities": candidates,
                    "claims": [],
                }
            results = self.who_knows({"skill_id": skill_matches[0]["entity_id"], "limit": 10})
            work = [
                row
                for row in results
                if row.get("person_id") == person_matches[0]["entity_id"]
                and row.get("expertise_basis") == "demonstrated_work"
            ]
            if work and _has_provenance(work):
                return _answer(
                    "supported",
                    "Cited completed, authored, or reviewed work demonstrates experience.",
                    work,
                )
            return _answer(
                "insufficient_evidence",
                "No current completed, authored, or reviewed work links this person to that skill.",
            )

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
                    conflicts = self.find_conflicts({"entity_id": entity_id, "limit": 10})
                    if conflicts:
                        return _answer(
                            "conflicting",
                            "Incompatible current exclusive-owner claims have evidence.",
                            conflicts,
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
              AND source.id = node.source_id
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
        labels = ("Person", "Team", "Project", "Task", "Decision", "Skill", "Rationale")
        branches = [
            f"MATCH (entity:{label}) "
            "WHERE entity.id = $query OR toLower(entity.name) = toLower($query) "
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
            MATCH (fact:Fact {id: projection.fact_id})
                  -[support:SUPPORTED_BY {status: 'accepted'}]->
                  (chunk:Chunk)<-[:HAS_CHUNK]-(source:SourceRecord)
            WHERE source.status = 'active'
              AND fact.status IN ['accepted', 'stale']
              AND fact.predicate = 'OWNS'
              AND fact.subject_id = owner.id
              AND fact.object_id = target.id
              AND source.active_revision_id = chunk.revision_id
              AND source.id = chunk.source_id
              AND (fact.valid_from IS NULL OR fact.valid_from <= $as_of)
              AND (fact.valid_to IS NULL OR fact.valid_to > $as_of)
            RETURN DISTINCT owner.id AS owner_id,
                            owner.name AS owner_name,
                            labels(owner) AS owner_types,
                            target.id AS entity_id,
                            target.name AS entity_name,
                            fact.id AS fact_id,
                            fact.ownership_policy AS ownership_policy,
                            support.quote AS quote,
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
        skill_id = _required(arguments, "skill_id")
        as_of = _as_of(arguments)
        limit = _limit(arguments)
        explicit = self._query(
            """
            MATCH (person:Person)-[projection:HAS_SKILL]->(skill:Skill)
            WHERE skill.id = $skill_id
            MATCH (fact:Fact {id: projection.fact_id})
                  -[support:SUPPORTED_BY {status: 'accepted'}]->
                  (chunk:Chunk)<-[:HAS_CHUNK]-(source:SourceRecord)
            WHERE source.status = 'active'
              AND fact.status IN ['accepted', 'stale']
              AND fact.predicate = 'HAS_SKILL'
              AND fact.subject_id = person.id
              AND fact.object_id = skill.id
              AND source.active_revision_id = chunk.revision_id
              AND source.id = chunk.source_id
              AND (fact.valid_from IS NULL OR fact.valid_from <= $as_of)
              AND (fact.valid_to IS NULL OR fact.valid_to > $as_of)
            RETURN DISTINCT person.id AS person_id,
                            person.name AS person_name,
                            skill.id AS skill_id,
                            fact.id AS fact_id,
                            support.quote AS quote,
                            chunk.id AS chunk_id,
                            source.id AS source_id,
                            source.canonical_url AS canonical_url,
                            chunk.revision_id AS revision_id,
                            chunk.citation_locator AS citation_locator
            LIMIT $limit
            """,
            {
                "skill_id": skill_id,
                "as_of": as_of,
                "limit": limit,
            },
        )
        demonstrated = self._query(
            """
            MATCH (person:Person)-[work_projection]->(task:Task)
            WHERE type(work_projection) IN $work_types
              AND work_projection.fact_id IS NOT NULL
            MATCH (task)-[skill_projection:REFERENCES]->(skill:Skill)
            WHERE skill.id = $skill_id
              AND skill_projection.fact_id IS NOT NULL
            MATCH (work_fact:Fact {id: work_projection.fact_id})
                  -[work_support:SUPPORTED_BY {status: 'accepted'}]->
                  (work_chunk:Chunk)<-[:HAS_CHUNK]-(work_source:SourceRecord)
            MATCH (skill_fact:Fact {id: skill_projection.fact_id})
                  -[skill_support:SUPPORTED_BY {status: 'accepted'}]->
                  (skill_chunk:Chunk)<-[:HAS_CHUNK]-(skill_source:SourceRecord)
            WHERE work_fact.status IN ['accepted', 'stale']
              AND skill_fact.status IN ['accepted', 'stale']
              AND work_fact.predicate = type(work_projection)
              AND work_fact.subject_id = person.id
              AND work_fact.object_id = task.id
              AND skill_fact.predicate = 'REFERENCES'
              AND skill_fact.subject_id = task.id
              AND skill_fact.object_id = skill.id
              AND work_source.status = 'active'
              AND work_source.active_revision_id = work_chunk.revision_id
              AND work_source.id = work_chunk.source_id
              AND skill_source.status = 'active'
              AND skill_source.active_revision_id = skill_chunk.revision_id
              AND skill_source.id = skill_chunk.source_id
              AND (work_fact.valid_from IS NULL OR work_fact.valid_from <= $as_of)
              AND (work_fact.valid_to IS NULL OR work_fact.valid_to > $as_of)
              AND (skill_fact.valid_from IS NULL OR skill_fact.valid_from <= $as_of)
              AND (skill_fact.valid_to IS NULL OR skill_fact.valid_to > $as_of)
            RETURN DISTINCT person.id AS person_id,
                            person.name AS person_name,
                            skill.id AS skill_id,
                            'demonstrated_work' AS expertise_basis,
                            work_fact.id AS first_fact_id,
                            work_support.quote AS first_quote,
                            work_chunk.id AS first_chunk_id,
                            work_source.id AS first_source_id,
                            work_source.canonical_url AS first_canonical_url,
                            work_chunk.revision_id AS first_revision_id,
                            work_chunk.citation_locator AS first_citation_locator,
                            skill_fact.id AS second_fact_id,
                            skill_support.quote AS second_quote,
                            skill_chunk.id AS second_chunk_id,
                            skill_source.id AS second_source_id,
                            skill_source.canonical_url AS second_canonical_url,
                            skill_chunk.revision_id AS second_revision_id,
                            skill_chunk.citation_locator AS second_citation_locator
            LIMIT $limit
            """,
            {
                "skill_id": skill_id,
                "as_of": as_of,
                "work_types": ["COMPLETED", "AUTHORED", "REVIEWED"],
                "limit": limit,
            },
        )
        for row in explicit:
            row["expertise_basis"] = "explicit_skill"
        return (explicit + demonstrated)[:limit]

    def why_decision(self, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        return self._query(
            """
            MATCH (decision:Decision {id: $decision_id})
            MATCH (fact:Fact {predicate: 'DECISION_RATIONALE'})
            WHERE fact.subject_id = decision.id
            MATCH (rationale:Rationale {id: fact.object_id})
            MATCH (fact)-[support:SUPPORTED_BY {status: 'accepted'}]->
                  (chunk:Chunk)<-[:HAS_CHUNK]-(source:SourceRecord)
            WHERE source.status = 'active'
              AND fact.status IN ['accepted', 'stale']
              AND source.active_revision_id = chunk.revision_id
              AND source.id = chunk.source_id
              AND (fact.valid_from IS NULL OR fact.valid_from <= $as_of)
              AND (fact.valid_to IS NULL OR fact.valid_to > $as_of)
            RETURN fact.id AS fact_id,
                   chunk.id AS chunk_id,
                   support.quote AS quote,
                   chunk.content AS rationale,
                   chunk.citation_locator AS citation_locator,
                   source.id AS source_id,
                   source.canonical_url AS canonical_url,
                   chunk.revision_id AS revision_id
            LIMIT $limit
            """,
            {
                "decision_id": _required(arguments, "decision_id"),
                "as_of": _as_of(arguments),
                "limit": _limit(arguments),
            },
        )

    def decision_lineage(self, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        frontier = [_required(arguments, "decision_id")]
        direction = arguments.get("direction", "older")
        if direction not in {"older", "newer"}:
            raise ValueError("direction must be older or newer")
        visited = set(frontier)
        lineage: list[dict[str, Any]] = []
        for _ in range(MAX_DEPTH):
            if not frontier or len(lineage) >= _limit(arguments):
                break
            rows = self._query(
                """
                MATCH (newer:Decision)-[projection:SUPERSEDES]->(older:Decision)
                WHERE ($direction = 'older' AND newer.id IN $decision_ids)
                   OR ($direction = 'newer' AND older.id IN $decision_ids)
                MATCH (fact:Fact {id: projection.fact_id})
                      -[support:SUPPORTED_BY {status: 'accepted'}]->
                      (chunk:Chunk)<-[:HAS_CHUNK]-(source:SourceRecord)
                WHERE source.status = 'active'
                  AND fact.status IN ['accepted', 'stale']
                  AND fact.predicate = 'SUPERSEDES'
                  AND fact.subject_id = newer.id
                  AND fact.object_id = older.id
                  AND source.active_revision_id = chunk.revision_id
                  AND source.id = chunk.source_id
                  AND (fact.valid_from IS NULL OR fact.valid_from <= $as_of)
                  AND (fact.valid_to IS NULL OR fact.valid_to > $as_of)
                RETURN DISTINCT newer.id AS newer_id,
                                older.id AS older_id,
                                fact.id AS fact_id,
                                support.quote AS quote,
                                source.id AS source_id,
                                source.canonical_url AS canonical_url,
                                chunk.id AS chunk_id,
                                chunk.revision_id AS revision_id,
                                chunk.citation_locator AS citation_locator
                LIMIT $limit
                """,
                {
                    "decision_ids": frontier,
                    "direction": direction,
                    "as_of": _as_of(arguments),
                    "limit": _limit(arguments),
                },
            )
            frontier = []
            for row in rows:
                lineage.append(row)
                next_id = row.get("older_id" if direction == "older" else "newer_id")
                if isinstance(next_id, str) and next_id not in visited:
                    visited.add(next_id)
                    frontier.append(next_id)
                if len(lineage) >= _limit(arguments):
                    break
        return lineage

    def impact(self, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        return self._query(
            """
            MATCH path=(affected)-[projections:DEPENDS_ON*1..5]->(changed)
            WHERE changed.id = $entity_id
              AND all(node IN nodes(path) WHERE
                      single(candidate IN nodes(path) WHERE candidate.id = node.id))
            UNWIND projections AS projection
            MATCH (fact:Fact {id: projection.fact_id})
                  -[support:SUPPORTED_BY {status: 'accepted'}]->
                  (chunk:Chunk)<-[:HAS_CHUNK]-(source:SourceRecord)
            WHERE source.status = 'active'
              AND fact.status IN ['accepted', 'stale']
              AND source.active_revision_id = chunk.revision_id
              AND source.id = chunk.source_id
              AND (fact.valid_from IS NULL OR fact.valid_from <= $as_of)
              AND (fact.valid_to IS NULL OR fact.valid_to > $as_of)
              AND fact.predicate = type(projection)
              AND fact.subject_id = startNode(projection).id
              AND fact.object_id = endNode(projection).id
            WITH path, projections,
                 count(DISTINCT projection) AS verified_edges,
                 collect(DISTINCT {
                     fact_id: fact.id,
                     quote: support.quote,
                     source_id: source.id,
                     canonical_url: source.canonical_url,
                     chunk_id: chunk.id,
                     revision_id: chunk.revision_id,
                     citation_locator: chunk.citation_locator
                 }) AS evidence
            WHERE verified_edges = size(projections)
            RETURN [node IN nodes(path) | {
                       id: node.id, name: node.name, labels: labels(node)
                   }] AS path_nodes,
                   [projection IN projections | type(projection)] AS relationships,
                   evidence
            ORDER BY size(projections) ASC
            LIMIT $limit
            """,
            {
                "entity_id": _required(arguments, "entity_id"),
                "as_of": _as_of(arguments),
                "limit": _limit(arguments),
            },
        )

    def find_conflicts(self, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        return self._query(
            """
            MATCH (first:Fact {predicate: 'OWNS'})
                  -[first_support:SUPPORTED_BY {status: 'accepted'}]->
                  (first_chunk:Chunk)<-[:HAS_CHUNK]-(first_source:SourceRecord)
            MATCH (second:Fact {predicate: 'OWNS'})
                  -[second_support:SUPPORTED_BY {status: 'accepted'}]->
                  (second_chunk:Chunk)<-[:HAS_CHUNK]-(second_source:SourceRecord)
            WHERE first.object_id = second.object_id
              AND ($entity_id IS NULL OR first.object_id = $entity_id)
              AND first.subject_id <> second.subject_id
              AND first.id < second.id
              AND first.status IN ['accepted', 'stale']
              AND second.status IN ['accepted', 'stale']
              AND first.ownership_policy = 'exclusive'
              AND second.ownership_policy = 'exclusive'
              AND first_source.status = 'active'
              AND first_source.active_revision_id = first_chunk.revision_id
              AND first_source.id = first_chunk.source_id
              AND second_source.status = 'active'
              AND second_source.active_revision_id = second_chunk.revision_id
              AND second_source.id = second_chunk.source_id
              AND (first.valid_from IS NULL OR first.valid_from <= $as_of)
              AND (first.valid_to IS NULL OR first.valid_to > $as_of)
              AND (second.valid_from IS NULL OR second.valid_from <= $as_of)
              AND (second.valid_to IS NULL OR second.valid_to > $as_of)
            RETURN DISTINCT first.object_id AS entity_id,
                            first.subject_id AS first_owner_id,
                            first.id AS first_fact_id,
                            first_support.quote AS first_quote,
                            first_source.id AS first_source_id,
                            first_source.canonical_url AS first_canonical_url,
                            first_chunk.id AS first_chunk_id,
                            first_chunk.revision_id AS first_revision_id,
                            first_chunk.citation_locator AS first_citation_locator,
                            second.subject_id AS second_owner_id,
                            second.id AS second_fact_id,
                            second_support.quote AS second_quote,
                            second_source.id AS second_source_id,
                            second_source.canonical_url AS second_canonical_url,
                            second_chunk.id AS second_chunk_id,
                            second_chunk.revision_id AS second_revision_id,
                            second_chunk.citation_locator AS second_citation_locator
            LIMIT $limit
            """,
            {
                "entity_id": arguments.get("entity_id"),
                "as_of": _as_of(arguments),
                "limit": _limit(arguments),
            },
        )

    def find_stale_claims(self, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        return self._query(
            """
            MATCH (fact:Fact)-[old_support:SUPPORTED_BY {status: 'accepted'}]->
                  (old_chunk:Chunk)
            MATCH (old_source:SourceRecord {id: old_chunk.source_id})
            WHERE ($entity_id IS NULL OR fact.subject_id = $entity_id
                                       OR fact.object_id = $entity_id)
              AND fact.status IN ['accepted', 'stale']
              AND (fact.valid_from IS NULL OR fact.valid_from <= $as_of)
              AND (fact.valid_to IS NULL OR fact.valid_to > $as_of)
            OPTIONAL MATCH (fact)-[:SUPPORTED_BY {status: 'accepted'}]->
                           (current_chunk:Chunk)<-[:HAS_CHUNK]-(current_source:SourceRecord)
            WHERE current_source.status = 'active'
              AND current_source.active_revision_id = current_chunk.revision_id
              AND current_source.id = current_chunk.source_id
            WITH fact, old_chunk, old_source, count(DISTINCT current_chunk) AS active_supports
            WHERE active_supports = 0
            RETURN DISTINCT fact.id AS fact_id,
                            fact.predicate AS predicate,
                            fact.subject_id AS subject_id,
                            fact.object_id AS object_id,
                            old_support.quote AS quote,
                            old_chunk.id AS chunk_id,
                            old_chunk.source_id AS source_id,
                            old_source.canonical_url AS canonical_url,
                            old_chunk.revision_id AS revision_id,
                            old_chunk.citation_locator AS citation_locator
            LIMIT $limit
            """,
            {
                "entity_id": arguments.get("entity_id"),
                "as_of": _as_of(arguments),
                "limit": _limit(arguments),
            },
        )

    def how_related(self, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        return self._query(
            """
            MATCH path=(left)-[projections*1..5]->(right)
            WHERE left.id = $from_id
              AND right.id = $to_id
              AND all(projection IN projections WHERE
                      type(projection) IN $relationship_types
                      AND projection.fact_id IS NOT NULL)
              AND all(node IN nodes(path) WHERE
                      single(candidate IN nodes(path) WHERE candidate.id = node.id))
            UNWIND projections AS projection
            MATCH (fact:Fact {id: projection.fact_id})
                  -[support:SUPPORTED_BY {status: 'accepted'}]->
                  (chunk:Chunk)<-[:HAS_CHUNK]-(source:SourceRecord)
            WHERE source.status = 'active'
              AND fact.status IN ['accepted', 'stale']
              AND source.active_revision_id = chunk.revision_id
              AND source.id = chunk.source_id
              AND (fact.valid_from IS NULL OR fact.valid_from <= $as_of)
              AND (fact.valid_to IS NULL OR fact.valid_to > $as_of)
              AND fact.predicate = type(projection)
              AND fact.subject_id = startNode(projection).id
              AND fact.object_id = endNode(projection).id
            WITH path, projections,
                 count(DISTINCT projection) AS verified_edges,
                 collect(DISTINCT {
                     fact_id: fact.id,
                     quote: support.quote,
                     source_id: source.id,
                     canonical_url: source.canonical_url,
                     chunk_id: chunk.id,
                     revision_id: chunk.revision_id,
                     citation_locator: chunk.citation_locator
                 }) AS evidence
            WHERE verified_edges = size(projections)
            RETURN [node IN nodes(path) | {
                       id: node.id, name: node.name, labels: labels(node)
                   }] AS path_nodes,
                   [projection IN projections | type(projection)] AS relationships,
                   evidence
            ORDER BY size(projections) ASC
            LIMIT $limit
            """,
            {
                "from_id": _required(arguments, "from_id"),
                "to_id": _required(arguments, "to_id"),
                "relationship_types": list(PATH_RELATIONSHIPS),
                "as_of": _as_of(arguments),
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
        normalized = _required(arguments, "as_of")
        try:
            parsed = datetime.strptime(normalized, "%Y-%m-%d")
        except ValueError as error:
            raise ValueError("as_of must use YYYY-MM-DD format") from error
        if parsed.strftime("%Y-%m-%d") != normalized:
            raise ValueError("as_of must use YYYY-MM-DD format")
        return normalized
    return datetime.now(UTC).date().isoformat()


def _has_provenance(rows: list[dict[str, Any]]) -> bool:
    required = {"fact_id", "chunk_id", "source_id", "revision_id", "citation_locator"}
    if not rows:
        return False
    for row in rows:
        evidence = [row]
        nested = row.get("evidence")
        if isinstance(nested, list):
            evidence.extend(item for item in nested if isinstance(item, dict))
        for prefix in ("first_", "second_"):
            if f"{prefix}fact_id" in row:
                evidence.append({key: row.get(f"{prefix}{key}") for key in required})
        cited = [
            item
            for item in evidence
            if required.issubset(item) and all(item.get(key) for key in required)
        ]
        if not cited:
            return False
    return True


def _answer(
    state: str,
    text: str,
    claims: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {"status": state, "answer": text, "claims": claims or []}


def _answer_text(tool_name: str, results: list[dict[str, Any]]) -> str:
    if tool_name == "who_owns":
        names = sorted({str(row.get("owner_name") or row.get("owner_id")) for row in results})
        return "Current evidence supports these owner(s): " + ", ".join(names) + "."
    if tool_name == "who_knows":
        names = sorted({str(row.get("person_name") or row.get("person_id")) for row in results})
        bases = {row.get("expertise_basis") for row in results}
        distinction = (
            "Explicit skill assertions and demonstrated work support "
            if bases == {"explicit_skill", "demonstrated_work"}
            else "Demonstrated work supports "
            if bases == {"demonstrated_work"}
            else "Explicit skill assertions support "
        )
        return distinction + ", ".join(names) + "."
    if tool_name == "why_decision":
        reasons = sorted(
            {
                str(row.get("rationale"))
                for row in results
                if isinstance(row.get("rationale"), str) and row.get("rationale")
            }
        )
        return "Recorded rationale: " + " ".join(reasons)
    if tool_name == "decision_lineage":
        links = [f"{row.get('newer_id')} supersedes {row.get('older_id')}" for row in results]
        return "Validated decision lineage: " + "; ".join(links) + "."
    if tool_name == "impact":
        return "These bounded dependency paths may be affected."
    if tool_name == "how_related":
        paths = [
            " -> ".join(str(node.get("id")) for node in row.get("path_nodes", []))
            for row in results
            if isinstance(row.get("path_nodes"), list)
        ]
        return "Verified graph path: " + "; ".join(paths) + "."
    return "The requested claim is supported by current evidence."
