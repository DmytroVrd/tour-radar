from app.agent.runner import run_agent
from app.agent.tools import TourTools
from app.qloo import QlooClient
from app.qloo.mock import MockSource


def noop(_):
    pass


def tools_after_scan(n_dates=4):
    tools = TourTools(QlooClient(MockSource()), n_dates)
    tools.call("search_artists", {"names": ["Phoebe Bridgers"]}, noop)
    tools.call("scan_region", {}, noop)
    return tools


def test_scripted_agent_runs_end_to_end_on_mock(monkeypatch):
    monkeypatch.setattr("app.agent.runner.settings", type("S", (), {
        "gemini_api_key": "", "agent_mode": "scripted", "qloo_mock": True, "allow_scripted_agent": False,
        "gemini_model": "x", "gemini_fallback_models": ()})())
    monkeypatch.setattr("app.agent.runner.get_client", lambda: QlooClient(MockSource()))
    events = list(run_agent(["Phoebe Bridgers"], 4))
    types = [e["type"] for e in events]
    assert types[0] == "run" and types[-1] == "done"
    assert all("t" in e for e in events)
    plan = next(e for e in events if e["type"] == "plan")["plan"]
    assert len(plan["stops"]) == 4
    assert all(s["venues"] for s in plan["stops"])


def test_agent_refuses_scripted_mode_against_live_qloo(monkeypatch):
    monkeypatch.setattr("app.agent.runner.settings", type("S", (), {
        "gemini_api_key": "", "agent_mode": "", "qloo_mock": False, "allow_scripted_agent": False})())
    from app.agent.runner import agent_mode
    assert agent_mode() == "unavailable"


def test_plan_route_rejects_unknown_and_drops_close_cities():
    tools = tools_after_scan()
    res = tools.call("plan_route", {"city_ids": ["san-francisco", "oakland", "portland", "atlantis"]}, noop)
    assert res.ok
    assert res.llm["ignored_unknown_ids"] == ["atlantis"]
    assert res.llm["dropped_too_close_to_another_stop"] == ["oakland"]
    assert set(res.llm["order"]) == {"san-francisco", "portland"}


def test_submit_plan_rejects_invented_venues_and_missing_stops():
    tools = tools_after_scan()
    tools.call("plan_route", {"city_ids": ["portland", "chicago"]}, noop)
    tools.call("profile_cities", {"city_ids": ["portland", "chicago"]}, noop)
    bad = tools.call("submit_plan", {"headline": "h", "stops": [
        {"city_id": "portland", "why": "w", "venue_ids": ["made-up-venue"]}]}, noop)
    assert not bad.ok
    assert any("made-up-venue" in e for e in bad.llm["errors"])
    assert any("chicago" in e for e in bad.llm["errors"])

    good_venues = {c: tools.state.city_profiles[c]["venues"][0]["id"] for c in ("portland", "chicago")}
    ok = tools.call("submit_plan", {"headline": "h", "stops": [
        {"city_id": c, "why": "w", "venue_ids": [v]} for c, v in good_venues.items()]}, noop)
    assert ok.ok
    assert [s["city_id"] for s in tools.full_plan()["stops"]] == [c["id"] for c in tools.state.route]


def test_plan_route_warns_about_long_drives():
    tools = tools_after_scan()
    res = tools.call("plan_route", {"city_ids": ["boston", "seattle"]}, noop)
    assert "warning" in res.llm and "Seattle" in res.llm["warning"]
    short = tools.call("plan_route", {"city_ids": ["boston", "new-york"]}, noop)
    assert "warning" not in short.llm


def test_scan_uses_neighbours_when_qloo_has_no_map_data():
    class SparseClient(QlooClient):
        def region_heatmap(self, entity_ids, wkt):
            if "MOCK-ART-0001-PHOEBE-BRIDGERS" in entity_ids:
                return super().region_heatmap(entity_ids, wkt)[:2]
            return super().region_heatmap(entity_ids, wkt)

    tools = TourTools(SparseClient(MockSource()), 4)
    tools.call("search_artists", {"names": ["Phoebe Bridgers"]}, noop)
    res = tools.call("scan_region", {}, noop)
    assert res.ok and tools.state.proxy
    assert tools.state.proxy["for"] == ["Phoebe Bridgers"]
    assert "note" in res.llm and "No map data" in res.summary
    assert len(res.ui["cities"]) > 10
    assert "MOCK-ART-0001-PHOEBE-BRIDGERS" not in tools.state.artist_ids
