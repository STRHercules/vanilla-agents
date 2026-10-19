// Centre scene, 3D: each agent is the game's own character model sitting at the game's own desk and chair, facing the viewer across a curved monitor.
// Everything (positions, models, animation names, lights) comes from workshop.json "scene3d". State: ?state=working|idle in the URL, else agent events.
import * as THREE from 'three';
import { loadModel, preloadModel } from './models.js';
import { makeTyping } from './typing.js';
import { makeMonitor } from './monitor.js';
import { TUNE } from './tune.js';
import { makeKeyboard, makeCandle, makeDust } from './props.js';
import { makeRoom } from './room.js';
import { loadProp, makeSteam, holdBag, dropBag, stepBag, standWeapon, wearGear } from './carry.js';
import { showToast } from '../js/toast.js';
import { makeJukeAudio } from './jukeaudio.js';
import { makePath, startArrival, startDeparture, stepArrival } from './arrival.js';

const place = (obj, p, rotDeg = 0, scale = 1) => { obj.position.set(...p); obj.rotation.y = THREE.MathUtils.degToRad(rotDeg); obj.scale.setScalar(scale); };
const HISTORY = 8;

// "Edit src/a.rs" -> "> Edit src/a.rs", "Bash cargo test" -> "$ cargo test" (all the UI knows is the one activity line per agent)
function activityLine(act) { const m = /^Bash\s+(.*)$/.exec(act); return m ? '$ ' + m[1] : '> ' + act; }

