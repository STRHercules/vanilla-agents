# How Vanilla Agents connects to Claude and Codex

| Path | Direction | What it carries | Set up by |
|---|---|---|---|
| Hooks (`scripts/claude_hook.py`) | Claude Code -> window | every session start/stop, tool use, file edits, prompts; prints your open Change/Investigate requests into the session | `scripts/install_hooks.py` |
| Transcripts (`events/transcripts.py`) | Claude Code -> window | live sessions without hooks, read-only from `~/.claude/projects` | nothing (automatic) |
| Codex sessions (`events/codex.py`, `events/codex_db.py`, V-053/V-056) | Codex CLI + desktop app -> window | recent Codex sessions per project, read-only from `CODEX_HOME/sessions` and the app's `state_*.sqlite` / `thread_history_*.sqlite`; turn start/end, tool names/types, model, output tokens; never prompts, reasoning or `item_json` | nothing (automatic; see `docs/CODEX.md`) |
| **MCP tools** (`vanilla_agents/mcp.py`) | **both ways** | Claude reads status/quests and the checker's findings, starts quests, reports progress, hands quests in, answers your requests | `scripts/install_mcp.py` |
| Codex MCP tools (same server) | **both ways** | optional: the same quest/status tools for Codex CLI | `scripts/install_codex_mcp.py` |
| Window buttons / `scripts/decide.py` | you -> program | approve, change, investigate, verify, pause, done elsewhere | - |

The Claude and Codex watchers run together for the selected project. A Claude project folder that does not exist when the project is opened is watched for as soon as the session starts; no reselection is needed.

## MCP tools
`status`, `list_quests` (with check results and "sent back" evidence), `start_quest`, `report_progress`, `quest_ready`,
`pending_requests`, `answer_request`, `done_for_now`, `propose_fix` (V-045: a fix for a quest where you let several helpers try; proposals only). The server tells every session the working agreement (start or pick a
quest, report in plain words, hand in with the quest id in the commit message, check your requests).

Where it works:
- **Claude Code** on this PC: `claude mcp add --scope user vanilla-agents -- python <repo>/scripts/vanilla_mcp.py` (the installer does it).
- **Claude desktop app**: entry `vanilla-agents` in `claude_desktop_config.json` (the installer adds it with a backup; restart the app).
- **Cloud sessions linked to this PC** through the desktop app reach the same local tools.
- **Codex CLI**: `python scripts/install_codex_mcp.py` (or `connect-codex-mcp.cmd` on Windows) once, then restart Codex. It uses `codex mcp add` and never replaces a different server with the same name; `--remove` disconnects only this checkout. Codex tools are not tied to a helper unless the model passes the `agent` argument. See `docs/CODEX.md`.

Safety line (deliberate): agents can add and hand in quests and propose competing fixes, but cannot approve, verify, pause, mark work done, open a competition or pick the winning fix. A quest an
agent creates starts as "needs your decision"; only your click (the window key) or `scripts/decide.py` on your word approves it,
and only the trusted checker completes it.

## Feature check (2026-10-18)
Working end to end: quest log with approval tiers and the Change/Investigate loop, hooks + transcripts, Verify now with each
project's trusted checks and hidden checks, world map from git, inventory of reports and changed files, sounds, the inn with
30 heroes, options window, now the MCP tools.
Built but switched off: launching helper agents automatically (the durable scheduler/watchdog/recovery engine is tested, nothing
starts agents), helm/shoulder/weapon models on heroes. Not there yet: per-agent token use for the raid frames' blue bar, a
scheduled (automatic) Verify after `quest_ready`.
