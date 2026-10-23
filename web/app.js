import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { installRegister, registered, inject } from './loader.js';
import { World, DAY } from './model.js';
import { FRAMES, apply } from './frames.js';
import { LAGRANGE } from './lagrange.js';
import { RE_KM } from './states.js';
import { StellarBackground } from './sky.js';
import { SKY_STYLE } from './sky-style.js';
import { Trails } from './scene/trails.js';
import { Bodies } from './scene/bodies.js';
import { PointLayer, SHAPE } from './scene/markers.js';
import { LagrangeLayer, TrackletLayer, Chevrons, ScaleBar, ScaleRings, edgePoint, lagrangeVisibility } from './scene/overlays.js';
import * as cam from './camera.js';
import { Labels, Occupancy } from './labels.js';
import { TargetList } from './ui/list.js';
import { renderTarget, updateTarget, renderSituation, isStale, targetTip } from './ui/readout.js';
import { readState, writeState } from './ui/url.js';
import { renderLegend, legendUI as legendRows } from './ui/legend.js';
import { utc, iso, fmt, rangeText, escapeHtml } from './ui/format.js';
import { Clock } from './time.js';
import { token } from './ui/theme.js';

const $ = id => document.getElementById(id);
installRegister();
const core = registered('core'), catalog = registered('catalog'), manifest = registered('manifest');
const base = window.CLE_DATA_BASE || 'data';
if (!core || !catalog) {
  $('loading').textContent = `Data sidecars missing: ${base}/core.js and ${base}/catalog.js must load before the page script (run make build).`;
  throw new Error('cislunar-eye: core.js / catalog.js not registered');
}

const W = new World(core, catalog), url = readState();
const LAYERS = { trails: true, labels: true, lagrange: true, tracklets: true, sightlines: false, moonTrail: true, rings: false,
  stars: true, starNames: false, grid: false, constellations: false, twilight: false };
const layers = { ...LAYERS, starGain: 1 };
if (url.layers) for (const k of Object.keys(LAYERS)) layers[k] = url.layers.includes(k);
const chips = { t1: true, t2: false, nat: false, t3: false };
if (url.tiers) for (const k of Object.keys(chips)) chips[k] = url.tiers.includes(k);
const TRAILS = { '1d': DAY, '3d': 3 * DAY, '15d': 15 * DAY, full: W.window_s };
const VIEWS = ['em', 'wide', 'earth', 'moon', 'l1', 'l2'];          // 'em' (the Earth–Moon framing) opens the page (V1)
const state = { frame: FRAMES[url.frame] ? url.frame : 'se', trail: url.trail in TRAILS ? url.trail : 'auto', selected: null, focus: 'earth', hovered: null, view: null, moonDistPx: 0 };
W.frame = state.frame;
const clock = new Clock(W.t0, W.t1, W.now);
if (url.t && Number.isFinite(Date.parse(url.t))) clock.set(Date.parse(url.t)); else clock.setLive(true);
const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches, phone = matchMedia('(max-width: 700px)');
const chipOn = T => T.natural ? chips.nat : T.tier === 1 ? chips.t1 : T.tier === 2 ? chips.t2 : chips.t3;
const isVisible = T => !!T && T.draw && !!T.track && T.visible;
const defaultOn = T => T.draw && chipOn(T) && (T.tier !== 1 || T.cat.default_visible !== false || !T.cat.drawn);
for (const T of W.targets) T.visible = defaultOn(T);
let neocpObjects = [], neocpBuilt = null, neocpSnapshot = null, missing = [];
let renderer, scene, camera, controls, camTween, sky, bodies, trails, markers, rings, lagrange, tracklets, chevrons, scaleBar, scaleRings, labels, list;
let renderReady = false, dirty = true, paramsDirty = true, uiPending = true, forceUI = true, lastFrame = null, lastUI = 0, lastList = 0, lastSituation = 0, readoutFor = undefined, camMoving = false;
const lineMaterials = [], focusKm = new Float64Array(3), occupancy = new Occupancy();
const tween = { start: 0, dur: 0, fromId: null, from: new Float64Array(3), active: false };
let lagVis = LAGRANGE.map(() => true);

function applyNeocp(p) {
  neocpBuilt = p.built_utc; neocpSnapshot = p.neocp_snapshot_utc;
  const present = new Set();
  for (const [desig, obj] of Object.entries(p.objects || {})) {
    const cat = { tier: 1, group: 'NEOCP live', regime: 'NEOCP_GEO', obj_type: 'OTHER', name: desig, label: desig, ...obj.catalog, id: obj.catalog?.id || desig.toLowerCase() };
    const fresh = !W.byId.has(cat.id), T = W.addTarget(cat, 'neocp');
    W.attach(cat.id, obj); T.fit = obj.fit; T.desig = desig; T.removed = false; present.add(cat.id);
    if (fresh || !T.userSet) T.visible = defaultOn(T);
  }
  for (const T of W.targets) if (T.source === 'neocp' && !present.has(T.id)) { T.track = null; T.removed = true; }
  // Tracklets of identified (catalogued) targets ride along with the live objects' (§7).
  const attached = Object.entries(p.attached || {}).filter(([id]) => W.byId.has(id)).map(([id, a]) => [id, { catalog: { id }, tracklets: a.tracklets || [] }]);
  neocpObjects = [...Object.entries(p.objects || {}), ...attached];
}

async function loadData() {
  const chunks = manifest?.chunks || catalog.chunks || [...new Set(catalog.targets.map(t => t.chunk).filter(c => c && c !== 'neocp'))];
  await Promise.all(chunks.map(c => inject(`${base}/${c}.js`)));
  for (const c of chunks) {
    const p = registered(c);
    if (!p) { missing.push(c); continue; }
    for (const [id, payload] of Object.entries(p.targets)) W.attach(id, payload);
  }
  if (await inject(`${base}/${manifest?.neocp?.file || 'neocp.js'}`) && registered('neocp')) applyNeocp(registered('neocp'));
}

// Every 10 min re-inject neocp.js with a cache-buster; swap only when built_utc changed (§11).
async function refreshNeocp() {
  const ok = await inject(`${base}/${manifest?.neocp?.file || 'neocp.js'}?v=${Date.now()}`), p = registered('neocp');
  if (!ok || !p || p.built_utc === neocpBuilt) return false;
  applyNeocp(p);
  if (renderReady) { trails.rebuild(state.frame); tracklets.rebuild(state.frame, neocpObjects); ensureMarkers(); }
  paramsDirty = dirty = true; list?.render(); ticks();
  return true;
}

// ---------- focus, fly-to and camera ----------
const RADIUS = { earth: 1, moon: W.moonRadius / RE_KM };
const isPoint = id => LAGRANGE.some(L => L.key === id);
const view = () => ({ fov: camera.fov, aspect: camera.aspect, maxDistance: controls.maxDistance });
function minDistance(id) { return id === 'earth' ? 1.02 : id === 'moon' ? RADIUS.moon * 1.02 : 2e-4; }

