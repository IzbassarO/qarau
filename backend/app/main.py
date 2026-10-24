from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import crud, findings, health, inspections, runs, upload
from .settings import get_settings

app = FastAPI(title="Qarau Vision API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in get_settings().qarau_cors_origins.split(",") if o.strip()],
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["Authorization", "Content-Type"],
)
app.include_router(health.router)
app.include_router(runs.router)
app.include_router(crud.router)
app.include_router(inspections.router)
app.include_router(upload.router)
app.include_router(findings.router)
