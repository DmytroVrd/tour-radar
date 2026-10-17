from google.genai import errors as genai_errors

from app.agent import gemini


class StubModels:
    def __init__(self):
        self.calls = []

    def generate_content(self, model, contents, config):
        self.calls.append(model)
        if model == "busy-model":
            raise genai_errors.APIError(429, {"error": {"message": "quota", "status": "RESOURCE_EXHAUSTED"}})
        return f"ok from {model}"


def make_agent(monkeypatch):
    gemini._cooldown.clear()
    agent = gemini.GeminiAgent.__new__(gemini.GeminiAgent)
    agent.models = ["busy-model", "spare-model"]
    agent.llm_calls = 0
    agent.model_used = None
    stub = StubModels()
    agent.client = type("C", (), {"models": stub})()
    return agent, stub


def test_quota_error_falls_back_and_cools_down(monkeypatch):
    agent, stub = make_agent(monkeypatch)
    assert agent.generate([], None) == "ok from spare-model"
    assert stub.calls == ["busy-model", "spare-model"]
    # The exhausted model is skipped on the next turn instead of retried.
    stub.calls.clear()
    agent.generate([], None)
    assert stub.calls == ["spare-model"]
    assert agent.model_used == "spare-model"