// The focus point eases from the previous focus (both tracked live) with the camera tween's curve (camera.js).
function currentFocus(out) {
  if (!W.focusPosition(state.focus, out)) out.set(focusKm);
  if (!tween.active) return out;
  const s = tween.dur ? Math.min(1, (performance.now() - tween.start) / tween.dur) : 1, e = cam.ease(s), a = new Float64Array(3);
  if (!W.focusPosition(tween.fromId, a)) a.set(tween.from);
  for (let k = 0; k < 3; k++) out[k] = a[k] + (out[k] - a[k]) * e;
  if (s >= 1) tween.active = false;
  return out;
}

// zoom: fly to the target's framing (camera.js focusView: distance per regime, the plane view for HEO targets);
// dir/dist (presets): slerp to that direction and distance. Both ease over ms (0 under reduced motion).
function focusOn(id, zoom = true, dir = null, dist = null, ms = reduced ? 0 : 1000) {
  const p = new Float64Array(3);
  if (!(id === 'earth' || id === 'moon' || isPoint(id) || W.byId.has(id)) || !W.focusPosition(id, p)) return false;
  if (W.byId.has(id) && !W.byId.get(id).visible) setVisible(W.byId.get(id), true);
  tween.from.set(focusKm); tween.fromId = tween.active ? null : state.focus; state.focus = id; state.view = null;   // interrupted: from the eased point
  trails.focus(W.byId.get(id) || null); paramsDirty = true;                 // §9.2 anchor for the focused trail
  for (const b of $('views').querySelectorAll('button')) b.classList.remove('active');
  controls.minDistance = minDistance(id);
  if (zoom && dist == null) ({ dir, dist } = cam.focusView(W.byId.get(id) || id, state.frame, W, clock.t, view()));
  dist ??= Math.max(camera.position.length(), controls.minDistance * 1.2);
  tween.start = performance.now(); tween.dur = ms; tween.active = true;
  camTween.tweenTo(dir, Math.min(controls.maxDistance, dist), ms); camMoving = true;
  dirty = true; saveURL();
  return true;
}

// Earth–Moon framing (V1): the Moon's orbit over ~60 % of the width, but Earth at least 5 px (2.5 px on a phone).
function emDistance() {
  const w = $('scene').clientWidth, h = $('scene').clientHeight, t = Math.tan(THREE.MathUtils.degToRad(camera.fov) / 2);
  const px = Math.max(.6 * w / (2 * Math.hypot(...W.moonF) / RE_KM), phone.matches ? 2.5 : 5.2);
  return Math.min(controls.maxDistance, h / 2 / t / px);
}

function preset(name, ms = reduced ? 0 : 1000) {
  const v = view(), views = { wide: ['earth', ['se', 'sun'], cam.fitDistance(275, v)], em: ['earth', ['em', state.frame === 'em' ? 'moon' : 'sun'], emDistance()],
    earth: ['earth', ['se', 'sun'], 12], moon: ['moon', null, 2.0], l1: ['SE-L1', ['se', 'sun', 55], cam.fitDistance(150, v)], l2: ['SE-L2', ['se', 'sun', 55], cam.fitDistance(150, v)] };
  const key = views[name] ? name : 'em', [id, dir, d] = views[key];
  focusOn(id, false, dir ? cam.planeView(W, clock.t, ...dir) : null, d, ms);
  state.view = key;
  for (const b of $('views').querySelectorAll('button')) b.classList.toggle('active', b.dataset.view === key);
  dirty = true; saveURL();
}

// ---------- selection, visibility, frames ----------
function select(id) {
  state.selected = id && W.byId.has(id) ? id : null;
  if (state.selected && !W.byId.get(id).visible && W.byId.get(id).draw) setVisible(W.byId.get(id), true);
  trails.setIdleDim(!state.selected);                                        // V2: the HEO tangle idles dim while nothing is selected
  paramsDirty = dirty = forceUI = true; readoutFor = undefined; list.render(); saveURL();
}
function setVisible(T, on) { T.visible = on && T.draw; T.userSet = true; paramsDirty = dirty = true; list?.render(); }
function setGroupVisible(group, on) { for (const T of W.targets) if (T.group === group && T.draw) T.visible = on; paramsDirty = dirty = true; list.render(); }
function setChip(key, on) {
  chips[key] = on;
  for (const T of W.targets) if ({ t1: !T.natural && T.tier === 1, t2: !T.natural && T.tier === 2, nat: T.natural, t3: !T.natural && T.tier >= 3 }[key]) T.visible = on && defaultOn(T);
  for (const b of $('chips').querySelectorAll('button')) b.setAttribute('aria-pressed', String(chips[b.dataset.chip]));
  legendUI(); paramsDirty = dirty = forceUI = true; list.render(); saveURL();
}

function setFrame(key) {
  if (!FRAMES[key]) return;
  const oldS = Float64Array.from(W.S), oldR = Float64Array.from(W.R);
  state.frame = W.frame = key; W.evaluate(clock.t);
  // Keep the view continuous: rotate the camera offset and a running camera tween's end points by S_new · S_oldᵀ (scene),
  // and the focus tween's start point by R_new · R_oldᵀ (frame km).
  const rot = (A, B, v) => { const o = [B[0] * v.x + B[3] * v.y + B[6] * v.z, B[1] * v.x + B[4] * v.y + B[7] * v.z, B[2] * v.x + B[5] * v.y + B[8] * v.z];
    v.x = A[0] * o[0] + A[1] * o[1] + A[2] * o[2]; v.y = A[3] * o[0] + A[4] * o[1] + A[5] * o[2]; v.z = A[6] * o[0] + A[7] * o[1] + A[8] * o[2]; };
  rot(W.S, oldS, camera.position);
  if (camTween.active) { rot(W.S, oldS, camTween.from); rot(W.S, oldS, camTween.to); }
  if (tween.active) { const f = { x: tween.from[0], y: tween.from[1], z: tween.from[2] }; rot(W.R, oldR, f); tween.from.set([f.x, f.y, f.z]); }
  trails.rebuild(key); tracklets.rebuild(key, neocpObjects);
  frameUI(); paramsDirty = dirty = true; saveURL();
}

function frameUI() {
  for (const b of $('frames').querySelectorAll('button')) b.setAttribute('aria-pressed', String(b.dataset.frame === state.frame));
  $('frame-note').textContent = FRAMES[state.frame].note;
  const title = `${FRAMES[state.frame].label} · focus ${state.focus === 'earth' ? 'Earth' : state.focus === 'moon' ? 'Moon' : W.byId.get(state.focus)?.label || state.focus}`;
  if ($('view-title').textContent !== title) { $('view-title').textContent = title; keepOut = null; }
  $('view-description').textContent = FRAMES[state.frame].note;
}

