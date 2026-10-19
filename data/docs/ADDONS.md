# Addons

Small scripts of your own that run inside the Vanilla Agents window, like WoW addons: a sign, a counter, a little window of your own.
Manage them in the **AddOns** window: **Esc > AddOns**, or **Options > Vanilla Agents > AddOns** (V-051).

> **Addons are trusted code.** Every addon runs in the window with the window's full power: it can read everything the window
> shows and press any button you can (approve quests, whisper helpers, start Verify). What an addon lists under `permissions` is
> what it *says* it does, so you can decide; it is not a lock. **Only enable addons you wrote or trust.** New addons start switched off.

## Two shapes

Both live in `<assets>/addons/` (your assets folder, `~/.vanilla-agents/assets` unless `app.json assets_dir` says otherwise).

**A folder addon** (recommended) - `<assets>/addons/<id>/`:

```
hello-inn/
  addon.json     the manifest (below)
  main.js        the script (an ES module)
  main.css       optional: linked automatically when present
  LICENSE        needed if the Workshop is to host a copy
  ...            optional images, sounds, more .js / .css / .json files
```

**A single file** (the old way, still works) - `<assets>/addons/<name>.js`. The window shows it as *Single file (old style)*, with a
name made from the file name and no author, version or permissions. Old single files stay switched on, as before; switch one off in the
AddOns window like any other.

## addon.json

```json
{
  "id": "hello-inn",
  "name": "Hello, Inn",
  "author": "You",
  "version": "1.0.0",
  "summary": "One line: what it does.",
  "description": "As long as you like (up to 4000 characters). Line breaks start new paragraphs.",
  "license": "MIT",
  "homepage": "https://example.com/hello-inn",
  "source_url": "https://github.com/you/hello-inn",
  "permissions": ["state", "panel"]
}
```

| Key | Rule |
|---|---|
| `id` | 2-48 lower-case letters, digits or dashes (`^[a-z0-9-]{2,48}$`); must equal the folder name |
| `name` | up to 48 characters |
| `author` | up to 80 characters |
| `version` | letters, digits, `.`, `+`, `-`, up to 32 characters (e.g. `1.0.2`) |
| `summary` | up to 280 characters, one line |
| `description` | optional, up to 4000 characters |
| `license` | a licence name, ideally an SPDX id (`MIT`, `GPL-3.0`, ...) |
| `homepage`, `source_url` | optional, `https://` only |
| `permissions` | a list of the names below |

Unknown keys are ignored. A folder whose `addon.json` is missing or wrong is listed with the problem in red and cannot be switched on.

### Permissions (what an addon says it does)

| Name | Means |
|---|---|
| `state` | reads the live picture: helpers, quests, chat lines and changed file names (`onState`) |
| `panel` | adds its own small window or sign to the screen (`panel`) |
| `inn` | draws on, or changes, the inn / workshop view |
| `chat` | reads or adds lines in the chat window (in this window only; never sent to Claude) |
| `actions` | presses buttons that change things: Verify, approve, whisper helpers, start quests (`ctx.actions`, `ctx.net.post`) |
| `network` | talks to other websites on the internet |

Again: these are labels for you, not a sandbox. An addon without `actions` can still call them.

## What an addon gets

`main.js` (or the single file) exports a default function. The window calls it once after it has started, with one object:

```js
// <assets>/addons/hello-inn/main.js
export default function ({ ctx, onState, panel, toast, addon, asset }) {
  const body = panel('Hello, Inn');                 // a small framed box; returns its body element
  onState((s) => { body.textContent = s.counts.done + ' of ' + s.counts.total + ' quests done'; });
  toast('Hello from ' + addon.name + ' ' + addon.version);
}
```

- `ctx` - the window: `ctx.panels.<name>.toggle()` (open/close a window, names as in `app/config/panels/`), `ctx.select(id)` (pick a
  helper), `ctx.talk(id)` (open its talk window), `ctx.actions.verify()` / `.pause()` / `.hearth()` (the action bar buttons),
  `ctx.t(key, vars)` (a sentence from `strings/ui.json`), `ctx.last` (the latest state snapshot), `ctx.net.get(path)` / `ctx.net.post(path, body)`.
- `onState(fn)` - `fn` is called once right away with the current snapshot (when there is one) and then with every new one. A
  snapshot has `agents`, `tasks`, `chat`, `files`, `counts`, `messages`, `usage`, `verify` (see `docs/DESIGN.md`).
- `panel(title)` - adds a framed box (class `addon-panel`, at the left by default) and returns its body element. Move or style it from
  your `main.css`, e.g. `.addon-panel:has(.my-class) { left: auto; right: 20px; }`.
- `toast(text, color)` - the gold banner (`gold`, `red`, `grey`).
- `addon` - `{ id, name, version, base }` from your manifest; `base` is the address of your folder (`/addons/<id>/`).
- `asset(rel)` - the address of a file in your folder, e.g. `img.src = asset('img/sign.png')`.

A failing addon only logs an error in the browser console (F12); the window keeps working. Changes (enable, disable, install,
remove) take effect after **Reload window**.

