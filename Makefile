API_PORT ?= 8000
COMPOSE = docker compose --env-file .env -f infra/docker-compose.yml

.PHONY: up down api web test lint sample local local-stop

up:
	$(COMPOSE) up -d --build --wait

down:
	$(COMPOSE) down

api:
	cd backend && set -a && . ../.env && set +a && uv run uvicorn app.main:app --reload --port $(API_PORT)

web:
	cd web && pnpm dev

# Everything for a real run on this Mac (docs/DEMO.md); LAN=1 also serves the phone.
local:
	infra/local.sh start

local-stop:
	infra/local.sh stop

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
