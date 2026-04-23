#!/usr/bin/env python3
"""Refresh OpenRouter model recommendation in local .env.* files.

Reads the current recommendation from shir-man's API and updates any
bot env files that already use OpenRouter so they point at the current primary model
plus a generic fallback router.

Exit codes:
  0 = no changes
  10 = env files changed
  1 = fetch/parse/update failed
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

URL = "https://shir-man.com/api/free-llm/top-models"
USER_AGENT = "telebot-openrouter-updater/1.0"
FALLBACK_DEFAULT = "openrouter/free"
ENV_GLOB = ".env.*"


def fetch_top_models() -> dict:
    req = urllib.request.Request(URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_models(payload: dict) -> tuple[str, str]:
    models = payload.get("models") or []
    if not models or not isinstance(models[0], dict) or not models[0].get("id"):
        raise RuntimeError("primary recommendation not found")
    primary = models[0]["id"]
    fallback_data = payload.get("fallback")
    if isinstance(fallback_data, dict):
        fallback = fallback_data.get("id") or FALLBACK_DEFAULT
    elif isinstance(fallback_data, str):
        fallback = fallback_data
    else:
        fallback = FALLBACK_DEFAULT
    return primary, fallback


def update_env_file(path: Path, primary: str, fallback: str) -> bool:
    text = path.read_text(encoding="utf-8")
    if "OPENROUTER_API_KEY=" not in text:
        return False

    new_text = text
    if re.search(r"^OPENROUTER_MODEL=.*$", new_text, flags=re.M):
        new_text = re.sub(r"^OPENROUTER_MODEL=.*$", f"OPENROUTER_MODEL={primary}", new_text, flags=re.M)
    else:
        if not new_text.endswith("\n"):
            new_text += "\n"
        new_text += f"OPENROUTER_MODEL={primary}\n"

    if re.search(r"^OPENROUTER_FALLBACK_MODEL=.*$", new_text, flags=re.M):
        new_text = re.sub(r"^OPENROUTER_FALLBACK_MODEL=.*$", f"OPENROUTER_FALLBACK_MODEL={fallback}", new_text, flags=re.M)
    else:
        if not new_text.endswith("\n"):
            new_text += "\n"
        new_text += f"OPENROUTER_FALLBACK_MODEL={fallback}\n"

    if new_text == text:
        return False

    path.write_text(new_text, encoding="utf-8")
    return True


def main() -> int:
    root = Path.cwd()
    payload = fetch_top_models()
    primary, fallback = parse_models(payload)

    changed = []
    for path in sorted(root.glob(ENV_GLOB)):
        if path.name == ".env.example":
            continue
        if update_env_file(path, primary, fallback):
            changed.append(path.name)

    if changed:
        print(f"updated primary={primary} fallback={fallback} files={','.join(changed)}")
        return 10

    print(f"no changes primary={primary} fallback={fallback}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"update_openrouter_recommendation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
