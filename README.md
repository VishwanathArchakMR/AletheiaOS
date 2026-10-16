# AletheiaOS

**The Truth Layer for Organizational Intelligence**

AletheiaOS is a local-first Company Brain prototype. It stores evidence revisions, accepted/candidate facts, typed entities, and fact-linked graph projections in FalkorDB. The FastAPI layer exposes parameterized, allowlisted graph tools; the React app can load a synthetic company, search evidence, ask supported questions, and inspect citations and verified paths.

The answer layer is deterministic and intentionally has no required LLM. It abstains when an exact entity, current fact, graph path, or eligible citation is missing. A retrieved passage is not treated as proof by itself.

## Run the demo

Requirements: Docker Desktop, Node.js 22+, Python 3.12+, npm, and uv.

1. Optionally copy `.env.example` to `.env` to override local settings.
2. Start the pinned local stack:

   ```powershell
   docker compose up --build
   ```

3. Open [http://localhost:5173](http://localhost:5173), select **Ask AletheiaOS**, and click **Load synthetic company demo**. Or load the same curated records from the API container:

   ```powershell
   docker compose exec backend uv run python -m app.seed_demo
   ```

Try **Who owns Project Launch?**, **Who knows FalkorDB?**, **What work demonstrates Maya Chen's experience with FalkorDB?**, **Why did the company choose Graph architecture?**, **Which decision superseded Blue-Green Rollout Decision?**, **What depends on Authentication Service?**, **How is Maya Chen connected to Project Launch?**, **Who owns Billing?**, and **Who owns Orion?**. Results include evidence source/revision/chunk citations; path answers show ordered entities and relationships. Billing is deliberately inconsistent in the fictional demo, and Orion is intentionally ambiguous.

Loading is repeatable. To explicitly retire and reload only source IDs listed in `data/demo/` while preserving old revision history:

```powershell
docker compose exec backend uv run python -m app.seed_demo --reset-demo-sources
```

This does not delete the graph or user sources. Do not use `docker compose down -v` unless deleting the entire persisted FalkorDB volume is intended.

## Development checks

From the repository root:

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

The integration test and the 31-case evidence evaluation each create a uniquely named graph and delete only that isolated graph. Run them only with a reachable FalkorDB:

```powershell
$env:ALETHEIA_RUN_FALKORDB_INTEGRATION = "1"
uv run --directory backend pytest tests/test_falkordb_integration.py
uv run --directory backend python -m app.evaluate_demo
Remove-Item Env:ALETHEIA_RUN_FALKORDB_INTEGRATION
```

See [local development](docs/local-development.md) and [architecture](docs/architecture.md). The integration test has not been validated against the pinned server in environments where Docker is unavailable; a passing unit suite is not evidence of live FalkorDB compatibility.
