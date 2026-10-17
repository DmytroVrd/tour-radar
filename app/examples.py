"""Recorded agent runs for the one-click examples.

Each file in app/examples/ is the full event stream of a real run (live Qloo
data, Gemini agent), captured with scripts/record_example.py. Replaying it
lets a visitor see the whole experience instantly without spending API quota.
"""

from __future__ import annotations

import json
import time
from functools import lru_cache
from pathlib import Path
from typing import Iterator

EXAMPLES_DIR = Path(__file__).resolve().parent / "examples"
REPLAY_SECONDS = 13.0  # whole replay squeezed into roughly this long
MAX_GAP_S = 2.2


@lru_cache
def load_example(slug: str) -> dict | None:
    path = EXAMPLES_DIR / f"{slug}.json"
    if not path.is_file() or not slug.replace("-", "").isalnum():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def list_examples() -> list[dict]:
    out = []
    for path in sorted(EXAMPLES_DIR.glob("*.json")):
        data = load_example(path.stem)
        if data:
            out.append({"slug": path.stem, **data["meta"]})
    out.sort(key=lambda e: e.get("order", 99))
    return out


def replay(slug: str) -> Iterator[dict]:
    data = load_example(slug)
    if data is None:
        yield {"type": "error", "message": "Unknown example."}
        return
    events = data["events"]
    total = max((e.get("t", 0) for e in events), default=0) / 1000
    scale = min(1.0, REPLAY_SECONDS / total) if total else 1.0
    prev = 0
    for ev in events:
        gap = (ev.get("t", prev) - prev) / 1000 * scale
        prev = ev.get("t", prev)
        if gap > 0:
            time.sleep(min(gap, MAX_GAP_S))
        out = dict(ev)
        if out["type"] == "run":
            out["replay"] = {"recorded_at": data["meta"].get("recorded_at")}
        yield out
