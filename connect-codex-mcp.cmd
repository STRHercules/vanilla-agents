@echo off
rem One-time (optional): give Codex CLI the Vanilla Agents quest tools. Restart Codex afterwards.
rem Remove it again with: connect-codex-mcp.cmd --remove
cd /d "%~dp0data"
python scripts\install_codex_mcp.py %*
if errorlevel 1 pause
