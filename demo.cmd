@echo off
rem Try it without any agents: six pretend helpers work through a few quests (nothing real is changed).
cd /d "%~dp0data"
start "vanilla-agents demo feed" /min python scripts\demo_feed.py --out "%TEMP%\vanilla-demo.jsonl" --live --agents 6
python -m vanilla_agents.app.server --open --feed "%TEMP%\vanilla-demo.jsonl" --fake-verify
