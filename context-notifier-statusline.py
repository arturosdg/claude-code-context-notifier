#!/usr/bin/env python3
# statusLine hook version — CLI only (claude terminal interactive mode)
# Add to ~/.claude/settings.json:
#
#   "statusLine": {
#     "type": "command",
#     "command": "python3 ~/.claude/hooks/context-notifier-statusline.py"
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
    workspace = payload.get("workspace") or {}
    worktree = payload.get("worktree") or {}
    project_dir = workspace.get("project_dir")
    project_name = Path(project_dir).name if project_dir else "proyecto"
    worktree_name = worktree.get("name")
    if worktree_name:
        return f"{project_name} | {worktree_name}"
    return project_name


def build_notification_message(payload: dict, used_percentage: int, threshold: int) -> str:
    del threshold
    rate_limits = payload.get("rate_limits") or {}
    five_hour = (rate_limits.get("five_hour") or {}).get("used_percentage")
    quota_text = "cuota n/d"
    if five_hour is not None:
        try:
            quota_text = f"cuota 5h {float(five_hour):.1f}%"
        except (TypeError, ValueError):
            quota_text = "cuota n/d"
    return f"Contexto {used_percentage}% | {quota_text}"


def build_notification_title(payload: dict) -> str:
    return build_session_label(payload) or DEFAULT_TITLE


def main() -> int:
    payload = load_payload()
    context = payload.get("context_window") or {}
    session_id = payload.get("session_id")
    used_percentage = normalize_percentage(context.get("used_percentage"))

    if not session_id or used_percentage is None:
        print("")
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
                build_notification_message(payload, used_percentage, threshold),
                build_notification_title(payload),
            )
            notified.add(threshold)

    session.update({
        "last_percentage": used_percentage,
        "model_id": (payload.get("model") or {}).get("id"),
        "transcript_path": payload.get("transcript_path"),
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

    print("")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
