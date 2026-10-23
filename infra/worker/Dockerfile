# Pipeline worker (S1, ADR 0022): an RQ worker (backend/) that runs the workflow steps as
# `python -m qarau_pipeline step …` children in the pipeline/ environment. Python 3.12 + ffmpeg;
# both uv projects installed from their lock files. linux/amd64 only: pycolmap 4.2.1 ships no
# linux/arm64 wheel (on Apple silicon Docker runs it under emulation; fine for dev).
# Build contexts (infra/docker-compose.yml): `pipeline` and `backend` are the two project dirs.

FROM --platform=linux/amd64 python:3.12-slim
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 UV_LINK_MODE=copy
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv

WORKDIR /app
# Dependencies first (cached until a lock file changes), then the code.
COPY --from=pipeline pyproject.toml uv.lock /app/pipeline/
COPY --from=backend pyproject.toml uv.lock /app/backend/
RUN cd /app/pipeline && uv sync --frozen --no-dev --no-install-project \
    && cd /app/backend && uv sync --frozen --no-dev --no-install-project
COPY --from=pipeline qarau_pipeline /app/pipeline/qarau_pipeline
COPY --from=pipeline config.yaml /app/pipeline/config.yaml
COPY --from=backend app /app/backend/app
COPY --from=backend alembic /app/backend/alembic
COPY --from=backend alembic.ini /app/backend/alembic.ini
RUN cd /app/pipeline && uv sync --frozen --no-dev && cd /app/backend && uv sync --frozen --no-dev

WORKDIR /app/backend
ENV QARAU_PIPELINE_DIR=/app/pipeline QARAU_RUNS_DIR=/data/runs
# `rq worker` reads the Valkey URL from QUEUE_URL (RQ_REDIS_URL); the queue name is `qarau`.
CMD ["sh", "-c", "exec uv run --no-sync rq worker --url \"$QUEUE_URL\" qarau"]
