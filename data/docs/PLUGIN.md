# Vanilla Agents as a Claude Code plugin

`data/plugin/` is a Claude Code plugin that does what `scripts/install_hooks.py` and `scripts/install_mcp.py` do for Claude Code,
without editing your settings by hand:

- **hooks** (`hooks/hooks.json`): the same session events, with the same "in the background" (async) flags, as the hook installer
- **tools** (`.mcp.json`): the Vanilla Agents MCP server (`scripts/vanilla_mcp.py`: quests, progress, requests)

The folder is also a one-plugin local marketplace (`.claude-plugin/marketplace.json`, name `vanilla-agents-local`), because Claude
Code installs plugins from marketplaces. A plugin from a marketplace added from a local folder is loaded **in place**, so its
commands can use the scripts next to it (`${CLAUDE_PLUGIN_ROOT}/../scripts/...`). Do not copy the `plugin` folder somewhere else.

## Install (once)

In a terminal, from the Vanilla Agents folder (replace the path with yours):

```
claude plugin marketplace add "C:\path\to\vanilla-agents\data\plugin"
claude plugin install vanilla-agents@vanilla-agents-local
```

or inside a Claude Code session: `/plugin marketplace add C:\path\to\vanilla-agents\data\plugin`, then
`/plugin install vanilla-agents@vanilla-agents-local`. Start a new session (or run `/reload-plugins`).

To try it for one session only: `claude --plugin-dir "C:\path\to\vanilla-agents\data\plugin"`.

Remove: `claude plugin uninstall vanilla-agents@vanilla-agents-local` (and `claude plugin marketplace remove vanilla-agents-local`).

## Use the plugin OR the installers, not both

If you ran `setup.cmd` with hooks / tools, or `scripts/install_hooks.py` / `scripts/install_mcp.py`, take those out first, or every
event arrives twice:

```
python scripts/install_hooks.py --remove
python scripts/install_mcp.py --remove --no-desktop
```

(`--no-desktop` keeps the tools in the Claude desktop app, which does not use Claude Code plugins.)

## Python command

- Hooks try `python3` and fall back to `python`, so they work on Windows, macOS and Linux.
- The tools start with `python` (the usual name on Windows). On macOS/Linux without a `python` command, regenerate with
  `python3 scripts/make_plugin.py --python python3`.

## Not in the plugin

- The **status line bridge** (real cost, memory and plan limits, `scripts/install_statusline.py`): a plugin cannot set your main
  status line, so this stays an opt-in installer (`setup --statusline`).
- The Claude desktop app's tools config (use `scripts/install_mcp.py`).

## For maintainers

The plugin files are generated: change `install_hooks.EVENTS` / `ASYNC` or the version in `data/VERSION`, then run
`python scripts/make_plugin.py`. `tests/test_plugin.py` fails while the files are out of date or differ from the installer.
Check with the real CLI: `claude plugin validate data/plugin/.claude-plugin/plugin.json` (and the marketplace file).
