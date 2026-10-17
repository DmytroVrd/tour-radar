"""Tools the tour agent can call.

Every fact a tool returns comes from Qloo (or pure geometry for routing). Each
call yields two views of its result: a compact dict the model reads, and a
richer payload streamed to the browser for the map and trace panel.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

from app.geo import haversine_km
from app.qloo import QlooClient, QlooError
from app.route import MIN_GAP_KM, order_route
from app.taste import city_profile, region_scan, taste_profile

Emit = Callable[[dict], None]


@dataclass
class ToolResult:
    llm: dict  # what the model sees
    ui: dict | None = None  # what the browser receives
    summary: str = ""  # one line for the trace panel
    ok: bool = True


@dataclass
class TourState:
    """Everything Qloo told us during one run, used to validate the final plan."""

    artists: list[dict] = field(default_factory=list)
    cities: dict[str, dict] = field(default_factory=dict)
    ranked: list[dict] = field(default_factory=list)
    profile: dict | None = None
    route: list[dict] = field(default_factory=list)
    legs_km: list[int] = field(default_factory=list)
    city_profiles: dict[str, dict] = field(default_factory=dict)
    plan: dict | None = None

    @property
    def artist_ids(self) -> list[str]:
        return [a["id"] for a in self.artists]


def pct(x: float) -> int:
    return int(round(x * 100))


class TourTools:
    def __init__(self, client: QlooClient, n_dates: int, region: str = "us"):
        self.client = client
        self.n_dates = n_dates
        self.region = region
        self.state = TourState()

    # --- dispatch --------------------------------------------------------------

    def call(self, name: str, args: dict, emit: Emit) -> ToolResult:
        fn = getattr(self, f"tool_{name}", None)
        if fn is None:
            return ToolResult({"error": f"Unknown tool {name}"}, ok=False, summary="Unknown tool")
        try:
            return fn(emit=emit, **args)
        except QlooError as exc:
            return ToolResult({"error": f"Qloo request failed: {exc}"}, ok=False, summary="Qloo request failed")
        except TypeError as exc:
            return ToolResult({"error": f"Bad arguments: {exc}"}, ok=False, summary="Bad arguments")

    # --- tools -----------------------------------------------------------------

    def tool_search_artists(self, names: list[str], emit: Emit) -> ToolResult:
        found, missing = [], []
        for name in [n for n in names if n.strip()][:3]:
            hits = self.client.search(name, take=3)
            if not hits:
                missing.append(name)
                continue
            top = hits[0]
            genres = [t["name"] for t in top.get("tags", []) if "genre" in t.get("id", "")][:3]
            found.append({"query": name, "name": top["name"], "id": top["entity_id"],
                          "popularity": round(top.get("popularity") or 0, 3), "genres": genres})
        self.state.artists = found
        summary = ", ".join(f"{a['name']}" for a in found) or "No matches"
        llm = {"artists": found}
        if missing:
            llm["not_found"] = missing
        return ToolResult(llm, {"artists": found}, f"Resolved {summary}", ok=bool(found))

    def tool_scan_region(self, emit: Emit, artist_ids: list[str] | None = None, region: str = "us") -> ToolResult:
        ids = self._ids(artist_ids)
        emit({"text": "Pulling one nationwide taste heatmap plus a mainstream baseline"})
        scan = region_scan(self.client, ids, self.region)
        self.state.ranked = scan["cities"]
        self.state.cities = {c["id"]: c for c in scan["cities"]}
        top = scan["cities"][:16]
        llm = {
            "method": "taste_score 0-100 = 60% heatmap affinity + 40% lift over a mainstream baseline. "
                      "lift_pts = how many points this taste beats a mainstream audience there.",
            "cities_scored": len(scan["cities"]),
            "top_cities": [{"id": c["id"], "name": f"{c['name']}, {c['state']}", "taste_score": pct(c["score"]),
                            "lift_pts": pct(c["lift"])} for c in top],
        }
        lead = top[0] if top else None
        summary = (f"Scored {len(scan['cities'])} cities from {scan['cell_count']:,} heatmap cells"
                   + (f" · strongest: {lead['name']} {pct(lead['score'])}" if lead else ""))
        return ToolResult(llm, {"cities": scan["cities"], "cells": scan["cells"]}, summary)

    def tool_taste_profile(self, emit: Emit, artist_ids: list[str] | None = None) -> ToolResult:
        prof = taste_profile(self.client, self._ids(artist_ids))
        self.state.profile = prof
        llm = {k: [x["name"] for x in v] for k, v in prof.items()}
        bits = [prof["similar_artists"][0]["name"] if prof["similar_artists"] else None,
                prof["brands"][0]["name"] if prof["brands"] else None]
        summary = "Audience also loves " + ", ".join(b for b in bits if b) if any(bits) else "No profile"
        return ToolResult(llm, {"profile": prof}, summary)

    def tool_plan_route(self, city_ids: list[str], emit: Emit, start_city_id: str | None = None) -> ToolResult:
        if not self.state.cities:
            return ToolResult({"error": "Call scan_region first."}, ok=False, summary="Needs a scan first")
        unknown = [c for c in city_ids if c not in self.state.cities]
        chosen = []
        for cid in dict.fromkeys(c for c in city_ids if c in self.state.cities):
            city = self.state.cities[cid]
            if all(haversine_km(city["lat"], city["lon"], o["lat"], o["lon"]) >= MIN_GAP_KM for o in chosen):
                chosen.append(city)
        too_close = [c for c in city_ids if c in self.state.cities and self.state.cities[c] not in chosen]
        if len(chosen) < 2:
            return ToolResult({"error": "Pick at least two scanned cities.", "unknown_ids": unknown},
                              ok=False, summary="Not enough valid cities")
        start = self.state.cities.get(start_city_id) if start_city_id else None
        ordered = order_route(chosen, start=start if start in chosen else None)
        legs = [round(haversine_km(a["lat"], a["lon"], b["lat"], b["lon"])) for a, b in zip(ordered, ordered[1:])]
        self.state.route, self.state.legs_km = ordered, legs
        llm = {
            "order": [c["id"] for c in ordered],
            "legs_km": legs,
            "total_km": sum(legs),
        }
        if unknown:
            llm["ignored_unknown_ids"] = unknown
        if too_close:
            llm["dropped_too_close_to_another_stop"] = too_close
        ui = {"route": {"stops": ordered, "legs_km": legs, "total_km": sum(legs)}}
        summary = f"{len(ordered)} stops, {sum(legs):,} km · {ordered[0]['name']} → {ordered[-1]['name']}"
        return ToolResult(llm, ui, summary)

    def tool_profile_cities(self, city_ids: list[str], emit: Emit) -> ToolResult:
        ids = [c for c in dict.fromkeys(city_ids) if c in self.state.cities][:12]
        if not ids:
            return ToolResult({"error": "No valid city ids. Use ids from scan_region."}, ok=False,
                              summary="No valid cities")
        artist_ids = self.state.artist_ids

        def work(cid: str) -> tuple[str, dict]:
            prof = city_profile(self.client, artist_ids, self.state.cities[cid])
            emit({"text": f"Mapped {prof['city']['name']}: {_hotspot_line(prof)}", "city_profile": prof})
            return cid, prof

        with ThreadPoolExecutor(max_workers=3) as pool:
            for cid, prof in pool.map(work, ids):
                self.state.city_profiles[cid] = prof

        llm = {"cities": [_llm_city(self.state.city_profiles[c]) for c in ids]}
        venues = sum(len(self.state.city_profiles[c]["venues"]) for c in ids)
        return ToolResult(llm, None, f"Mapped neighbourhoods in {len(ids)} cities · {venues} venues found")

    def tool_submit_plan(self, headline: str, stops: list[dict], emit: Emit) -> ToolResult:
        errors, clean = [], []
        route_ids = [c["id"] for c in self.state.route]
        for s in stops:
            cid = s.get("city_id")
            prof = self.state.city_profiles.get(cid)
            if cid not in route_ids:
                errors.append(f"{cid} is not on the planned route")
                continue
            if not prof:
                errors.append(f"{cid} was not profiled; call profile_cities for it")
                continue
            venue_map = {v["id"]: v for v in prof["venues"]}
            venue_ids = [v for v in s.get("venue_ids", []) if v in venue_map][:2]
            bad = [v for v in s.get("venue_ids", []) if v not in venue_map]
            if bad:
                errors.append(f"{cid}: venue ids {bad} are not among its Qloo venues")
            if not venue_ids and prof["venues"]:
                venue_ids = [prof["venues"][0]["id"]]
            spot_map = {p["id"]: p for p in prof["spots"]}
            clean.append({
                "city_id": cid,
                "why": str(s.get("why", "")).strip()[:600],
                "neighborhood": s.get("neighborhood") or (prof["hotspots"][0]["name"] if prof["hotspots"] else None),
                "venue_ids": venue_ids,
                "spot_ids": [p for p in s.get("spot_ids", []) if p in spot_map][:2],
                "collab": str(s.get("collab", "")).strip()[:240] or None,
            })
        missing = [c for c in route_ids if c not in {s["city_id"] for s in clean}]
        if missing:
            errors.append(f"Missing stops for: {missing}")
        if errors:
            return ToolResult({"ok": False, "errors": errors}, ok=False, summary=f"Plan rejected: {errors[0]}")
        order = {cid: i for i, cid in enumerate(route_ids)}
        clean.sort(key=lambda s: order[s["city_id"]])
        self.state.plan = {"headline": str(headline).strip()[:200], "stops": clean}
        return ToolResult({"ok": True}, None, "Tour plan validated against Qloo data")

    # --- helpers ---------------------------------------------------------------

    def _ids(self, artist_ids: list[str] | None) -> list[str]:
        known = self.state.artist_ids
        ids = [a for a in (artist_ids or []) if a in known] or known
        if not ids:
            raise QlooError("No resolved artists yet; call search_artists first.")
        return ids

    def full_plan(self) -> dict:
        """Plan enriched with the Qloo facts the browser needs to render it."""
        st = self.state
        stops = []
        for i, s in enumerate(st.plan["stops"] if st.plan else []):
            prof = st.city_profiles[s["city_id"]]
            city = st.cities[s["city_id"]]
            venues = {v["id"]: v for v in prof["venues"]}
            spots = {p["id"]: p for p in prof["spots"]}
            stops.append({
                **s,
                "order": i + 1,
                "city": {k: city[k] for k in ("id", "name", "state", "lat", "lon", "score", "affinity", "lift", "rank")},
                "venues": [venues[v] for v in s["venue_ids"]],
                "spots": [spots[p] for p in s["spot_ids"]],
                "leg_km": st.legs_km[i - 1] if i else 0,
            })
        return {
            "headline": st.plan["headline"] if st.plan else "",
            "artists": st.artists,
            "profile": st.profile,
            "total_km": sum(st.legs_km),
            "stops": stops,
        }


def _hotspot_line(prof: dict) -> str:
    names = [h["name"] for h in prof["hotspots"] if h["name"]]
    return ", ".join(names[:2]) if names else f"{len(prof['venues'])} venues"


def _llm_city(prof: dict) -> dict:
    return {
        "city_id": prof["city"]["id"],
        "hotspots": [{"neighborhood": h["name"], "taste": pct(h["taste"])} for h in prof["hotspots"]],
        "venues": [{"id": v["id"], "name": v["name"], "neighborhood": v["neighborhood"],
                    "affinity": pct(v["affinity"]), "km_to_hotspot": v.get("km_to_hotspot")}
                   for v in prof["venues"][:6]],
        "local_spots": [{"id": p["id"], "name": p["name"], "neighborhood": p["neighborhood"],
                         "kind": (p["tags"] or [None])[0]} for p in prof["spots"][:4]],
    }


def timed(fn: Callable[[], Any]) -> tuple[Any, int]:
    t0 = time.perf_counter()
    out = fn()
    return out, int((time.perf_counter() - t0) * 1000)
