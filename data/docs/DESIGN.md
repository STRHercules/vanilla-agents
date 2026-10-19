# vanilla-agents visual bible and app architecture (2026-10-17)

Owner: the owner (non-coder). Every helper reads this before touching the app. If a change conflicts with it, stop and ask.

## 1. What the program is
A Windows program (double-click icon, own window, no browser chrome). You point it at a project folder. It shows, live and in plain language, what vanilla-agents and the Claude sessions are doing. The whole program is the WoW 1.12 interface; the project is the game.

## 2. Metaphor map (do not reinvent; extend this table)
| WoW thing | Hotkey | Shows |
|---|---|---|
| Quest log | L | Tasks. Gold "!" = new, "?" = ready to turn in (verify). Objectives with check marks = acceptance criteria, e.g. "Tests passing 5/5". |
| Quest complete | - | Verifier VERIFIED: scroll animation, level-up sound, golden toast. FAILED = red "Quest Failed" (tasteful, no death screen). BLOCKED = grey "Out of range". |
| Character sheet | C | One agent: 3D model, work done, pass rate, tools allowed. |
| Backpack | B | Work products (patches, reports, verdict bundles) as item icons. |
| Spellbook | P | Checks as spells: slow check = cooldown plus cast bar while running. |
| World map | M | Code as zones; unchecked code = undiscovered fog. |
| Chat window | Enter | Audit log, coloured channels (system yellow, agents, verifier). |
| Buffs/debuffs | - | Lease = buff with timer; blocked task = debuff. |
| Raid frames | - | Every agent's progress bar in a grid. |
| Mana bar | - | The usage allowance. "Out of mana" = limit hit; refills at reset. |
| Tooltips | hover | The explain-like-I'm-five text: gold title, white body, green "Use:" line. |
| Esc menu | Esc | Settings, project picker, quit. |
| Group loot roll (Need or Greed) | - | Competing fixes (V-045): several helpers each propose a fix for one quest; the owner compares them and picks the winner (Need). The dice are only for show. |

Agent status is written as NPC dialogue: "The builder says: I fixed the movement code. The checker is looking at it now."

## 3. The workshop (centre scene)
Low-poly 3D room behind the panels. Each agent is a WoW character model sitting at a desk with a chair, in front of a 32 inch curved screen showing the REAL current work (file and highlighted diff, command and output, or verdict). Working = typing pose (sit pose with arm bones moving); idle = sit pose, arms resting. Click an agent: camera flies to its screen and fills the window (zoom). Screens go through the redaction code first (no secrets on screen).
- Furniture and props: the game's own doodad models where they exist (chairs, tables, desks), not hand-made copies. The monitor has no 1.12 equivalent: build it as an engineering-style display (brass bezel, arcane glow, frame art from game UI textures).
- Typing pose is procedural bone offsets on a real sit animation. Time-box tuning; check by screenshot.

## 4. Art direction rules (hard)
- Window look (2026-10-18, the owner's mockup in `app/skin/source/`): dark metal panels with gold title plates and red close buttons, the quest log on parchment, the chat as a scroll, portraits in a gold ring (rendered from each hero's own model), raid rows with portrait and two bars, a system monitor panel, an inventory grid, the action bar between blue orbs, and a gold celebration banner. Frame art = `theme.json` frames `metal`, `parchment`, `scroll` and `skin.*`; layout = `app/web/css/skin.css`.
- Faithful to the game: warm key light against cooler ambient, depth fog toward sky colour, painterly low-res textures, point/bilinear filtering, no PBR gloss.
- NO bloom, NO heavy post-processing, NO pixelation filter. Glow = soft additive sprites (candles, screen). Life = candle flicker, dust motes, monitor pulse.
- Interface: the game's own dialog/tooltip/parchment frames (9-slice), gold-on-dark palette, Friz Quadrata (text), game icon set. UI is HTML/CSS using real extracted textures.
- Lighting constants come from the lighting reverse-engineering notes (session ghidra-lighting) when available; do not invent numbers (project rule: real game data, no guesses; approximations go in docs/APPROXIMATIONS.md).
- Idle must look calm and still.
- Memorable signature moments: quest-complete scroll, mana bar for the usage limit, fog-of-war map, NPC-voice status lines.

## 5. Assets and legal
Blizzard textures, fonts, models and sounds come from the client files the owner owns. They are baked locally into `~\.vanilla-agents\assets` (outside git, never committed, never shared). Only code is committed. Private use only.

## 6. Cost rules
No headless Claude/Codex workers (D-120). Plain-language text comes from fixed templates, never an AI call. Small models for routine helpers. C: is nearly full: temp/bake output small, delete after.

## 7. Editability architecture (the "change it with 100% accuracy" requirement)
Principle: the owner says "change X"; a helper must find exactly one place, change it, and prove it.
1. Data, not code, for everything visual and textual. All under `app/config/`:
   - `theme.json` colours, fonts, frame/texture names, sizes
   - `hotkeys.json` keys to panels
   - `panels/*.json` one file per panel: layout, which data fields, which template
   - `strings/eli5.json` every plain-language sentence template (placeholders like {agent}, {file})
   - `workshop.json` desk positions, model per role, animation names, camera spots
   - `metaphors.json` the table in section 2 as data (event type -> quest/spell/buff)
2. `CHANGE-GUIDE.md`: a lookup table "what the owner asks for -> which file/key". Kept in sync by a test that fails if a config key is not documented.
3. `vanilla-agents check-app` validator: JSON schema checks, every referenced texture/model/icon exists, every placeholder in a template exists in the event schema, no unknown keys. Runs in the test suite and before the app starts; the app refuses to start with a plain-language error rather than render something wrong.
4. Visual regression: headless Chrome screenshots of each panel from fixed fake data; baseline images stored locally (they contain game art, so outside git); any change must be re-approved by the owner seeing before/after.
5. One event schema (`vanilla_agents/events/schema`) is the only contract between engine and UI. UI reads events only; it never touches the database.
6. Small files (<300 lines), one purpose each, names that match the metaphor table.
7. Every change: edit config, run `check-app` + tests, screenshot diff, commit with a one-line plain description.

## 8. Build order
1 rename (done, D-123) | 2 this doc + CHANGE-GUIDE | 3 live event feed (events schema, hook receiver, ELI5 templates) | 4 UI-art extraction check + bake to ~\.vanilla-agents\assets | 5 program window with quest log, character sheet, chat, map panels (placeholder characters) | 6 bake 3D characters/desk/chair/monitor, sit/type/idle | 7 live monitor texture, zoom, sounds, polish.
