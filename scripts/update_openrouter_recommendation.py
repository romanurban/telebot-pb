#!/usr/bin/env python3
"""Rotate the OpenRouter model in local .env.* files to the best free model
that passes a preflight check right now.

Run by `start.sh autoupdate` every cycle; the actual rotation is throttled to
once per OPENROUTER_ROTATE_HOURS (default 12) via a timestamp file.
`--force` skips the throttle.

1. Fetch the ranked free-model list (shir-man's API).
2. Walk candidates in rank order (tool-capable only, skipping OPENROUTER_MODEL_BLOCKLIST).
3. Preflight each one against OpenRouter with the bots' own API key:
   - answers a Russian chat prompt in Russian (no CJK / English words leaking)
   - performs a tool call when asked
   - responds within PREFLIGHT_TIMEOUT seconds
4. The first candidate that passes becomes OPENROUTER_MODEL in every env file
   that already uses OpenRouter. If none passes, nothing changes.

Exit codes:
  0  = no changes (or throttled)
  10 = env files changed
  1  = fetch/parse/update failed
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

URL = "https://shir-man.com/api/free-llm/top-models"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
USER_AGENT = "telebot-openrouter-updater/1.0"
FALLBACK_DEFAULT = "openrouter/free"
ENV_GLOB = ".env.*"
STAMP_FILE = Path(".pids/openrouter_rotation.json")
ROTATE_HOURS = float(os.getenv("OPENROUTER_ROTATE_HOURS", "12"))
PREFLIGHT_TIMEOUT = 60
MAX_CANDIDATES = 4  # each candidate costs 2 requests of the free daily quota

CJK_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")
LATIN_WORD_RE = re.compile(r"[A-Za-z]{3,}")
CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")

LANG_PROMPT = (
    "Ты обычный парень из Риги в телеграм-чате с друзьями. В 2–3 коротких предложениях "
    "расскажи, как прошли выходные и что ел на ужин, и пошути про погоду. "
    "Только по-русски, без английских слов."
)
TOOL_PROMPT = "Который сейчас час в Риге? Используй инструмент."
TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "get_current_datetime",
        "description": "Return the current Riga time with the weekday name.",
        "parameters": {"type": "object", "properties": {}},
    },
}


def log(msg: str) -> None:
    print(f"[openrouter-rotate] {msg}", flush=True)


def fetch_top_models() -> dict:
    req = urllib.request.Request(URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_candidates(payload: dict, blocklist: set[str] | None = None) -> tuple[list[str], str]:
    """Return (ranked tool-capable model ids, fallback model id)."""
    blocklist = blocklist or set()
    models = payload.get("models") or []
    candidates = [
        m["id"]
        for m in models
        if isinstance(m, dict) and m.get("id")
        and m.get("supportsTools", True)
        and m["id"] not in blocklist
    ]
    if not candidates:
        raise RuntimeError("no candidate models in recommendation")
    fallback_data = payload.get("fallback")
    if isinstance(fallback_data, dict):
        fallback = fallback_data.get("id") or FALLBACK_DEFAULT
    elif isinstance(fallback_data, str) and fallback_data:
        fallback = fallback_data
    else:
        fallback = FALLBACK_DEFAULT
    return candidates, fallback


def check_language(text: str) -> str | None:
    """Return a failure reason, or None if the text is clean Russian."""
    if not text or not text.strip():
        return "empty reply"
    if CJK_RE.search(text):
        return "CJK characters in reply"
    if not CYRILLIC_RE.search(text):
        return "no Cyrillic in reply"
    if len(LATIN_WORD_RE.findall(text)) >= 2:  # one brand/name is fine
        return "Latin words in reply"
    return None


def _chat(api_key: str, model: str, messages: list[dict], tools: list | None = None) -> dict:
    body = {"model": model, "messages": messages, "max_tokens": 4000}  # room for reasoning models
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    req = urllib.request.Request(
        OPENROUTER_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(req, timeout=PREFLIGHT_TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if "error" in data:
        raise RuntimeError(f"api error: {data['error']}")
    return data["choices"][0]["message"]


def preflight(api_key: str, model: str) -> str | None:
    """Return a failure reason, or None if the model passed."""
    try:
        msg = _chat(api_key, model, [{"role": "user", "content": LANG_PROMPT}])
        reason = check_language(msg.get("content") or "")
        if reason:
            return f"{reason}: {(msg.get('content') or '')[:80]!r}"
        msg = _chat(api_key, model, [{"role": "user", "content": TOOL_PROMPT}], tools=[TOOL_DEF])
        if not msg.get("tool_calls"):
            return "no tool call"
    except Exception as exc:  # network, HTTP 4xx/5xx, timeout, bad JSON
        return f"request failed: {exc}"
    return None


def read_env_value(path: Path, key: str) -> str | None:
    m = re.search(rf"^{key}=(.*)$", path.read_text(encoding="utf-8"), flags=re.M)
    return m.group(1).strip().strip('"').strip("'") if m else None


def openrouter_env_files(root: Path) -> list[Path]:
    return [
        p for p in sorted(root.glob(ENV_GLOB))
        if p.name != ".env.example" and ".bak" not in p.name
        # only bots that already opted into OpenRouter (never switch an OpenAI bot)
        and re.search(r"^OPENROUTER_MODEL=", p.read_text(encoding="utf-8"), flags=re.M)
        and "OPENROUTER_API_KEY=" in p.read_text(encoding="utf-8")
    ]


def update_env_file(path: Path, primary: str, fallback: str) -> bool:
    text = path.read_text(encoding="utf-8")
    if "OPENROUTER_API_KEY=" not in text:
        return False

    new_text = text
    for key, value in (("OPENROUTER_MODEL", primary), ("OPENROUTER_FALLBACK_MODEL", fallback)):
        if re.search(rf"^{key}=.*$", new_text, flags=re.M):
            new_text = re.sub(rf"^{key}=.*$", f"{key}={value}", new_text, flags=re.M)
        else:
            if not new_text.endswith("\n"):
                new_text += "\n"
            new_text += f"{key}={value}\n"

    if new_text == text:
        return False
    path.write_text(new_text, encoding="utf-8")
    return True


def rotation_due(now: float) -> bool:
    try:
        last = json.loads(STAMP_FILE.read_text())["checked_at"]
    except Exception:
        return True
    return now - last >= ROTATE_HOURS * 3600


def write_stamp(now: float, model: str | None, rejected: dict[str, str]) -> None:
    STAMP_FILE.parent.mkdir(parents=True, exist_ok=True)
    STAMP_FILE.write_text(json.dumps(
        {"checked_at": now, "model": model, "rejected": rejected}, ensure_ascii=False, indent=1
    ))


def pick_model(api_key: str, candidates: list[str], check=preflight) -> tuple[str | None, dict[str, str]]:
    rejected: dict[str, str] = {}
    for model in candidates[:MAX_CANDIDATES]:
        reason = check(api_key, model)
        if reason is None:
            log(f"preflight PASS {model}")
            return model, rejected
        log(f"preflight FAIL {model}: {reason}")
        rejected[model] = reason
    return None, rejected


def main() -> int:
    root = Path.cwd()
    now = time.time()
    if not rotation_due(now) and "--force" not in sys.argv:
        return 0

    env_files = openrouter_env_files(root)
    if not env_files:
        log("no env files use OpenRouter")
        return 0
    api_key = read_env_value(env_files[0], "OPENROUTER_API_KEY")
    current = read_env_value(env_files[0], "OPENROUTER_MODEL")

    blocklist = {m.strip() for m in os.getenv("OPENROUTER_MODEL_BLOCKLIST", "").split(",") if m.strip()}
    candidates, fallback = parse_candidates(fetch_top_models(), blocklist)
    log(f"current={current} candidates={candidates[:MAX_CANDIDATES]}")

    primary, rejected = pick_model(api_key, candidates)
    write_stamp(now, primary, rejected)
    if primary is None:
        log("no candidate passed preflight; keeping current model")
        return 0

    changed = [p.name for p in env_files if update_env_file(p, primary, fallback)]
    if changed:
        log(f"rotated {current} -> {primary} (fallback={fallback}) files={','.join(changed)}")
        return 10
    log(f"keeping {primary}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"update_openrouter_recommendation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
