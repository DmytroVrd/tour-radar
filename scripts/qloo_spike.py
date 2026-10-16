"""Day-one probe of the Qloo hackathon API.

Answers the open questions behind Tour Radar's design with a small, fixed set
of requests (about a dozen). Raw responses are saved under .cache/spike/ for
inspection; the API key is read from .env and never printed.

    python scripts/qloo_spike.py
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.geo import REGIONS  # noqa: E402

load_dotenv()
KEY = os.getenv("QLOO_API_KEY", "")
BASE = os.getenv("QLOO_BASE_URL", "https://hackathon.api.qloo.com")
OUT = Path(".cache/spike")
OUT.mkdir(parents=True, exist_ok=True)

if not KEY:
    sys.exit("QLOO_API_KEY is not set in .env")

client = httpx.Client(base_url=BASE, headers={"X-Api-Key": KEY}, timeout=40)
timings: list[float] = []


def call(tag: str, path: str, params: dict) -> dict | None:
    t0 = time.perf_counter()
    try:
        r = client.get(path, params=params)
    except httpx.HTTPError as exc:
        print(f"  [{tag}] network error: {exc.__class__.__name__}")
        return None
    ms = (time.perf_counter() - t0) * 1000
    timings.append(ms)
    limits = {k: v for k, v in r.headers.items() if "limit" in k.lower() or "retry" in k.lower()}
    print(f"  [{tag}] {r.status_code} in {ms:.0f} ms {limits or ''}")
    try:
        data = r.json()
    except ValueError:
        data = {"_raw": r.text[:2000]}
    (OUT / f"{tag}.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    if r.status_code != 200:
        print(f"      body: {json.dumps(data)[:300]}")
        return None
    return data


def spread(values: list[float]) -> str:
    if not values:
        return "no values"
    return (f"n={len(values)} min={min(values):.4f} max={max(values):.4f} "
            f"median={statistics.median(values):.4f} stdev={statistics.pstdev(values):.4f}")


print("\n1) Resolve artists with /search")
ids = {}
for name in ["Phoebe Bridgers", "Khruangbin", "Turnstile"]:
    data = call(f"search-{name.split()[0].lower()}", "/search",
                {"query": name, "types": "urn:entity:artist", "take": 3})
    if data and data.get("results"):
        top = data["results"][0]
        ids[name] = top["entity_id"]
        print(f"      -> {top.get('name')} {top['entity_id']} keys={sorted(top)}")
pb = ids.get("Phoebe Bridgers")
if not pb:
    sys.exit("Could not resolve Phoebe Bridgers; stopping.")

print("\n2) City heatmap with an artist signal (is it flat?)")
for city in ["Portland", "New York"]:
    data = call(f"heatmap-{city.split()[0].lower()}", "/v2/insights", {
        "filter.type": "urn:heatmap", "filter.location.query": city,
        "signal.interests.entities": pb})
    if data:
        pts = data.get("results", {}).get("heatmap", [])
        print(f"      affinity   {spread([p['query']['affinity'] for p in pts])}")
        print(f"      popularity {spread([p['query']['popularity'] for p in pts])}")
        print(f"      locality   {data.get('query', {}).get('localities', {}).get('filter', {}).get('name')}")

print("\n3) Ranking cities")
print("   3a) one region-wide heatmap (WKT polygon of the US)")
data = call("heatmap-us-wkt", "/v2/insights", {
    "filter.type": "urn:heatmap", "filter.location": REGIONS["us"]["wkt"],
    "signal.interests.entities": pb})
if data:
    pts = data.get("results", {}).get("heatmap", [])
    lats = [p["location"]["latitude"] for p in pts]
    lons = [p["location"]["longitude"] for p in pts]
    print(f"      cells={len(pts)} lat {min(lats, default=0):.1f}..{max(lats, default=0):.1f} "
          f"lon {min(lons, default=0):.1f}..{max(lons, default=0):.1f}")
    print(f"      geohash length={len(pts[0]['location']['geohash']) if pts else '-'}")
print("   3b) region-wide heatmap by natural-language query")
data = call("heatmap-us-query", "/v2/insights", {
    "filter.type": "urn:heatmap", "filter.location.query": "United States",
    "signal.interests.entities": pb})
if data:
    print(f"      cells={len(data.get('results', {}).get('heatmap', []))}")
print("   3c) locality insights")
for ftype in ["urn:entity:locality", "urn:entity:destination"]:
    data = call(f"insights-{ftype.split(':')[-1]}", "/v2/insights", {
        "filter.type": ftype, "signal.interests.entities": pb, "take": 15})
    if data:
        ents = data.get("results", {}).get("entities", [])
        print(f"      {ftype}: {[e.get('name') for e in ents][:15]}")

print("\n4) Places: are music venues in there, and is there a tag?")
data = call("tags-music-venue", "/v2/tags", {"filter.query": "music venue", "take": 10})
if data:
    tags = data.get("results", {}).get("tags", data.get("results", []))
    print(f"      tags: {[(t.get('name'), t.get('id')) for t in tags][:10]}")
data = call("places-portland", "/v2/insights", {
    "filter.type": "urn:entity:place", "filter.location.query": "Portland",
    "signal.interests.entities": pb, "take": 10})
if data:
    ents = data.get("results", {}).get("entities", [])
    for e in ents:
        tag_names = [t.get("name") for t in e.get("tags", [])][:4]
        print(f"      {e.get('name')!r:40} {tag_names}")
    if ents:
        print(f"      entity keys={sorted(ents[0])}")

print("\n5) Coverage outside the US")
data = call("heatmap-london", "/v2/insights", {
    "filter.type": "urn:heatmap", "filter.location.query": "London",
    "signal.interests.entities": pb})
if data:
    pts = data.get("results", {}).get("heatmap", [])
    print(f"      affinity {spread([p['query']['affinity'] for p in pts])}")

print(f"\nLatency over {len(timings)} calls: {spread(timings)} (ms)")
print(f"Raw responses saved in {OUT}/")
