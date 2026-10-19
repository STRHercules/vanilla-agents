# Known limitations


## Security posture: level_0_integrity_checks_only
- Integrity checks exist (hash chain, content-addressed evidence, append-only triggers, authority checks). **Isolation is not established.**
- Any process running as the same Windows user can open `vanilla.db`, drop the triggers, edit evidence, delete or rewrite checkpoints and delete the lock file. The chain and checkpoints are **tamper-evident, not tamper-proof** (D-018). A hash shows consistency, not that the original data was trustworthy.
- The authority matrix and the single-controller lock are application-level boundaries. They stop mistakes and confused callers, not a hostile process under the same account (D-022).
- A checkpoint folder on the same drive is neither a backup nor an independent security boundary.

## Functional limits
- Fencing tokens protect vanilla-agents-controlled operations only (D-017). Stopping an external process requires confirmed termination, which Stage 1 only models in data.
- No real worker, lease target, agent or verifier exists. Recovery is proven on data, subprocess kills and simulations. All claims and qualifications in tests come from a fixture verifier (I-005).
- Validation is explicit Python code, not a JSON Schema implementation (D-016). Staleness is conservative (commit or environment change invalidates). Dependency-aware invalidation is a later stage.
- Evidence retention: nothing is deleted automatically. Orphaned published files are reported only.
- Backups: a daily copy of the open project's quest database goes to state/backups (last 7 kept, V-029); copy that folder elsewhere for a real off-machine backup.

## Unverified in this stage
- Behaviour on non-Windows systems (the lock has a `flock` fallback that is not exercised here).
- Behaviour on network drives, antivirus interference with the lock file, or very large databases.
- Whether a low-integrity or other restricted process could use the database: out of scope for Stage 1.

## Added after the second independent review
- `StorageService.conn` is a public writable connection and `Actor` objects can be constructed by any code in the process. The authority matrix therefore protects against mistakes and confused callers, not against hostile code in the same process (same-user boundary, D-022). Tables other than the append-only ones have no triggers.
- `register_evidence`, the clock, the migrations list and the posture are injectable by the process owner. The posture is validated against the known values; the clock and migrations are test hooks.
- Fencing tokens exist and are tested (`require_fence`) but no storage mutation demands one yet, so fencing is proven for the function, not enforced on an operation (Stage 3).
- `rejected` events are unbounded: a hostile caller could grow the log.
- Checkpoint classification looks at the newest checkpoint and each file present; a deleted middle checkpoint file is not detected.
- The six-process lock test proves exclusion at one instant; a very slow machine could make it flaky (not wrongly passing).

## Stage 2 limitations
- Same-user Windows: the manifest, the verifier code and the checkout folders can be changed by any process under the user's account. The manifest detects changes between runs; it does not stop a hostile local process (security posture stays level_0_integrity_checks_only).
- Weakened-test detection is textual (regular expressions over added and removed lines), not semantic: it catches deleted assertions, deleted tests and added skip markers in common test layouts, not every way of making a test meaningless. Trusted-test overlay is the real protection for protected paths.
- The Job Object is attached just after the process starts, so there is a tiny window in which a very fast grandchild could start outside it (the tree is still killed by pid as a fallback). Behaviour verified on this machine only.
- The verifier proves the mechanism on synthetic fixture repositories. Real projects are verified through the checks their onboarding picked (see Onboarding below).
- Checks are run as given by the trusted definitions; a malicious or buggy check definition can do anything the user account can do. Only the manifest owner (user) may change them.
- Environment fingerprint is descriptive (OS, Python, git, verifier, check definitions, manifest), not a hash of the whole toolchain.

