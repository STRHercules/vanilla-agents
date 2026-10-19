# Code health notes (review of 2026-10-18)

Fixed in the fork: unused imports/variables (pyflakes clean in `vanilla_agents/` and `scripts/`), MPQ patch order
(patch-3 > patch-2 > patch > base), file handles closed when an archive is bad, Python checks use `{python}` instead of a bare
`python`, `HOME` passed to checks on Linux/macOS, the window and the Claude hook read the same feed path, `~` paths expanded
everywhere, missing room textures skipped instead of crashing, stale comments naming deleted scripts, duplicated quaternion code,
localStorage read guarded in the Esc menu. The Windows-only Python 3.12 `rmtree(onexc=)` in tests now works on 3.10+.

## Still open (bigger changes; decide before doing)
- **Opening the window off Windows:** `app/launch.py` only looks for Chrome/Edge `.exe` files. Add `shutil.which("google-chrome"/"chromium")` and macOS `.app` paths.
- **Unused outside tests:** `verify/client.py` (the app verifies in-process), `durable/scheduler.py`, `recovery.py`, `watchdog.py`, parts of `storage.py` (`logical_dump`, `require_fence`, `use_action_approval`) and several `contracts.py` schemas. They are the tested engine surface for launching helpers automatically, which is switched off; delete them only if that idea is dropped.
- **Silent failures:** `events/transcripts.py` `poll()` swallows every error per file/line, so a bug looks like "no agents". Add an error counter to the snapshot.
- **Big files:** `storage.py` (880 lines: split leases / evidence / approvals / ingest), `server.py` `do_POST` (63-line if-chain: use a route table), `inn.js` (451 lines: navigation, people, stations and hero input could be modules). Big functions: `verifier.run_verification`, `check_app`, `state.apply`, `export.mesh`.
- **Duplicated JS helpers:** `place`, `HISTORY`, `activityLine` and the FNV hash in `workshop3d/index.js` and `inn.js`; the xorshift rng in `inn.js` and `typing.js` (the second lacks the zero-seed guard). Move to `workshop3d/util.js`.
- **Half-built features:** worker `attach` (helm/shoulders) is always empty and the weapons manifest is empty, so that browser code never runs yet.
- **Magic numbers:** `runs.read(..., 200)` repeated four times and the 30 s project re-detect interval in `server.py`/`verify_job.py`/`zones_api.py`.
- **Onboarding env lists** in `onboard/detect.py` are Windows-flavoured; POSIX projects may also need `USER`, tool-specific homes.
- **Same-user trust:** see LIMITATIONS.md: anything running as your Windows user can read/modify `~/.vanilla-agents`.
