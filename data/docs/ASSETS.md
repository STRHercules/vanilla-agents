# Game art pipeline (WoW 1.12 Data folder -> ~/.vanilla-agents/assets)

`python -m vanilla_agents.assets bake [steps]` reads every `*.MPQ` in `data_dir` (patch archives first, like the game) and writes:

| Step | Reads | Writes |
|---|---|---|
| `skin` | `app/skin/*.png` (window frames cut from the owner's mockup sheet, committed; `scripts/dev/make_skin.py` re-cuts them, needs Pillow) | `skin/*.png` |
| `ui` | every texture/font/sound the config references + `app/config/ui_kit.json`; `Interface/FrameXML/*OptionsFrame.lua` + `GlobalStrings.lua` | `interface/**.png`, `fonts/*.ttf`, `sound/**.wav`, `ui/wow_settings.json` |
| `room` | two dungeon textures | `textures/floor.png`, `textures/wall.png` (office renderer) |
| `models` | `vanilla_agents/assets/models.json` recipes (props by file name; characters by a fixed look) | `models/*.glb` |
| `workers` | race models + CharSections, CharHairGeosets, CharacterFacialHairStyles, ItemDisplayInfo, NameGen | `models/workers/*.glb` + `manifest.json` (see WORKERS.md) |
| `music` | `Sound/Music/**.mp3` (up to 200 MB) | `sound/music/*.mp3` + `manifest.json` (jukebox) |
| `inn` | `GoldshireInn.wmo` + its 12 group files + doodad sets 0 and 2 | `models/inn.glb` + `models/inn.spots.json` |

Readers (all standard library): `mpq.py` (MPQ v0/v1; zlib and bzip2 sectors), `dbc.py` (WDBC; column layouts in `COLUMNS`),
`blp.py` (BLP2 palette/DXT1/3/5/BGRA -> PNG), `m2v.py` (M2 v256), `wmo.py` (WMO v17), `export.py` + `gltf.py` (glTF 2.0 binary).

Approximations (be honest):
- Armour is random by ItemDisplayInfo row (1.12 item -> display mapping lives on the server), so a worker's gear is not tied to a real item or level.
- Helmets, shoulders, capes and weapons are not drawn on workers yet (attachment models).
- Global-sequence animations (looping props such as banners) are not exported; props are static.
- WMO vertex colours (baked lighting), liquids and portals are ignored; the inn is lit by the scene lights.
- MPQ: PKWARE implode, Huffman/ADPCM sound compression and encrypted files raise a clear error (not used by the bundled archives).
- Options window: labels, tooltips, CVar names and slider ranges are the game's; values are only stored in the browser (master volume / sound on-off drive the app).
