@echo off
rem Disconnects Vanilla Agents from Claude (hooks and tools; backups of your Claude settings are kept) and removes the Desktop shortcut.
rem Your baked game art, settings and quest history stay in %USERPROFILE%\.vanilla-agents until you delete that folder yourself.
cd /d "%~dp0data"
echo This takes Vanilla Agents out of Claude Code and the Claude desktop app, and removes the Desktop shortcut.
choice /c YN /m "Go ahead"
if errorlevel 2 exit /b 0
python scripts\install_hooks.py --remove
python scripts\install_mcp.py --remove
if exist "%USERPROFILE%\Desktop\vanilla-agents.lnk" del "%USERPROFILE%\Desktop\vanilla-agents.lnk"
echo.
echo Done. To remove everything else:
echo   - delete %USERPROFILE%\.vanilla-agents   ^(baked game art, settings, quest history^)
echo   - delete this folder: %~dp0
pause
