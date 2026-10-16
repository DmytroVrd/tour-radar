from itertools import permutations

import pytest

from app.geo import find_city
from app.route import choose_stops, order_route, plan_route, _length


def city(cid, affinity=1.0):
    return {**find_city(cid), "affinity": affinity}


def test_choose_stops_skips_neighbours():
    ranked = [city("san-francisco", 0.99), city("oakland", 0.98), city("portland", 0.9)]
    ids = [s["id"] for s in choose_stops(ranked, 2)]
    assert ids == ["san-francisco", "portland"]


def test_order_route_runs_west_to_east_and_beats_ranked_order():
    stops = [city(c) for c in ["boston", "seattle", "chicago", "portland", "new-york", "denver"]]
    ordered = order_route(stops)
    assert ordered[0]["id"] in {"seattle", "portland"}
    assert ordered[-1]["id"] == "boston"
    assert {s["id"] for s in ordered} == {s["id"] for s in stops}
    assert _length(ordered) < _length(stops)


def test_order_route_matches_brute_force_on_small_tours():
    stops = [city(c) for c in ["portland", "seattle", "los-angeles", "chicago", "denver", "austin"]]
    best = min(_length(list(p)) for p in permutations(stops))
    assert _length(order_route(stops)) == pytest.approx(best)


def test_order_route_respects_fixed_start():
    stops = [city(c) for c in ["chicago", "seattle", "denver"]]
    assert order_route(stops, start=stops[0])[0]["id"] == "chicago"


def test_plan_route_reports_legs():
    ranked = [city(c, 1 - i / 10) for i, c in enumerate(["portland", "seattle", "chicago"])]
    plan = plan_route(ranked, 3)
    assert len(plan["stops"]) == 3
    assert len(plan["legs_km"]) == 2
    assert plan["total_km"] == sum(plan["legs_km"])
