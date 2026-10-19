# AGENTS.md - Vanilla Agents (read this first, any AI tool)

**What this is:** a local program that shows AI coding sessions as WoW 1.12 characters in the Goldshire Inn. Agents take
quests; a quest counts as done only after the trusted verifier's hidden checks pass. Python standard library server
(3.10+) + plain JS browser UI (vendored three.js). Deterministic core: SQLite state, state machines, claims ledger,
evidence store, hash-chained audit events.

**Layout** (the repository root only holds the launchers `setup.cmd`, `vanilla-agents.cmd`, `demo.cmd`, plus README and CLAUDE.md; everything below is in `data/`, and every command runs from `data/`)
- `vanilla_agents/` - engine (`storage.py`, `ledger.py`, `durable/`, `verify/`), the app server (`app/`), event feed and Claude hooks (`events/`), project onboarding (`onboard/`), game-data readers and the baker (`assets/`: `mpq.py`, `dbc.py`, `blp.py`, `m2v.py`, `wmo.py`, `character.py`, `export.py`, `gltf.py`, `settings.py`, `bake.py`).
- `app/config/*.json` - every visible text and setting (validated by `vanilla_agents/app/schema.py` + `check_app.py`; documented row by row in `docs/CHANGE-GUIDE.md`, enforced by a test).
- `app/web/` - the UI (`js/panels/*` one file per panel, `workshop3d/` the 3D inn/office).
- `scripts/` - hook entry (`claude_hook.py`), installer (`install_hooks.py`), setup, demo feed, onboarding, phone decisions.

**Run:** `python -m vanilla_agents.app.server --open` (or `vanilla-agents.cmd`). Demo: `python scripts/demo_feed.py --out <f> --live --agents 6`, then `python -m vanilla_agents.app.server --feed <f> --fake-verify --show-key`.
**Test:** `python -W error::ResourceWarning -m unittest discover -s tests -t .` (all must pass; game-data tests run when `data_dir` is set). JS has no unit tests: `node --input-type=module --check < file.js` (plain `node --check` misses some module errors) and look at it in a browser.
**Game data:** never in git. `data_dir` (WoW 1.12 Data folder) and `assets_dir` (default `~/.vanilla-agents/assets`) come from `vanilla.local.json`. Re-bake with `python -m vanilla_agents.assets bake [step]`.

**Working on another project while Vanilla Agents runs?** Use its MCP tools (`list_quests`, `start_quest`, `report_progress`, `quest_ready`, `pending_requests`, `answer_request`): see `docs/INTEGRATION.md`.

**Rules**
1. Standard library only for Python. No new dependency without the owner's OK.
2. Every user-visible text/setting is a config row: add it to `schema.py` AND `docs/CHANGE-GUIDE.md` (a test checks).
3. Game facts come from the game's own data (DBC tables, model files, FrameXML), not guesses. Note unavoidable approximations in `docs/ASSETS.md`.
4. Small commits with a plain one-line message. Never push or publish without the owner's OK. No secrets, no game files in the repo.
5. The owner is not necessarily a coder: report in plain language, decide technical details yourself, ask only about product choices.
6. Before handing off, update `HANDOFF.md` (what changed, what is verified, what is next).
