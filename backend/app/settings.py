from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    database_url: str = Field(
        default="postgresql+psycopg://qarau:qarau_dev@localhost:5433/qarau",
        validation_alias=AliasChoices("QARAU_DATABASE_URL", "DATABASE_URL"),
    )
    s3_endpoint: str = "http://localhost:7070"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket: str = "qarau"
    qarau_runs_dir: str = "../data/runs"
    # Job queue: Valkey (ADR 0012) over the Redis protocol; RQ queue `qarau`.
    queue_url: str = "redis://localhost:6379/0"
    # The pipeline checkout whose `.venv/bin/python -m qarau_pipeline step …` the worker spawns.
    qarau_pipeline_dir: str = "../pipeline"
    # Shared read-only token (M1). Empty = open dev mode: reads need no token, writes always do.
    qarau_api_token: str = ""
    qarau_cors_origins: str = "http://localhost:5173"
    # `python -m app.seed` runs only with QARAU_ENV=dev and these three tokens set.
    qarau_env: str = ""
    qarau_dev_admin_token: str = ""
    qarau_dev_engineer_token: str = ""
    qarau_dev_viewer_token: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
