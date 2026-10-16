.PHONY: up down test lint seed-demo reset-demo
up:
	docker compose up --build
down:
	docker compose down
test:
	uv run --directory backend pytest
	npm test --prefix frontend
lint:
	uv run --directory backend ruff check app tests
	npm run --prefix frontend lint
	npm run --prefix frontend format:check
seed-demo:
	docker compose exec backend uv run python -m app.seed_demo
reset-demo:
	docker compose exec backend uv run python -m app.seed_demo --reset-demo-sources
