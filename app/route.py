"""Tour route planning: choose stops and order them. No API calls."""

from __future__ import annotations

from app.geo import haversine_km

MIN_GAP_KM = 80  # two stops closer than this would split the same audience


def choose_stops(ranked: list[dict], n_dates: int, min_gap_km: float = MIN_GAP_KM) -> list[dict]:
    """Take the strongest cities, skipping ones too close to an already chosen stop."""
    stops: list[dict] = []
    for city in ranked:
        if all(haversine_km(city["lat"], city["lon"], s["lat"], s["lon"]) >= min_gap_km for s in stops):
            stops.append(city)
        if len(stops) == n_dates:
            break
    return stops


def _length(path: list[dict]) -> float:
    return sum(haversine_km(a["lat"], a["lon"], b["lat"], b["lon"]) for a, b in zip(path, path[1:]))


def _nearest_neighbour(stops: list[dict], first: dict) -> list[dict]:
    remaining = [s for s in stops if s is not first]
    path = [first]
    while remaining:
        last = path[-1]
        nxt = min(remaining, key=lambda s: haversine_km(last["lat"], last["lon"], s["lat"], s["lon"]))
        remaining.remove(nxt)
        path.append(nxt)
    return path


def _two_opt(path: list[dict], keep_first: bool = False) -> list[dict]:
    improved = True
    while improved:
        improved = False
        for i in range(1 if keep_first else 0, len(path) - 1):
            for j in range(i + 1, len(path)):
                candidate = path[:i] + path[i:j + 1][::-1] + path[j + 1:]
                if _length(candidate) + 1e-9 < _length(path):
                    path, improved = candidate, True
    return path


def order_route(stops: list[dict], start: dict | None = None) -> list[dict]:
    """Shortest open path we can find quickly: nearest neighbour + 2-opt.

    With no fixed start, every stop is tried as the opening night and the
    result is oriented west to east, which reads naturally on a US map.
    """
    if start is not None:
        return _two_opt(_nearest_neighbour(stops, start), keep_first=True)
    if len(stops) <= 2:
        return sorted(stops, key=lambda s: s["lon"])
    best = min((_two_opt(_nearest_neighbour(stops, s)) for s in stops), key=_length)
    return best if best[0]["lon"] <= best[-1]["lon"] else best[::-1]


def plan_route(ranked: list[dict], n_dates: int) -> dict:
    stops = order_route(choose_stops(ranked, n_dates))
    legs = [round(haversine_km(a["lat"], a["lon"], b["lat"], b["lon"])) for a, b in zip(stops, stops[1:])]
    return {"stops": stops, "legs_km": legs, "total_km": sum(legs)}
