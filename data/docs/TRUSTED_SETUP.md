# Trusted folders (what the verifier trusts)

Every project has its own trusted folder `~/.vanilla-agents/trusted/<name>`, created by `python scripts/onboard.py <repo>`
(or Esc > Add project). It holds `checks.json` (the commands that prove work, picked by `onboard/detect.py` from the
project's files), `protected.json` (paths a worker may not change to make its own checks pass), optional `holdout.json`
(hidden checks) and a pinned `MANIFEST.json`. The manifest hash is recorded in the project registry
(`~/.vanilla-agents/work/projects.json`); Verify refuses to run when the folder no longer matches it.

The manifest also pins the verifier code (`vanilla_agents/verify/*`, `audit.py`, `contracts.py`, `__init__.py`). After changing
any of those, run `python scripts/onboard.py --regen` (all projects, or name one) so its fingerprint is rebuilt.
Hidden checks: `python scripts/add_holdout.py`.
