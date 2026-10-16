"""Development-only Qloo source.

Responses follow the documented shapes (see app/fixtures/README.md) and mimic
one behaviour visible in the docs example: heatmap ``affinity`` is a rank
percentile inside a single response, so cells are only comparable within the
same request. Nothing produced here is real taste data.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import re
from functools import lru_cache
from pathlib import Path

from app.geo import geohash_encode, haversine_km, load_cities

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

VENUE_WORDS = ["Lantern", "Crescent", "Union", "Echo", "Velvet", "Foundry", "Hollow",
               "Signal", "Juniper", "Atlas", "Harbor", "Ember", "Meridian", "Parlor"]
VENUE_KINDS = ["Room", "Hall", "Ballroom", "Theatre", "Social Club", "Lounge", "Music Hall"]
BRANDS = ["Patagonia", "Aesop", "Blue Bottle Coffee", "Carhartt WIP", "Everlane",
          "Dr. Martens", "Levi's", "Allbirds", "Stumptown Coffee", "Polaroid"]


def _seed(*parts: object) -> int:
    return int(hashlib.md5("|".join(map(str, parts)).encode()).hexdigest()[:12], 16)


def _uuid(*parts: object) -> str:
    h = hashlib.md5("|".join(map(str, parts)).encode()).hexdigest().upper()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


@lru_cache
def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _split(value: object) -> list[str]:
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return [v for v in str(value).split(",") if v]


def _rank_percentile(scores: list[float]) -> list[float]:
    """Map raw scores to 1.0 … ~0 by rank, like the docs example."""
    n = len(scores)
    order = sorted(range(n), key=lambda i: -scores[i])
    out = [0.0] * n
    for rank, i in enumerate(order):
        out[i] = 1 - rank / n
    return out


class MockSource:
    name = "mock"

    def get(self, path: str, params: dict) -> dict:
        if path == "/search":
            data = self._search(str(params.get("query", "")), int(params.get("take", 5)))
        elif path == "/v2/insights":
            data = self._insights(params)
        else:
            data = {"success": False, "results": [], "duration": 0}
        data["_source"] = "mock"
        return data

    # --- /search -------------------------------------------------------------

    def _search(self, query: str, take: int) -> dict:
        key = query.strip().lower()
        fixtures = _fixture("search.json")
        if key in fixtures:
            return json.loads(json.dumps(fixtures[key]))
        for name, payload in fixtures.items():
            if key and (key in name or name in key):
                return json.loads(json.dumps(payload))
        if not key:
            return {"success": True, "duration": 3, "results": []}
        rng = random.Random(_seed("search", key))
        title = " ".join(w.capitalize() for w in query.split())
        return {
            "success": True,
            "duration": rng.randint(25, 60),
            "results": [{
                "name": title,
                "entity_id": "MOCK-ART-" + _uuid("artist", key),
                "types": ["urn:entity:artist"],
                "popularity": round(rng.uniform(0.55, 0.9), 4),
                "disambiguation": "Artist",
                "properties": {"short_description": "Artist"},
                "tags": [],
            }][:take],
        }

    # --- /v2/insights ----------------------------------------------------------

    def _insights(self, params: dict) -> dict:
        ftype = params.get("filter.type")
        ids = _split(params.get("signal.interests.entities"))
        if ftype == "urn:heatmap":
            if params.get("filter.location.query"):
                return self._city_heatmap(ids, str(params["filter.location.query"]))
            return self._region_heatmap(ids)
        if ftype == "urn:entity:place":
            return self._places(ids, str(params.get("filter.location.query", "")),
                                int(params.get("take", 10)))
        return self._related(ids, str(ftype), int(params.get("take", 5)))

    def _city_weights(self, ids: list[str]) -> dict[str, float]:
        profiles = _fixture("taste_profiles.json")
        cities = load_cities()
        total = {c["id"]: 0.0 for c in cities}
        for eid in ids or ["anonymous"]:
            profile = profiles.get(eid)
            rng = random.Random(_seed("profile", eid))
            for c in cities:
                base = profile.get(c["id"]) if profile else None
                if base is None:
                    base = rng.uniform(0.15, 0.55) if profile else rng.uniform(0.2, 1.0)
                total[c["id"]] += base
        n = max(len(ids), 1)
        return {k: v / n for k, v in total.items()}

    def _region_heatmap(self, ids: list[str]) -> dict:
        weights = self._city_weights(ids)
        rng = random.Random(_seed("region", *ids))
        cells: list[tuple[float, float, float, float]] = []
        for c in load_cities():
            w = weights[c["id"]]
            for _ in range(7):
                lat = c["lat"] + rng.gauss(0, 0.18)
                lon = c["lon"] + rng.gauss(0, 0.22)
                cells.append((lat, lon, w * rng.uniform(0.85, 1.0), rng.uniform(0.4, 1.0)))
        for _ in range(140):
            lat, lon = rng.uniform(26, 48), rng.uniform(-123, -70)
            cells.append((lat, lon, rng.uniform(0.0, 0.35), rng.uniform(0.0, 0.4)))
        return self._heatmap_payload(cells, precision=4, locality=None)

    def _city_heatmap(self, ids: list[str], location: str) -> dict:
        city = _match_city(location)
        if city is None:
            return {"success": True, "results": {"heatmap": []}, "query": {}, "duration": 12}
        rng = random.Random(_seed("city", city["id"], *ids))
        hotspots = _hotspots(city, ids)
        cells = []
        step_lat, step_lon = 0.0055, 0.011
        for i in range(-26, 27):
            for j in range(-20, 21):
                lat = city["lat"] + i * step_lat
                lon = city["lon"] + j * step_lon
                d_center = haversine_km(lat, lon, city["lat"], city["lon"])
                if d_center > 16:
                    continue
                score = 0.05 * math.exp(-d_center / 9)
                for h in hotspots:
                    d = haversine_km(lat, lon, h["lat"], h["lon"])
                    score += h["strength"] * math.exp(-(d ** 2) / (2 * h["radius_km"] ** 2))
                score *= rng.uniform(0.9, 1.1)
                pop = min(1.0, 0.25 + 0.75 * math.exp(-d_center / 6) * rng.uniform(0.7, 1.0))
                cells.append((lat, lon, score, pop))
        locality = {
            "entity_id": _uuid("locality", city["id"]),
            "name": city["name"],
            "subtype": "urn:entity:locality",
            # The docs example lists lng/lat swapped under these keys; mirror it.
            "location": {"lat": city["lon"], "lng": city["lat"]},
            "popularity": 1,
            "disambiguation": f"{city['name']}, {city['state']}, United States of America",
        }
        return self._heatmap_payload(cells, precision=6, locality=locality)

    @staticmethod
    def _heatmap_payload(cells, precision: int, locality: dict | None) -> dict:
        affinity = _rank_percentile([c[2] for c in cells])
        jitter = random.Random(len(cells))
        items = []
        for (lat, lon, _, pop), aff in zip(cells, affinity):
            items.append({
                "location": {
                    "latitude": round(lat, 6),
                    "longitude": round(lon, 6),
                    "geohash": geohash_encode(lat, lon, precision),
                },
                "query": {
                    "affinity": aff,
                    "affinity_rank": max(0.0, min(1.0, aff - jitter.uniform(0, 0.004))),
                    "popularity": pop,
                },
            })
        items.sort(key=lambda it: -it["query"]["affinity"])
        query = {"localities": {"filter": locality}} if locality else {}
        return {"success": True, "results": {"heatmap": items}, "query": query, "duration": 61}

    def _places(self, ids: list[str], location: str, take: int) -> dict:
        city = _match_city(location)
        if city is None:
            return {"success": True, "results": {"entities": []}, "duration": 9}
        rng = random.Random(_seed("places", city["id"], *ids))
        hotspots = _hotspots(city, ids)
        words = VENUE_WORDS[:]
        rng.shuffle(words)
        entities = []
        for k in range(min(take, len(words))):
            h = hotspots[k % len(hotspots)]
            lat = h["lat"] + rng.gauss(0, h["radius_km"] / 220)
            lon = h["lon"] + rng.gauss(0, h["radius_km"] / 170)
            name = f"The {words[k]} {rng.choice(VENUE_KINDS)}"
            entities.append({
                "name": name,
                "entity_id": "MOCK-PLC-" + _uuid("place", city["id"], name),
                "type": "urn:entity",
                "subtype": "urn:entity:place",
                "properties": {
                    "address": f"{rng.randint(100, 2999)} {rng.choice(['Main', 'Division', 'Union', 'Burnside', 'Grand', 'Mercer'])} St, {city['name']}, {city['state']}",
                    "business_rating": round(rng.uniform(4.1, 4.9), 1),
                    "is_closed": False,
                },
                "location": {"lat": round(lat, 6), "lon": round(lon, 6),
                             "geohash": geohash_encode(lat, lon, 9)},
                "popularity": round(rng.uniform(0.6, 0.98), 4),
                "query": {"affinity": round(0.99 - k * rng.uniform(0.015, 0.04), 4)},
                "tags": [{"id": "urn:tag:category:place:music_venue", "name": "Music Venue",
                          "type": "urn:tag:category:place"}],
                "disambiguation": f"{city['name']}, {city['state']}",
            })
        return {"success": True, "results": {"entities": entities}, "duration": 47}

    def _related(self, ids: list[str], ftype: str, take: int) -> dict:
        rng = random.Random(_seed("related", ftype, *ids))
        names = BRANDS[:]
        rng.shuffle(names)
        subtype = ftype if ftype.startswith("urn:entity:") else "urn:entity:brand"
        entities = [{
            "name": n,
            "entity_id": "MOCK-ENT-" + _uuid(subtype, n),
            "type": "urn:entity",
            "subtype": subtype,
            "properties": {},
            "popularity": round(rng.uniform(0.5, 0.95), 4),
            "query": {"affinity": round(0.98 - i * 0.03, 4)},
            "tags": [],
        } for i, n in enumerate(names[:take])]
        return {"success": True, "results": {"entities": entities}, "duration": 33}


def _match_city(location: str) -> dict | None:
    q = re.sub(r"[^a-z ]", " ", location.lower()).split()
    text = " ".join(q)
    for c in sorted(load_cities(), key=lambda c: -len(c["name"])):
        if c["name"].lower().replace(".", "") in text.replace(".", ""):
            return c
    return None


def _hotspots(city: dict, ids: list[str]) -> list[dict]:
    rng = random.Random(_seed("hotspots", city["id"], *ids))
    spots = []
    for k in range(3):
        angle = rng.uniform(0, 2 * math.pi)
        dist_km = rng.uniform(1.5, 7.5) if k else rng.uniform(0.8, 3.0)
        spots.append({
            "lat": city["lat"] + (dist_km / 111) * math.sin(angle),
            "lon": city["lon"] + (dist_km / (111 * math.cos(math.radians(city["lat"])))) * math.cos(angle),
            "strength": 1.0 - k * 0.25,
            "radius_km": rng.uniform(0.9, 1.8),
        })
    return spots