// Scene overlays (title, legend, chips, hint, credit, banner) that labels must not cover; measured once per layout change.
let keepOut = null, barPx = 0;
function keepOutBoxes() {
  const r = $('scene').getBoundingClientRect();
  return [...document.querySelectorAll('.scene-title, #legend, #legend-toggle, #scene-hint, #help-toggle, .scene-credit, #banner')].filter(e => e.offsetParent && !e.hidden)
    .map(e => { const b = e.getBoundingClientRect(); return { x: b.left - r.left, y: b.top - r.top, width: b.width, height: b.height }; });
}

// ---------- per-target trail and marker styling ----------
function trailParams() {
  for (const T of W.targets) {
    // Selected: the whole window, except Moon-anchored orbiters (3 trail lengths; a 2 h orbit would fan into hundreds of rings).
    const sel = state.selected === T.id, preset = TRAILS[state.trail], moon = T.track?.center === 'moon';
    const past = sel ? (moon ? Math.min(W.window_s, 3 * T.trail.past) : W.window_s) : preset ?? T.trail.past;
    const future = sel ? (moon ? T.trail.past : W.window_s) : preset ? (state.trail === 'full' ? W.window_s : preset / 2) : T.trail.future;
    trails.setParams(T.index, T.color, isVisible(T) && layers.trails, past, future, sel);
  }
  // The Moon (V1): one synodic month (±14.75 d, inside the ±15 d window) so the orbit the opening view is framed on closes;
  // 7 d back in the EM frame, where the Moon sits on +x.
  const em = state.frame === 'em';
  trails.setParams(trails.moonIndex, MOON_C, layers.trails && layers.moonTrail, em ? 7 * DAY : 14.75 * DAY, em ? 0 : 14.75 * DAY, false);
  paramsDirty = false;
}

function ensureMarkers() {
  const need = W.targets.length + 2;
  if (markers && markers.capacity >= need) return;
  markers?.points.removeFromParent();
  markers = new PointLayer(need + 16); scene.add(markers.points);
  trails.ensureTexture(); paramsDirty = true;
}

const sv = (p, out) => out.set((p[0] - focusKm[0]) / RE_KM, (p[2] - focusKm[2]) / RE_KM, -(p[1] - focusKm[1]) / RE_KM);
const _v = new THREE.Vector3(), _w = new THREE.Vector3(), MOON_C = new THREE.Color(token('--moon')), RING_C = new THREE.Color(token('--ink')), EARTH_C = RING_C;
function pixelRadius(center, radius) {
  const h = $('scene').clientHeight, d = camera.position.distanceTo(center);
  return h / 2 / Math.tan(THREE.MathUtils.degToRad(camera.fov) / 2) * Math.tan(Math.asin(Math.min(1, radius / d)));
}

function updateScene() {
  W.evaluate(clock.t);
  currentFocus(focusKm);
  if (paramsDirty) trailParams();
  const pr = renderer.getPixelRatio(), res = new THREE.Vector2($('scene').clientWidth, $('scene').clientHeight);
  bodies.update(clock.t, focusKm, layers);
  trails.update(clock.t, focusKm, T => (T ? isVisible(T) && layers.trails : layers.trails && layers.moonTrail), res);
  for (const m of lineMaterials) m.resolution.copy(res);
  let n = 0;
  for (const T of W.targets) {
    const on = isVisible(T) && T.has;
    T.scene = on ? sv(T.pos, T.scene || new THREE.Vector3()) : null;
    if (!on) { markers.set(n++, [0, 0, 0], T.color, 0, 0, false, 0); continue; }
    // V4 (markers.targetStyle): hollow = stale or extrapolated; derelict and GP dimmer. Staleness is §6.4's isStale.
    markers.setTarget(n++, T.scene.toArray(), T, { selected: state.selected === T.id, hovered: state.hovered === T.id, stale: isStale(T, clock.t) });
  }
  const earthPx = pixelRadius(bodies.earth.position, 1), moonPx = pixelRadius(bodies.moon.position, RADIUS.moon);
  // Earth under 8 px (5.2 px in the opening view, 2.4 px on a phone, smaller than any marker): a ring 3 px outside the limb;
  // under 2 px the filled 7 px dot.
  markers.set(n++, bodies.earth.position.toArray(), EARTH_C, earthPx < 2 ? 7 : 2 * earthPx + 6, SHAPE.circle, earthPx >= 2, earthPx < 8 ? .8 : 0);
  markers.set(n++, bodies.moon.position.toArray(), MOON_C, 6, SHAPE.circle, false, moonPx < 2 && layers.moon !== false ? 1 : 0);
  markers.commit(n, pr);
  state.earthPx = earthPx; state.moonPx = moonPx;
  const e = _v.copy(bodies.earth.position).project(camera), m = _w.copy(bodies.moon.position).project(camera);
  state.moonDistPx = Math.hypot((e.x - m.x) * res.x / 2, (e.y - m.y) * res.y / 2);
  lagVis = lagrangeVisibility(state.frame, state.moonDistPx, W.byId.get(state.focus) || state.focus);        // V5
  const S = state.selected && W.byId.get(state.selected), H = state.hovered && W.byId.get(state.hovered);
  rings.set(0, S?.scene ? S.scene.toArray() : [0, 0, 0], RING_C, 21, SHAPE.ring, true, S?.scene ? .9 : 0);         // clear of the 12 / 10 px glyph corners (V4)
  rings.set(1, H?.scene && H !== S ? H.scene.toArray() : [0, 0, 0], RING_C, 18, SHAPE.ring, true, H?.scene && H !== S ? .5 : 0);
  rings.commit(2, pr);
  lagrange.update(focusKm, layers.lagrange, pr, lagVis);
  scaleRings.update(bodies, W, layers.rings, earthPx);
  const F = W.byId.get(state.focus);
  tracklets.update(focusKm, layers, id => isVisible(W.byId.get(id)), pr, F?.has ? { id: F.id, pos: F.pos } : null, clock.t, windowOf);
}

// The trail window (s) a target is drawn with now, as trails.restyle() wrote it (idle dim, EM far cap, hover, selection),
// and whether it is selected, hovered or focused: its tracklet beads show only inside that window (TrackletLayer.update).
function windowOf(id) {
  const T = W.byId.get(id), o = T && (trails.width + T.index) * 4;
  return T ? { past: trails.data[o], future: trails.data[o + 1], emph: id === state.selected || id === state.hovered || id === state.focus } : null;
}

// ---------- labels, Sun indicator, picking ----------
function occluded(p, occluders) {
  const ray = _v.copy(p).sub(camera.position), len = ray.length();
  ray.divideScalar(len);
  for (const o of occluders) {
    const rel = _w.copy(o.position).sub(camera.position), d = rel.dot(ray);
    if (d > 0 && d < len - o.radius * .2 && rel.addScaledVector(ray, -d).lengthSq() < (o.radius * .995) ** 2) return true;
  }
  return false;
}

