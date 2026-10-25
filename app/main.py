import json
import logging
import queue
import threading
from pathlib import Path
from typing import Iterator

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.agent.email import draft_booking_email
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


HEARTBEAT_SECONDS = 5
_DONE = object()


def _sse(events: Iterator[dict]) -> StreamingResponse:
    """Stream events, with a comment line whenever the agent is quiet.

    A model turn can take 10+ seconds with nothing to send; hosting proxies close
    streams that stay silent that long. EventSource ignores comment lines.
    """
    def stream():
        q: queue.Queue = queue.Queue()
        stop = threading.Event()

        def pump():
            try:
                for ev in events:
                    if stop.is_set():
                        break
                    q.put(ev)
            except Exception:
                logging.getLogger(__name__).exception("Event stream failed")
                q.put({"type": "error", "message": "The agent stopped unexpectedly. Try again or open an example."})
            finally:
                close = getattr(events, "close", None)
                if close:
                    close()
                q.put(_DONE)

        threading.Thread(target=pump, daemon=True).start()
        try:
            while True:
                try:
                    ev = q.get(timeout=HEARTBEAT_SECONDS)
                except queue.Empty:
                    yield ": keep-alive\n\n"
                    continue
                if ev is _DONE:
                    break
                yield f"data: {json.dumps(ev, separators=(',', ':'))}\n\n"
        finally:
            stop.set()  # the browser went away: let the agent stop at its next step

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
def search(q: str = Query("", max_length=80)) -> list[dict]:
    """Artist autocomplete backed by Qloo search."""
    if len(q.strip()) < 2:
        return []
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


class EmailRequest(BaseModel):
    artists: list[str] = Field(..., min_length=1, max_length=3)
    city: str = Field(..., max_length=80)
    venue: str = Field(..., max_length=120)
    venue_address: str = Field("", max_length=200)
    neighborhood: str = Field("", max_length=80)
    taste_score: int = Field(0, ge=0, le=100)
    stop_number: int = Field(1, ge=1, le=20)
    total_stops: int = Field(1, ge=1, le=20)
    why: str = Field("", max_length=600)


@app.post("/api/email")
def email(req: EmailRequest) -> dict:
    """Draft a booking inquiry for one venue on the tour."""
    try:
        return draft_booking_email(req.model_dump())
    except Exception as exc:
        logging.getLogger(__name__).exception("Email draft failed")
        raise HTTPException(503, "The drafting model is busy. Try again in a minute.") from exc


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
