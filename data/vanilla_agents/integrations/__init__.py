"""V-044 integration hub (incoming): GitHub issues / pull requests and Jira / Linear work items as bounty PROPOSALS on the Bounty
Board, and GitHub Actions runs as an informational CI line. Nothing here creates a quest: only the owner's click in the window does
(server POST /api/bounty/quest, never an agent tool). Modules: gitremote (which GitHub repository), httpjson (one HTTPS JSON call,
secrets never in errors), github (gh CLI or REST), csvimport (Jira / Linear CSV exports), linear and jira (optional API mode),
isettings (the owner's settings, secrets masked), board (the imported bounties per project), hub (refreshing, status, CI)."""
