"""Qloo Taste AI client.

Two interchangeable sources sit behind one client:

* ``HttpSource`` calls the hackathon base URL with ``X-Api-Key``, bounded
  retries and an on-disk response cache.
* ``MockSource`` serves fixtures that copy the documented response shapes.
  It exists for development only and every response it returns is tagged
  ``"_source": "mock"`` so the UI can label it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Protocol

import httpx

from app.config import settings

log = logging.getLogger(__name__)

Params = dict[str, Any]


class QlooError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class Source(Protocol):
    name: str

    def get(self, path: str, params: Params) -> dict: ...


def _normalize(params: Params) -> list[tuple[str, str]]:
    """Flatten list values to comma-separated strings and drop empties."""
    out: list[tuple[str, str]] = []
    for key in sorted(params):
        value = params[key]
        if value is None or value == "" or value == []:
            continue
        if isinstance(value, (list, tuple)):
            value = ",".join(str(v) for v in value)
        elif isinstance(value, bool):
            value = "true" if value else "false"
        out.append((key, str(value)))
    return out


def cache_key(path: str, params: Params) -> str:
    raw = path + "?" + "&".join(f"{k}={v}" for k, v in _normalize(params))
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


class HttpSource:
    name = "qloo"

    def __init__(
        self,
        api_key: str,
        base_url: str,
        cache_dir: str | Path | None = None,
        timeout: float = 20.0,
        max_retries: int = 2,
        transport: httpx.BaseTransport | None = None,
    ):
        if not api_key:
            raise QlooError("QLOO_API_KEY is not set")
        self._client = httpx.Client(
            base_url=base_url,
            headers={"X-Api-Key": api_key, "Accept": "application/json"},
            timeout=timeout,
            transport=transport,
        )
        self._cache_dir = Path(cache_dir) if cache_dir else None
        if self._cache_dir:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._max_retries = max_retries

    def _cache_path(self, path: str, params: Params) -> Path | None:
        if not self._cache_dir:
            return None
        return self._cache_dir / f"{cache_key(path, params)}.json"

    def get(self, path: str, params: Params) -> dict:
        cached = self._cache_path(path, params)
        if cached and cached.exists():
            return json.loads(cached.read_text(encoding="utf-8"))

        query = _normalize(params)
        delay = 0.8
        for attempt in range(self._max_retries + 1):
            try:
                resp = self._client.get(path, params=query)
            except httpx.TransportError as exc:
                if attempt == self._max_retries:
                    raise QlooError(f"Qloo unreachable: {exc.__class__.__name__}") from exc
            else:
                if resp.status_code == 200:
                    data = resp.json()
                    if cached:
                        cached.write_text(json.dumps(data), encoding="utf-8")
                    return data
                if resp.status_code in (401, 403):
                    raise QlooError(
                        "Qloo rejected the key (check that the base URL is the hackathon one)",
                        resp.status_code,
                    )
                retryable = resp.status_code == 429 or resp.status_code >= 500
                if not retryable or attempt == self._max_retries:
                    raise QlooError(
                        f"Qloo {path} returned {resp.status_code}: {resp.text[:200]}",
                        resp.status_code,
                    )
            log.warning("Qloo %s retry %d", path, attempt + 1)
            time.sleep(delay)
            delay *= 2
        raise QlooError("unreachable")  # pragma: no cover


class QlooClient:
    """Thin, typed wrappers over the endpoints Tour Radar uses."""

    def __init__(self, source: Source):
        self.source = source

    @property
    def is_mock(self) -> bool:
        return self.source.name == "mock"

    def search(self, query: str, types: str = "urn:entity:artist", take: int = 5) -> list[dict]:
        data = self.source.get("/search", {"query": query, "types": types, "take": take})
        return data.get("results", [])

    def insights(self, filter_type: str, **params: Any) -> dict:
        """Raw ``GET /v2/insights``. Keyword names use ``__`` for dots,
        e.g. ``signal__interests__entities=[...]``."""
        query = {"filter.type": filter_type}
        query.update({k.replace("__", "."): v for k, v in params.items()})
        return self.source.get("/v2/insights", query)

    def heatmap(self, entity_ids: list[str], location_query: str) -> dict:
        data = self.insights(
            "urn:heatmap",
            filter__location__query=location_query,
            signal__interests__entities=entity_ids,
        )
        return {
            "points": data.get("results", {}).get("heatmap", []),
            "locality": data.get("query", {}).get("localities", {}).get("filter"),
        }

    def places(
        self,
        entity_ids: list[str],
        location_query: str,
        tags: list[str] | None = None,
        take: int = 10,
    ) -> list[dict]:
        data = self.insights(
            "urn:entity:place",
            filter__location__query=location_query,
            filter__tags=tags,
            signal__interests__entities=entity_ids,
            take=take,
        )
        return data.get("results", {}).get("entities", [])

    def related(self, entity_ids: list[str], filter_type: str, take: int = 5) -> list[dict]:
        data = self.insights(filter_type, signal__interests__entities=entity_ids, take=take)
        return data.get("results", {}).get("entities", [])


_client: QlooClient | None = None


def get_client() -> QlooClient:
    global _client
    if _client is None:
        if settings.qloo_mock:
            from app.qloo.mock import MockSource

            source: Source = MockSource()
        else:
            source = HttpSource(
                settings.qloo_api_key,
                settings.qloo_base_url,
                cache_dir=Path(settings.cache_dir) / "qloo",
            )
        _client = QlooClient(source)
    return _client
