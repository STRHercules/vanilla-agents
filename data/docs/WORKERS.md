# Workshop workers (random characters)

Each agent that walks into the inn is a WoW character from a baked pool of 30 (`python -m vanilla_agents.assets bake workers [--count 30] [--seed 7]`).

- **Look:** every customization the 1.12 tables allow for that race and sex is picked at random: skin colour, face, hair style and colour, facial hair (CharSections, CharHairGeosets, CharacterFacialHairStyles; NPC-only skins excluded). The 256x256 skin atlas is painted in the client's order: skin, face, facial hair, scalp, underwear, then armour.
- **Armour:** random ItemDisplayInfo rows for chest, legs, feet and hands (textures from `Item/TextureComponents`, geosets for sleeves, robes, gloves, boots).
- **Names:** NameGen.dbc, unique within the pool.
- **Stable:** worker n always uses seed `seed*1000+n`, so a re-bake gives the same faces. The browser picks a worker by a hash of the agent id (`app/web/workshop3d/inn.js` `workerFile`), so the same session keeps the same hero.
- **Level band:** each worker has a level (5..60, spread over the pool); the office renderer matches it to the quest's difficulty (`vanilla_agents/app/worker_level.py`).
- **Animations:** stand (with idle variations), walk, run, strafe, back, jump, fall, sit on ground (97) and sit in chair (102-104), exported as `a<id>_<n>` clips with the sequence's blend time and frequency in the manifest `seq`.

Not done: helm/shoulder/weapon models on attachment points; hair hidden under helms.
