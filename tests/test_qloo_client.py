import json
import time
from pathlib import Path

import httpx
import pytest

from app.qloo.client import HttpSource, QlooClient, QlooError, _Throttle, cache_key
from app.qloo.mock import MockSource

FIXTURES = Path(__file__).resolve().parent.parent / "app/fixtures"
REAL_HEATMAP = json.loads((FIXTURES / "heatmap_portland_real.json").read_text(encoding="utf-8"))


def test_mock_city_heatmap_matches_real_shape():
    data = MockSource().get("/v2/insights", {
        "filter.type": "urn:heatmap",
        "filter.location.query": "Portland, Oregon",
        "signal.interests.entities": "MOCK-ART-0001-PHOEBE-BRIDGERS",
    })
    assert set(data) - {"_source"} == set(REAL_HEATMAP)
    real_item, item = REAL_HEATMAP["results"]["heatmap"][0], data["results"]["heatmap"][0]
    assert set(item) == set(real_item)
    assert set(item["location"]) == set(real_item["location"])
    assert set(item["query"]) == set(real_item["query"])
    real_loc = REAL_HEATMAP["query"]["localities"]["filter"]
    loc = data["query"]["localities"]["filter"]
    assert isinstance(loc, list) and isinstance(real_loc, list)
    assert set(loc[0]["location"]) == set(real_loc[0]["location"]) == {"lat", "lon"}
    assert data["_source"] == "mock"


def test_client_heatmap_reads_locality_list():
    hm = QlooClient(MockSource()).heatmap(["x"], "Austin, Texas")
    assert hm["locality"]["name"] == "Austin"
    assert len(hm["points"]) > 100


def test_mock_heatmap_affinity_is_rank_percentile():
    pts = QlooClient(MockSource()).heatmap(["x"], "Austin")["points"]
    affs = [p["query"]["affinity"] for p in pts]
    assert affs[0] == 1
    assert affs == sorted(affs, reverse=True)


def test_search_fixture_and_unknown_artist():
    client = QlooClient(MockSource())
    assert client.search("Phoebe Bridgers")[0]["entity_id"] == "MOCK-ART-0001-PHOEBE-BRIDGERS"
    assert client.search("boygenius")[0]["entity_id"] == "MOCK-ART-0002-BOYGENIUS"
    assert client.search("Some Unknown Band")[0]["types"] == ["urn:entity:artist"]


def test_http_source_sends_key_retries_and_caches(tmp_path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, json={"code": 429})
        return httpx.Response(200, json={"success": True, "results": []})

    src = HttpSource("secret", "https://hackathon.api.qloo.com", cache_dir=tmp_path,
                     per_second=100, transport=httpx.MockTransport(handler))
    params = {"query": "a", "types": ["urn:entity:artist"]}
    src.get("/search", params)
    src.get("/search", params)
    assert len(calls) == 2  # one retry, then the second call is served from cache
    assert calls[0].headers["X-Api-Key"] == "secret"
    assert calls[0].url.host == "hackathon.api.qloo.com"
    assert calls[0].url.params["types"] == "urn:entity:artist"
    assert (tmp_path / f"{cache_key('/search', params)}.json").exists()


def test_http_source_auth_and_bad_request_are_not_retried():
    for status in (401, 400):
        calls = []

        def handler(request, calls=calls, status=status):
            calls.append(request)
            return httpx.Response(status, json={})

        src = HttpSource("k", "https://hackathon.api.qloo.com", per_second=100,
                         transport=httpx.MockTransport(handler))
        with pytest.raises(QlooError) as err:
            src.get("/search", {"query": "a"})
        assert err.value.status == status
        assert len(calls) == 1


def test_throttle_spaces_requests():
    t = _Throttle(per_second=20)
    start = time.monotonic()
    for _ in range(5):
        t.wait()
    assert time.monotonic() - start >= 0.15
