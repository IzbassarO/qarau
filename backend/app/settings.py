from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    database_url: str = "postgresql+psycopg://qarau:qarau_dev@localhost:5433/qarau"
    s3_endpoint: str = "http://localhost:7070"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket: str = "qarau"
    qarau_runs_dir: str = "../data/runs"
    qarau_api_token: str = ""
    qarau_cors_origins: str = "http://localhost:5173"


@lru_cache
def get_settings() -> Settings:
    return Settings()
