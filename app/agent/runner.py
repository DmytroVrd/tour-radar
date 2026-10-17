"""Run the tour agent and turn everything it does into a stream of events.

Event types (all JSON objects with a ``type``):
  run         mode, model and data source for this run
  thought     a short sentence the model wrote before acting
  tool_start  id, tool, label, args
  progress    id, text (+ optional city_profile payload)
  tool_end    id, tool, ok, ms, summary, data (payload for the map)
  plan        the validated tour plan
  done        totals
  error       message
"""

from __future__ import annotations

import itertools
import queue
import threading
import time
from typing import Iterator

from app.agent.gemini import LABELS, GeminiAgent
from app.agent.tools import TourTools, pct
from app.config import settings
from app.qloo import QlooClient, get_client


class CountingClient(QlooClient):
    """Counts Qloo requests made during one run (cache hits included)."""

    def __init__(self, inner: QlooClient):
        super().__init__(inner.source)
        self.calls = 0
        self._lock = threading.Lock()
        original = self.source.get

        class _Counted:
            name = inner.source.name

            def get(_self, path, params):  # noqa: N805
                with self._lock:
                    self.calls += 1
                return original(path, params)

        self.source = _Counted()


def agent_mode() -> str:
    """'gemini' whenever a key exists; 'scripted' only for mock/dev runs."""
    if settings.gemini_api_key and settings.agent_mode != "scripted":
        return "gemini"
    if settings.qloo_mock or settings.allow_scripted_agent:
        return "scripted"
    return "unavailable"


def run_agent(artists: list[str], n_dates: int, region: str = "us") -> Iterator[dict]:
    """Stream a full agent run; every event carries ``t``, ms since the start."""
    started = time.perf_counter()
    for ev in _run(artists, n_dates, region, started):
        ev["t"] = int((time.perf_counter() - started) * 1000)
        yield ev


def _run(artists: list[str], n_dates: int, region: str, started: float) -> Iterator[dict]:
    client = CountingClient(get_client())
    tools = TourTools(client, n_dates, region)
    mode = agent_mode()
    ids = itertools.count(1)

    def emit_tool(name: str, args: dict) -> Iterator[dict]:
        call_id = next(ids)
        yield {"type": "tool_start", "id": call_id, "tool": name, "label": LABELS.get(name, name),
               "args": _public_args(args)}
        q: queue.Queue = queue.Queue()
        box: dict = {}

        def work():
            t0 = time.perf_counter()
            box["result"] = tools.call(name, args, lambda ev: q.put(ev))
            box["ms"] = int((time.perf_counter() - t0) * 1000)
            q.put(None)

        threading.Thread(target=work, daemon=True).start()
        while True:
            ev = q.get()
            if ev is None:
                break
            yield {"type": "progress", "id": call_id, **ev}
        res = box["result"]
        yield {"type": "tool_end", "id": call_id, "tool": name, "ok": res.ok, "ms": box["ms"],
               "summary": res.summary, "data": res.ui}
        yield {"type": "_result", "llm": res.llm}

    yield {"type": "run", "mode": mode, "source": "mock" if client.is_mock else "qloo",
           "model": settings.gemini_model if mode == "gemini" else None,
           "request": {"artists": artists, "dates": n_dates, "region": region}}

    try:
        if mode == "gemini":
            agent = GeminiAgent(settings.gemini_api_key, [settings.gemini_model, *settings.gemini_fallback_models])
            request = (f"We sound like {', '.join(artists)}. Plan a US tour with {n_dates} dates. "
                       f"Region id: {region}.")
            yield from agent.run(tools, request, emit_tool)
            llm_calls, model = agent.llm_calls, agent.model_used
        elif mode == "scripted":
            yield from _scripted(tools, artists, n_dates, emit_tool)
            llm_calls, model = 0, None
        else:
            raise RuntimeError("The planning agent is not configured on this server.")
    except Exception as exc:  # surfaced to the UI as a designed error state
        yield {"type": "error", "message": _friendly(exc)}
        return

    if not tools.state.plan:
        yield {"type": "error", "message": "The agent could not finish this plan. Try again or pick an example."}
        return
    yield {"type": "plan", "plan": tools.full_plan()}
    yield {"type": "done", "ms": int((time.perf_counter() - started) * 1000), "qloo_calls": client.calls,
           "llm_calls": llm_calls, "model": model}


def _scripted(tools: TourTools, artists: list[str], n_dates: int, emit_tool) -> Iterator[dict]:
    """Fixed sequence for offline development; never used against live Qloo in production."""

    def call(name, args):
        out = {}
        for ev in emit_tool(name, args):
            if ev["type"] == "_result":
                out = ev["llm"]
            else:
                yield ev
        return out

    yield {"type": "thought", "text": "Resolving the artists first (scripted dev agent)."}
    yield from call("search_artists", {"names": artists})
    if not tools.state.artists:
        return
    yield from call("scan_region", {})
    yield from call("taste_profile", {})
    picks, chosen = [], []
    from app.geo import haversine_km
    from app.route import MIN_GAP_KM
    for c in tools.state.ranked:
        if all(haversine_km(c["lat"], c["lon"], o["lat"], o["lon"]) >= MIN_GAP_KM for o in chosen):
            chosen.append(c)
            picks.append(c["id"])
        if len(picks) == n_dates:
            break
    yield from call("plan_route", {"city_ids": picks})
    route_ids = [c["id"] for c in tools.state.route]
    yield from call("profile_cities", {"city_ids": route_ids})
    brands = (tools.state.profile or {}).get("brands") or [{"name": "a local shop"}]
    stops = []
    for i, cid in enumerate(route_ids):
        prof = tools.state.city_profiles[cid]
        city = tools.state.cities[cid]
        hood = prof["hotspots"][0]["name"] if prof["hotspots"] else city["name"]
        stops.append({
            "city_id": cid,
            "why": f"Taste score {pct(city['score'])}, rank {city['rank']} nationally. "
                   f"The strongest pocket is around {hood}.",
            "neighborhood": hood,
            "venue_ids": [v["id"] for v in prof["venues"][:1]],
            "spot_ids": [p["id"] for p in prof["spots"][:1]],
            "collab": f"Limited tour merch drop with {brands[i % len(brands)]['name']}.",
        })
    yield from call("submit_plan", {"headline": f"{len(stops)} dates where this taste runs hot", "stops": stops})


def _public_args(args: dict) -> dict:
    out = {}
    for k, v in args.items():
        if k == "stops":
            out[k] = f"{len(v)} stops"
        elif isinstance(v, list) and len(v) > 8:
            out[k] = v[:8] + [f"+{len(v) - 8} more"]
        else:
            out[k] = v
    return out


def _friendly(exc: Exception) -> str:
    text = str(exc)
    if "Gemini" in text or "unavailable" in text.lower():
        return "The planning model is busy right now. Try again in a minute, or open one of the examples."
    if "Qloo" in text:
        return "Qloo did not answer in time. Try again in a moment."
    return "Something went wrong while planning. Try again, or open one of the examples."
