"""Deploy Tour Radar to a Hugging Face Space (Docker SDK).

    python scripts/deploy_space.py <user>/tour-radar

Needs `huggingface_hub` and a prior `hf auth login` with a write token.
Copies only what the container needs into a staging folder, adds the Space
README header, uploads it, and sets the keys from .env as Space secrets
(values are never printed).
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parent.parent
SHIP = ["app", "static", "Dockerfile", "requirements.txt", "LICENSE"]
SECRETS = ["QLOO_API_KEY", "GEMINI_API_KEY"]
VARIABLES = {"QLOO_MOCK": "0"}

SPACE_README = """---
title: Tour Radar
emoji: 🎸
colorFrom: yellow
colorTo: red
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: Plan a tour where your sound already has an audience
---

# Tour Radar

An agent for independent musicians: name the acts you sound like, and it uses the
Qloo Taste AI API to find where that taste is over-represented, routes a tour
through those cities, and picks neighbourhoods, venues and pre-show spots.

Source, docs and run instructions: https://github.com/DmytroVrd/tour-radar
"""


def read_env(path: Path) -> dict[str, str]:
    env = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def stage(dest: Path) -> None:
    for name in SHIP:
        src = ROOT / name
        if src.is_dir():
            shutil.copytree(src, dest / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(src, dest / name)
    (dest / "README.md").write_text(SPACE_README, encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo_id", help="e.g. username/tour-radar")
    ap.add_argument("--skip-secrets", action="store_true")
    args = ap.parse_args()

    api = HfApi()
    print(f"Logged in as {api.whoami()['name']}")
    api.create_repo(args.repo_id, repo_type="space", space_sdk="docker", exist_ok=True)

    if not args.skip_secrets:
        env = read_env(ROOT / ".env")
        missing = [k for k in SECRETS if not env.get(k)]
        if missing:
            sys.exit(f"Missing in .env: {', '.join(missing)}")
        for key in SECRETS:
            api.add_space_secret(args.repo_id, key, env[key])
        variables = dict(VARIABLES)
        for key in ("GEMINI_MODEL", "GEMINI_FALLBACK_MODELS"):
            if env.get(key):
                variables[key] = env[key]
        for key, value in variables.items():
            api.add_space_variable(args.repo_id, key, value)
        print(f"Secrets set: {', '.join(SECRETS)}; variables: {', '.join(variables)}")

    with tempfile.TemporaryDirectory() as tmp:
        stage(Path(tmp))
        api.upload_folder(repo_id=args.repo_id, repo_type="space", folder_path=tmp,
                          commit_message="Deploy Tour Radar", delete_patterns=["*"])
    print(f"Uploaded. Space: https://huggingface.co/spaces/{args.repo_id}")


if __name__ == "__main__":
    main()
