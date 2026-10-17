"""Gemini tool-calling loop for the tour agent."""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Callable, Iterator

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from app.agent.tools import TourTools

log = logging.getLogger(__name__)

SYSTEM = """You are Tour Radar, a tour-planning agent for independent musicians.

The artist tells you which acts they sound like. Your job is to plan a tour
where people with that taste are over-represented, using ONLY facts returned
by your tools, which come from the Qloo Taste AI API. Never invent cities,
venues, neighborhoods, numbers or brands.

Work in this order:
1. search_artists with the names the artist gave.
2. scan_region and taste_profile (call both in the same turn).
3. plan_route: choose exactly the requested number of dates from the scanned
   top cities. Favour high taste scores, but make it a tour a van can drive:
   spread stops sensibly and avoid two stops in one metro. If plan_route
   warns about long legs, re-plan a tighter regional route unless the taste
   scores clearly justify the extra driving; say which you chose.
4. profile_cities for every city on the route (one call with all ids).
5. submit_plan with one stop per routed city, in route order. For each stop:
   - why: two short sentences in plain English for the musician. Use one
     concrete fact from the data (taste score, rank, lift over mainstream, the
     hotspot neighborhood, a venue near it). Similar artists and brands describe
     the audience as a whole: never tie them to a specific neighborhood or city.
     Frame taste as "over-represented" or "runs strong", never as facts about
     individuals: do not write that fans live, gather or go anywhere. Vary the
     wording from stop to stop.
   - neighborhood: the #1 hotspot name from profile_cities. If you favour a
     venue near another hotspot, say so explicitly in "why".
   - venue_ids: one or two venue ids from that city's profile, preferring
     venues close to the hotspot that fit the act's size.
   - spot_ids: up to two local spot ids worth a pre-show visit or flyer drop.
   - collab: one concrete merch or content idea with a brand from
     taste_profile, or a local spot.
   headline: one line that sums up the tour's shape.

Before each tool call, write one short sentence (max 15 words) saying what you
are about to check and why. Be concrete and calm; no hype words.
If submit_plan returns errors, fix exactly those and submit again."""


def _decl(name: str, description: str, properties: dict, required: list[str]) -> types.FunctionDeclaration:
    return types.FunctionDeclaration(
        name=name,
        description=description,
        parameters_json_schema={"type": "object", "properties": properties, "required": required},
    )


IDS = {"type": "array", "items": {"type": "string"}}
DECLARATIONS = [
    _decl("search_artists", "Resolve up to three artist names to Qloo entities.",
          {"names": {"type": "array", "items": {"type": "string"}}}, ["names"]),
    _decl("scan_region", "Score US cities by how over-represented this taste is (Qloo heatmap vs mainstream baseline).",
          {"artist_ids": IDS}, []),
    _decl("taste_profile", "Cross-domain taste of this audience: similar artists, brands, travel destinations.",
          {"artist_ids": IDS}, []),
    _decl("plan_route", "Order the chosen cities into a drivable tour. Use ids from scan_region.",
          {"city_ids": IDS, "start_city_id": {"type": "string"}}, ["city_ids"]),
    _decl("profile_cities", "For each city: taste hotspot neighborhoods, live music venues and local spots from Qloo.",
          {"city_ids": IDS}, ["city_ids"]),
    _decl("submit_plan", "Submit the final tour plan. It is validated against the Qloo data gathered.", {
        "headline": {"type": "string"},
        "stops": {"type": "array", "items": {"type": "object", "properties": {
            "city_id": {"type": "string"},
            "why": {"type": "string"},
            "neighborhood": {"type": "string"},
            "venue_ids": IDS,
            "spot_ids": IDS,
            "collab": {"type": "string"},
        }, "required": ["city_id", "why", "venue_ids"]}},
    }, ["headline", "stops"]),
]

LABELS = {
    "search_artists": "Finding the artists in Qloo",
    "scan_region": "Scanning the country for this taste",
    "taste_profile": "Reading the audience's wider taste",
    "plan_route": "Routing the tour",
    "profile_cities": "Mapping neighbourhoods and venues",
    "submit_plan": "Checking the plan against Qloo data",
}

RETRYABLE = (500, 503, 504)
DEFAULT_COOLDOWN_S = 30.0
MAX_QUOTA_WAIT_S = 25.0

# Free-tier keys allow only a few requests per minute per model. A model that
# answers 429 is skipped until Google's own retry delay has passed.
_cooldown: dict[str, float] = {}


class AllModelsBusy(RuntimeError):
    def __init__(self, wait_s: float, last: Exception | None):
        super().__init__(f"All Gemini models are over quota for {wait_s:.0f}s more: {last}")
        self.wait_s = wait_s


def _retry_delay(exc: Exception) -> float:
    match = re.search(r"retry in ([0-9.]+)s", str(exc)) or re.search(r"'retryDelay': '([0-9.]+)s'", str(exc))
    return float(match.group(1)) + 1.0 if match else DEFAULT_COOLDOWN_S


