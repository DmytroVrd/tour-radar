from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.geo import REGIONS, load_cities
from app.qloo import QlooError, get_client
from app.route import plan_route
from app.taste import score_cities

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="Tour Radar")


def _mode() -> str:
    return "mock" if settings.qloo_mock else "live"


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "mode": _mode()}


@app.get("/api/cities")
def cities() -> list[dict]:
    return list(load_cities())


@app.get("/api/scan")
def scan(
    artists: str = Query(..., description="Comma-separated artist names"),
    dates: int = Query(8, ge=2, le=20),
    region: str = Query("us"),
) -> dict:
    """Direct (non-agent) pipeline: resolve artists, rank cities, plan a route."""
    if region not in REGIONS:
        raise HTTPException(400, f"Unknown region {region}")
    client = get_client()
    try:
        resolved = []
        for name in [a.strip() for a in artists.split(",") if a.strip()][:3]:
            hits = client.search(name)
            if hits:
                resolved.append({"name": hits[0]["name"], "id": hits[0]["entity_id"]})
        if not resolved:
            raise HTTPException(404, "No matching artists found")
        ids = [a["id"] for a in resolved]
        cells = client.region_heatmap(ids, REGIONS[region]["wkt"])
        ranked = score_cities(cells)
    except QlooError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {
        "mode": _mode(),
        "artists": resolved,
        "region": {"id": region, "bounds": REGIONS[region]["bounds"]},
        "cells": [
            [c["location"]["longitude"], c["location"]["latitude"], round(c["query"]["affinity"], 4)]
            for c in cells
        ],
        "cities": ranked,
        "route": plan_route(ranked, dates),
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
