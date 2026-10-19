"""Draft a booking email for one venue from facts the agent already gathered."""

from __future__ import annotations

import json

from google.genai import types

from app.agent.gemini import GeminiAgent
from app.config import settings

PROMPT = """Write a short booking inquiry email from an independent touring act to a
music venue. Use ONLY the facts in the JSON below. Anything you do not know
(band name, dates, links, ticket history) must stay as a placeholder in
square brackets: use exactly [Band name], [proposed date], [link to music].
Do not mention stop numbers or the number of dates; venues don't need them.

Tone: friendly, specific, professional; no hype. 110-150 words in the body,
in two or three short paragraphs, signed "[Your name], [Band name]".
Say in plain words why this venue and neighbourhood fit (people who like
these acts are unusually concentrated around it); do not quote internal
scores or the words "lift" or "baseline". Name the acts the band sounds like
and ask about availability for the routed date.
Return JSON: {"subject": "...", "body": "..."}.

FACTS:
"""


def draft_booking_email(facts: dict) -> dict:
    if not settings.gemini_api_key:
        raise RuntimeError("Gemini is not configured")
    agent = GeminiAgent(settings.gemini_api_key, [*settings.gemini_fallback_models, settings.gemini_model])
    config = types.GenerateContentConfig(
        temperature=0.6,
        response_mime_type="application/json",
        response_schema={"type": "object", "properties": {"subject": {"type": "string"}, "body": {"type": "string"}},
                         "required": ["subject", "body"]},
    )
    resp = agent.generate([types.Content(role="user", parts=[types.Part.from_text(
        text=PROMPT + json.dumps(facts, ensure_ascii=False))])], config)
    data = json.loads(resp.text)
    return {"subject": str(data.get("subject", ""))[:200], "body": str(data.get("body", ""))[:2000],
            "model": agent.model_used}