## Stage 2 review fixes (stage 2c)
- Job Object race closed on Windows: the check is created suspended, assigned to the job, then resumed with ntdll NtResumeProcess (undocumented but stable API). Any failure of CreateJobObjectW, SetInformationJobObject, assignment or resume yields a start error (BLOCKED), never a pass. Elsewhere the process group is the only containment.
- Check output is forgeable: a check (or the code under test) can print exactly the text the verdict rules look for ("Ran 5 tests", "OK"). The rules prove the declared pattern appeared, not that the tests ran; only exit code, evidence presence and the protected-file overlay back it. A real guarantee needs a trusted, structured reporter owned by the manifest.
- The manifest can be compared with an externally supplied expected hash (`require_manifest(expected_hash=...)`), but the verifier does not yet receive one from outside the trusted folder; until it does, rewriting manifest and files together is undetected.
- safe_path rejects short names, reserved device names and trailing dots/spaces. Shadowing of stdlib modules (unittest.py, conftest.py) by a patch is NOT yet reported as a finding (rules.py belongs to another change).

## Stage 3 (durable execution) known gaps
- Controller cannot prove a worker process is dead; caller supplies is_terminated(worker_id), default never says yes, so orphaned tasks stay BLOCKED(worker_unreachable). Stage 5 adapter must provide a real kill/alive check.
- Retry cap (D-060): a task orphaned max_attempts times (default 3, None = unlimited) becomes BLOCKED(retries_exhausted) and is never requeued; a human must act. Counts orphan events, not task runtime failures.
- Scheduler (D-061) skips a ready task whose resource is busy and takes the next; this needs resource_of(task_id) to know per-task resources. With one fixed resource every task is busy, so LeaseBusy still surfaces. A task can starve if its resource is always taken first by newer tasks (no aging).
- Nothing tested against a real worker process or a real full disk.
- Test-gutting heuristic (D-055): per hunk, a body replaced only by pass/return/assert True/... while assertion lines are kept verbatim is flagged (test_body_gutted). Not caught: non-trivial no-op stubs (`x = 1`), gutting spread over several hunks, bodies in languages whose stubs differ (Rust `{}`).
- Shadowing (D-054): added unittest/conftest/pytest/sitecustomize/usercustomize .py block (any depth); other stdlib-named files are non-blocking. Not caught: shadowing via package __init__, .pth files, or an existing file modified in place.
- Manifest hash (D-053): verifier uses req["expected_manifest_hash"] when present and refuses on mismatch; it is optional, so a caller that omits it still gets no protection against a rewritten manifest+files pair.

## Stage 5 (agent adapter) known gaps
- Headless Claude (`-p`) billing and terms of use are UNVERIFIED; check before enabling. Real launching is OFF by default (RealLauncher needs enable_real=True) and was never exercised against a real claude.exe or codex.exe; command flags (e.g. --verbose, --allowedTools) and event shapes are from documentation and memory, not a live run.
- Event parser field names are best-effort; unknown shapes yield zero tokens rather than an error.
- Registry: pid+creation time proves death or reuse, not liveness of the intended process tree; an unregistered worker is never reported terminated, so tasks of a worker that never registered stay blocked. RealLauncher kills the tree with taskkill/kill, not a Job Object, so escaped grandchildren survive.
- Judge output is validated for shape, evidence presence and (when allowed_refs is passed) that refs were requested; whether the quoted text really exists in the file is not checked. Read-only mode is an allow-list + plan mode + deny-list passed to the CLI (D-095); the flags are unrun against a real exe.
- Windows command line is limited to 32767 chars: prompts over 30000 chars are refused (agent_prompt_too_long). Passing long prompts via stdin/file is not implemented (CLI support unverified).
- Executable discovery still trusts APPDATA/LOCALAPPDATA and the highest-version/newest dir; no signature check. Registry file has no lock; a corrupt file resets to empty.


