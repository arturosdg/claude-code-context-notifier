#!/usr/bin/env python3
# Stop hook version — works with Claude Desktop app and CLI
# Calculates context usage by reading token counts from the session transcript.
#
# Add to ~/.claude/settings.json:
#
#   "hooks": {
#     "Stop": [
#       {
#         "hooks": [
#           {
#             "type": "command",
#             "command": "python3 ~/.claude/hooks/context-notifier-stop.py"
#           }
#         ]
#       }
#     ]
#   }

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


STATE_DIR = Path.home() / ".claude" / "state"
STATE_PATH = STATE_DIR / "context-notifier.json"
LOG_PATH = STATE_DIR / "context-notifier.log"
THRESHOLDS = {33, 50}
RESET_THRESHOLD = 25
DEFAULT_TITLE = "Claude Code context"

# Context window size in tokens per model family.
# All current Claude models share 200k; extend if needed.
MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "claude-opus-4": 200_000,
    "claude-sonnet-4": 200_000,
    "claude-haiku-4": 200_000,
    "claude-opus-3-5": 200_000,
    "claude-sonnet-3-5": 200_000,
    "claude-haiku-3-5": 200_000,
}
DEFAULT_CONTEXT_WINDOW = 200_000


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_error(message: str) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"{now_iso()} {message}\n")
    except OSError:
        pass


def load_payload() -> dict:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        log_error(f"invalid stdin json: {exc}")
        return {}
    return data if isinstance(data, dict) else {}


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {"sessions": {}}
    try:
        with STATE_PATH.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        log_error(f"invalid state file: {exc}")
        return {"sessions": {}}
    if not isinstance(data, dict):
        return {"sessions": {}}
    if not isinstance(data.get("sessions"), dict):
        data["sessions"] = {}
    return data


def save_state(state: dict) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        temp_path = STATE_PATH.with_suffix(".tmp")
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, sort_keys=True)
            handle.write("\n")
        temp_path.replace(STATE_PATH)
    except OSError as exc:
        log_error(f"failed to save state: {exc}")


def get_context_window(model_id: str) -> int:
    if not model_id:
        return DEFAULT_CONTEXT_WINDOW
    for prefix, size in MODEL_CONTEXT_WINDOWS.items():
        if model_id.startswith(prefix):
            return size
    return DEFAULT_CONTEXT_WINDOW


def read_transcript(transcript_path: str) -> tuple[Optional[int], Optional[str]]:
    """Parse transcript and return (total_tokens_used, model_id) from the last assistant turn."""
    try:
        p = Path(transcript_path)
        if not p.exists():
            return None, None

        last_usage = None
        last_model = None

        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                d = json.loads(line)
                msg = d.get("message", {})
                if msg.get("role") == "assistant" and "usage" in msg:
                    last_usage = msg["usage"]
                    last_model = msg.get("model")
            except (json.JSONDecodeError, AttributeError):
                continue

        if last_usage is None:
            return None, last_model

        total = (
            last_usage.get("input_tokens", 0)
            + last_usage.get("cache_creation_input_tokens", 0)
            + last_usage.get("cache_read_input_tokens", 0)
            + last_usage.get("output_tokens", 0)
        )
        return total, last_model

    except OSError as exc:
        log_error(f"transcript read failed: {exc}")
        return None, None


def normalize_percentage(value) -> Optional[int]:
    if value is None:
        return None
    try:
        pct = int(float(value))
    except (TypeError, ValueError):
        return None
    return max(0, min(100, pct))


def escape_applescript(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def notify(message: str, title: str) -> None:
    script = f'display notification "{escape_applescript(message)}" with title "{escape_applescript(title)}"'
    try:
        subprocess.run(["osascript", "-e", script], check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        log_error(f"osascript failed: {exc}")


def build_session_label(payload: dict) -> str:
    # Stop hook provides cwd but not workspace; derive project name from cwd
    cwd = payload.get("cwd") or ""
    return Path(cwd).name if cwd else DEFAULT_TITLE


def build_notification_message(used_percentage: int) -> str:
    return f"Contexto {used_percentage}%"


def build_notification_title(payload: dict) -> str:
    return build_session_label(payload) or DEFAULT_TITLE


def main() -> int:
    payload = load_payload()
    session_id = payload.get("session_id")
    transcript_path = payload.get("transcript_path")

    if not session_id or not transcript_path:
        return 0

    total_tokens, model_id = read_transcript(transcript_path)
    if total_tokens is None:
        return 0

    context_window = get_context_window(model_id or "")
    used_percentage = normalize_percentage((total_tokens / context_window) * 100)
    if used_percentage is None:
        return 0

    state = load_state()
    sessions = state.setdefault("sessions", {})
    session = sessions.get(session_id)

    if not isinstance(session, dict):
        session = {"notified_thresholds": [], "last_percentage": None}

    notified = {
        int(v)
        for v in session.get("notified_thresholds", [])
        if isinstance(v, int) or (isinstance(v, str) and v.isdigit())
    }
    last_percentage = normalize_percentage(session.get("last_percentage"))

    if used_percentage < RESET_THRESHOLD:
        notified.clear()

    # On first run last_percentage is None — treat as 0 so thresholds already
    # crossed at session start are still notified.
    effective_last = last_percentage if last_percentage is not None else 0
    for threshold in sorted(THRESHOLDS):
        if effective_last < threshold <= used_percentage and threshold not in notified:
            notify(
                build_notification_message(used_percentage),
                build_notification_title(payload),
            )
            notified.add(threshold)

    session.update({
        "last_percentage": used_percentage,
        "model_id": model_id,
        "total_tokens": total_tokens,
        "context_window": context_window,
        "transcript_path": transcript_path,
        "updated_at": now_iso(),
        "notified_thresholds": sorted(notified),
    })

    # Update current session without clearing others so parallel sessions
    # (multiple worktrees, Desktop + CLI) don't lose their notified state.
    # Prune to the 20 most recently updated sessions to bound file size.
    sessions[session_id] = session
    if len(sessions) > 20:
        sorted_ids = sorted(
            sessions,
            key=lambda k: sessions[k].get("updated_at", ""),
        )
        for old_id in sorted_ids[:-20]:
            del sessions[old_id]
    save_state(state)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
