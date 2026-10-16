# AletheiaOS architecture

The browser communicates only with FastAPI. FastAPI owns configuration, CORS, graph lifecycle, and future bounded graph operations. It reaches FalkorDB through the private Compose aletheia network. FalkorDB is the primary persisted graph database.

The locked future model will use generic SourceRecord nodes, immutable revision-specific Chunk evidence, evidence-backed Fact claims, typed cross-source edges, and one active_revision_id per source. A future ingestion flow will reconcile evidence and graph staleness. Future agents will use allowlisted parameterized operations rather than arbitrary Cypher, and future answers will validate evidence, graph paths, and uncertainty.

This foundation intentionally does not include domain schema, ingestion, extraction, search, MCP, graph analytics, approval actions, cloud deployment, or LLM integration.

