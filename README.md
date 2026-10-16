# AletheiaOS

AletheiaOS is the foundation for a proof-carrying company graph: a future system that connects organizational knowledge to supporting evidence. This stage provides local infrastructure and health visibility only. It does not implement ingestion, graph schema, facts, agents, or LLM calls.

## Start locally

1. Install Docker Desktop, Node.js 22+, Python 3.12+, npm, and uv.
2. Optionally copy .env.example to .env to override local defaults.
3. Run docker compose up --build.

Open the frontend at http://localhost:5173, API at http://localhost:8000, and OpenAPI documentation at http://localhost:8000/docs.

The graph runs as the pinned falkordb/falkordb:v4.20.4 image with the named falkordb_data volume. Stop services using docker compose down. docker compose down -v also removes persisted graph data, so use it only when an intentional reset is required.

See [local development](docs/local-development.md) and [architecture](docs/architecture.md).