export function initWorkshop3D(ctx) {
  const w = ctx.cfg.workshop, c = w.scene3d, forced = new URLSearchParams(location.search).get('state');
  const canvas = document.getElementById('workshop');
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: false });
  renderer.setPixelRatio(window.devicePixelRatio || 1);
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(c.lights.fog);
  scene.fog = new THREE.Fog(c.lights.fog, c.lights.fog_near, c.lights.fog_far);
  scene.add(new THREE.AmbientLight(c.lights.ambient, c.lights.ambient_intensity));
  const key = new THREE.PointLight(c.lights.key, c.lights.key_intensity, 0, 0.6);
  key.position.set(...c.lights.key_pos); scene.add(key);
  const camera = new THREE.PerspectiveCamera(c.camera.fov, 1, 0.1, 100);
  const home = { pos: new THREE.Vector3(...c.camera.pos), look: new THREE.Vector3(...c.camera.look) };
  const cam = { pos: home.pos.clone(), look: home.look.clone() };
  let slots = [], free = { x: 0, y: 0, w: innerWidth, h: innerHeight };
  camera.position.copy(cam.pos); camera.lookAt(cam.look);

  const room = makeRoom(scene, TUNE.room);
  const juke = makeJukeAudio(TUNE.jukebox), jbPos = new THREE.Vector3();
  let cur = null;   // the track on the jukebox now: toasts say what plays
  juke.onTrack = (t) => { cur = t; showToast(ctx.t('jukebox.now', { title: t.title }), 'gold'); };
  ctx.jukebox = {
    toggle: () => juke.toggle().then((on) => { if (!on) showToast(ctx.t(cur ? 'jukebox.paused' : 'jukebox.none'), 'grey'); }),
    next: () => juke.next(), get playing() { return juke.playing; },
  };
  const dust = makeDust(TUNE.dust); scene.add(dust.points);

  let stations = [], state = { agents: [] }, built = '', building = false;
  const seen = new Set();   // agents already shown once: only a NEW agent walks in; the first picture after opening the window is everybody seated
  const clock = new THREE.Clock(), hist = {}, frustum = new THREE.Frustum(), pv = new THREE.Matrix4(), tmp = new THREE.Vector3(), sphere = new THREE.Sphere(tmp, 1.5);

  // measured numbers for the report: draw calls / geometries / textures from renderer.info, JS heap from performance.memory (Chrome only)
  const stats = () => ({ calls: renderer.info.render.calls, tris: renderer.info.render.triangles, geometries: renderer.info.memory.geometries, textures: renderer.info.memory.textures,
    heapMB: performance.memory ? Math.round(performance.memory.usedJSHeapSize / 1048576) : null });


  // desks for n agents: one gentle arc, or two staggered rows when that fits the free area better (see frame())
  function arrange(n, rows = 1) {
    const f = c.fit, out = [], perRow = Math.ceil(n / rows);
    for (let i = 0; i < n; i++) {
      const r = Math.floor(i / perRow), cnt = Math.min(perRow, n - r * perRow), k = i - r * perRow, xi = k - (cnt - 1) / 2;
      out.push({ x: xi * f.spacing + (rows > 1 ? (r ? f.spacing / 4 : -f.spacing / 4) : 0), z: -r * f.row_gap + f.arc * xi * xi, rot: 180 - f.turn * xi });
    }
    return out;
  }
  // the largest axis-aligned part of the window not covered by an open panel (pop-up menus in the middle don't count)
  function freeRect() {
    const W = innerWidth, H = innerHeight, pad = c.fit.pad, boxes = [];
    document.querySelectorAll('section.panel:not([hidden])').forEach((p) => {
      if (/esc_menu|sound_menu|game_settings/.test(p.dataset.panel)) return;
      const r = p.getBoundingClientRect(); boxes.push([r.left - pad, r.top - pad, r.right + pad, r.bottom + pad]);
    });
    const xs = [0, W, ...boxes.flatMap((b) => [b[0], b[2]])].filter((v) => v >= 0 && v <= W).sort((a, b) => a - b);
    const ys = [0, H, ...boxes.flatMap((b) => [b[1], b[3]])].filter((v) => v >= 0 && v <= H).sort((a, b) => a - b);
    let best = { x: 0, y: 0, w: W, h: H, a: -1 };
    for (const x0 of xs) for (const x1 of xs) for (const y0 of ys) for (const y1 of ys) {
      if (x1 - x0 < 100 || y1 - y0 < 100 || boxes.some((b) => x0 < b[2] && x1 > b[0] && y0 < b[3] && y1 > b[1])) continue;
      const n = slots.length || 3, one = Math.min((x1 - x0) / (n * c.fit.spacing + 0.8), (y1 - y0) / 2.8), two = n > 3 ? Math.min((x1 - x0) / (Math.ceil(n / 2) * c.fit.spacing + 1.4), (y1 - y0) / 5) : 0, a = Math.floor(Math.max(one, two) / 10) * 10 + (x1 - x0) * (y1 - y0) * 1e-7; // pixels per metre, best of 1 or 2 rows
      if (a > best.a) best = { x: x0, y: y0, w: x1 - x0, h: y1 - y0, a };
    }
    return best;
  }
  // camera that frames every station inside aspect (same viewing direction as workshop.json camera): returns pos, look
  const fc = new THREE.PerspectiveCamera(c.camera.fov, 1, 0.1, 100), dir = new THREE.Vector3(...c.camera.pos).sub(new THREE.Vector3(...c.camera.look)).normalize();
  function frame(sl, aspect) {
    const pts = [];
    for (const p of sl) for (const lx of [-1.05, 1.05]) for (const ly of [0, 1.9]) for (const lz of [-0.6, 2]) {
      const v = new THREE.Vector3(lx, ly, lz).applyAxisAngle(new THREE.Vector3(0, 1, 0), THREE.MathUtils.degToRad(p.rot)); pts.push(v.add(new THREE.Vector3(p.x, 0, p.z)));
    }
    const look = new THREE.Vector3(); pts.forEach((v) => look.add(v)); look.divideScalar(pts.length);
    fc.aspect = aspect; fc.updateProjectionMatrix();
    const th = Math.tan(THREE.MathUtils.degToRad(c.camera.fov) / 2);
    const fits = (d) => { fc.position.copy(look).addScaledVector(dir, d); fc.lookAt(look); fc.updateMatrixWorld(true); return pts.every((v) => { const n = v.clone().project(fc); return Math.abs(n.x) <= 1 && Math.abs(n.y) <= 1; }); };
    let d = 0;
    for (let it = 0; it < 3; it++) {
      let lo = 1, hi = 80; for (let i = 0; i < 30; i++) { d = (lo + hi) / 2; if (fits(d)) hi = d; else lo = d; } d = hi; fits(d);
      let x0 = 9, x1 = -9, y0 = 9, y1 = -9; for (const v of pts) { const n = v.clone().project(fc); x0 = Math.min(x0, n.x); x1 = Math.max(x1, n.x); y0 = Math.min(y0, n.y); y1 = Math.max(y1, n.y); }
      const right = new THREE.Vector3().setFromMatrixColumn(fc.matrixWorld, 0), up = new THREE.Vector3().setFromMatrixColumn(fc.matrixWorld, 1);
      look.addScaledVector(right, (x0 + x1) / 2 * d * th * aspect).addScaledVector(up, (y0 + y1) / 2 * d * th);
    }
    return { d, pos: look.clone().addScaledVector(dir, d), look };
  }
  // pick one or two rows, aim the camera, and render only into the free rectangle via a view offset (floor and wall still cover the whole window)
  function reframe() {
    if (building) return;
    free = freeRect();
    const n = Math.min(stations.length, c.fit.max_agents);
    if (!n) return;
    const asp = free.w / free.h, one = arrange(n, 1), two = n > 3 ? arrange(n, 2) : null, f1 = frame(one, asp), f2 = two && frame(two, asp);
    slots = f2 && f2.d < f1.d ? two : one;
    stations.forEach((s, i) => { s.group.position.set(slots[i].x, 0, slots[i].z); s.group.rotation.y = THREE.MathUtils.degToRad(slots[i].rot); });
    const f = f2 && f2.d < f1.d ? f2 : f1;
    home.pos.copy(f.pos); home.look.copy(f.look);
    room.layout(slots);
    camera.aspect = asp; camera.setViewOffset(free.w, free.h, -free.x, -free.y, innerWidth, innerHeight); camera.updateProjectionMatrix();
  }

  // Random worker look: each agent id gets one baked character (race/sex/class/gear, vanilla_agents/assets/bake.py bake_workers) the first time it is seen; none baked = role model.
  const looks = new Map(); let roster = null;
  // easy job = low-level gear, medium = mid, hard = level 50-60 epics (worker_level.py decides the band from the quest's size and risk)
  const BAND = { easy: (l) => l <= 12, medium: (l) => l > 12 && l < 50, hard: (l) => l >= 50 };
  async function workerLook(id, band) {
    if (roster === null) {
      roster = await fetch('/assets/models/workers/manifest.json').then((r) => (r.ok ? r.json() : [])).catch(() => []);
      (async () => { for (const w of roster) await preloadModel(w.file).catch(() => {}); })();   // warm the cache in the background: no wait when someone walks in
    }
    if (!looks.has(id)) {
      const fit = roster.filter((w) => (BAND[band] || BAND.medium)(w.level || 0)), pool = fit.length ? fit : roster;
      let h = 2166136261; for (const ch of String(id)) h = Math.imul(h ^ ch.charCodeAt(0), 16777619) >>> 0;   // stable: same worker id -> same look and name on every load
      const taken = new Set([...looks.values()].map((w) => w && w.name));
      let k = h % (pool.length || 1); for (let t = 0; t < pool.length && taken.has(pool[k].name); t++) k = (k + 1) % pool.length;   // no two workers share a name while free ones exist
      looks.set(id, pool.length ? pool[k] : null);
    }
    return looks.get(id);
  }
  window.__workerName = (id) => (looks.get(id) || {}).name;   // the UI panels show this instead of the raw id

  // legendary weapons (no weapon baker yet: the weapons manifest is empty): a hard + risky job (worker_level.py `legendary`) gets one by its desk; ?legend in the page URL forces it for every worker (for looking)
  const FORCE_LEGEND = new URLSearchParams(location.search).has('legend');
  let weapons = null;
  async function weaponFile() {
    if (weapons === null) weapons = await fetch('/assets/models/weapons/manifest.json').then((r) => (r.ok ? r.json() : [])).catch(() => []);
    return weapons.length ? weapons[Math.floor(Math.random() * weapons.length)].file : null;
  }

  async function build(agents) {
    building = true;
    stations.forEach((s) => scene.remove(s.group)); stations = [];
    const count = Math.min(agents.length, c.fit.max_agents);
    slots = arrange(count);
    for (let i = 0; i < count; i++) {
      const sp = slots[i], g = new THREE.Group();
      place(g, [sp.x, 0, sp.z], sp.rot);
      const parts = {}; let wl = null;
      for (const name of ['desk', 'chair', 'character']) {
        wl = name === 'character' ? await workerLook(agents[i].id, agents[i].difficulty && agents[i].difficulty.band) : null;
        const gltf = await loadModel(name === 'character' ? (wl ? wl.file : TUNE.role_model[agents[i].role] || c.models.character) : c.models[name]);
        place(gltf.scene, c.layout[name].pos, c.layout[name].rot, (c.layout[name].scale || 1) * (wl ? wl.scale || 1 : 1));
        g.add(gltf.scene); parts[name] = gltf;
      }
      const mon = makeMonitor({ ...c.monitor, text: c.monitor.bezel }); place(mon.group, c.layout.monitor.pos, c.layout.monitor.rot); g.add(mon.group);
      const kb = makeKeyboard(TUNE.keyboard.size); place(kb, TUNE.keyboard.pos, TUNE.keyboard.rot); g.add(kb);
      const candle = makeCandle(TUNE.candle); candle.group.position.set(...TUNE.candle.pos); g.add(candle.group);
      const mug = await loadProp(TUNE.mug.model, TUNE.mug.h); mug.position.set(...TUNE.mug.pos); g.add(mug);
      const steam = makeSteam(TUNE.mug.steam_puffs, TUNE.mug.steam_rise); steam.group.position.set(TUNE.mug.pos[0], TUNE.mug.pos[1] + TUNE.mug.h, TUNE.mug.pos[2]); g.add(steam.group);
      const bag = await loadProp(TUNE.bag.model, TUNE.bag.h);
      const mixer = new THREE.AnimationMixer(parts.character.scene);
      const clip = (n) => parts.character.animations.find((a) => a.name === n);
      mixer.clipAction(clip(c.clips.sit)).play();
      scene.add(g);
      const arriving = c.arrival && seen.size > 0 && !seen.has(agents[i].id) && !forced && clip(c.clips.walk) && !document.hidden;
      seen.add(agents[i].id);
      stations.push({ id: agents[i].id, level: wl ? wl.level : null, group: g, mixer, typing: makeTyping(parts.character.scene, c.typing, i + 1), mon, candle, amount: 0, parts, pulse: 0, was: null, shown: 0, sig: '', seed: i * 1.7, pendingArrival: arriving, steam, bag });
      const st = stations[stations.length - 1];
      if (wl && wl.attach) wearGear(st, wl.attach).catch(() => {});
      if (FORCE_LEGEND || (agents[i].difficulty && agents[i].difficulty.legendary)) weaponFile().then((f) => f && standWeapon(st, f, TUNE.weapon)).catch(() => {});
      if (arriving && holdBag(st, bag, TUNE.bag)) {} else { bag.position.set(...TUNE.bag.floor); g.add(bag); st.bag = bag; }
    }
    built = agents.map((a) => a.id).join(',');
    building = false;
    window.dispatchEvent(new Event('workers-ready'));   // names are known now: panels redraw
    size.last = ''; reframe();
    ctx.workshop3d = window.vanilla3d = { cfg: c, agents: () => agentsNow(), slots: () => slots, stations, tune: TUNE, scene, camera, renderer, stats, home };
  }

  const idleAt = new Map();   // agent id -> when it went idle; idle long enough = it leaves (and is forgotten, so its next visit is a new worker walking in)
  function agentsNow() {
    const t0 = Date.now();
    const a = state.agents.filter((x) => {
      if (x.state !== 'idle') { idleAt.delete(x.id); return true; }
      if (!idleAt.has(x.id)) idleAt.set(x.id, t0);
      const stay = t0 - idleAt.get(x.id) < TUNE.leave_idle_s * 1000;
      if (!stay) { seen.delete(x.id); looks.delete(x.id); }
      return stay;
    }).slice(0, c.fit.max_agents);
    return a.length ? a : (forced ? [{ id: 'preview', role: 'preview', state: forced, activity: 'Edit src/movement.rs' }] : []);
  }

  function screenText(s, a) {
    const h = hist[s.id] || [], working = a && a.state === 'working';
    return { head: ((a && a.role) || s.id) + (s.level ? ' (Lv ' + s.level + ')' : '') + (working ? ' - working' : ' - waiting'),
      lines: h.length ? h.map((t, i) => ({ text: t, dim: i < h.length - 1 })) : [{ text: '-- waiting for a job --', dim: true }], cursor: working };
  }

  // camera glide target for the selected agent: in front of its screen, far enough that the screen fills about the central part of the window
  function zoomTarget(s) {
    s.mon.group.updateMatrixWorld(true);
    const centre = s.mon.group.getWorldPosition(new THREE.Vector3()), nrm = new THREE.Vector3(0, 0, 1).transformDirection(s.mon.group.matrixWorld);
    const hf = 2 * Math.atan(Math.tan(THREE.MathUtils.degToRad(c.camera.fov) / 2) * camera.aspect);
    if (s.zoomFor === camera.aspect) return s.zoom;   // body size and window shape do not change per frame
    const d0 = Math.max(s.mon.width / (2 * TUNE.camera.zoom_fill * Math.tan(hf / 2)), 0.6);
    // smarter zoom: also fit the whole character body (a tauren is much bigger than a gnome), measured from its own model box
    s.parts.character.scene.updateMatrixWorld(true);
    const box = new THREE.Box3().setFromObject(s.parts.character.scene), look = box.getCenter(new THREE.Vector3());   // aim at the worker; the screen only has to fit in view
    const pts = [centre.clone()];
    for (const x of [box.min.x, box.max.x]) for (const y of [box.min.y, box.max.y]) for (const z of [box.min.z, box.max.z]) pts.push(new THREE.Vector3(x, y, z));
    const zc = new THREE.PerspectiveCamera(c.camera.fov, camera.aspect, 0.1, 200);
    // one viewing angle for every worker (consistent): a fixed pitch above the screen's normal, far enough that the whole body fits
    const th = THREE.MathUtils.degToRad(TUNE.camera.zoom_pitch), dir = nrm.clone().multiplyScalar(Math.cos(th)).addScaledVector(new THREE.Vector3(0, 1, 0), Math.sin(th)).normalize();
    const fits = (dd) => { zc.position.copy(look).addScaledVector(dir, dd); zc.lookAt(look); zc.updateMatrixWorld(true); return pts.every((v) => { const n = v.clone().project(zc); return Math.abs(n.x) <= 0.9 && Math.abs(n.y) <= 0.9; }); };
    let lo = 0.6, hi = 60; for (let i = 0; i < 24; i++) { const m = (lo + hi) / 2; if (fits(m)) hi = m; else lo = m; }
    const dist = Math.max(d0, hi);
    const goal = { pos: look.clone().addScaledVector(dir, dist), look };
    s.zoomFor = camera.aspect; s.zoom = goal;
    return goal;
  }

  function tick() {
    const dt = Math.min(clock.getDelta(), 0.1), t = clock.elapsedTime, now = agentsNow(), sel = ctx.selected;
    const ids = now.map((a) => a.id).join(',');
    if (!building && ids !== built) {
      if (stations.length && now.every((a) => stations.some((s) => s.id === a.id && !s.gone && !s.leaving))) {   // only leavers: they walk out, nobody else moves
        for (const s of stations) if (!s.gone && !s.leaving && !now.some((a) => a.id === s.id)) {
          const sl = slots[stations.indexOf(s)], half = Math.max(...slots.map((q) => Math.abs(q.x))) + TUNE.room.side_gap, L = c.layout.character;
          s.leaving = true; s.pendingArrival = false;
          startDeparture(s, s.parts.character.animations.find((q) => q.name === c.clips.walk), makePath(s.group, sl, half, L.pos, c.arrival), c.arrival, THREE.MathUtils.degToRad(L.rot));
        }
        built = ids;
      } else build(now);
    }
    camera.updateMatrixWorld(); pv.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse); frustum.setFromProjectionMatrix(pv);
    for (const s of stations) {
      const a = now.find((x) => x.id === s.id), st = forced || (a && a.state), working = st === 'working';
      if (s.was !== null && s.was !== st) s.pulse = 1;
      s.was = st;
      s.pulse = Math.max(0, s.pulse - dt / TUNE.screen.pulse_s);
      s.amount += ((working ? 1 : 0) - s.amount) * Math.min(1, dt * 8);
      if (s.pendingArrival) {   // slots are final after the first reframe: build the walking path now
        s.pendingArrival = false;
        const sl = slots[stations.indexOf(s)], half = Math.max(...slots.map((q) => Math.abs(q.x))) + TUNE.room.side_gap, L = c.layout.character, clip = (n) => s.parts.character.animations.find((q) => q.name === n);
        startArrival(s, clip(c.clips.walk), clip(c.clips.sit), makePath(s.group, sl, half, L.pos, c.arrival), c.arrival, THREE.MathUtils.degToRad(L.rot));
      }
      const was = !!s.arr && s.arr.phase, walking = stepArrival(s, dt);
      if (was === 'walk' && s.arr && s.arr.phase === 'sit') dropBag(s, s.group, TUNE.bag);
      stepBag(s, dt, TUNE.bag);
      s.steam.update(t);
      if (walking) s.amount = 0;
      s.typing.undo();
      s.mixer.update(dt);
      s.typing.apply(t, s.amount);
      s.mon.glow.material.opacity = TUNE.screen.glow_base + TUNE.screen.glow_work * s.amount + 0.04 * Math.sin(t * 2) + TUNE.screen.pulse_gain * s.pulse;
      const fl = 1 + c.lights.flicker * 3 * Math.sin(t * 11 + s.seed) * Math.sin(t * 4.3 + s.seed * 2);
      s.candle.light.intensity = TUNE.candle.intensity * fl; s.candle.flame.scale.set(0.16 * (0.9 + 0.1 * fl), 0.2 * fl, 1);
      // live screen texture: only for stations in view, a few times per second
      if (t - s.shown >= 1 / TUNE.screen.refresh_hz && (s.group.getWorldPosition(tmp), frustum.intersectsSphere(sphere))) {
        s.shown = t;
        const x = screenText(s, a), blink = x.cursor && Math.floor(t * 2) % 2 === 0, sig = x.head + x.lines.map((l) => l.text).join('|') + blink;
        if (sig !== s.sig) { s.sig = sig; s.mon.paint(x.head, x.lines, blink); }
      }
    }
    // selection -> camera glides to the monitor; no selection -> back home
    const tgt = sel && stations.find((s) => s.id === sel), goal = tgt ? zoomTarget(tgt) : home, k = 1 - Math.exp(-dt * TUNE.camera.glide);
    cam.pos.lerp(goal.pos, k); cam.look.lerp(goal.look, k); camera.position.copy(cam.pos); camera.lookAt(cam.look);
    key.intensity = c.lights.key_intensity * (1 + c.lights.flicker * Math.sin(t * 9) * Math.sin(t * 3.7));
    dust.update(t); room.update(t);
    const jb = room.jukebox(); if (jb) jb.update(t, dt, juke.update(t, camera, jb.worldPos(jbPos)));
    renderer.render(scene, camera);
    requestAnimationFrame(tick);
  }

  function size() { renderer.setSize(window.innerWidth, window.innerHeight, false); reframe(); }
  window.addEventListener('resize', size);
  const ray = new THREE.Raycaster(), ndc = new THREE.Vector2();
  const pick = (e) => {
    ndc.set(e.clientX / window.innerWidth * 2 - 1, -(e.clientY / window.innerHeight) * 2 + 1); ray.setFromCamera(ndc, camera);
    return stations.find((s) => ray.intersectObject(s.group, true).length);
  };
  const pickJuke = (e) => {   // the jukebox body itself (its lights are Points/Lines, not meshes)
    const jb = room.jukebox(); if (!jb) return false;
    ndc.set(e.clientX / window.innerWidth * 2 - 1, -(e.clientY / window.innerHeight) * 2 + 1); ray.setFromCamera(ndc, camera);
    return ray.intersectObject(jb.group, true).some((h) => h.object.isMesh);
  };
  canvas.addEventListener('click', (e) => { const s = pick(e); if (!s && pickJuke(e)) return ctx.jukebox.toggle(); ctx.select(s && s.id !== ctx.selected ? s.id : null); }); // click the same agent again to go back; click the jukebox to play/pause
  window.addEventListener('keydown', (e) => { if (e.key === 'Escape' && ctx.selected) { ctx.select(null); e.stopImmediatePropagation(); } }, true); // Esc leaves the zoom first
  canvas.addEventListener('mousemove', (e) => { const s = pick(e); canvas.style.cursor = s || pickJuke(e) ? 'pointer' : 'default'; if (s) ctx.tip.show({ dataset: { tip: w.agent_tip } }, e.clientX, e.clientY); else if (pickJuke(e)) ctx.tip.show({ dataset: { tip: 'jukebox' } }, e.clientX, e.clientY); else ctx.tip.hide(); });
  size();
  setInterval(() => { const r = JSON.stringify(freeRect()); if (r !== size.last) { size.last = r; reframe(); } }, 400); // panels opened/closed
  tick();
  return {
    update(s) {
      state = s;
      for (const a of s.agents) { // rolling list of what each agent did, for the screen
        const h = hist[a.id] || (hist[a.id] = []);
        if (a.activity && a.state === 'working') { const l = activityLine(a.activity); if (h[h.length - 1] !== l) { h.push(l); if (h.length > HISTORY) h.shift(); } }
      }
    },
    redraw() {},
  };
}
