from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="Tour Radar")


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "mode": "mock" if settings.qloo_mock else "live"}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