def _config_for(model: str, config: types.GenerateContentConfig | None):
    # thinking_level is a Gemini 3 setting; older models reject it.
    if config is not None and config.thinking_config is not None and model.startswith("gemini-2"):
        return config.model_copy(update={"thinking_config": None})
    return config


class GeminiAgent:
    def __init__(self, api_key: str, models: list[str], max_turns: int = 14):
        self.client = genai.Client(api_key=api_key)
        self.models = [m for m in dict.fromkeys(models) if m]
        self.max_turns = max_turns
        self.llm_calls = 0
        self.model_used: str | None = None

    def generate(self, contents: list[types.Content], config: types.GenerateContentConfig | None):
        last: Exception | None = None
        ready = [m for m in self.models if _cooldown.get(m, 0) <= time.monotonic()]
        for model in ready:
            for attempt in range(2):
                try:
                    self.llm_calls += 1
                    resp = self.client.models.generate_content(model=model, contents=contents,
                                                               config=_config_for(model, config))
                    self.model_used = model
                    return resp
                except genai_errors.APIError as exc:
                    last = exc
                    code = getattr(exc, "code", None)
                    if code == 429:
                        _cooldown[model] = time.monotonic() + _retry_delay(exc)
                        log.warning("Gemini %s is over quota; skipping it for now", model)
                        break
                    if code in (400, 404):
                        # Retired models answer 404; some reject a config with 400. Try the next one.
                        log.warning("Gemini %s rejected the request: %s", model, str(exc)[:160])
                        break
                    if code not in RETRYABLE:
                        raise
                    log.warning("Gemini %s failed with %s (attempt %d)", model, code, attempt + 1)
                    time.sleep(1.5 * (attempt + 1))
                except httpx.TransportError as exc:
                    last = exc
                    log.warning("Gemini %s connection dropped (attempt %d)", model, attempt + 1)
                    time.sleep(1.0)
        soonest = min((_cooldown.get(m, 0) for m in self.models), default=0) - time.monotonic()
        if soonest > 0:
            raise AllModelsBusy(soonest, last)
        raise RuntimeError(f"All Gemini models are unavailable right now: {last}")

    def generate_patiently(self, contents, config) -> Iterator:
        """Like generate, but waits out short free-tier rate limits.

        Yields trace events while waiting and finally ``("response", resp)``.
        """
        waited = 0.0
        while True:
            try:
                yield ("response", self.generate(contents, config))
                return
            except AllModelsBusy as busy:
                if waited + busy.wait_s > MAX_QUOTA_WAIT_S * 2:
                    raise
                pause = min(busy.wait_s, MAX_QUOTA_WAIT_S)
                yield {"type": "thought", "text": f"Model rate limit reached on the free tier; waiting {pause:.0f}s."}
                time.sleep(pause)
                waited += pause

    def run(self, tools: TourTools, request: str, emit_tool: Callable[[str, dict], Iterator[dict]]) -> Iterator[dict]:
        """Yield trace events. ``emit_tool(name, args)`` runs a tool and yields its events."""
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM,
            tools=[types.Tool(function_declarations=DECLARATIONS)],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            temperature=0.4,
            thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW),
        )
        contents = [types.Content(role="user", parts=[types.Part.from_text(text=request)])]
        nudges = 0
        for _ in range(self.max_turns):
            resp = None
            for item in self.generate_patiently(contents, config):
                if isinstance(item, tuple):
                    resp = item[1]
                else:
                    yield item
            content = resp.candidates[0].content if resp.candidates else None
            if content is None or not content.parts:
                nudges += 1
                if nudges > 2:
                    break
                contents.append(types.Content(role="user", parts=[types.Part.from_text(
                    text="Continue with the next tool call.")]))
                continue
            contents.append(content)
            for part in content.parts:
                if part.text and not getattr(part, "thought", False) and part.text.strip():
                    yield {"type": "thought", "text": part.text.strip()[:400]}
            calls = [p.function_call for p in content.parts if p.function_call]
            if not calls:
                if tools.state.plan:
                    return
                nudges += 1
                if nudges > 2:
                    break
                contents.append(types.Content(role="user", parts=[types.Part.from_text(
                    text="Use the tools to continue; finish with submit_plan.")]))
                continue

            replies = []
            for fc in calls:
                result_holder: dict = {}
                for event in emit_tool(fc.name, dict(fc.args or {})):
                    if event.get("type") == "_result":
                        result_holder = event["llm"]
                    else:
                        yield event
                replies.append(types.Part.from_function_response(
                    name=fc.name, response={"result": json.loads(json.dumps(result_holder, default=str))}))
            contents.append(types.Content(role="user", parts=replies))
            if tools.state.plan:
                return
        if not tools.state.plan:
            raise RuntimeError("The agent stopped before submitting a plan.")
