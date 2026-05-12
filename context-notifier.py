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

# Sorted tuple — no need to call sorted() on every run
THRESHOLDS = (33, 50)
RESET_THRESHOLD = 25
DEFAULT_TITLE = "Claude Code context"
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


def _iter_lines_reversed(path: Path, chunk_size: int = 8192):
    """Yield lines from a file in reverse order without loading it all into memory."""
    with path.open("rb") as f:
        f.seek(0, 2)
        remaining = f.tell()
        buf = b""
        while remaining > 0:
            read_size = min(chunk_size, remaining)
            remaining -= read_size
            f.seek(remaining)
            buf = f.read(read_size) + buf
            lines = buf.split(b"\n")
            buf = lines[0]  # may be a partial line — carry it over
            for line in reversed(lines[1:]):
                yield line.decode("utf-8", errors="replace")
        if buf:
            yield buf.decode("utf-8", errors="replace")


def read_transcript(transcript_path: str) -> tuple[Optional[int], Optional[str]]:
    """Return (total_tokens, model_id) from the last assistant turn in the transcript.

    Reads the file backwards so it stops after the first (last) matching entry,
    avoiding loading the entire transcript into memory.
    """
    try:
        p = Path(transcript_path)
        if not p.exists():
            return None, None

        for line in _iter_lines_reversed(p):
            if not line.strip():
                continue
            try:
                d = json.loads(line)
                msg = d.get("message", {})
                if msg.get("role") == "assistant" and "usage" in msg:
                    usage = msg["usage"]
                    total = (
                        usage.get("input_tokens", 0)
                        + usage.get("cache_creation_input_tokens", 0)
                        + usage.get("cache_read_input_tokens", 0)
                        + usage.get("output_tokens", 0)
                    )
                    return total, msg.get("model")
            except (json.JSONDecodeError, AttributeError):
                continue

        return None, None

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


def build_notification_title(payload: dict) -> str:
    cwd = payload.get("cwd") or ""
    return Path(cwd).name if cwd else DEFAULT_TITLE


def main() -> int:
    payload = load_payload()
    session_id = payload.get("session_id")
    transcript_path = payload.get("transcript_path")

    if not session_id or not transcript_path:
        return 0

    # --- Load state and session ---
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

    # --- Early exit: nothing can fire, skip transcript read entirely ---
    # All thresholds already notified and context hasn't reset below RESET_THRESHOLD.
    if (
        last_percentage is not None
        and last_percentage >= RESET_THRESHOLD
        and all(t in notified for t in THRESHOLDS)
    ):
        return 0

    # --- Read transcript (backwards — stops at first match) ---
    total_tokens, model_id = read_transcript(transcript_path)
    if total_tokens is None:
        return 0

    used_percentage = normalize_percentage((total_tokens / DEFAULT_CONTEXT_WINDOW) * 100)
    if used_percentage is None:
        return 0

    # --- Check thresholds ---
    if used_percentage < RESET_THRESHOLD:
        notified.clear()

    # Treat None last_percentage as 0 so thresholds already crossed at session
    # start are notified on the first run.
    effective_last = last_percentage if last_percentage is not None else 0
    for threshold in THRESHOLDS:
        if effective_last < threshold <= used_percentage and threshold not in notified:
            notify(
                f"Contexto {used_percentage}%",
                build_notification_title(payload),
            )
            notified.add(threshold)

    # --- Persist only if something changed ---
    new_notified = sorted(notified)
    if used_percentage == last_percentage and new_notified == session.get("notified_thresholds", []):
        return 0

    session.update({
        "last_percentage": used_percentage,
        "model_id": model_id,
        "updated_at": now_iso(),
        "notified_thresholds": new_notified,
    })

    sessions[session_id] = session
    if len(sessions) > 20:
        sorted_ids = sorted(sessions, key=lambda k: sessions[k].get("updated_at", ""))
        for old_id in sorted_ids[:-20]:
            del sessions[old_id]
    save_state(state)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
