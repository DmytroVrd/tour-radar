"""Turn raw Qloo responses into tour-planning facts.

Heatmap ``affinity`` is a rank percentile inside one response and, inside a
city, it tracks overall activity closely (Spearman ~0.95 with ``popularity``).
To surface *taste* rather than population, every heatmap is compared with a
baseline heatmap for a broad mainstream audience over the same area:

    lift = affinity(artist) - affinity(baseline)      per geohash cell

City scores blend the artist's own affinity with that lift so a big city does
not win by default and a tiny one does not win on noise alone.
"""

from __future__ import annotations

import math
import statistics
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

from app.geo import REGIONS, haversine_km, load_cities, reverse_neighborhood
from app.qloo import QlooClient
from app.qloo.client import LIVE_MUSIC_VENUE, LOCAL_SPOT_TAGS

BASELINE_ARTISTS = ["Taylor Swift", "Drake", "Morgan Wallen", "Bad Bunny", "Ed Sheeran"]
CITY_RADIUS_KM = 40
AFFINITY_WEIGHT = 0.6

STATE_NAMES = {
    "AZ": "Arizona", "CA": "California", "CO": "Colorado", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "ID": "Idaho", "IL": "Illinois", "LA": "Louisiana",
    "MA": "Massachusetts", "MD": "Maryland", "MI": "Michigan", "MN": "Minnesota", "MO": "Missouri",
    "NC": "North Carolina", "NE": "Nebraska", "NM": "New Mexico", "NV": "Nevada", "NY": "New York",
    "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VA": "Virginia", "VT": "Vermont",
    "WA": "Washington", "WI": "Wisconsin",
}


def blend(affinity: float, lift: float) -> float:
    return AFFINITY_WEIGHT * affinity + (1 - AFFINITY_WEIGHT) * max(0.0, min(1.0, lift + 0.5))


def location_query(city: dict) -> str:
    return f"{city['name']}, {STATE_NAMES.get(city['state'], city['state'])}"


@lru_cache(maxsize=4)
def _baseline_ids(client: QlooClient) -> tuple[str, ...]:
    ids = []
    for name in BASELINE_ARTISTS:
        hits = client.search(name, take=1)
        if hits:
            ids.append(hits[0]["entity_id"])
    return tuple(ids)


def baseline_ids(client: QlooClient) -> list[str]:
    """Qloo IDs of a broad mainstream audience, resolved once per client."""
    return list(_baseline_ids(client))


def _cell_map(cells: list[dict]) -> dict[str, float]:
    return {c["location"]["geohash"]: c["query"]["affinity"] for c in cells}


# --- region ------------------------------------------------------------------------


def score_cities(cells: list[dict], baseline: dict[str, float] | None = None) -> list[dict]:
    """Score every candidate city from region-wide heatmap cells."""
    baseline = baseline or {}
    ranked = []
    for city in load_cities():
        near = [
            c for c in cells
            if haversine_km(city["lat"], city["lon"], c["location"]["latitude"],
                            c["location"]["longitude"]) <= CITY_RADIUS_KM
        ]
        if len(near) < 2:
            continue
        aff = statistics.mean(c["query"]["affinity"] for c in near)
        lift = statistics.mean(
            c["query"]["affinity"] - baseline.get(c["location"]["geohash"], 0.5) for c in near
        ) if baseline else 0.0
        ranked.append({
            **city,
            "score": round(blend(aff, lift), 4),
            "affinity": round(aff, 4),
            "lift": round(lift, 4),
            "cells": len(near),
        })
    ranked.sort(key=lambda c: -c["score"])
    for i, c in enumerate(ranked):
        c["rank"] = i + 1
    return ranked


