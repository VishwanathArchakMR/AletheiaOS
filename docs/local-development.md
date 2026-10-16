# Local development

## Prerequisites and startup

Install Docker Desktop and start its Linux engine, plus Node.js 22+, Python 3.12+, npm, and uv. Confirm the Docker daemon before starting the stack:

```powershell
docker info
docker compose version
```

Start all services from the repository root:

```powershell
docker compose up --build
```

Open the UI at [http://localhost:5173](http://localhost:5173), API at [http://localhost:8000](http://localhost:8000), and OpenAPI docs at [http://localhost:8000/docs](http://localhost:8000/docs). FalkorDB is not published to the host. Compose mounts only `data/demo/` read-only into the backend for reproducible seeding.

Load the synthetic company records using the **Load synthetic company demo** button in the Ask view or:

```powershell
docker compose exec backend uv run python -m app.seed_demo
```

The loader validates every fixture before use and re-ingestion is idempotent. To deliberately retire and reload only source IDs declared in the fixtures, preserving revision history:

```powershell
docker compose exec backend uv run python -m app.seed_demo --reset-demo-sources
```

No reset runs at application startup. `docker compose down` stops services and keeps the named graph volume. `docker compose down -v` deletes all persisted FalkorDB data and is not a demo-only reset.

## Host-side checks

```powershell
uv sync --directory backend --all-groups
uv run --directory backend pytest
uv run --directory backend ruff check app tests
npm ci --prefix frontend
npm test --prefix frontend
npm run --prefix frontend build
npm run --prefix frontend lint
npm run --prefix frontend format:check
```

The backend unit tests and frontend tests do not require Docker. To run the live integration test and full graph evaluation, ensure the pinned Compose FalkorDB service is running, then:

```powershell
$env:ALETHEIA_RUN_FALKORDB_INTEGRATION = "1"
uv run --directory backend pytest tests/test_falkordb_integration.py
uv run --directory backend python -m app.evaluate_demo
Remove-Item Env:ALETHEIA_RUN_FALKORDB_INTEGRATION
```

Both live commands use uniquely named isolated graphs and remove only those graphs on completion. Do not point them at a graph name that you use for application data; the test code generates its own name.

`/api/v1/health/live` reports API liveness. `/api/v1/health/ready` initializes/verifies graph schema and performs a harmless database query; it returns HTTP 503 while FalkorDB is unavailable. Inspect services with `docker compose logs -f backend`, `docker compose logs -f falkordb`, or `docker compose logs -f frontend`.

The local environment used during the current implementation had no reachable Docker Desktop Linux engine, so the pinned image's index/full-text/query compatibility and live integration/evaluation results must be verified on a machine with Docker available. Do not interpret skipped integration tests as passed.
