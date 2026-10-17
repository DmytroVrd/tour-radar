import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    qloo_api_key: str = os.getenv("QLOO_API_KEY", "")
    qloo_base_url: str = os.getenv("QLOO_BASE_URL", "https://hackathon.api.qloo.com").rstrip("/")
    qloo_mock: bool = _flag("QLOO_MOCK", "0")
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
    gemini_fallback_models: tuple[str, ...] = tuple(
        m.strip() for m in os.getenv(
            "GEMINI_FALLBACK_MODELS",
            "gemini-3.5-flash-lite,gemini-3.1-flash-lite,gemini-flash-lite-latest,gemini-flash-latest,gemini-3.5-flash",
        ).split(",")
        if m.strip()
    )
    # "scripted" forces the offline dev agent; it only runs with mock data or ALLOW_SCRIPTED_AGENT=1.
    agent_mode: str = os.getenv("AGENT_MODE", "")
    allow_scripted_agent: bool = _flag("ALLOW_SCRIPTED_AGENT", "0")
    cache_dir: str = os.getenv("CACHE_DIR", ".cache")


settings = Settings()