function project(p, w, h) {
  const ndc = _v.copy(p).project(camera);
  if (!(ndc.z > -1 && ndc.z < 1) || Math.abs(ndc.x) > 1.05 || Math.abs(ndc.y) > 1.05) return null;
  return { x: (ndc.x + 1) * w / 2, y: (1 - ndc.y) * h / 2, z: ndc.z };
}

function positionLabels() {
  const w = $('scene').clientWidth, h = $('scene').clientHeight, occluders = [{ position: bodies.earth.position, radius: 1 }, { position: bodies.moon.position, radius: RADIUS.moon }];
  occupancy.clear();
  const bar = scaleBar.update(camera, h);
  if (Math.abs(bar.px - barPx) > 40) { barPx = bar.px; keepOut = null; }        // the foot column changed width: the legend may reflow
  for (const b of keepOut ||= keepOutBoxes()) occupancy.add(b, true);
  occupancy.add(scaleBar.box($('scene')), true);
  const cands = [];
  const add = (key, text, p, priority, cls, always = false, free = false) => {
    const s = project(p, w, h);
    if (!s || occluded(p, occluders.filter(o => o.position !== p))) return null;
    if (layers.labels || always) cands.push({ key, text, x: s.x, y: s.y, priority: priority - s.z, cls, always, free });
    return s;
  };
  if (state.earthPx < 150) add('earth', 'Earth', bodies.earth.position, 950, 'body', true);
  add('moon', 'Moon', bodies.moon.position, 940, 'body', true);
  for (const T of W.targets) {
    T.screen = null;
    if (!T.scene) continue;
    const s = project(T.scene, w, h);
    if (!s || occluded(T.scene, occluders)) continue;
    T.screen = s;
    const sel = state.selected === T.id, focus = state.focus === T.id, hov = state.hovered === T.id;
    if (!layers.labels && !sel && !focus) continue;
    cands.push({ key: T.id, text: T.label, x: s.x, y: s.y, cls: sel ? 'sel' : T.tier > 1 ? 'dim' : '', always: sel || focus,
      priority: (focus ? 1000 : sel ? 900 : hov ? 800 : 0) + (T.tier === 1 ? 300 : 150) + (T.shape === 0 ? 30 : 0) + (T.source === 'neocp' ? 20 : 0) - s.z });
  }
  // On-screen markers, Lagrange glyphs (SE-L1 ±12 px) and bodies: the edge-placed Sun and the chevrons keep clear of them
  // (one on top would hide it and catch its taps).
  const box = (s, r = 5) => s && { x: s.x - r, y: s.y - r, width: 2 * r, height: 2 * r };
  const marks = [...W.targets.map(T => box(T.screen)), ...lagrange.scene.map((p, i) => lagrange.shown[i] && box(project(p, w, h), i ? 5 : 12)),
    box(project(bodies.earth.position, w, h), Math.max(5, state.earthPx + 3)), box(project(bodies.moon.position, w, h), Math.max(5, state.moonPx + 3))].filter(Boolean);
  const sun = sunIndicator(w, h, marks);
  occupancy.add(sun, true);
  for (const b of edgeMarkers(w, h, [sun, ...marks])) occupancy.add(b, true);
  // Labels never cover another marker: a soft 8 px box per on-screen marker (own marker clears the 9 px offsets).
  const dot = s => s && occupancy.add({ x: s.x - 4, y: s.y - 4, width: 8, height: 8 });
  for (const T of W.targets) dot(T.screen);
  lagrange.scene.forEach((p, i) => lagrange.shown[i] && dot(project(p, w, h)));
  // Lagrange labels (short keys) sit outside the 40-label cap; the frame's own system (SE or EM) is always labelled.
  LAGRANGE.forEach((L, i) => {
    if (!lagrange.shown[i]) return;
    const own = L.system === state.frame, focus = state.focus === L.key;
    add(L.key, L.key, lagrange.scene[i], (own ? 400 : 120) + (focus ? 700 : 0), 'lagrange', own || focus, true);
  });
  labels.place(cands, w, h, occupancy);
  sky.positionLabels(w, h, occupancy, camera, occluders);
}

function sunIndicator(w, h, marks = []) {
  const el = $('sun-indicator'), s = W.sunF, d = _v.set(s[0], s[2], -s[1]).normalize(), c = d.clone().applyQuaternion(camera.quaternion.clone().invert());
  el.hidden = false;
  const ndc = d.clone().project(sky.camera), sx = (ndc.x + 1) * w / 2, sy = (1 - ndc.y) * h / 2;
  // SE-L1 lies on the Sun line: where its glyph is within 24 px of the in-view Sun point the Sun goes to the edge instead.
  const l1 = lagrange.shown[0] && project(lagrange.scene[0], w, h);
  let x, y, right = false;
  if (c.z < 0 && Math.abs(ndc.x) < .95 && Math.abs(ndc.y) < .92 && !(l1 && Math.hypot(l1.x - sx, l1.y - sy) < 24)) {
    x = sx - 5; y = sy - 5;
    el.classList.remove('edge'); el.firstChild.style.transform = '';
  } else {
    const a = Math.atan2(-c.y, c.x);
    ({ x, y, right } = edgePoint(a, w, h));
    y = cam.placeEdgeBox({ x: right ? x - 32 : x - 2, y: y - 3, width: 46, height: 20 }, marks, h).y + 3;     // slid off SE-L1 and markers at the edge
    el.classList.add('edge'); el.firstChild.style.transform = `rotate(${a}rad)`;
  }
  el.classList.toggle('right', right);
  el.style.transform = `translate(${right ? x - 30 : x}px,${y}px)`;
  return { x: right ? x - 32 : x - 2, y: y - 3, width: 46, height: 20 };                      // the 15-px line box plus a gap
}

// Edge chevrons (overlays.Chevrons, V1/V12) for what is off screen, in this order: the selected target, Earth, the Moon,
// SE-L1/L2 and visible natural objects (34–131 Mkm out, beyond the 38 Mkm camera limit), with the range from the focus,
// clear of the overlays and of `avoid` (the Sun glyph, on-screen markers, glyphs and bodies). Returns their boxes.
function edgeMarkers(w, h, avoid) {
  const S = state.selected && W.byId.get(state.selected), items = [];
  if (S?.scene) items.push({ id: S.id, label: S.label, color: S.css, p: S.scene });
  items.push({ id: 'earth', label: 'Earth', color: 'var(--ink)', p: bodies.earth.position }, { id: 'moon', label: 'Moon', color: 'var(--moon)', p: bodies.moon.position });
  for (const i of [0, 1]) if (lagrange.shown[i]) {                          // "SE-L1 · 8 · 1.49 Mkm": 8 shown targets of that regime off screen
    const key = LAGRANGE[i].key, n = W.targets.filter(T => T.scene && !T.screen && T.regime.key === (i ? 'SE_L2' : 'SE_L1')).length;
    items.push({ id: key, label: n ? `${key} · ${n}` : key, title: n ? `${n} ${key} target${n > 1 ? 's' : ''} off screen; click to fly there` : '', color: 'var(--lagrange)', p: lagrange.scene[i] });
  }
  for (const T of W.targets) if (T.natural && T.scene && T !== S) items.push({ id: T.id, label: T.label, color: T.css, p: T.scene });
  return chevrons.update(items, camera, w, h, [...(keepOut ||= keepOutBoxes()), ...avoid]);
}

