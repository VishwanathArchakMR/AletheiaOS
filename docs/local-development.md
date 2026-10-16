# Local development

Prerequisites are Docker Desktop with a running daemon, Node.js 22+, Python 3.12+, npm, and uv. Copy .env.example to .env only if you need settings other than the documented local defaults.

Start all three services from the project root:

    docker compose up --build

The frontend is at http://localhost:5173, API at http://localhost:8000, and API documentation at http://localhost:8000/docs.

For host checks use: uv sync --directory backend; uv run --directory backend pytest; uv run --directory backend ruff check .; uv run --directory backend ruff format --check .; npm ci --prefix frontend; npm run --prefix frontend lint; npm run --prefix frontend format:check; npm test --prefix frontend; and npm run --prefix frontend build.

/api/v1/health/live checks only that FastAPI is alive. /api/v1/health/ready performs a harmless FalkorDB RETURN 1 and returns HTTP 503 if graph connectivity is unavailable. Inspect logs with docker compose logs -f backend, docker compose logs -f falkordb, or docker compose logs -f frontend.

Use docker compose down to stop while preserving graph data. docker compose down -v deletes the named volume. If a host port is occupied, stop the conflicting process or update the host side of the Compose mapping. If Docker cannot connect, start Docker Desktop and confirm docker info. FalkorDB is intentionally not published to a host port.

