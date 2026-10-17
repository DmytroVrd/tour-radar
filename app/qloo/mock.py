"""Development-only Qloo source.

Responses follow the shapes observed from the real API (see
app/fixtures/README.md), including the rank-percentile behaviour of heatmap
``affinity``. Nothing produced here is real taste data, and every response is
tagged ``"_source": "mock"``.
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
SPOT_KINDS = [("Coffee", "Coffee shop"), ("Records", "Record store"), ("Books", "Bookstore"),
              ("Taproom", "Brewery"), ("Vintage", "Vintage store"), ("Kitchen", "Restaurant")]
NEIGHBORHOODS = ["Old Town", "Riverside", "Eastgate", "Midtown", "Arts District", "Northside",
                 "Hillcrest", "Warehouse Row", "Southpark", "Uptown"]
POOLS = {
    "urn:entity:brand": ["Patagonia", "Aesop", "Blue Bottle Coffee", "Carhartt WIP", "Everlane",
                         "Dr. Martens", "Levi's", "Polaroid", "Rough Trade", "Criterion Collection"],
    "urn:entity:destination": ["Ojai", "Marfa", "Taos", "Asheville", "Burlington", "Big Sur",
                               "Woodstock", "Bend", "Santa Fe", "Provincetown"],
    "urn:entity:artist": ["Julien Baker", "Lucy Dacus", "Big Thief", "Snail Mail", "Soccer Mommy",
                          "Japanese Breakfast", "Mitski", "Adrianne Lenker", "Waxahatchee", "Clairo"],
}


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
    """Map raw scores to 1.0 … 0 by rank, like the real API."""
    n = len(scores)
    order = sorted(range(n), key=lambda i: -scores[i])
    out = [0.0] * n
    for rank, i in enumerate(order):
        out[i] = 1 - rank / max(n - 1, 1)
    return out


class MockSource:
    name = "mock"

    def get(self, path: str, params: dict) -> dict:
        if path == "/search":
            data = self._search(str(params.get("query", "")), int(params.get("take", 5)))
        elif path == "/v2/insights":
            data = self._insights(params)
        else:
            data = {"success": False, "results": []}
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
            return {"success": True, "results": []}
        rng = random.Random(_seed("search", key))
        title = " ".join(w.capitalize() for w in query.split())
        return {
            "success": True,
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
        ftype = str(params.get("filter.type"))
        ids = _split(params.get("signal.interests.entities"))
        if ftype == "urn:heatmap":
            if params.get("filter.location.query"):
                return self._city_heatmap(ids, str(params["filter.location.query"]))
            return self._region_heatmap(ids)
        if ftype == "urn:entity:place":
            return self._places(ids, str(params.get("filter.location.query", "")),
                                _split(params.get("filter.tags")), int(params.get("take", 10)))
        return self._related(ids, ftype, int(params.get("take", 6)))

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
                    base = rng.uniform(0.15, 0.55) if profile else rng.uniform(0.3, 0.7)
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
                lat = c["lat"] + rng.gauss(0, 0.16)
                lon = c["lon"] + rng.gauss(0, 0.2)
                cells.append((lat, lon, w * rng.uniform(0.85, 1.0), rng.uniform(0.5, 1.0)))
        for _ in range(160):
            lat, lon = rng.uniform(26, 48), rng.uniform(-123, -70)
            cells.append((lat, lon, rng.uniform(0.0, 0.35), rng.uniform(0.0, 0.4)))
        return self._heatmap_payload(cells, precision=4, locality=None)

    def _city_heatmap(self, ids: list[str], location: str) -> dict:
        city = _match_city(location)
        if city is None:
            return {"success": True, "results": {"heatmap": []}, "query": {}}
        rng = random.Random(_seed("city", city["id"], *ids))
        hotspots = _hotspots(city, ids)
        cells = []
        step_lat, step_lon = 0.0055, 0.0075
        for i in range(-26, 27):
            for j in range(-26, 27):
                lat = city["lat"] + i * step_lat
                lon = city["lon"] + j * step_lon
                d_center = haversine_km(lat, lon, city["lat"], city["lon"])
                if d_center > 15:
                    continue
                pop = min(1.0, 0.2 + 0.8 * math.exp(-d_center / 6) * rng.uniform(0.7, 1.0))
                score = 0.6 * pop
                for h in hotspots:
                    d = haversine_km(lat, lon, h["lat"], h["lon"])
                    score += h["strength"] * math.exp(-(d ** 2) / (2 * h["radius_km"] ** 2))
                cells.append((lat, lon, score * rng.uniform(0.92, 1.08), pop))
        locality = {
            "entity_id": _uuid("locality", city["id"]),
            "name": city["name"],
            "subtype": "urn:entity:locality",
            "location": {"lat": city["lat"], "lon": city["lon"]},
            "popularity": 0.99,
            "disambiguation": f"{city['name']}, {city['state']}, United States",
        }
        return self._heatmap_payload(cells, precision=7, locality=locality)

    @staticmethod
    def _heatmap_payload(cells, precision: int, locality: dict | None) -> dict:
        affinity = _rank_percentile([c[2] for c in cells])
        popularity = _rank_percentile([c[3] for c in cells])
        items = []
        for (lat, lon, _, _), aff, pop in zip(cells, affinity, popularity):
            items.append({
                "location": {
                    "latitude": round(lat, 6),
                    "longitude": round(lon, 6),
                    "geohash": geohash_encode(lat, lon, precision),
                },
                "query": {
                    "affinity": aff,
                    "affinity_rank": aff,
                    "popularity": pop,
                    "entity_artist_affinity_rank": aff,
                    "entity_artist_affinity": aff,
                },
            })
        items.sort(key=lambda it: -it["query"]["affinity"])
        query = {"localities": {"filter": [locality]}} if locality else {}
        return {"success": True, "results": {"heatmap": items}, "query": query}

    def _places(self, ids: list[str], location: str, tags: list[str], take: int) -> dict:
        city = _match_city(location)
        if city is None:
            return {"success": True, "results": {"entities": []}}
        venues = any("music_venue" in t for t in tags)
        rng = random.Random(_seed("places", city["id"], venues, *ids))
        hotspots = _hotspots(city, ids)
        words = VENUE_WORDS[:]
        rng.shuffle(words)
        entities = []
        for k in range(min(take, len(words))):
            h = hotspots[k % len(hotspots)]
            lat = h["lat"] + rng.gauss(0, h["radius_km"] / 260)
            lon = h["lon"] + rng.gauss(0, h["radius_km"] / 200)
            if venues:
                name = f"The {words[k]} {rng.choice(VENUE_KINDS)}"
                tag = {"id": "urn:tag:category:place:live_music_venue", "name": "Live music venue",
                       "type": "urn:tag:category:place", "weight": None}
            else:
                word, kind = rng.choice(SPOT_KINDS)
                name = f"{words[k]} {word}"
                tag = {"id": f"urn:tag:category:place:{kind.lower().replace(' ', '_')}", "name": kind,
                       "type": "urn:tag:category:place", "weight": None}
            entities.append({
                "name": name,
                "entity_id": "MOCK-PLC-" + _uuid("place", city["id"], name),
                "type": "urn:entity",
                "subtype": "urn:entity:place",
                "properties": {
                    "address": f"{rng.randint(100, 2999)} {rng.choice(['Main', 'Division', 'Union', 'Grand', 'Mercer'])} St, {city['name']}, {city['state']}",
                    "geocode": {"city": city["name"], "name": h["name"], "country_code": "US",
                                "admin1_region": city["state"]},
                    "business_rating": round(rng.uniform(4.1, 4.9), 1),
                },
                "location": {"lat": round(lat, 6), "lon": round(lon, 6), "geohash": geohash_encode(lat, lon, 12)},
                "popularity": round(rng.uniform(0.6, 0.98), 4),
                "query": {"affinity": round(0.9 - k * rng.uniform(0.004, 0.012), 4), "distance": 0,
                          "measurements": {"audience_growth": 0}},
                "tags": [tag],
                "external": {},
                "references": {},
                "disambiguation": f"{city['name']}, {city['state']}",
            })
        return {"success": True, "results": {"entities": entities}}

    def _related(self, ids: list[str], ftype: str, take: int) -> dict:
        rng = random.Random(_seed("related", ftype, *ids))
        names = POOLS.get(ftype, POOLS["urn:entity:brand"])[:]
        rng.shuffle(names)
        entities = [{
            "name": n,
            "entity_id": "MOCK-ENT-" + _uuid(ftype, n),
            "type": "urn:entity",
            "subtype": ftype,
            "properties": {},
            "popularity": round(rng.uniform(0.5, 0.95), 4),
            "query": {"affinity": round(0.95 - i * 0.012, 4)},
            "tags": [],
            "disambiguation": "",
        } for i, n in enumerate(names[:take])]
        return {"success": True, "results": {"entities": entities}}


def _match_city(location: str) -> dict | None:
    text = " ".join(re.sub(r"[^a-z ]", " ", location.lower().replace(".", "")).split())
    for c in sorted(load_cities(), key=lambda c: -len(c["name"])):
        if c["name"].lower().replace(".", "") in text:
            return c
    return None


def _hotspots(city: dict, ids: list[str]) -> list[dict]:
    rng = random.Random(_seed("hotspots", city["id"], *ids))
    names = NEIGHBORHOODS[:]
    rng.shuffle(names)
    spots = []
    for k in range(3):
        angle = rng.uniform(0, 2 * math.pi)
        dist_km = rng.uniform(2.5, 7.5) if k else rng.uniform(1.5, 4.0)
        spots.append({
            "name": names[k],
            "lat": city["lat"] + (dist_km / 111) * math.sin(angle),
            "lon": city["lon"] + (dist_km / (111 * math.cos(math.radians(city["lat"])))) * math.cos(angle),
            "strength": 1.0 - k * 0.2,
            "radius_km": rng.uniform(0.8, 1.5),
        })
    return spots
