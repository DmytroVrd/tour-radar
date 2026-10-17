from app.geo import find_city
from app.qloo import QlooClient
from app.qloo.mock import MockSource
from app.taste import blend, city_profile, hotspots, region_scan, score_cities, taste_profile

PHOEBE = "MOCK-ART-0001-PHOEBE-BRIDGERS"


def cell(lat, lon, aff, gh):
    return {"location": {"latitude": lat, "longitude": lon, "geohash": gh}, "query": {"affinity": aff}}


def test_blend_rewards_lift_and_clamps():
    assert blend(0.8, 0.2) > blend(0.8, -0.2)
    assert 0 <= blend(0.0, -5) <= blend(1.0, 5) <= 1


def test_score_cities_penalises_mainstream_only_cities():
    portland, chicago = find_city("portland"), find_city("chicago")
    cells = [
        cell(portland["lat"], portland["lon"], 0.80, "p1"), cell(portland["lat"] + 0.05, portland["lon"], 0.78, "p2"),
        cell(chicago["lat"], chicago["lon"], 0.85, "c1"), cell(chicago["lat"] + 0.05, chicago["lon"], 0.83, "c2"),
    ]
    baseline = {"p1": 0.3, "p2": 0.3, "c1": 0.95, "c2": 0.95}
    ranked = score_cities(cells, baseline)
    assert [c["id"] for c in ranked] == ["portland", "chicago"]
    assert ranked[0]["rank"] == 1 and ranked[0]["lift"] > 0 > ranked[1]["lift"]


def test_region_scan_on_mock_follows_profile():
    scan = region_scan(QlooClient(MockSource()), [PHOEBE])
    top = [c["id"] for c in scan["cities"][:8]]
    assert "portland" in top
    assert scan["cells"] and all(len(c) == 3 for c in scan["cells"])


def test_hotspots_are_separated():
    pts = [{"lat": 45.5, "lon": -122.6, "taste": 0.9}, {"lat": 45.5001, "lon": -122.6, "taste": 0.8},
           {"lat": 45.55, "lon": -122.6, "taste": 0.7}]
    picked = hotspots(pts, k=2)
    assert [p["taste"] for p in picked] == [0.9, 0.7]


def test_city_profile_names_hotspots_and_finds_venues():
    prof = city_profile(QlooClient(MockSource()), [PHOEBE], find_city("portland"))
    assert len(prof["hotspots"]) == 3
    assert prof["hotspots"][0]["name"]
    assert prof["venues"] and all(v["neighborhood"] for v in prof["venues"])
    assert all("Live music venue" in v["tags"] for v in prof["venues"])
    assert prof["heat"] and max(h[2] for h in prof["heat"]) == 1


def test_taste_profile_excludes_input_artists():
    prof = taste_profile(QlooClient(MockSource()), [PHOEBE])
    assert prof["brands"] and prof["destinations"] and prof["similar_artists"]
    assert PHOEBE not in {a["id"] for a in prof["similar_artists"]}


def test_side_name_falls_back_to_compass_side():
    from app.taste import _side_name

    denver = find_city("denver")
    assert _side_name(denver, denver["lat"], denver["lon"] + 0.003) == "Central Denver"
    assert _side_name(denver, denver["lat"] + 0.1, denver["lon"]) == "North Denver"
    assert _side_name(denver, denver["lat"], denver["lon"] - 0.2) == "West Denver"