## Stage 3 review items left open (STAGE3_REVIEW 7-10; 1-6 fixed in D-100..D-105)
- Item 7: if claim_next fails after creating a lease, the lease is left release_pending until its (never-run) worker is terminated; the resource stays busy until then.
- Item 9: wall-clock time drives leases and heartbeats; a forward clock jump (NTP, sleep/resume) makes live workers look silent and leases expired at once. No monotonic base or grace period.
- Item 10: requeued tasks keep their old created_at and go to the front, so a poison task retries back to back (bounded by max_attempts=3).
- Item 4 remainder: fencing is still opt-in (fence=None is accepted) and worker_event is unfenced; only the worker/holder match was added.
- Orphaning is still not one transaction; repair relies on a later watchdog scan (D-101). A concurrent multi-process change can still make a step raise.

## Event feed (build step 3) known gaps
- A reader whose cursor is older than the oldest kept event silently misses rotated-away events; it must compare with oldest_id() and resync from derive_events/current_state. Defaults keep about 4 MB.
- Redaction is the existing regex set (secrets, emails, user paths); a secret in an unusual shape can pass into the feed. The feed file is plain text, same-user readable and writable: events are not authenticated, so the UI must treat them as display-only.
- The feed lock uses msvcrt.locking, which blocks up to about 10 s; a hook then gives up (3 s watchdog) and that event is lost. Hook events carry no task id unless VANILLA_TASK is set; agent names are session id prefixes.
- Derived events map only: task_created/ready/blocked, verification changes, claims, worker alive/termination, lease created/released. Lease expiry by clock, tool_use and usage_update exist only in the live feed. Hook payload shapes (tool_response keys) are from the documented format, not verified against a live Claude run.

## Build step 5: program window
- Placeholders: Verify now / Pause do nothing (toast says so). Backpack, spellbook, world map panels and the level-up sound are not built. Quit only works as "close the window" (browsers cannot close themselves).
- Workshop is flat 2D with simple figures; monitor shows the agent's activity text, not real diffs. Max desks = entries in workshop.json (5); more agents are not drawn but are in the quest/chat panels.
- Quest-log and character sheet use the dialog frame, not the parchment book art; frame piece order (left,right,top,bottom,corners) is assumed from the standard edge-strip layout and judged by eye only.
- Server re-reads the whole feed file when its size/mtime changes (fine up to the 1 MB cap); a project's database is read once at selection, later DB-only changes appear only via the feed.
- Config is read at server start: after editing app/config, restart the server (reload the window).
- Only visually checked in Chrome at 1280x800 (screenshots in ~\.vanilla-agents\work\shots, not in git). No automated visual-regression baseline yet (DESIGN 7.4).
- Hotkey Enter toggles the chat window (there is no chat input). Colour values for chat channels are remembered, not extracted.

## Build step 6 prototype: 3D workshop
- Rough: the chair back hides the character from the camera side; the character head/shoulders slightly clip the desk top; arms do not visibly rest on the desk or reach a keyboard (no keyboard model). Typing is only a small elbow wobble; amplitudes are guesses.
- Character is bald and wears a flat texture robe: hair needs the hair texture (type 6) which was not located; no clothing geometry (sleeves, boots). One model for every role.
- Light colours, intensities and fog distances are tuned by eye, not taken from the game's lighting data (should go to docs/APPROXIMATIONS.md when that exists). Camera is fixed; no zoom/fly-to yet (step 7). The monitor shows no text (step 7).
- Exporter limits: hermite/bezier tracks become linear, bone tracks that use global sequences are skipped (fine for characters, wrong for animated props), only the first skin profile and animation variation 0 are used, blend modes other than opaque/alpha-key become plain alpha blend, no particle/ribbon/attachment data. Sit ids 105/106 are 1000 ms and unchecked.
- 3D mode needs WebGL; five stations max (workshop.json). Not tested at other window sizes than 1280x800. Config still needs a server restart. Unit tests use synthetic fixtures only; the real conversion was checked by eye.

