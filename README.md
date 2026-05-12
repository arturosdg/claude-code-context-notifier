# Claude Code Context Notifier

## Why this exists

Claude Code has a finite context window. When it fills up, Claude starts forgetting earlier parts of the conversation — tool outputs, decisions, code it already wrote. This happens silently, without any warning, and it's easy to miss while you're focused on a task.

This hook sends a native macOS notification when your session crosses context usage thresholds, so you can decide to `/clear`, start a new session, or wrap up before things degrade.

```text
Title: mo-library-desktop-ui
Body:  Context 51%
```

Thresholds: **33%** and **50%**.

---

## Two scripts, two hooks

Claude Code exposes different hooks depending on where it runs. There are two variants:

| Script | Hook | Where it works | Context source |
|---|---|---|---|
| `context-notifier-statusline.py` | `statusLine` | CLI only | `context_window.used_percentage` from payload |
| `context-notifier-stop.py` | `Stop` | CLI + Desktop app | Token counts parsed from session transcript |

> **Why two?** The `statusLine` hook is a CLI-only feature — it fires continuously in the terminal and receives context window data directly in its payload. Claude Desktop app never invokes it.
>
> The `Stop` hook fires at the end of every turn in both environments, but its payload does not include context window data. The Stop variant works around this by reading `input + cache + output` token counts from the `.jsonl` session transcript and dividing by the model's context window size (200k for all current Claude models).

---

## Install

```bash
mkdir -p ~/.claude/hooks
cp context-notifier-stop.py ~/.claude/hooks/context-notifier.py     # Desktop + CLI
# or
cp context-notifier-statusline.py ~/.claude/hooks/context-notifier.py  # CLI only
chmod +x ~/.claude/hooks/context-notifier.py
```

### Option A — Stop hook (Desktop app + CLI)

Add to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/.claude/hooks/context-notifier.py"
          }
        ]
      }
    ]
  }
}
```

### Option B — statusLine hook (CLI only)

Add to `~/.claude/settings.json`:

```json
{
  "statusLine": {
    "type": "command",
    "command": "python3 ~/.claude/hooks/context-notifier.py"
  }
}
```

---

## How each variant works

### Stop variant

The `Stop` hook fires after every Claude turn. Its payload includes `transcript_path` but no context window data. The script:

1. Loads per-session state — if all thresholds are already notified and context hasn't reset, exits immediately without touching the transcript
2. Reads the `.jsonl` transcript **backwards** in 8KB chunks, stopping at the first (last) assistant message
3. Sums `input_tokens + cache_creation_input_tokens + cache_read_input_tokens + output_tokens`
4. Divides by 200,000 to get the usage percentage
5. Fires a notification if any threshold is newly crossed
6. Skips writing state to disk if nothing changed

### statusLine variant

The `statusLine` fires continuously in the CLI terminal. Its payload includes `context_window.used_percentage` directly — no transcript parsing needed. The notification body also includes 5-hour quota usage when available:

```text
Context 51% | 5h quota 23.5%
```

---

## State

Both scripts store per-session state in `~/.claude/state/context-notifier.json`:

- Each session (worktree, Desktop window, CLI tab) tracks its thresholds independently
- Notifications fire only when a threshold is **crossed**, not on every turn
- Thresholds reset when context drops below `25%` (e.g. after `/clear`)
- Up to 20 sessions are kept; older ones are pruned automatically
- Errors are logged to `~/.claude/state/context-notifier.log`

---

## Notes

- macOS only — uses `osascript` for system notifications.
- No external dependencies beyond Python 3 (included with macOS).
- Works across machines — no hardcoded paths.
