from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    database_url: str = Field(
        default="postgresql+psycopg://qarau:qarau_dev@localhost:5433/qarau",
        validation_alias=AliasChoices("QARAU_DATABASE_URL", "DATABASE_URL"),
    )
    # Object storage (ADR 0024). The compose .env's un-prefixed S3_* names still work for the
    # endpoint and keys; the bucket is QARAU_S3_BUCKET only: empty = local files only.
    qarau_s3_endpoint: str = Field(
        default="http://localhost:7070",
        validation_alias=AliasChoices("QARAU_S3_ENDPOINT", "S3_ENDPOINT"),
    )
    qarau_s3_access_key: str = Field(
        default="", validation_alias=AliasChoices("QARAU_S3_ACCESS_KEY", "S3_ACCESS_KEY")
    )
    qarau_s3_secret_key: str = Field(
        default="", validation_alias=AliasChoices("QARAU_S3_SECRET_KEY", "S3_SECRET_KEY")
    )
    qarau_s3_region: str = "us-east-1"
    qarau_s3_bucket: str = ""
    # How /api/runs/{id}/manifest.json and /files/{path} answer when the file is not in the local
    # cache: `stream` the object through the API (default, works with any CORS setup) or
    # `redirect` (302 to a signed URL on the bucket; the bucket needs CORS for the web origin).
    qarau_files_mode: Literal["stream", "redirect"] = "stream"
    qarau_signed_url_ttl_s: int = 300
    qarau_runs_dir: str = "../data/runs"
    # Job queue: Valkey (ADR 0012) over the Redis protocol (hence the redis:// scheme the
    # redis-py client expects); RQ queue `qarau`. Compose sets redis://valkey:6379/0.
    valkey_url: str = "redis://localhost:6379/0"
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
