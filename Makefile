COMPOSE = docker compose --env-file .env -f infra/docker-compose.yml

.PHONY: up down api web test lint sample

up:
	$(COMPOSE) up -d --build --wait

down:
	$(COMPOSE) down

api:
	cd backend && set -a && . ../.env && set +a && uv run uvicorn app.main:app --reload --port 8000

web:
	cd web && pnpm dev

test:
	cd backend && uv run pytest
	@if [ -d pipeline ]; then cd pipeline && uv run pytest; fi
	@if [ -d web ]; then cd web && pnpm test; fi

lint:
	cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy .
	@if [ -d pipeline ]; then cd pipeline && uv run ruff check . && uv run ruff format --check . && uv run mypy .; fi
	@if [ -d web ]; then cd web && pnpm lint && pnpm typecheck; fi

sample:
	cd pipeline && uv run python -m qarau_pipeline export --sparse ../data/samples/south-building/sparse --images ../data/samples/south-building/images --out ../data/runs/south-building
