# Claude Code Context Notifier

macOS notifier for Claude Code that sends a native system notification when a session crosses context usage thresholds:

- `33%` context used
- `50%` context used

The notification title is derived from the current project and worktree (CLI) or working directory (Desktop):

```text
Title: mo-library-desktop-ui
Body:  Contexto 51%
```

---

## Two scripts, two hooks

There are two variants depending on how you run Claude Code:

| Script | Hook | Where it works | Context source |
|---|---|---|---|
| `context-notifier-statusline.py` | `statusLine` | CLI only | `context_window.used_percentage` from payload |
| `context-notifier-stop.py` | `Stop` | CLI + Desktop app | Token counts parsed from session transcript |

> **Note:** `statusLine` is a CLI-only feature — it is never invoked by Claude Desktop app.  
> The `Stop` hook fires at the end of every turn in both environments, but its payload does not include context window data, so the Stop variant derives the percentage by reading `input + cache + output` tokens from the `.jsonl` transcript.

---

## Install

Copy the script you want to use:

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

### statusLine variant

Claude Code CLI passes a JSON payload on stdin with:

- `context_window.used_percentage` — direct percentage, no calculation needed
- `workspace.project_dir`, `worktree.name`
- `rate_limits.five_hour.used_percentage`

The notification body includes the 5-hour quota usage when available:

```text
Contexto 51% | cuota 5h 23.5%
```

### Stop variant

The `Stop` hook payload includes `transcript_path` but no context window data. The script:

1. Reads the `.jsonl` transcript
2. Finds the last assistant message's `usage` field
3. Sums `input_tokens + cache_creation_input_tokens + cache_read_input_tokens + output_tokens`
4. Divides by the model's context window size (200k for all current Claude models)

---

## State

Both scripts store per-session state in `~/.claude/state/context-notifier.json`:

- Notifications fire only when a threshold is crossed (not repeatedly)
- Thresholds reset when context drops below `25%`

---

## Notes

- Designed for macOS — uses `osascript` for system notifications.
- Errors are logged to `~/.claude/state/context-notifier.log`.
- Avoid project-specific hardcoded paths so the script works across machines.