function pick(x, y, radius = 12) {
  let best = null, bd = radius;
  for (const T of W.targets) if (T.screen) { const d = Math.hypot(T.screen.x - x, T.screen.y - y); if (d < bd) { bd = d; best = T.id; } }
  if (best) return best;
  const w = $('scene').clientWidth, h = $('scene').clientHeight;
  for (const [id, mesh, r] of [['moon', bodies.moon, Math.max(6, state.moonPx)], ['earth', bodies.earth, Math.max(6, state.earthPx)]]) {
    const s = project(mesh.position, w, h);
    if (s && Math.hypot(s.x - x, s.y - y) < r) return id;
  }
  for (const [i, L] of LAGRANGE.entries()) {
    if (!lagrange.shown[i]) continue;
    const s = project(lagrange.scene[i], w, h);
    if (s && Math.hypot(s.x - x, s.y - y) < 8) return L.key;
  }
  return pickTrail(x, y);
}

// Nearest drawn trail within `radius` px: the batched segments inside each target's drawn window (the restyled texels),
// projected with one matrix per batch (f32 is fine for a hover). Runs only when nothing else is under the pointer.
const _mvp = new THREE.Matrix4();
function pickTrail(x, y, radius = 6) {
  if (!layers.trails) return null;
  const w = $('scene').clientWidth, h = $('scene').clientHeight, now = (clock.t - W.t0) / 1000, tex = trails.data, tw = trails.width;
  let best = null, bd = radius;
  for (const [b, group] of [[trails.batches.earth, trails.earthGroup], [trails.batches.moon, trails.moonGroup], [trails.focusBatch, trails.focusGroup]]) {
    if (!b) continue;
    const e = _mvp.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse).multiply(group.matrixWorld).elements;
    const { start, end, time, info } = b.buf, n = b.geometry.instanceCount;
    let last = -1, skip = true, past = 0, future = 0, T = null;
    for (let i = 0; i < n; i++) {
      const idx = info[i * 2];
      if (idx !== last) {
        last = idx; T = idx === trails.focusIndex ? trails.focusT : W.targets[idx];
        skip = !T || !isVisible(T) || tex[idx * 4 + 3] < .5; past = tex[(tw + idx) * 4]; future = tex[(tw + idx) * 4 + 1];
      }
      if (skip || time[i * 2 + 1] < now - past || time[i * 2] > now + future) continue;
      const ax = start[i * 3], ay = start[i * 3 + 1], az = start[i * 3 + 2], aw = e[3] * ax + e[7] * ay + e[11] * az + e[15];
      const bx = end[i * 3], by = end[i * 3 + 1], bz = end[i * 3 + 2], bw = e[3] * bx + e[7] * by + e[11] * bz + e[15];
      if (aw <= 0 || bw <= 0) continue;
      const x0 = ((e[0] * ax + e[4] * ay + e[8] * az + e[12]) / aw + 1) * w / 2, y0 = (1 - (e[1] * ax + e[5] * ay + e[9] * az + e[13]) / aw) * h / 2;
      const x1 = ((e[0] * bx + e[4] * by + e[8] * bz + e[12]) / bw + 1) * w / 2, y1 = (1 - (e[1] * bx + e[5] * by + e[9] * bz + e[13]) / bw) * h / 2;
      if (Math.min(x0, x1) > x + bd || Math.max(x0, x1) < x - bd || Math.min(y0, y1) > y + bd || Math.max(y0, y1) < y - bd) continue;
      const dx = x1 - x0, dy = y1 - y0, dd = dx * dx + dy * dy, u = dd ? Math.max(0, Math.min(1, ((x - x0) * dx + (y - y0) * dy) / dd)) : 0;
      const d = Math.hypot(x0 + u * dx - x, y0 + u * dy - y);
      if (d < bd) { bd = d; best = T.id; }
    }
  }
  return best;
}

function tooltip(id, x, y) {
  const el = $('tooltip'), T = W.byId.get(id);
  if (!id) { el.hidden = true; return; }
  if (!T) {
    const i = LAGRANGE.findIndex(L => L.key === id), note = i < 0 ? '' : `${rangeText(Math.hypot(...W.lagrange.out[i]))} from Earth · ${LAGRANGE[i].system === 'se' ? 'nearly stationary in the Sun–Earth frame' : 'co-moves with the Moon'}`;
    el.innerHTML = `<b>${id === 'earth' ? 'Earth' : id === 'moon' ? 'Moon' : id}</b>${note ? `<p class="fine">${note}</p>` : ''}<p class="fine">double-click to focus</p>`;
  } else {
    el.innerHTML = targetTip(T, clock.t);
  }
  el.hidden = false;
  const w = $('scene').clientWidth;
  el.style.left = `${Math.min(x + 14, w - 290)}px`; el.style.top = `${y + 14}px`;
}

// ---------- UI ----------
// UI refreshes run after the next draw, so readouts and the Situation panel see the new epoch.
function setTime(t) { clock.set(t); clock.playing = false; dirty = forceUI = true; saveURL(); }
function ui(force = false) {
  banner();                                               // first: it sets snapshotStale, which the readouts show
  const t = clock.t, u = utc(t);
  $('utc-date').textContent = u.slice(0, 10); $('utc-time').textContent = u.slice(11, 19); $('utc-clock').dateTime = iso(t);
  $('top-clock').textContent = `${u} UTC`; $('utc-mini').textContent = `${u} UTC`; $('scene-clock').textContent = `${u} UTC`;
  const rel = (t - W.now) / 1000, sign = rel < 0 ? '−' : '+', a = Math.abs(rel);
  $('elapsed').textContent = `build now ${sign}${Math.floor(a / 86400)}d ${String(Math.floor(a % 86400 / 3600)).padStart(2, '0')}:${String(Math.floor(a % 3600 / 60)).padStart(2, '0')}`;
  $('timeline').value = String((t - W.t0) / 1000);
  $('live').setAttribute('aria-pressed', String(clock.live)); $('live').disabled = !clock.liveAvailable;
  $('live-badge').classList.toggle('on', clock.live); $('live-badge').textContent = clock.live ? 'LIVE' : clock.playing ? 'PLAY' : 'PAUSED';
  $('play').textContent = clock.playing ? 'Ⅱ Pause' : '▶ Play'; $('reverse').setAttribute('aria-pressed', String(clock.dir < 0));
  const sel = state.selected && W.byId.get(state.selected);
  const changed = readoutFor !== (sel?.id ?? null);
  if (changed) { readoutFor = sel?.id ?? null; if (sel) renderTarget($('readout'), sel, W, tracklets); ticks(); }
  if (sel) updateTarget(sel, W, t);
  else if (force || changed || performance.now() - lastSituation > 1000) { renderSituation($('readout'), W, { isVisible, neocpNote: neocpNote() + naturalNote() }, t); lastSituation = performance.now(); }
  if (force || performance.now() - lastList > 500) { list.refresh(t); lastList = performance.now(); }
  const shown = W.targets.filter(T => isVisible(T)).length;
  $('target-count').textContent = `${shown} shown · ${W.targets.filter(T => T.draw).length} drawable · ${W.targets.length} listed`;
  frameUI();
}