## Sounds
- Sound only plays while a window is open on a live event stream; changing project does not reset the "who was working" memory, so one stray "finished" sound is possible right after switching.
- Defaults other than the peon, bell and quest-failed are my picks (blocked = auction close, mana low = many coins, helper started = few coins, off by default, quest ready = quest update). The listfile has no igbackpack/igquestlog sounds in this install, so those were not baked.
- Settings are read by the server on each request; sounds.json changes still need a restart. The Esc menu shows no sound preview for a muted master (Test is silent when muted, by design).
- Not heard by ear: verified through the Audio element state only.

## 3D workshop polish pass
- Fixed: camera sees faces, no desk clipping, keyboard, live screen, zoom, candle/dust/pulse. Measured at 1264x705 with 3 agents (renderer.info): about 37-57 draw calls, 4.0-4.6k triangles, 52 geometries, 19 textures, JS heap about 30 MB (performance.memory); texture memory is small (one 512x~230 screen canvas per station plus 128x48 keyboard).
- Still rough: character is bald (hair needs the hair texture/geoset from real CharSections/hair DB2 data, not located, nothing guessed); the idle arm lean (tune.js rest) is empty, hands just happen to land near the keyboard from the sit animation, typing is small procedural rotations on forearm bones plus a tiny torso bob, tuned by eye; knees poke through the desk drawers; side desks hide behind the left/right UI panels at 1264 wide (only the middle area is free); the screen shows activity lines, not command output (server events are not forwarded to the UI); effect numbers are in tune.js, not workshop.json.

- 3D framing (D-190..D-194): at 1000x700 the free middle is narrow (about 350 px wide) so the desks are small; the free rectangle is the best single axis-aligned box, not an arbitrary shape; 6+ agents use two staggered rows where back-row desks are partly hidden by the front row; the idle arm lean (20 degrees) was judged by eye at small size; layout is only re-measured every 400 ms.

## Real sessions from transcripts (D-200..D-203) - what transcripts do not reveal
- Read-only: no hooks, no settings.json edits. It only sees what Claude Code has already written to its session files, so it lags real life by up to the poll interval (2 s) plus Claude's own write delay.
- Thinking in progress is invisible: while Claude is "thinking" or waiting for a long tool, nothing is written, so the agent looks idle or stuck on its last tool. Permission prompts waiting for you are also not recorded.
- "Working" starts at your prompt or a tool call and ends at the assistant's end of turn. A session that was closed or crashed is only shown as away after `transcript_idle_minutes` of silence.
- Tool output is never shown (only tool name, truncated file/command, and a marker "it hit an error"). Subagents are named from their type plus a short id; the description is their role.
- The usage bar from transcripts is how full the main conversation's memory is (tokens / `transcript_context_tokens`), NOT the plan limit; the plan limit is not in transcripts. The bar is scaled to the rotation point (150000 tokens, D-419): 100% = write the handoff and /clear.
- Only the newest `transcript_max_agents` recently touched sessions/subagents are shown. A newly seen file is read from only its last 128 KB (last 6 events), so old history is not replayed.
- The folder name rule (every non-letter/digit becomes "-") was checked against the real folders, but Claude's format can change; unknown records are ignored, so a format change shows as silence, not a crash.

