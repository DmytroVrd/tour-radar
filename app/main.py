import json
import logging
from pathlib import Path
from typing import Iterator

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.agent.runner import agent_mode, run_agent
from app.config import settings
from app.examples import list_examples, replay
from app.geo import REGIONS, load_cities
from app.qloo import QlooError, get_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="Tour Radar")


def _source() -> str:
    return "mock" if settings.qloo_mock else "qloo"


def _sse(events: Iterator[dict]) -> StreamingResponse:
    def stream():
        for ev in events:
            yield f"data: {json.dumps(ev, separators=(',', ':'))}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "source": _source(), "agent": agent_mode()}


@app.get("/api/cities")
def cities() -> list[dict]:
    return list(load_cities())


@app.get("/api/examples")
def examples() -> list[dict]:
    return list_examples()


@app.get("/api/search")
def search(q: str = Query(..., min_length=2, max_length=80)) -> list[dict]:
    """Artist autocomplete backed by Qloo search."""
    try:
        hits = get_client().search(q.strip(), take=6)
    except QlooError as exc:
        raise HTTPException(502, "Search is unavailable right now") from exc
    return [{"id": h["entity_id"], "name": h["name"], "popularity": round(h.get("popularity") or 0, 3),
             "note": h.get("disambiguation") or ""} for h in hits]


@app.get("/api/agent")
def agent(
    artists: str = Query("", max_length=200),
    dates: int = Query(8, ge=3, le=12),
    region: str = Query("us"),
    example: str = Query("", max_length=40),
) -> StreamingResponse:
    """Stream an agent run as server-sent events (or replay a recorded example)."""
    if example:
        return _sse(replay(example))
    if region not in REGIONS:
        raise HTTPException(400, f"Unknown region {region}")
    names = [a.strip() for a in artists.split(",") if a.strip()][:3]
    if not names:
        raise HTTPException(400, "Name at least one artist")
    return _sse(run_agent(names, dates, region))


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
