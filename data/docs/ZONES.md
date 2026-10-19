# Zones & Inns (F3)

Every inn you can work in sits in a zone of the old world. The Zones window (F3, or Esc > Zones & Inns, the button at the
bottom of the Flight Master, or "Pick a zone on the map..." in Options) shows them by continent:

- **Left:** the zones of Eastern Kingdoms, Kalimdor, or your own buildings, each with its faction crest and how many inns it has.
- **Right:** the zone's map with every area explored, and its inns as cards. Each card has a picture of the inside, taken the first
  time you open that inn. **Travel here** moves your inn there. The window reloads with that building's music, the zone's light
  outside the windows and the zone's loading screen.
- **Take pictures of the N inns without one:** visits every inn that has no picture yet, photographs it, and brings you back.
- **Always use ... for the project ...:** gives the open project its own zone. Opening that project later (Esc > Choose project, or
  the Flight Master) takes you straight to that inn, so every project is its own themed room.

## The zones and inns

| Continent | Zone | Inns |
|---|---|---|
| Eastern Kingdoms | Elwynn Forest | Lion's Pride Inn, Goldshire (three furniture sets: as in the game, quiet night, busy) |
| | Stormwind City | Town Hall, Guild House, Mage Tower (offices) |
| | Westfall | Sentinel Hill Inn; Moonbrook Inn (abandoned) |
| | Redridge Mountains | Lakeshire Inn |
| | Duskwood | Scarlet Raven Tavern, Darkshire |
| | Dun Morogh | Thunderbrew Distillery, Kharanos; Dwarven Inn; Anvilmar, Coldridge Valley |
| | Loch Modan | Stoutlager Inn, Thelsamar |
| | Wetlands | Deepwater Tavern, Menethil Harbor |
| | Tirisfal Glades | Gallows' End Tavern, Brill |
| | Western Plaguelands | Andorhal Inn (abandoned) |
| | Stranglethorn Vale | Troll Inn |
| Kalimdor | Teldrassil | Dolanaar Inn |
| | Darkshore | Auberdine Inn |
| | Durotar | Razor Hill Inn |
| | The Barrens | Ratchet Inn |
| | Mulgore | Bloodhoof Village Inn |
| | Tanaris | Gadgetzan Inn |

Some towns use the same kind of building as others; the game reuses inn buildings. The names follow the towns where the game
uses that building. The Bloodhoof, Gadgetzan, Auberdine and troll inns use the game's own "inn" furniture sets.

## Where it comes from (all from your own game data, on your PC)

- `vanilla_agents/assets/places.json`: the zones. `map` is the world map folder, `light` the hand-made look outside the windows
  (`PLACE_LIGHT` in `app/web/workshop3d/inn.js`, used until the game's own light is baked), `continent` picks the loading screen,
  `map_id` + `pos` (V-046) say where the inn's town is: continent (0 Eastern Kingdoms, 1 Kalimdor) and world coordinates
  [x north, y west], read off the zone map with the zone's WorldMapArea.dbc bounds (approximate is fine).
- The game's own light (V-046, `vanilla_agents/assets/lighting.py`): the bake picks the Light.dbc sphere covering `pos` (the
  strongest, a smaller one on a tie; partly covered = mixed with the continent default; none = the default) and samples its
  LightIntBand / LightFloatBand keyframes at noon, 19:30 and midnight into `places/index.json` zone `lighting` (sky = horizon,
  ambient, diffuse, sun, fog colours, fog end in yards). `app/web/workshop3d/innsky.js` turns that into the inn's light (lifted
  so the room stays readable, never black at night). `python -m vanilla_agents.assets.lighting <folder of .dbc files>` prints
  the same from loose tables.
- `vanilla_agents/assets/buildings.json`: the inns, each with `zone`, `town` and `inn`.
- `python -m vanilla_agents.assets bake places` joins each zone's 12 map tiles, draws every explored-area overlay
  (WorldMapOverlay.dbc) on top and writes `places/<zone>.png`, the two loading screens and `places/index.json`.
- `python -m vanilla_agents.assets bake buildings` bakes the inns (`--only kharanos,brill` for just some).
- Inn pictures are JPEGs of the 3D view (480x270), saved through `POST /api/inn_photo` into `places/photos/<key>.jpg`. They are
  your game's art, so they stay on your PC like everything else in the assets folder.

## Adding a zone or an inn

1. Find the building: `python -m vanilla_agents.assets ls "World\wmo\*Inn*.wmo"`.
2. Add a recipe to `buildings.json` (`sets`: 0 plus the furniture set you want; the bake prints the set names and counts).
3. If it is a new zone, add it to `places.json` with its world map folder (`ls "Interface\WorldMap\*1.blp"`).
4. Bake `places buildings --only <key>`, open the Zones window and travel there.
