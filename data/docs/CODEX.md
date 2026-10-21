# Codex CLI sessions

Vanilla Agents shows **Claude Code** and **Codex** sessions (CLI and desktop app) in the same inn. Claude keeps its hooks, MCP tools and status line; Codex is followed read-only from the local session files Codex writes itself. Nothing needs installing for that, and Codex settings are never edited.

## What appears in the inn

- Every recent Codex session for the selected project becomes a helper, with its own look, raid frame, chat lines and quest work.
- Claude Code and Codex run **together**: both watchers start for the selected project, and a Claude session started later is picked up too. Each helper stays separate; Resurrect chooses its own command.
- A Codex helper is marked `Codex` (not a Claude role) and its id starts with `codex-`.
- The watcher reports the start of a turn, tool names, model, output-token counts, turn end, an aborted turn and a compacted conversation. It never reports prompts, answers, reasoning, tool arguments or terminal output.
- Selecting another project restarts both watchers for that project.
- **Resurrect** chooses the right command for the helper: `codex resume <id>` for a Codex helper, `claude --resume <id>` for a Claude one.

## Where the session files are

Codex writes JSONL rollouts under `CODEX_HOME/sessions/YYYY/MM/DD/rollout-*.jsonl`. By default `CODEX_HOME` is `~/.codex`, so the watcher looks in `~/.codex/sessions` (`%USERPROFILE%\.codex\sessions` on Windows). The **Codex desktop app** writes the same files there (its rollouts say `"originator": "Codex Desktop"`), so its sessions appear the same way as CLI sessions.

The desktop app also keeps **live turn history in SQLite** in the Codex home: `state_*.sqlite` (threads) and `thread_history_*.sqlite` (turns and items). Vanilla Agents reads those read-only as well, because the app may stop appending to the JSONL rollout while a turn is still running. It selects only thread id, folder, model, branch, turn status and tool item **types**; it never selects titles, previews, first messages, `item_json`, prompts or tool arguments. The JSONL watcher skips sessions the SQLite watcher manages, so one session is never two helpers.

Settings live in `app/config/app.json` under `codex` (also listed in `docs/CHANGE-GUIDE.md`):

| Setting | Meaning |
|---|---|
| `codex.enabled` | Turn the Codex watcher on or off (default on; Claude is unaffected either way) |
| `codex.sessions_dir` | The sessions folder (blank = `CODEX_HOME/sessions`) |
| `codex.db_dir` | The Codex home with `state_*.sqlite` / `thread_history_*.sqlite` (blank = `CODEX_HOME` or `~/.codex`) |
| `codex.offsets_path` | Where read positions are kept (default `~/.vanilla-agents/work/codex-offsets.json`) |
| `codex.poll_seconds` | How often to look (minimum 1) |
| `codex.idle_minutes` | Silence after which a helper shows as away |
| `codex.recent_minutes` | How recent a session must be to appear |
| `codex.max_agents` | Most Codex helpers shown at once |

Environment overrides, mostly for tests and unusual installs:

| Variable | Purpose |
|---|---|
| `CODEX_HOME` | Codex home; the watcher reads its `sessions` subfolder |
| `VANILLA_CODEX_SESSIONS` | Use this sessions folder directly |
| `VANILLA_CODEX_DB_DIR` | Use this Codex home for the SQLite history |
| `VANILLA_CODEX_OFFSETS` | Use this read-position file |
| `VANILLA_CODEX_ENABLED=0` | Turn off only the Codex watcher; Claude keeps working |

Codex must keep local history: JSONL rollouts for the CLI, or the SQLite history of the desktop app. Ephemeral or cloud-only sessions do not leave either and cannot appear.

## Optional: give Codex the Vanilla Agents quest tools

Codex can use the same MCP tools as Claude (`status`, `list_quests`, `start_quest`, `report_progress`, `quest_ready`, `pending_requests`, `answer_request`, and so on). This is separate from the watcher and is not required for helpers to appear.

On Windows, run `connect-codex-mcp.cmd` once and restart Codex. Anywhere, run:

```powershell
cd data
python scripts/install_codex_mcp.py
```

The script uses `codex mcp add`. It refuses to overwrite a different server called `vanilla-agents`, and `--remove` removes only a registration pointing at this checkout. Claude's own registration is untouched. If you move the Vanilla Agents folder, run it again (`reconnect.py` re-points Claude's entries only). `uninstall.cmd` also disconnects the Codex registration when there is one.

Codex does not know which helper is asking, so its tool calls are not tied to a per-session agent id unless the model passes the `agent` argument. Tools that need a quest title or id work normally.

## Privacy

The watcher reads only the first line (the session header) to learn the session id and folder, then the new records of the file. Only the bounded facts above are written to the event feed. Prompts, answers, reasoning, tool arguments, source code and terminal output are dropped while reading and are never stored, logged or sent anywhere. Read positions are kept in `~/.vanilla-agents/work/codex-offsets.json`. See `docs/PRIVACY.md`.

## Limits and troubleshooting

See `docs/LIMITATIONS.md` (Codex section). If no Codex helper appears:

1. Check that Codex actually wrote a file under `CODEX_HOME/sessions` for the selected project.
2. Check that the project you selected in the window is the folder Codex ran in (or a folder inside it).
3. Check `codex.enabled` and `VANILLA_CODEX_ENABLED`.
4. Start a new Codex turn; the watcher only reads complete lines.

Tests: `python -m unittest tests.test_codex_watcher tests.test_codex_mcp_setup -v` from `data/`.
