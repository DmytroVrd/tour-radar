"""Record a real agent run as a one-click example.

    python scripts/record_example.py phoebe "Phoebe Bridgers, boygenius" 8 --label "Phoebe Bridgers + boygenius" --order 1

Requires live Qloo (QLOO_MOCK=0) and a Gemini key: examples must show real data.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent.runner import agent_mode, run_agent  # noqa: E402
from app.config import settings  # noqa: E402
from app.examples import EXAMPLES_DIR  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("slug")
    ap.add_argument("artists", help="comma-separated")
    ap.add_argument("dates", type=int)
    ap.add_argument("--label", default="")
    ap.add_argument("--order", type=int, default=9)
    args = ap.parse_args()

    if settings.qloo_mock or agent_mode() != "gemini":
        sys.exit("Examples must be recorded with live Qloo data and the Gemini agent (QLOO_MOCK=0).")

    names = [a.strip() for a in args.artists.split(",") if a.strip()]
    events = []
    for ev in run_agent(names, args.dates):
        events.append(ev)
        line = ev.get("summary") or ev.get("text") or ev.get("message") or ""
        print(f"{ev['t'] / 1000:6.1f}s {ev['type']:10} {ev.get('tool', '')} {line[:90]}")
    if not any(e["type"] == "plan" for e in events):
        sys.exit("Run did not produce a plan; nothing saved.")

    done = next(e for e in events if e["type"] == "done")
    meta = {
        "label": args.label or " + ".join(names),
        "artists": names,
        "dates": args.dates,
        "order": args.order,
        "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "model": done.get("model"),
        "qloo_calls": done.get("qloo_calls"),
    }
    EXAMPLES_DIR.mkdir(exist_ok=True)
    out = EXAMPLES_DIR / f"{args.slug}.json"
    out.write_text(json.dumps({"meta": meta, "events": events}, separators=(",", ":")), encoding="utf-8")
    print(f"Saved {out} ({out.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
