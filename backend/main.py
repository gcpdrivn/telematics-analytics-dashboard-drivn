"""FastAPI app: `uv run uvicorn backend.main:app --reload`"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend import data_loader
from backend.routers import crosstab, customers, kpi, meta, trajectories, vehicles

app = FastAPI(title="Drivn Telematics API")

_dev_origins = os.environ.get(
    "BACKEND_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_dev_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(kpi.router, prefix="/api")
app.include_router(customers.router, prefix="/api")
app.include_router(crosstab.router, prefix="/api")
app.include_router(vehicles.router, prefix="/api")
app.include_router(trajectories.router, prefix="/api")
app.include_router(meta.router, prefix="/api")


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/admin/refresh-cache")
def refresh_cache():
    """Manually invalidate the in-process BigQuery table cache -- call this
    (or wait for the TTL) after running the ingestion pipeline to pick up
    new data immediately."""
    data_loader.refresh()
    return {"status": "cache invalidated"}
