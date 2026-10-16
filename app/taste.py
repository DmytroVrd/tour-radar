"""Turn raw Qloo heatmaps into tour-planning facts."""

from __future__ import annotations

from app.geo import REGIONS, haversine_km, load_cities
from app.qloo import QlooClient

CITY_RADIUS_KM = 45
TOP_CELLS = 3


def rank_cities(client: QlooClient, entity_ids: list[str], region: str = "us") -> list[dict]:
    """Score every candidate city from a single region-wide heatmap.

    A city's score is the mean affinity of its strongest nearby cells. Using one
    request keeps scores comparable, because heatmap affinity is ranked within
    a response.
    """
    cells = client.region_heatmap(entity_ids, REGIONS[region]["wkt"])
    ranked = []
    for city in load_cities():
        near = [
            c["query"]["affinity"]
            for c in cells
            if haversine_km(city["lat"], city["lon"], c["location"]["latitude"],
                            c["location"]["longitude"]) <= CITY_RADIUS_KM
        ]
        if not near:
            continue
        top = sorted(near, reverse=True)[:TOP_CELLS]
        ranked.append({**city, "affinity": round(sum(top) / len(top), 4), "cells": len(near)})
    ranked.sort(key=lambda c: -c["affinity"])
    return ranked


def hotspots(points: list[dict], k: int = 3, min_sep_km: float = 2.0) -> list[dict]:
    """Pick the k strongest, mutually separated cells of a city heatmap."""
    picked: list[dict] = []
    for p in sorted(points, key=lambda p: -p["query"]["affinity"]):
        lat, lon = p["location"]["latitude"], p["location"]["longitude"]
        if all(haversine_km(lat, lon, q["lat"], q["lon"]) >= min_sep_km for q in picked):
            picked.append({"lat": lat, "lon": lon, "affinity": p["query"]["affinity"],
                           "geohash": p["location"]["geohash"]})
        if len(picked) == k:
            break
    return picked
