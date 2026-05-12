# Claude Code Context Notifier

Small macOS notifier for Claude Code that uses `statusLine` input as a sensor for context usage.

It sends a native system notification when a session crosses:

- `33%` context used
- `50%` context used

The notification title is derived from the current project and worktree:

- `project-name | worktree-name`

The notification body shows:

- current context usage
- current 5-hour Claude quota usage when available

Example:

```text
Title: mo-library-desktop-ui | nostalgic-satoshi
Body:  Contexto 51% | cuota 5h 23.5%
```

## How It Works

Claude Code `statusLine` scripts receive structured JSON on stdin, including:

- `context_window.used_percentage`
- `workspace.project_dir`
- `worktree.name`
- `rate_limits.five_hour.used_percentage`

This script:

1. reads the JSON payload from stdin
2. stores per-session state in `~/.claude/state/context-notifier.json`
3. triggers `osascript` notifications only when thresholds are crossed
4. rearms after context drops below `25%`

## Install

Copy the script somewhere stable, for example:

```bash
mkdir -p ~/.claude/hooks
cp context-notifier.py ~/.claude/hooks/context-notifier.py
chmod +x ~/.claude/hooks/context-notifier.py
```

Then add this to `~/.claude/settings.json`:

```json
{
  "statusLine": {
    "type": "command",
    "command": "python3 ~/.claude/hooks/context-notifier.py"
  }
}
```

## Notes

- Designed for macOS because it uses `osascript`.
- It does not require Claude Code hooks.
- It intentionally keeps notifications short and avoids project-specific hardcoded paths.