function neocpNote() {
  if (!neocpBuilt) return '<p class="fine">neocp.js not loaded.</p>';
  if (!neocpSnapshot) return `<p class="fine">No live NEOCP snapshot in neocp.js (built ${utc(Date.parse(neocpBuilt)).slice(0, 16)} UTC).</p>`;
  return `<p class="fine">Snapshot ${utc(Date.parse(neocpSnapshot)).slice(0, 16)} UTC</p>`;
}

function naturalNote() {
  const nat = W.targets.filter(T => T.natural && isVisible(T) && T.has);
  if (!chips.nat || !nat.length || nat.some(T => T.screen)) return '';
  const r = nat.map(T => Math.hypot(T.state[0], T.state[1], T.state[2]) / 1e6);
  return `<p class="fine">${nat.length} natural object${nat.length > 1 ? 's' : ''} ${fmt(Math.min(...r))}–${fmt(Math.max(...r))} Mkm away, off screen (edge arrows): double-click a row to fly there.</p>`;
}

// Legend entries (ui/legend.js) follow their layer or chip: Sun −12° with the twilight ring, tracklet rows with the
// tracklets layer, ▲ natural with the Natural chip.
function legendUI() { legendRows($('legend'), layers, chips); keepOut = null; }

// '?' help overlay (C9): keys and glyphs; Esc, the × or a click on the scrim closes it.
const FAMILY = { LUNAR_ORBIT: 'lunar', EM_L1: 'Earth–Moon / translunar', CISLUNAR_HEO: 'cislunar HEO', SE_L1: 'SE-L1', SE_L2: 'SE-L2',
  EARTH_NEAR_HELIO: 'heliocentric', NEOCP_GEO: 'NEOCP', OTHER: 'other' };
function helpUI(open = $('help').hidden) {
  if (open) {                                     // colour = regime family, from catalog.js (src/cle/catalog.py FAMILY)
    const fam = new Map();
    for (const r of [...W.regimes.values()].sort((a, b) => a.order - b.order)) if (!fam.has(r.color)) fam.set(r.color, FAMILY[r.key] || r.label);
    $('help-colours').innerHTML = 'colour = regime family: ' + [...fam].map(([c, name]) => `<span class="nw"><i class="swatch" style="background:${c}"></i>${escapeHtml(name)}</span>`).join(' · ');
  }
  $('help').hidden = !open; $('help-toggle').setAttribute('aria-pressed', String(open));
  if (open) $('help-close').focus();
}

// Slider ticks (§10): now, the selected target's segment joins, and NEOCP observation epochs (the selected target's,
// or every live object's).
function ticks() {
  const T = state.selected && W.byId.get(state.selected), v = new Set([W.now]);
  for (const s of T?.track?.segments.slice(1) || []) v.add(s.t0_ms);
  const own = neocpObjects.filter(([d, o]) => (o.catalog?.id || d) === T?.id);
  for (const [, o] of own.length ? own : neocpObjects.filter(([, o]) => o.track)) for (const tk of o.tracklets || []) v.add(tk.t_ms[0]);
  $('ticks').innerHTML = [...v].filter(t => t >= W.t0 && t <= W.t1).sort((a, b) => a - b).map(t => `<option value="${Math.round((t - W.t0) / 1000)}"></option>`).join('');
}

function banner() {
  const msgs = [];
  if (W.t1 - Date.now() < DAY * 1000 && W.t1 > Date.now() - 30 * DAY * 1000) msgs.push(`Data window ends ${utc(W.t1).slice(0, 16)} UTC — run make daily`);
  if (Date.now() > W.t1 || Date.now() < W.t0) msgs.push(`Wall clock outside the data window; showing build epoch ${utc(W.now).slice(0, 16)} UTC`);
  const old = !!neocpSnapshot && Date.now() - Date.parse(neocpSnapshot) > 3 * 3600e3;
  for (const T of W.targets) if (T.source === 'neocp' && T.snapshotStale !== old) { T.snapshotStale = old; readoutFor = undefined; dirty = true; }
  if (missing.length) msgs.push(`Missing data chunks: ${missing.join(', ')}`);
  const text = msgs.join(' · ');
  if ($('banner').hidden !== !msgs.length || $('banner').textContent !== text) { $('banner').hidden = !msgs.length; $('banner').textContent = $('banner').title = text; keepOut = null; }
}

// A preset view travels as `view` (the camera is implied); a hand-moved camera as `cam`.
function saveURL() {
  const c = camera?.position;
  writeState({ t: clock.live ? null : iso(clock.t), frame: state.frame, focus: state.focus === 'earth' ? null : state.focus, sel: state.selected,
    layers: Object.keys(LAYERS).filter(k => layers[k]), tiers: Object.keys(chips).filter(k => chips[k]), trail: state.trail === 'auto' ? null : state.trail,
    view: state.view, cam: c && !state.view ? [c.x, c.y, c.z].map(x => +x.toPrecision(5)) : null });
}

