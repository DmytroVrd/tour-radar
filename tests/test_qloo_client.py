import json
from pathlib import Path

import httpx
import pytest

from app.qloo.client import HttpSource, QlooClient, QlooError, cache_key
from app.qloo.mock import MockSource
from app.taste import hotspots, rank_cities

DOCS_HEATMAP = json.loads(
    (Path(__file__).resolve().parent.parent / "app/fixtures/heatmap_nyc_docs.json").read_text()
)


def keys(obj):
    return set(obj) - {"_source"}


def test_mock_city_heatmap_matches_documented_shape():
    data = MockSource().get("/v2/insights", {
        "filter.type": "urn:heatmap",
        "filter.location.query": "Portland",
        "signal.interests.entities": "MOCK-ART-0001-PHOEBE-BRIDGERS",
    })
    assert keys(data) == set(DOCS_HEATMAP)
    assert set(data["results"]) == set(DOCS_HEATMAP["results"])
    doc_item = DOCS_HEATMAP["results"]["heatmap"][0]
    item = data["results"]["heatmap"][0]
    assert set(item) == set(doc_item)
    assert set(item["location"]) == set(doc_item["location"])
    assert set(item["query"]) == set(doc_item["query"])
    assert set(data["query"]["localities"]["filter"]) == set(DOCS_HEATMAP["query"]["localities"]["filter"])
    assert data["_source"] == "mock"


def test_mock_heatmap_affinity_is_rank_percentile():
    pts = MockSource().get("/v2/insights", {
        "filter.type": "urn:heatmap", "filter.location.query": "Austin",
        "signal.interests.entities": "x"})["results"]["heatmap"]
    affs = [p["query"]["affinity"] for p in pts]
    assert affs[0] == 1
    assert affs == sorted(affs, reverse=True)


def test_rank_cities_on_mock_follows_profile():
    ranked = rank_cities(QlooClient(MockSource()), ["MOCK-ART-0001-PHOEBE-BRIDGERS"])
    top = [c["id"] for c in ranked[:6]]
    assert "portland" in top
    assert all(0 <= c["affinity"] <= 1 for c in ranked)


def test_hotspots_are_separated():
    client = QlooClient(MockSource())
    hm = client.heatmap(["MOCK-ART-0003-KHRUANGBIN"], "Austin")
    spots = hotspots(hm["points"], k=3)
    assert len(spots) == 3
    assert spots[0]["affinity"] == 1


def test_search_fixture_and_unknown_artist():
    client = QlooClient(MockSource())
    assert client.search("Phoebe Bridgers")[0]["entity_id"] == "MOCK-ART-0001-PHOEBE-BRIDGERS"
    other = client.search("Some Unknown Band")[0]
    assert other["types"] == ["urn:entity:artist"]


def test_http_source_sends_key_retries_and_caches(tmp_path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, json={"code": 429})
        return httpx.Response(200, json={"success": True, "results": []})

    src = HttpSource("secret", "https://hackathon.api.qloo.com", cache_dir=tmp_path,
                     transport=httpx.MockTransport(handler))
    src.get("/search", {"query": "a", "types": ["urn:entity:artist"]})
    src.get("/search", {"query": "a", "types": ["urn:entity:artist"]})
    assert len(calls) == 2  # one retry, then the second call is served from cache
    assert calls[0].headers["X-Api-Key"] == "secret"
    assert calls[0].url.host == "hackathon.api.qloo.com"
    assert calls[0].url.params["types"] == "urn:entity:artist"
    assert (tmp_path / f"{cache_key('/search', {'query': 'a', 'types': ['urn:entity:artist']})}.json").exists()


def test_http_source_auth_error_is_not_retried(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(401, json={})

    src = HttpSource("bad", "https://hackathon.api.qloo.com", transport=httpx.MockTransport(handler))
    with pytest.raises(QlooError) as err:
        src.get("/search", {"query": "a"})
    assert err.value.status == 401
    assert len(calls) == 1