## Verify now / Pause (D-220..D-225)
- Tests and the browser demo use a fake verifier; a real Verify runs the project's own checks from its trusted folder.
- Cast bar progress is a time guess, not real progress. Pause does not stop a check already running. One check named in the cast bar even if criteria list more.
- The verifier code is pinned by the manifest: any edit to vanilla_agents/verify/* needs `python scripts/seed_project.py --regen`.
- A crash of the app mid-run can leave ~/.vanilla-agents/work/verify/<run_id> behind; delete it by hand. Pause changes from another window show only after its next update.

## Room, hair, character variety (D-210..D-215)
- Floor and walls are Elwynn cobblestone tileset art (stone), not a wooden inn interior: no usable WMO textures in this build. Walls are flat planes with no windows or doors; the side walls sit just outside the outermost desks and are mostly hidden behind panels.
- Props are placed by fractions of the room (TUNE.room.props u/v), so with the 8-agent layout they spread wider but never move to avoid a panel; some may sit behind a panel.
- Hair uses the classic hairNN texture files with a colour picked by eye from average colour; the style-to-texture link is not in the DB. Dwarf is bald with a beard; no facial-hair textures on the face atlas.
- Role to model (TUNE.role_model) and all room numbers (TUNE.room) are in tune.js and should move into workshop.json plus schema later. More than 3 distinct roles reuse models; unknown roles get the human male.
- Every character glb embeds its own PNG skin atlas (about 1 MB each). Orc/other races were not baked. 
- The bookshelf and crates are drawn without their M2 particle or animation parts (static export).

## Spellbook and Backpack
- Verify now runs every registered check, so clicking one spell casts them all; a per-check run needs a verifier change.
- Only one check is shown casting (the status names the first registered check).
- Patches are not shown in the Backpack: no event carries a patch yet. Only reports and changed files.
- Per-check duration is the whole run's duration. The run log keeps about 512 KB at most (two files).
- Checks that are defined in the trusted folder but not registered for the project are listed dimmed and cannot be cast.
- World map: "cleared" is whole-project (no per-file coverage); one passing run clears every explored zone not edited after it. Reading counts as exploring; only edit tools stamp "last edit".
- World map: zones are the git HEAD tree: uncommitted new files do not appear and sizes are of the last commit. Line counts skip binary files. At most 24 areas drawn; the rest merge.
- World map: only tool_use file paths inside the project are seen (not Bash edits); a different path spelling (symlink, 8.3 name) can be missed.
- Fast/full route (D-300): decided from files changed since this project's last VERIFIED run; the first run, a missing git history or a binary file always takes the full route. Real fast runs only differ by skipping holdouts until a project lists `fast_criteria`. Not run against real cargo.
- Plan approval (D-310): a record only. The program cannot stop an agent from working before approval; with `plan_approval_required` on it only withholds the "done" count. the owner's own Yes cannot be undone from the window (delete the entry in the approvals file); an automatic yes can ("Undo the yes", D-385, until the quest is done; it does not stop work a helper already started).
- Sent back (D-320): evidence is only the one-line finding (for example "exit code 101"), not test output. A hidden check shows no detail by design.


## Review fixes for slices A and B (D-380..D-399): what is and is not protected
- **Same Windows user (the honest limit).** The approvals are real (only the approvals file counts, D-380) and every state-changing call needs the per-launch key (D-381). That stops a web page, another program or a `curl` without the key, and a forged feed line. It does NOT stop a process that runs as the same Windows user as the program: it can read `~\.vanilla-agents\work` (approvals file, project registry, Ideas, quests.json), read `~\.vanilla-agents\trusted` (checker code, checks and the hidden checks in holdout.json), write the approvals file directly, read the key from the browser window's command line or profile folder, or change the code. A Claude session is such a process. Closing this needs Windows permissions or Claude permission deny rules (Read/Edit on those two folders); nothing was added because it changes the owner's settings: ask the owner first. No Claude settings or hooks were edited.
- **Holdouts are readable by the same user.** The hidden checks sit in `holdout.json` in the project's trusted folder. The window and the feed never show them, but an agent that is allowed to read that folder can read them and tailor its work to them (D-290..D-293, D-336). Deny rules would fix that, same caveat as above.
- **The key** is in the launch address of the window (a command line another program of the same user can see) and in that window's session storage. `--show-key` prints it on purpose. Without `--open` the buttons that change things do not work. The page reads state without a key (state, config, runs are not secret).
- **NEVER is a label, not enforcement.** It only stops the AUTOMATIC yes; the owner can still press Yes on such a quest (D-387), and an agent that has a shell can run any command whatever the tier says. The words are a keyword guess in English (whole words, plural/-ing allowed): a quest worded as "ship it to everyone" or "get the money flowing" is not caught. Tune `app.json#tiers`.
- **Tiers.** Size and risk are written by hand in `quests.json`; a quest without them always asks. Only quests found in the project's current plan can start by themselves, and only after the Idea is approved; quests that appear later start by themselves only when the Idea is approved again. Editing a quest's size or risk changes the Idea key (needs a new approval); editing its wording does not. The scope check reads paths and out-of-scope folder names in the quest text, not what a helper actually changes. "Undo the yes" works until the quest is done and does not stop work already started. Quests that are not in the plan show "needs your decision".
- **Explain it more / Change it** reach a Claude session only through `scripts/claude_hook.py` at SessionStart and UserPromptSubmit; the owner has to add the UserPromptSubmit entry (scripts/install_hooks.py does it with a backup) (docs/HOOKS_SETUP.md). With no hook the request just waits in the feed. Answers are plain text under the quest.
- **Phone.** By default the window listens on 127.0.0.1 only. With Options > Phone > Phone access on (V-040), a second listener opens on this PC's home-network address and the phone page shows Approve / Pause / Verify when opened with the key link (QR code). Plain HTTP on your own network: anyone who sees that traffic or gets the link can use it until Vanilla Agents restarts (a new key) or Phone access is switched off. A firewall may block it (allow Python on private networks). Without Phone access a session on the PC can still run `scripts/decide.py` for the owner (recorded as "you (via phone session)"); that script is only as trustworthy as the session that runs it.
- **Worktrees (V-038).** The checkout is read from `.git` files (no git command). Two helpers in different worktrees never get the same-file warning; the Inspect window still shows only the open project's own checkout. A helper first seen through its transcript gets its folder from the transcript; one seen only through `claude agents` has a folder but no branch.
- **Resurrect (V-039).** Needs the `claude` command and a terminal program on this PC (Windows: cmd; macOS: Terminal via osascript; Linux: x-terminal-emulator, gnome-terminal, konsole, xfce4-terminal, mate-terminal, kitty, alacritty or xterm); otherwise the talk window shows the command to type. Only sessions whose id came from the hooks, the transcript or `claude agents` can be resumed (not sub-agents). Whether the resumed session keeps its id depends on the Claude Code version; a new id in the same folder within 10 minutes still counts as the same helper coming back for the revive effect.
- **Notifications (V-041).** Sent by the window's server, so nothing is sent while Vanilla Agents is closed. At most one copy of the same sentence per minute per channel; a full queue (200) drops the rest. ntfy topics are public to anyone who guesses them. Hero names in messages follow the baked roster (the same pick as the window).
- **Tokens per minute.** Counted from Claude Code's session files (output tokens of each answer), only for sessions in the open project's folder, and only from the part of a file read since the window started (the first look reads the last 128 KB). The transcript helper ("Claude session abcd") and the hook helper (claude-abcd1234) of one session are still separate entries.
- **Other projects.** All projects share one feed: their quests are hidden in this window (by id), but their chat lines still appear in the chat log.
- **Onboarding (Add project).** The type detection is a guess from files (Cargo.toml, package.json, ...): the checks it picks may be too few or the wrong ones, and each project's checks are fixed at onboarding (change them with `onboard.py --regen` or `add_holdout.py`). The folder must be a git folder; the program never runs project code except through the verifier. Links and junctions are resolved to the real folder; 8.3 short names, `subst` drives and network drives are not specially handled.
- **The Idea.** IDEA.md is drafted from the project's docs by simple rules (headings, lists, tables), so it can miss or wrongly add items (the draft may include stale items from old docs); the owner must read it. Plan items are found again from the docs each time, and a quest id is a hash of the original text, so rewording a plan line makes a new quest (the old one stays in the feed). At most 60 quests are found. Approval is of the Idea text and the quests' size and risk, not of the plan wording.
- **Folders.** `~\.vanilla-agents\trusted\MANIFEST.json`, `checks.json` and `protected.json` at the top level are leftovers from before D-337 and are not read by anything (docs/TRUSTED_SETUP.md); not deleted because they are outside the repo.