function wireUI() {
  $('timeline').max = String(W.window_s);
  $('window-start').textContent = `${utc(W.t0).slice(5, 16)}`; $('window-stop').textContent = `${utc(W.t1).slice(5, 16)}`;
  $('speed').value = '3600';
  $('speed').addEventListener('change', () => { clock.rate = Number($('speed').value); });
  $('play').addEventListener('click', () => { clock.live = false; if (!clock.playing && (clock.t >= W.t1 && clock.dir > 0 || clock.t <= W.t0 && clock.dir < 0)) clock.t = clock.dir > 0 ? W.t0 : W.t1; clock.playing = !clock.playing; dirty = forceUI = true; saveURL(); });
  $('reverse').addEventListener('click', () => { clock.dir *= -1; dirty = forceUI = true; });
  $('live').addEventListener('click', () => { clock.setLive(!clock.live); dirty = forceUI = true; saveURL(); });
  $('timeline').addEventListener('input', () => setTime(W.t0 + Number($('timeline').value) * 1000));
  for (const [id, dt] of [['minus-day', -DAY], ['minus-hour', -3600], ['plus-hour', 3600], ['plus-day', DAY]]) $(id).addEventListener('click', () => setTime(clock.t + dt * 1000));
  $('now').addEventListener('click', () => { if (clock.liveAvailable) { clock.setLive(true); dirty = forceUI = true; saveURL(); } else setTime(W.now); });
  $('chips').querySelector('[data-chip=t3]').hidden = !W.targets.some(T => T.tier >= 3);
  $('frames').addEventListener('click', e => { const f = e.target.closest('button')?.dataset.frame; if (f) setFrame(f); });
  $('views').addEventListener('click', e => { const v = e.target.closest('button')?.dataset.view; if (v) preset(v); });
  for (const el of document.querySelectorAll('[data-layer]')) {
    el.checked = !!layers[el.dataset.layer];
    el.addEventListener('change', () => { layers[el.dataset.layer] = el.checked; legendUI(); paramsDirty = dirty = true; saveURL(); });
  }
  $('trail-length').value = state.trail;
  $('trail-length').addEventListener('change', () => { state.trail = $('trail-length').value; paramsDirty = dirty = true; saveURL(); });
  $('star-gain').addEventListener('input', () => { layers.starGain = Number($('star-gain').value); dirty = true; });
  for (const b of $('chips').querySelectorAll('button')) {
    b.setAttribute('aria-pressed', String(chips[b.dataset.chip]));
    b.addEventListener('click', () => setChip(b.dataset.chip, !chips[b.dataset.chip]));
  }
  $('search').addEventListener('input', () => { list.query = $('search').value.trim().toLowerCase(); list.render(); });
  $('readout').addEventListener('click', e => { const id = e.target.closest('[data-id]')?.dataset.id; if (id) select(id); });
  $('legend-toggle').addEventListener('click', () => { const open = $('legend').classList.toggle('open'); $('legend-toggle').setAttribute('aria-pressed', String(open)); keepOut = null; dirty = true; });
  $('help-toggle').addEventListener('click', () => helpUI());
  $('help-close').addEventListener('click', () => helpUI(false));
  $('help').addEventListener('click', e => { if (e.target === $('help')) helpUI(false); });
  const narrow = matchMedia('(max-width: 980px)'), hidden = { left: false, right: false };
  const layout = () => {
    const ws = $('controls-panel').parentElement;
    ws.classList.toggle('no-left', hidden.left); ws.classList.toggle('no-right', hidden.right); ws.classList.toggle('time-bar', narrow.matches || hidden.left);
    $('toggle-controls').setAttribute('aria-pressed', String(narrow.matches ? $('controls-panel').classList.contains('open') : !hidden.left));
    $('toggle-info').setAttribute('aria-pressed', String(narrow.matches ? $('state-panel').classList.contains('open') : !hidden.right));
    if (labels) labels.cap = phone.matches ? 16 : 40;                                   // V13
    dirty = true; keepOut = null;
  };
  for (const [id, panel, other, side] of [['toggle-controls', 'controls-panel', 'state-panel', 'left'], ['toggle-info', 'state-panel', 'controls-panel', 'right']])
    $(id).addEventListener('click', () => { if (narrow.matches) { $(panel).classList.toggle('open'); $(other).classList.remove('open'); } else hidden[side] = !hidden[side]; layout(); });
  for (const [id, panel] of [['close-controls', 'controls-panel'], ['close-info', 'state-panel']]) $(id).addEventListener('click', () => { $(panel).classList.remove('open'); layout(); });
  narrow.addEventListener('change', layout); phone.addEventListener('change', layout); layout();
  addEventListener('keydown', e => {
    if (e.target.closest('input,select,textarea')) { if (e.key === 'Escape') e.target.blur(); return; }
    const visibleList = () => [...list.rows.keys()].filter(id => W.byId.get(id).visible);
    if (e.code === 'Space') { e.preventDefault(); $('play').click(); }
    else if (e.code === 'ArrowRight' || e.code === 'ArrowLeft') setTime(clock.t + (e.code === 'ArrowRight' ? 1 : -1) * (e.shiftKey ? DAY : 3600) * 1000);
    else if (e.key === 'l' || e.key === 'L') $('live').click();
    else if ((e.key === 'f' || e.key === 'F') && state.selected) focusOn(state.selected);
    else if (e.key === 'e' || e.key === 'E') focusOn('earth');
    else if (e.key === 'm' || e.key === 'M') focusOn('moon');
    else if (e.key === '/') { e.preventDefault(); $('search').focus(); }
    else if (e.key === '?') { e.preventDefault(); helpUI(); }
    else if (e.key === 'Escape') { if (!$('help').hidden) helpUI(false); else select(null); }
    else if (e.key === 'Enter' && state.selected) focusOn(state.selected);
    else if (e.key === '[' || e.key === ']') {
      const ids = visibleList(), i = ids.indexOf(state.selected);
      if (ids.length) select(ids[(i + (e.key === ']' ? 1 : -1) + ids.length) % ids.length]);
    }
  });
  document.addEventListener('visibilitychange', () => { if (document.hidden) clock.playing = false; });
}

function setupScene() {
  renderer = new THREE.WebGLRenderer({ antialias: true, logarithmicDepthBuffer: true, powerPreference: 'default' });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2)); renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.autoClear = false; renderer.setClearColor(SKY_STYLE.background);
  $('scene').prepend(renderer.domElement);
  scene = new THREE.Scene();
  camera = new THREE.PerspectiveCamera(38, 1, 1e-4, 1e5);
  camTween = new cam.CameraTween(camera);
  controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = !reduced; controls.dampingFactor = .1; controls.zoomSpeed = .9; controls.enablePan = false;
  controls.mouseButtons.RIGHT = THREE.MOUSE.ROTATE; controls.touches.TWO = THREE.TOUCH.DOLLY_ROTATE;
  controls.minDistance = 1.02; controls.maxDistance = 6000;
  controls.addEventListener('change', () => { dirty = true; });
  controls.addEventListener('start', () => { if (state.view) { state.view = null; for (const b of $('views').querySelectorAll('button')) b.classList.remove('active'); } });
  controls.addEventListener('end', saveURL);
  sky = new StellarBackground(JSON.parse($('stellar-data').textContent), $('scene'));
  const layer = document.createElement('div'); layer.className = 'label-layer'; $('scene').appendChild(layer);
  labels = new Labels(layer, phone.matches ? 16 : 40);
  bodies = new Bodies(W, scene, renderer, { earth: $('earth-texture'), moon: $('moon-texture'), night: $('night-texture') }, lineMaterials);
  trails = new Trails(W, scene); trails.ensureTexture(); trails.rebuild(state.frame);
  ensureMarkers();
  rings = new PointLayer(2, { depthTest: false, renderOrder: 6 }); scene.add(rings.points);
  lagrange = new LagrangeLayer(W, scene);
  scaleRings = new ScaleRings(scene, lineMaterials);
  tracklets = new TrackletLayer(W, scene); tracklets.rebuild(state.frame, neocpObjects);
  chevrons = new Chevrons($('scene'), id => focusOn(id)); scaleBar = new ScaleBar($('foot-right')); scaleBar.el.id = 'scale-bar';
  W.evaluate(clock.t);
  new ResizeObserver(resize).observe($('scene')); resize();
  // Opening camera (V1): ?cam wins, then ?view (which implies its focus), else the Earth–Moon framing; a ?focus without
  // ?cam or ?view flies there.
  const urlView = !url.cam && VIEWS.includes(url.view);
  if (url.cam) camera.position.copy(new THREE.Vector3(...url.cam)); else preset(urlView ? url.view : 'em', 0);
  camera.lookAt(0, 0, 0);
  if (url.focus && !urlView) focusOn(url.focus, !url.cam);
  trails.setIdleDim(!url.sel);
  let press = null, hoverQueued = false, hoverAt = null;
  const el = renderer.domElement;
  el.addEventListener('pointerdown', e => { press = e.button === 0 ? { x: e.clientX, y: e.clientY, t: performance.now() } : null; });
  el.addEventListener('pointerup', e => {
    const click = press && Math.hypot(e.clientX - press.x, e.clientY - press.y) < 5 && performance.now() - press.t < 600; press = null;
    if (!click) return;
    const r = el.getBoundingClientRect(), id = pick(e.clientX - r.left, e.clientY - r.top);
    if (id && W.byId.has(id)) select(id);
  });
  el.addEventListener('dblclick', e => { const r = el.getBoundingClientRect(), id = pick(e.clientX - r.left, e.clientY - r.top); if (id) focusOn(id); });
  el.addEventListener('pointermove', e => {
    const r = el.getBoundingClientRect(); hoverAt = [e.clientX - r.left, e.clientY - r.top];
    if (hoverQueued) return; hoverQueued = true;
    requestAnimationFrame(() => {
      hoverQueued = false;
      const id = pick(...hoverAt);
      if (id !== state.hovered) { state.hovered = id; trails.setHover(W.byId.has(id) ? id : null); dirty = true; }   // V2 hover emphasis
      tooltip(id, ...hoverAt); el.style.cursor = id ? 'pointer' : '';
    });
  });
  el.addEventListener('pointerleave', () => { state.hovered = null; trails.setHover(null); tooltip(null); dirty = true; });
  el.addEventListener('webglcontextlost', e => { e.preventDefault(); $('render-error').hidden = false; $('render-error-detail').textContent = 'Graphics context lost. Reload the page.'; renderReady = false; });
  renderReady = true;
}

