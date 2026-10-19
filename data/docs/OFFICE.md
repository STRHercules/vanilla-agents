# Your own office building

Put a `.glb` file in `<assets>/office/` (the assets folder is `assets_dir` in `vanilla.local.json`, by default `~/.vanilla-agents/assets`).
It shows up in **Options > Settings > Workshop > Building** as "<Name> (yours)". Pick it and the page reloads into it.

## Rules for the model
- Metres, Y up, the floor of the ground storey at **y = 0**. Floors higher than 8.5 m are not walked on.
- Everything in the file is solid: helpers walk around it and the camera stops at it. Leave doorways at least 0.7 m wide.
- Mark places with **empties** (Blender: Add > Empty; names are case-insensitive):
  - `desk_1`, `desk_2`, ... a helper's work place. The origin sits at the wall, **+Z points into the room** (the helper sits facing the wall). Up to 10.
    Without any `desk_*` empty, desks are found along walls automatically. Only an empty counts: a mesh, or a name like `desk_1_top`, is furniture
    (Blender's `desk_1.001` is fine).
  - `seat_*` a place to sit while idle, +Z = the way the helper faces.
  - `fire` where helpers stand while thinking. `board` where helpers wait when a quest is handed in (without one, the game's Help Wanted poster is hung near the start).
- Export as glTF Binary (.glb) with "+Y Up". Textures embedded.

## Optional sidecar: `<same name>.json`
```json
{ "label": "Our office", "music": ["stormwind", "TavernAlliance"], "start": [0, 3], "desk_y": [-0.3, 1.2] }
```
- `label` name in the picker; `music` words matched against the baked music (folder or title); `start` where you appear, `[x, z]` in metres;
  `desk_y` floor heights searched for automatic desks.

## Live PC screens
Model your PCs as part of the building and give the screen face its own material named **`Screen`** (any case; `Screen.001` too).
Each such mesh shows, live, the page of the helper working at the **nearest `desk_*` empty** (within 2.5 m, on the same floor; two screens
next to one desk both show it): what it is doing, line by line, with a blinking cursor. While nobody sits at that desk the screen shows a dim screensaver.
- UV-unwrap the screen face so the image fills it the right way up (glTF orientation, as Blender exports it). A screen without UVs gets a flat mapping,
  which may show mirrored.
- At a desk that has its own screen, Vanilla Agents adds only the chair (no desk, monitor or keyboard of its own). The `desk_*` empty stays at the
  wall as above; the chair stands 1.75 m out from it and the helper sits 1.6 m out, so keep your desk within about 1.2 m of the wall.
- Without `desk_*` empties, screens bind to the automatic desks only if one happens to be within 2.5 m; put an empty at every PC.
- No screens and no desks at all is fine: desks are found along the walls and get the default desk, monitor and keyboard.

## Your PC models
At a desk without a `Screen` of your own, the desk and chair come from `workshop.json scene3d.models`. To use your own PC/desk GLBs, copy them into `<assets>/models/`
and point `desk` / `chair` there (positions in `scene3d.layout`). The live terminal screen is drawn at `scene3d.layout.monitor`; move it to sit on your PC's screen.
