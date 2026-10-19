# ov-workshop-agents.patch - Vanilla Agents addons in the Open Vanilla Workshop

A proposed change to the Open Vanilla repository (not applied anywhere; the owner decides). Apply from the Open Vanilla repo root:

    patch -p1 --dry-run < ov-workshop-agents.patch
    patch -p1 < ov-workshop-agents.patch

It was made with `diff -u` against copies of `tools/workshop_lib.py`, `tools/workshop_build.py`, `docs/manual/14-workshop.md`
and `www/workshop/workshop.js` (as of 2026-10-19) and applies cleanly to them.

What it changes:

- **tools/workshop_lib.py** - a new category `agents` ("Vanilla Agents addons"), `KIND_AGENTS = "vanilla-agents-addon"`,
  `AGENTS_DIR = workshop/agents/`, `is_agents(entry)`, and `kind` / `permissions` at the end of FIELD_ORDER.
- **tools/workshop_build.py** - entries with `kind: vanilla-agents-addon` are packaged like other rehostable entries but **without the
  Lua / .toc client scan** (they are JavaScript for the Vanilla Agents window): the source is the pinned upstream repo, or the zip
  committed as `workshop/agents/<files[0].name>` (checked against the entry's sha256, unpacked safely: no absolute paths, `..`, links,
  devices, size/count limits; remembered as `submitted:` for later rebuilds). `addon.json` must carry the entry's id and a `main.js`.
  The folder is packed as `<id>/` with OPENVANILLA-CREDITS.txt; compat stays `unknown`, the scan note says why; a new badge
  `agents` ("Vanilla Agents"); the index card gets `kind`, `permissions` and `files` (Vanilla Agents' Browse reads them);
  no `openvanilla://` launcher install link for them. Also: det_zip lets an `extra` file (the credits) replace a file of the same name
  instead of writing the name twice (a submitted package already carries one).
- **docs/manual/14-workshop.md** - a "What goes where" row, `agents` in the category list, and a new section
  "Publish a Vanilla Agents addon" (make a folder addon, Package for the Workshop in Vanilla Agents, send the .yml + zip by pull
  request or upload, what maintainers run, the trust warning).
- **www/workshop/workshop.js** - cards of Vanilla Agents addons show a "Vanilla Agents" badge and chip instead of Works / Hosted
  (`OWW.isAgents(card)`); it reuses badge_unknown.webp as the icon (no new art).

Checked: the patched build was run against a stub `yaml` / `workshop_scan` (PyYAML and the scanner are not available here) with the
catalogue entry and zip that Vanilla Agents writes for its example addon: packaged without calling the scanner, category
"Vanilla Agents addons", card with kind/files, a rebuild from `submitted:` works, and Vanilla Agents' Browse + Install accepted the result.
Not checked: the real scanner, the site pages that use `OWW.badges`, the realm upload path (uploads still expect a `.toc`), and
a pull request flow. Note: the Workshop's id rule (first character a letter or digit) is a little stricter than Vanilla Agents' own.
