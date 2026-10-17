"""Small geo helpers: haversine distance, geohash encoding, region polygons."""

from __future__ import annotations

import json
import math
import threading
import time
from functools import lru_cache
from pathlib import Path

import httpx

DATA_DIR = Path(__file__).resolve().parent / "data"
_BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"

# Coarse WKT outlines used as the heatmap location for city ranking.
REGIONS: dict[str, dict] = {
    "us": {
        "name": "United States",
        "wkt": "POLYGON((-124.8 48.4,-123.0 49.0,-95.2 49.0,-82.5 45.5,-67.0 47.4,-69.9 41.2,"
        "-75.5 35.2,-80.0 31.5,-80.0 25.0,-82.0 25.0,-85.0 29.6,-97.2 25.9,-104.5 29.5,"
        "-106.5 31.8,-111.0 31.3,-117.1 32.5,-120.6 34.5,-124.4 40.4,-124.8 48.4))",
        "bounds": [[-125.5, 24.0], [-66.5, 49.5]],
    },
}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def geohash_encode(lat: float, lon: float, precision: int = 6) -> str:
    lat_range, lon_range = [-90.0, 90.0], [-180.0, 180.0]
    bits, bit, ch, even, out = [16, 8, 4, 2, 1], 0, 0, True, []
    while len(out) < precision:
        rng, val = (lon_range, lon) if even else (lat_range, lat)
        mid = (rng[0] + rng[1]) / 2
        if val > mid:
            ch |= bits[bit]
            rng[0] = mid
        else:
            rng[1] = mid
        even = not even
        if bit < 4:
            bit += 1
        else:
            out.append(_BASE32[ch])
            bit, ch = 0, 0
    return "".join(out)


@lru_cache
def load_cities() -> tuple[dict, ...]:
    return tuple(json.loads((DATA_DIR / "us_cities.json").read_text(encoding="utf-8")))


def find_city(query: str) -> dict | None:
    q = query.strip().lower()
    for city in load_cities():
        if q in (city["id"], city["name"].lower(), f"{city['name'].lower()}, {city['state'].lower()}"):
            return city
    return None


# --- neighbourhood names (fallback when Qloo places carry none) ---------------------

_NOMINATIM = "https://nominatim.openstreetmap.org/reverse"
_NOMINATIM_UA = "TourRadar/0.1 (+https://github.com/DmytroVrd/tour-radar)"
_name_lock = threading.Lock()
_last_call = [0.0]


def _names_cache_path() -> Path:
    from app.config import settings

    path = Path(settings.cache_dir) / "neighborhoods.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def reverse_neighborhood(lat: float, lon: float) -> str | None:
    """Name the neighbourhood at a point via OpenStreetMap Nominatim.

    Results are cached on disk and requests are spaced a second apart, per
    Nominatim's usage policy. Any failure simply returns None.
    """
    key = f"{lat:.3f},{lon:.3f}"
    with _name_lock:
        path = _names_cache_path()
        cache = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if key in cache:
            return cache[key]
        wait = 1.1 - (time.monotonic() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.monotonic()
        try:
            resp = httpx.get(_NOMINATIM, params={"format": "jsonv2", "lat": lat, "lon": lon, "zoom": 15,
                                                 "addressdetails": 1},
                             headers={"User-Agent": _NOMINATIM_UA}, timeout=6)
            addr = resp.json().get("address", {}) if resp.status_code == 200 else {}
        except (httpx.HTTPError, ValueError):
            return None
        name = next((addr[k] for k in ("neighbourhood", "suburb", "quarter", "city_district", "hamlet")
                     if addr.get(k)), None)
        cache[key] = name
        path.write_text(json.dumps(cache, indent=0), encoding="utf-8")
        return name
