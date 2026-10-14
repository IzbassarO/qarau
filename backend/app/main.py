from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import health, runs
from .settings import get_settings

app = FastAPI(title="Qarau Vision API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in get_settings().qarau_cors_origins.split(",") if o.strip()],
    allow_methods=["GET"],
    allow_headers=["Authorization"],
)
app.include_router(health.router)
app.include_router(runs.router)
