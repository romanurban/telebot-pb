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

import base64
import json
import os
import re
import struct
import sys
import time
import urllib.request
import zlib
from pathlib import Path

URL = "https://shir-man.com/api/free-llm/top-models"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
VISION_ENV_KEY = "OPENROUTER_VISION_MODEL"
VISION_EXCLUDE = ("safety", "lyria", "openrouter/")  # not general vision chat models
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


def _red_square_png(size: int = 64) -> str:
    """A solid red PNG as a data URL, built with stdlib only."""
    row = b"\x00" + b"\xff\x00\x00" * size
    raw = zlib.compress(row * size)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", raw) + chunk(b"IEND", b""))
    return "data:image/png;base64," + base64.b64encode(png).decode()


VISION_TEST_IMAGE = _red_square_png()
VISION_PROMPT = "Какого цвета этот квадрат? Ответь одним словом."


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


def fetch_free_vision_models() -> list[str]:
    req = urllib.request.Request(OPENROUTER_MODELS_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8")).get("data", [])

    def is_free(m: dict) -> bool:
        pricing = m.get("pricing") or {}
        try:
            return all(float(pricing.get(k) or 0) == 0 for k in ("prompt", "completion", "image", "request"))
        except (TypeError, ValueError):
            return False

    return [
        m["id"] for m in data
        if "image" in (m.get("architecture") or {}).get("input_modalities", [])
        and is_free(m)
        and not any(x in m["id"] for x in VISION_EXCLUDE)
    ]


def order_vision_candidates(ranked: list[str], free_vision: list[str]) -> list[str]:
    """Free vision models, the ones the ranking rates first, then the rest."""
    vision = set(free_vision)
    first = [m for m in ranked if m in vision]
    return first + [m for m in free_vision if m not in first]


def vision_preflight(api_key: str, model: str) -> str | None:
    """Return a failure reason, or None if the model really sees the test image."""
    try:
        msg = _chat(api_key, model, [{"role": "user", "content": [
            {"type": "text", "text": VISION_PROMPT},
            {"type": "image_url", "image_url": {"url": VISION_TEST_IMAGE}},
        ]}])
    except Exception as exc:
        return f"request failed: {exc}"
    answer = msg.get("content") or ""
    if not re.search(r"красн|red", answer, re.IGNORECASE):
        return f"did not see red square: {answer[:60]!r}"
    return None


def update_env_key(path: Path, key: str, value: str) -> bool:
    text = path.read_text(encoding="utf-8")
    if re.search(rf"^{key}=.*$", text, flags=re.M):
        new_text = re.sub(rf"^{key}=.*$", f"{key}={value}", text, flags=re.M)
    else:
        new_text = text + ("" if text.endswith("\n") else "\n") + f"{key}={value}\n"
    if new_text == text:
        return False
    path.write_text(new_text, encoding="utf-8")
    return True


def vision_env_files(root: Path) -> list[Path]:
    """.env (read by the MCP server's analyze_image) plus bot files that set the key."""
    files = [root / ".env"] if (root / ".env").exists() else []
    files += [
        p for p in sorted(root.glob(ENV_GLOB))
        if p.name != ".env.example" and ".bak" not in p.name
        and re.search(rf"^{VISION_ENV_KEY}=", p.read_text(encoding="utf-8"), flags=re.M)
    ]
    return files


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
    changed: list[str] = []
    if primary is None:
        log("no candidate passed preflight; keeping current model")
    else:
        changed += [p.name for p in env_files if update_env_file(p, primary, fallback)]
        log(f"rotated {current} -> {primary} (fallback={fallback})" if changed else f"keeping {primary}")

    # Vision model used by analyze_image (MCP server) for photos posted in chat.
    vision = None
    try:
        vision_candidates = order_vision_candidates(candidates, fetch_free_vision_models())
        log(f"vision candidates={vision_candidates[:MAX_CANDIDATES]}")
        vision, vision_rejected = pick_model(api_key, vision_candidates, check=vision_preflight)
        rejected.update({f"vision:{k}": v for k, v in vision_rejected.items()})
    except Exception as exc:
        log(f"vision rotation failed: {exc}")
    if vision:
        vchanged = [p.name for p in vision_env_files(root) if update_env_key(p, VISION_ENV_KEY, vision)]
        changed += vchanged
        log(f"vision model -> {vision} files={','.join(vchanged)}" if vchanged else f"keeping vision {vision}")

    write_stamp(now, primary, rejected)
    return 10 if changed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"update_openrouter_recommendation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
