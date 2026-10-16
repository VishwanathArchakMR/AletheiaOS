.PHONY: up down test lint
up:
	docker compose up --build
down:
	docker compose down
test:
	cd backend && uv run pytest
	cd frontend && npm test
lint:
	cd backend && uv run ruff check . && uv run ruff format --check .
	cd frontend && npm run lint && npm run format:check

