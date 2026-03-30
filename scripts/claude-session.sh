#!/usr/bin/env bash
set -euo pipefail

SESSION_FILE="${CLAUDE_SESSION_FILE:-.claude-code-session}"
CLAUDE_BIN="${CLAUDE_BIN:-$HOME/.local/bin/claude}"
PERMISSION_MODE="${CLAUDE_PERMISSION_MODE:-bypassPermissions}"
AUTO_MODE_FLAG="${CLAUDE_AUTO_MODE_FLAG:---enable-auto-mode}"
DANGEROUS_FLAG="${CLAUDE_DANGEROUS_FLAG:---dangerously-skip-permissions}"
OUTPUT_FORMAT="${CLAUDE_OUTPUT_FORMAT:-json}"

usage() {
  cat <<'EOF'
Usage:
  scripts/claude-session.sh run "prompt"
  scripts/claude-session.sh status
  scripts/claude-session.sh clear
  scripts/claude-session.sh id

Commands:
  run     Start or resume a Claude Code session for this project and send a prompt
  status  Show saved session id if present
  id      Print saved session id only
  clear   Remove saved session id

Environment:
  CLAUDE_BIN              Claude executable (default: claude)
  CLAUDE_SESSION_FILE     Session id file (default: .claude-code-session)
  CLAUDE_PERMISSION_MODE  Permission mode (default: bypassPermissions)
  CLAUDE_OUTPUT_FORMAT    Output format (default: json)
EOF
}

have_session() {
  [[ -f "$SESSION_FILE" ]] && [[ -s "$SESSION_FILE" ]]
}

read_session() {
  tr -d '[:space:]' < "$SESSION_FILE"
}

save_session_from_json() {
  python3 -c 'import json, sys
session_file = sys.argv[1]
raw = sys.stdin.read().strip()
if not raw:
    raise SystemExit(1)
obj = json.loads(raw)
session_id = obj.get("session_id")
if session_id:
    open(session_file, "w", encoding="utf-8").write(session_id + "\n")
print(raw)
' "$SESSION_FILE"
}

cmd_run() {
  local prompt="${1:-}"
  if [[ -z "$prompt" ]]; then
    echo "Prompt is required." >&2
    exit 1
  fi

  local output
  if have_session; then
    local sid
    sid="$(read_session)"
    output="$($CLAUDE_BIN $AUTO_MODE_FLAG $DANGEROUS_FLAG --resume "$sid" --permission-mode "$PERMISSION_MODE" --print --output-format "$OUTPUT_FORMAT" "$prompt")"
  else
    output="$($CLAUDE_BIN $AUTO_MODE_FLAG $DANGEROUS_FLAG --permission-mode "$PERMISSION_MODE" --print --output-format "$OUTPUT_FORMAT" "$prompt")"
  fi

  printf '%s' "$output" | save_session_from_json
}

cmd_status() {
  if have_session; then
    echo "saved_session_id=$(read_session)"
  else
    echo "saved_session_id="
  fi
}

cmd_id() {
  if have_session; then
    read_session
  fi
}

cmd_clear() {
  rm -f "$SESSION_FILE"
  echo "cleared"
}

main() {
  local cmd="${1:-}"
  shift || true
  case "$cmd" in
    run) cmd_run "$*" ;;
    status) cmd_status ;;
    id) cmd_id ;;
    clear) cmd_clear ;;
    -h|--help|help|"") usage ;;
    *)
      echo "Unknown command: $cmd" >&2
      usage
      exit 1
      ;;
  esac
}

main "$@"
