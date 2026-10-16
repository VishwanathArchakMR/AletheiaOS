# AletheiaOS architecture

The React client calls FastAPI; only the backend issues Cypher. FastAPI and FalkorDB communicate over the private Compose network. FalkorDB is pinned to `falkordb/falkordb:v4.20.4`, with persisted data in the named `falkordb_data` volume.

## Evidence and graph model

- `SourceRecord` represents a logical source and points at its authoritative `active_revision_id`.
- `Chunk` stores immutable content and a locator for one source revision. Full-text search returns only chunks attached to the active revision of a non-deleted source.
- `Fact` stores a stable typed assertion, temporal interval, and accepted/candidate/stale state.
- `SUPPORTED_BY` connects a fact to its evidence chunk. Its status and quote are specific to that support edge; candidate evidence cannot become eligible merely because another source accepted the same fact.
- Globally unique `Entity` nodes represent people, teams, projects, tasks, decisions, skills, and rationales, with one matching type label. Relationship projections carry `fact_id`; current graph tools validate the assertion and eligible active evidence behind each projected edge.

Ingestion parses and validates the entire structured source before writes, checks stable entity/fact identity against existing records, prepares chunks and graph records, verifies chunks/entities/facts/support links, then conditionally switches the active revision. The revision switch is a single graph query guarded by the previously observed revision and source state. Stale status is revalidated afterward; current query results independently require accepted support from an active revision, so a failed status-refresh cannot make old evidence current. Historical records are retained. Deletion retires a source rather than erasing its evidence.

Schema initialization migrates legacy typed entity nodes to the global `Entity` identity label and supplies default ownership policies for old facts. Legacy support edges without per-source status are retained as history but are not treated as current evidence; re-ingest those active sources to establish accepted or candidate support explicitly.

## Query and answer path

Graph tools are selected from a fixed registry and use query parameters; there is no arbitrary Cypher endpoint. Search is evidence retrieval only. Entity resolution is exact by stable ID or case-insensitive exact name and can return ambiguity. Ownership, expertise, rationale, lineage, conflict, stale-claim, dependency-impact, and related-path queries require active cited support and honor validity dates where relevant.

`how_related` and `impact` traverse a bounded, cycle-safe path, verify every edge's fact, direction, current support, and validity interval, and return ordered nodes, relationship types, and evidence. `impact` follows dependent-to-dependency edge direction, so a change to a service identifies its downstream dependents. Ownership conflicts require distinct owners, overlapping current validity, active accepted support, and exclusive ownership policy.

The question orchestrator is deterministic. It resolves question entities and dispatches only supported patterns; unsupported or unresolved questions abstain. Citation validation checks evidence structure and provenance, not semantic entailment. There is no required LLM, MCP server, external action execution, or claim of mathematical entailment.

## Demo and verification

The synthetic records in `data/demo/` span handbook, architecture, ticket, chat, pull-request, and decision source types. The dataset includes cross-source owner support, expertise supported by completed/reviewed work, decision rationale and supersession, a multi-hop dependency, deliberately conflicting ownership, and an ambiguous entity name. `data/evaluation/company_brain.json` contains 30 baseline questions and a stale-after-retraction scenario. The evaluator and live integration test use unique graph names and remove only those test graphs.

Real compatibility of schema/index/constraint creation, full-text search, and the complete integration flow still requires running against the pinned FalkorDB image. See [local development](local-development.md) for commands and limitations.