The server only hands out the files of addons that are switched on (`GET /addons/<id>/<file>`, types `.js .css .png .jpg .json .ogg
.wav .mp3 .woff2 .ttf`), never anything in `.removed/` or `.packages/`.

## Install, enable, remove

- **Install from .zip** (AddOns window): pick a zip of up to 2 MB (`app.json addons.max_zip_bytes`). The addon may sit at the top of the
  zip or in one folder (`hello-inn/addon.json`). Before anything is written the zip is checked: no absolute paths, no `..`, no
  backslashes or drive letters, no Windows device names, no links or device entries, no encrypted entries, at most 200 entries,
  at most 8 MB unpacked and no suspicious packed/unpacked ratio (zip bombs), only the file types above plus `.txt`/`.md` and
  `LICENSE`/`README`; hidden files (`.gitignore`, `.github/`) are left out. `addon.json` must be valid and `main.js` must be next to it.
- If an addon with the same `id` is installed, nothing happens until you choose **Replace**; the old one is then moved to `.removed/`.
- A new or replaced addon starts **switched off**. Read its Details (description, permissions, licence, links), then **Enable**.
- **Remove** moves the addon to `<assets>/addons/.removed/<id>-<date>-<time>/`. Nothing is ever deleted; move it back to restore it.
- Which addons are on is kept in your settings file (group `addons`), not in the addons folder. Only the AddOns window's own calls
  change it (with the window key); `/api/settings`, the phone page and the Vanilla Agents tools for Claude (MCP) cannot.

## Package for the Workshop

For a folder addon, **Package for the Workshop** writes two files into `<assets>/addons/.packages/`:

- `<id>-<version>.zip` - the folder under `<id>/`, plus `OPENVANILLA-CREDITS.txt` (name, author, source, licence). The zip is
  deterministic (sorted entries, fixed 1980-01-01 timestamps and attributes, hidden files left out), so packaging the same files twice
  gives the same bytes and the same sha256.
- `<id>.yml` - a catalogue entry in the Open Vanilla Workshop format (`workshop/catalog/<id>.yml`, keys as in the Workshop's
  `tools/workshop_lib.py`): `id, name, author, summary, description, category: agents, license, rehost, source_url, homepage, version,
  added, files: [{name, sha256, size, folders}], screenshots, tags, compat: unknown, ours: false, folders, verified: false, upstream`
  (for a GitHub `source_url`), plus `kind: vanilla-agents-addon` and `permissions`. `rehost` is true only for licences the Workshop may
  host: MIT, BSD-2-Clause, BSD-3-Clause, Apache-2.0, GPL-2.0, GPL-3.0, LGPL-2.1, LGPL-3.0, MPL-2.0, Unlicense, CC0-1.0. Other licences
  are listed as a link to you. Put a `LICENSE` file in your folder.

## Submit to the Open Vanilla Workshop

The window shows these steps after packaging (see the Workshop chapter of the Open Vanilla manual, "Publish to the Workshop"):

1. **Pull request:** in the Open Vanilla repository on GitHub, add `<id>.yml` to `workshop/catalog/` and the zip to `workshop/agents/`.
   Maintainers build it with `tools/workshop_build.py --fetch --only <id>`, which re-packs it with the credits file and records its fingerprint.
2. **Or upload:** on the Workshop page, **Share an addon** with the zip, licence and one-line summary (once uploads are open).
3. A person reviews every addon before anyone can install it.

Vanilla Agents addons are JavaScript for this window, not game addons: the Workshop lists them under **Vanilla Agents addons** and
does not run its WoW client scan on them. (The Workshop side of this is a proposed patch, `docs/openvanilla/ov-workshop-agents.patch`.)

## Browse the Workshop

**Browse the Workshop** is the only time addons use the internet, and only when you click it. It reads the Workshop index
(`app.json addons.workshop_index_url`, `https://openvanilla.app/community/workshop/index.json`) and lists the entries with
`kind: vanilla-agents-addon`. **Install** downloads the package named in that entry's `files` (https only, at most `max_zip_bytes`),
checks its sha256 against the entry, checks that its `addon.json` has the entry's id, then installs it exactly like a zip from your disk
(switched off). Until people share some, the list is empty.

## Server calls (all POSTs need the window key; none is an MCP tool or a phone call)

| Call | Body | Does |
|---|---|---|
| `GET /api/addons` | - | `{addons: [...], load: [...], permissions, folder, max_zip_bytes}`; `load` = what the window starts (switched on, no problem) |
| `POST /api/addons/enable` | `{key, enabled}` | switch one on/off (`key` = the folder id, or the file name of a single file) |
| `POST /api/addons/remove` | `{key}` | move it to `.removed/` |
| `POST /api/addons/install` | `{zip: base64, replace?}` | install a zip (body limit about 2.8 MB on this call only) |
| `POST /api/addons/package` | `{key}` | write the zip and the catalogue entry |
| `POST /api/addons/browse` | `{}` | the Workshop's Vanilla Agents addons |
| `POST /api/addons/workshop_install` | `{id, replace?}` | download, check, install |

An example to copy: `examples/addons/hello-inn/` (MIT). Copy the folder into `<assets>/addons/`, open the AddOns window, Enable, Reload window.
