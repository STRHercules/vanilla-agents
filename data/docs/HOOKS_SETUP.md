# Connecting Claude Code

`python scripts/install_hooks.py` adds entries to your Claude Code settings (`~/.claude/settings.json`) for SessionStart,
UserPromptSubmit, PreToolUse, PostToolUse, Notification, Stop and SubagentStop. Each runs `scripts/claude_hook.py`, which reads
the hook JSON on stdin, appends plain events to the feed and always exits 0 with no output (3 s watchdog), so it can never
block or break a session. It keeps a timestamped backup of your settings, leaves your other hooks alone, and is safe to run
twice. `--remove` takes the entries out again; `--dry-run` shows the result without writing.

Feed file: env `VANILLA_FEED`, else `~/.vanilla-agents/feed.jsonl` (the program reads the same default). Optional env
`VANILLA_TASK` tags events with a quest id.

Without hooks the program still shows live sessions by reading Claude Code's own transcript files read-only
(`vanilla_agents/events/transcripts.py`), just with less detail.

## "Explain it more" / "Change it"
At SessionStart and UserPromptSubmit the hook also prints the still-open requests from the program window (Claude Code
adds hook output to the conversation), each with the exact command to answer it:
`python scripts/answer_explain.py <quest-id> "<plain answer>"` or `... <quest-id> --changed "<what changed>"`.
Decisions from a session when you are away from the PC: `python scripts/decide.py approve|pause|yes ... --project <name>`
(recorded as "you (via phone session)").

Check it works: run any Claude Code command, then look at the inn (a new hero walks in) or at the last line of the feed file.

## Real usage numbers: the status line bridge (opt-in, V-030)
Hooks do not see cost or plan limits; Claude Code's status line does. `python scripts/install_statusline.py` (or
`setup.py --statusline`) points your `statusLine` setting at `scripts/statusline.py`, with a backup of your settings. It sends a
`session_stats` event per session at most every 20 s (model, conversation memory used, session cost, lines added/removed, 5-hour
and weekly plan use with reset times; any of them may be missing, e.g. plan limits only exist for Pro/Max after the first reply),
then prints the status line you had before (saved in `statusline.json` next to the feed, run with the same input, 2 s at most) or
`⚔ <model> · ctx 43% · 5h 12%`. In the window: helper mana = 100 - real memory use, the player frame's blue bar = the 5-hour
limit, and the damage meter (U) gets a Cost tab. `--remove` puts your old status line back. `vanilla-agents.cmd` re-points it if
the folder moved (scripts/reconnect.py).

## Plugin instead of installers (V-031)
`data/plugin/` packages the hooks and the tools as a Claude Code plugin: see docs/PLUGIN.md. Use the plugin or the installers, not both.