def region_scan(client: QlooClient, entity_ids: list[str], region: str = "us") -> dict:
    """Region-wide taste map: scored cities plus compact cells for the map."""
    wkt = REGIONS[region]["wkt"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        artist_f = pool.submit(client.region_heatmap, entity_ids, wkt)
        base_f = pool.submit(lambda: client.region_heatmap(baseline_ids(client), wkt))
        cells, base_cells = artist_f.result(), base_f.result()
    base = _cell_map(base_cells)
    compact = []
    for c in cells:
        aff = c["query"]["affinity"]
        value = blend(aff, aff - base.get(c["location"]["geohash"], 0.5))
        if value >= 0.35:
            compact.append([round(c["location"]["longitude"], 3), round(c["location"]["latitude"], 3),
                            round(value, 3)])
    return {"cities": score_cities(cells, base), "cells": compact, "cell_count": len(cells)}


# --- city --------------------------------------------------------------------------


def hotspots(points: list[dict], k: int = 3, min_sep_km: float = 1.5, key: str = "taste") -> list[dict]:
    """Pick the k strongest, mutually separated cells."""
    picked: list[dict] = []
    for p in sorted(points, key=lambda p: -p[key]):
        if all(haversine_km(p["lat"], p["lon"], q["lat"], q["lon"]) >= min_sep_km for q in picked):
            picked.append(p)
        if len(picked) == k:
            break
    return picked


def _smooth(cells: list[dict], radius_km: float = 0.8, min_neighbours: int = 3) -> None:
    """Add ``area_taste``: mean taste of cells within ``radius_km``.

    Single cells can spike on noise; a hotspot should be a coherent pocket,
    which is also what the heatmap glow shows. Cells are bucketed on a coarse
    grid so this stays fast for a few thousand cells.
    """
    step = 0.01  # ~1.1 km of latitude per bucket
    grid: dict[tuple[int, int], list[dict]] = {}
    for c in cells:
        grid.setdefault((int(c["lat"] // step), int(c["lon"] // step)), []).append(c)
    for c in cells:
        bi, bj = int(c["lat"] // step), int(c["lon"] // step)
        near = [
            o for di in (-1, 0, 1) for dj in (-1, 0, 1) for o in grid.get((bi + di, bj + dj), ())
            if haversine_km(c["lat"], c["lon"], o["lat"], o["lon"]) <= radius_km
        ]
        mean = sum(o["taste"] for o in near) / len(near)
        # Sparse pockets are shrunk towards the city-wide mean.
        c["area_taste"] = mean if len(near) >= min_neighbours else mean * len(near) / min_neighbours


def _place(e: dict) -> dict:
    props = e.get("properties", {}) or {}
    geo = props.get("geocode", {}) or {}
    loc = e.get("location", {}) or {}
    tags = [t.get("name") for t in e.get("tags", []) if t.get("name")]
    return {
        "id": e.get("entity_id"),
        "name": e.get("name"),
        "address": props.get("address"),
        "neighborhood": geo.get("name"),
        "lat": loc.get("lat"),
        "lon": loc.get("lon"),
        "affinity": round((e.get("query") or {}).get("affinity", 0), 4),
        "popularity": round(e.get("popularity") or 0, 4),
        "website": props.get("website"),
        "phone": props.get("phone"),
        "tags": tags[:5],
    }


def _side_name(city: dict, lat: float, lon: float) -> str:
    """Last-resort label: which part of the city a pocket sits in."""
    if haversine_km(city["lat"], city["lon"], lat, lon) < 2.0:
        return f"Central {city['name']}"
    dy = lat - city["lat"]
    dx = (lon - city["lon"]) * math.cos(math.radians(city["lat"]))
    if abs(dy) >= abs(dx):
        side = "North" if dy > 0 else "South"
    else:
        side = "East" if dx > 0 else "West"
    return f"{side} {city['name']}"


def _nearest_name(lat: float, lon: float, places: list[dict], max_km: float = 4.0) -> str | None:
    best, best_d = None, max_km
    for p in places:
        if p["lat"] is None or not p["neighborhood"]:
            continue
        d = haversine_km(lat, lon, p["lat"], p["lon"])
        if d < best_d:
            best, best_d = p["neighborhood"], d
    return best


def city_profile(client: QlooClient, entity_ids: list[str], city: dict) -> dict:
    """Neighbourhood heat, named hotspots, venues and local spots for one city."""
    query = location_query(city)
    with ThreadPoolExecutor(max_workers=4) as pool:
        heat_f = pool.submit(client.heatmap, entity_ids, query)
        base_f = pool.submit(lambda: client.heatmap(baseline_ids(client), query))
        venues_f = pool.submit(client.places, entity_ids, query, [LIVE_MUSIC_VENUE], 8)
        spots_f = pool.submit(client.places, entity_ids, query, LOCAL_SPOT_TAGS, 12)
        heat, base = heat_f.result(), base_f.result()
        venues = [_place(e) for e in venues_f.result()]
        venue_ids = {v["id"] for v in venues}
        spots = [_place(e) for e in spots_f.result() if e.get("entity_id") not in venue_ids]

    base_map = _cell_map(base["points"])
    cells = []
    for p in heat["points"]:
        gh = p["location"]["geohash"]
        if gh not in base_map:
            continue
        cells.append({
            "lat": p["location"]["latitude"],
            "lon": p["location"]["longitude"],
            "affinity": p["query"]["affinity"],
            "popularity": p["query"]["popularity"],
            "lift": p["query"]["affinity"] - base_map[gh],
        })
        cells[-1]["taste"] = blend(cells[-1]["affinity"], cells[-1]["lift"])
    # Ignore near-empty cells so a single quiet block cannot become a "hotspot".
    active = [c for c in cells if c["popularity"] >= 0.3] or cells
    _smooth(active)
    named = venues + spots
    spots_out: list[dict] = []
    seen_names: set[str | None] = set()
    for h in hotspots(active, k=8, key="area_taste"):
        name = _nearest_name(h["lat"], h["lon"], named)
        if name is None and not client.is_mock:
            name = reverse_neighborhood(h["lat"], h["lon"])
        if name is None:
            name = _side_name(city, h["lat"], h["lon"])
        if name is not None and name in seen_names:
            continue
        seen_names.add(name)
        spots_out.append({
            "rank": len(spots_out) + 1,
            "lat": h["lat"],
            "lon": h["lon"],
            "taste": round(h["area_taste"], 3),
            "lift": round(h["lift"], 3),
            "affinity": round(h["affinity"], 3),
            "name": name,
        })
        if len(spots_out) == 3:
            break
    for v in venues:
        if v["lat"] is not None and spots_out:
            near = min(spots_out, key=lambda s: haversine_km(v["lat"], v["lon"], s["lat"], s["lon"]))
            v["km_to_hotspot"] = round(haversine_km(v["lat"], v["lon"], near["lat"], near["lon"]), 1)

    # The glow uses the same active cells the hotspots come from, so the #1
    # hotspot always sits on the brightest patch.
    tastes = [c["taste"] for c in active]
    lo, hi = (min(tastes), max(tastes)) if tastes else (0.0, 1.0)
    span = max(hi - lo, 1e-6)
    heat_cells = [
        [round(c["lon"], 5), round(c["lat"], 5), round((c["taste"] - lo) / span, 3)]
        for c in active if c["taste"] > lo + 0.45 * span
    ]
    return {
        "city": {k: city[k] for k in ("id", "name", "state", "lat", "lon")},
        "hotspots": spots_out,
        "venues": venues,
        "spots": spots[:6],
        "heat": heat_cells,
        "cell_count": len(cells),
        "taste_vs_population": _rank_corr([c["affinity"] for c in cells], [c["popularity"] for c in cells]),
    }


def _rank_corr(a: list[float], b: list[float]) -> float | None:
    if len(a) < 3:
        return None

    def ranks(v):
        order = sorted(range(len(v)), key=v.__getitem__)
        r = [0.0] * len(v)
        for i, j in enumerate(order):
            r[j] = float(i)
        return r

    try:
        return round(statistics.correlation(ranks(a), ranks(b)), 3)
    except statistics.StatisticsError:
        return None


# --- audience ----------------------------------------------------------------------


def taste_profile(client: QlooClient, entity_ids: list[str]) -> dict:
    """Cross-domain picture of the audience: brands, travel, similar artists."""
    with ThreadPoolExecutor(max_workers=3) as pool:
        brands_f = pool.submit(client.related, entity_ids, "urn:entity:brand", 6)
        dest_f = pool.submit(client.related, entity_ids, "urn:entity:destination", 6,
                             filter__geocode__country_code="US")
        artists_f = pool.submit(client.related, entity_ids, "urn:entity:artist", 10)
        brands, dests, artists = brands_f.result(), dest_f.result(), artists_f.result()

    def slim(items, n):
        return [{"id": e.get("entity_id"), "name": e.get("name"),
                 "affinity": round((e.get("query") or {}).get("affinity", 0), 3)} for e in items[:n]]

    similar = [a for a in artists if a.get("entity_id") not in set(entity_ids)]
    return {"brands": slim(brands, 6), "destinations": slim(dests, 6), "similar_artists": slim(similar, 6)}
