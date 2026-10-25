import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.main as main


def _slow_events():
    yield {"type": "run"}
    time.sleep(0.25)  # a quiet model turn
    yield {"type": "done"}


def test_sse_sends_heartbeats_while_the_agent_is_quiet(monkeypatch):
    monkeypatch.setattr(main, "HEARTBEAT_SECONDS", 0.05)
    api = FastAPI()
    api.get("/s")(lambda: main._sse(_slow_events()))
    body = TestClient(api).get("/s").text
    assert ": keep-alive" in body
    assert body.index('"run"') < body.index(": keep-alive") < body.index('"done"')


def test_sse_reports_a_crash_as_an_error_event():
    def broken():
        yield {"type": "run"}
        raise RuntimeError("boom")

    api = FastAPI()
    api.get("/s")(lambda: main._sse(broken()))
    body = TestClient(api).get("/s").text
    assert '"type":"error"' in body
