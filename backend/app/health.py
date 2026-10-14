import boto3
import psycopg
from botocore.config import Config
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy.engine import make_url

from .settings import get_settings

router = APIRouter()
TIMEOUT_S = 2


def check_db() -> str:
    u = make_url(get_settings().database_url)
    try:
        with psycopg.connect(
            host=u.host,
            port=u.port,
            user=u.username,
            password=u.password,
            dbname=u.database,
            connect_timeout=TIMEOUT_S,
        ) as conn:
            conn.execute("SELECT 1")
        return "ok"
    except Exception as e:
        return f"error: {e}".strip()


def check_storage() -> str:
    s = get_settings()
    try:
        boto3.client(
            "s3",
            endpoint_url=s.s3_endpoint,
            aws_access_key_id=s.s3_access_key,
            aws_secret_access_key=s.s3_secret_key,
            region_name="us-east-1",
            config=Config(
                connect_timeout=TIMEOUT_S, read_timeout=TIMEOUT_S, retries={"max_attempts": 0}
            ),
        ).list_buckets()
        return "ok"
    except Exception as e:
        return f"error: {e}".strip()


@router.get("/health")
def health() -> JSONResponse:
    db, storage = check_db(), check_storage()
    ok = db == "ok" and storage == "ok"
    return JSONResponse(
        {"status": "ok" if ok else "degraded", "db": db, "storage": storage},
        status_code=200 if ok else 503,
    )