function resize() {
  const w = $('scene').clientWidth, h = $('scene').clientHeight;
  if (!w || !h) return;
  renderer.setSize(w, h); camera.aspect = w / h; camera.updateProjectionMatrix(); dirty = true; keepOut = null;
}

function frame(now) {
  requestAnimationFrame(frame);
  if (!renderReady) return;
  const dt = lastFrame === null ? 0 : Math.min(.1, (now - lastFrame) / 1000); lastFrame = now;
  const ticked = clock.tick(dt, now);
  if (ticked) dirty = true;
  if (tween.active) dirty = true;
  if (camTween.active) { camTween.step(now); dirty = true; } else if (camMoving) { camMoving = false; saveURL(); }
  if (controls.update()) dirty = true;
  if (dirty) { dirty = false; uiPending = true; draw(); }
  if (uiPending && (forceUI || now - lastUI > 80)) { ui(forceUI); lastUI = now; uiPending = forceUI = false; }
}

// MMX render order: sky (rotated with the display frame), depth clear, scene; then the DOM labels.
function draw() {
  camera.updateMatrixWorld(true);                        // updateScene projects with this draw's camera (V5 moonDistPx, chevrons)
  updateScene();
  renderer.clear(); sky.root.quaternion.copy(bodies.qf); sky.render(renderer, camera, layers); renderer.clearDepth(); renderer.render(scene, camera);
  positionLabels();
}

// Test hook: n synchronous frames 10 min apart, GPU-synced with a 1-pixel read; times in ms.
function probe(n = 60) {
  const gl = renderer.getContext(), px = new Uint8Array(4), times = [], t = clock.t;
  for (let i = 0; i < n; i++) {
    const s = performance.now();
    clock.set(Math.min(W.t1, t + i * 600e3)); draw(); gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
    times.push(performance.now() - s);
  }
  clock.set(t); dirty = true;
  times.sort((a, b) => a - b);
  return { n, median: times[n >> 1], p90: times[Math.floor(n * .9)], max: times[n - 1] };
}

// Test hook: screen position of a target at epoch t (its trail passes there).
function screenAt(id, t) {
  const T = W.byId.get(id), s = new Float64Array(6), p = new Float64Array(3), v = new THREE.Vector3();
  if (!T || !W.stateAt(T, t, s)) return null;
  apply(W.frames.matrix(state.frame, t), s, p);
  return project(sv(p, v), $('scene').clientWidth, $('scene').clientHeight);
}

async function main() {
  await loadData();
  list = new TargetList($('target-list'), W, { setVisible, setGroupVisible, select, focus: id => focusOn(id), chipOn, loaded: true, get selected() { return state.selected; } });
  renderLegend($('legend')); wireUI();
  try { setupScene(); } catch (error) {
    $('loading').hidden = true; $('render-error').hidden = false; $('render-error-detail').textContent = error.message; console.error('cislunar-eye renderer:', error);
  }
  if (url.sel) select(url.sel); else list.render();
  legendUI(); ticks(); frameUI(); ui(true);
  $('loading').hidden = true;
  setInterval(() => { refreshNeocp(); }, (manifest?.neocp?.refresh_s || 600) * 1000);
  requestAnimationFrame(frame);
}

window.__cle = { state, layers, chips, clock, world: W, select, setFrame, setChip, setVisible, focusOn, preset, setTime, refreshNeocp, isVisible, probe, pick, screenAt, helpUI,
  overlays: { lagrangeVisibility },                                            // test hooks for the camera and overlay contracts
  cam: { focusDistance: (id, frame = state.frame) => cam.focusDistance(W.byId.get(id) || id, frame, view()), tweenTo: (dir, dist, ms) => camTween.tweenTo(dir, dist, ms), get tween() { return camTween; } },
  get ready() { return renderReady && !!list; }, get camera() { return camera; }, get controls() { return controls; }, get sky() { return sky; },
  get trails() { return trails; }, get markers() { return markers; }, get bodies() { return bodies; }, get lagrange() { return lagrange; }, get labels() { return labels; },
  get chevrons() { return chevrons; }, get scaleBar() { return scaleBar; }, get scaleRings() { return scaleRings; }, get camTween() { return camTween; },
  get tracklets() { return tracklets; }, get neocpBuilt() { return neocpBuilt; }, get neocpSnapshot() { return neocpSnapshot; }, get missing() { return missing; }, get focusKm() { return focusKm; },
  labelsShown: () => [...document.querySelectorAll('.label-layer:not(.sky-labels) .body-label')].filter(e => !e.hidden).map(e => e.textContent),
  edgesShown: () => [...document.querySelectorAll('.edge-marker')].filter(e => !e.hidden).map(e => e.dataset.id),
  screenOf: id => W.byId.get(id)?.screen || null };
main();
