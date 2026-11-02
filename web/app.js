import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { installRegister, registered, inject, decodeTrack } from './loader.js';
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
// The panel modules are imported as namespaces (spec §C): an optional export a lane has not shipped yet is undefined
// here, not a build error.
import * as listUI from './ui/list.js';
import * as readout from './ui/readout.js';
import * as neocpUI from './ui/neocp.js';
import * as timelineUI from './ui/timeline.js';
import { readState, writeState } from './ui/url.js';
import { renderLegend, legendUI as legendRows } from './ui/legend.js';
import { utc, iso, rangeText, escapeHtml, glyphClass } from './ui/format.js';
import { Clock, SPEEDS } from './time.js';
import { token } from './ui/theme.js';

const $ = id => document.getElementById(id);
const text = (el, s) => { if (el.textContent !== s) el.textContent = s; };
installRegister();
const core = registered('core'), catalog = registered('catalog');
const base = window.CLE_DATA_BASE || 'data';
if (!core || !catalog) {
  $('loading').textContent = `Data sidecars missing: ${base}/core.js and ${base}/catalog.js must load before the page script (run make build).`;
  throw new Error('cislunar-eye: core.js / catalog.js not registered');
}

const W = new World(core, catalog), url = readState();
const LAYERS = { trails: true, labels: true, lagrange: true, tracklets: true, sightlines: false, moonTrail: true, rings: false,
  stars: true, starNames: false, grid: false, constellations: false };
const layers = { ...LAYERS, starGain: 1 };
if (url.layers) for (const k of Object.keys(LAYERS)) layers[k] = url.layers.includes(k);
const chips = { t1: true, t2: false, t3: false };
if (url.tiers) for (const k of Object.keys(chips)) chips[k] = url.tiers.includes(k);
const TRAILS = { '1d': DAY, '3d': 3 * DAY, '15d': 15 * DAY, full: W.window_s };
const VIEWS = ['em', 'wide', 'earth', 'moon', 'l1', 'l2'];          // 'em' (the Earth–Moon framing) opens the page (V1)
const KEY_FRAMES = ['se', 'eci', 'em'];                             // keys 1 2 3
const SHORT_FRAME = { se: 'SE rot', eci: 'ECI', em: 'EM rot' };
const RATE = Object.fromEntries(SPEEDS.map(([v, label]) => [v, label.replace(' / ', '/')]));   // pill: "▶ 1 h/s"
const state = { frame: FRAMES[url.frame] ? url.frame : 'se', trail: url.trail in TRAILS ? url.trail : 'auto', selected: null, focus: 'earth', hovered: null, view: null, moonDistPx: 0 };
W.frame = state.frame;
const clock = new Clock(W.t0, W.t1, W.now);
if (url.t && Number.isFinite(Date.parse(url.t))) clock.set(Date.parse(url.t)); else clock.setLive(true);
const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches, narrow = matchMedia('(max-width: 1100px)'), phone = matchMedia('(max-width: 700px)');
const chipOn = T => T.tier === 1 ? chips.t1 : T.tier === 2 ? chips.t2 : chips.t3;
const isVisible = T => !!T && T.draw && !!T.track && T.visible;
const defaultOn = T => T.draw && chipOn(T) && (T.tier !== 1 || T.cat.default_visible !== false || !T.cat.drawn);
for (const T of W.targets) T.visible = defaultOn(T);
const store = (() => { try { return window.localStorage; } catch { return null; } })();
document.documentElement.style.setProperty('--neocp', W.regimes.get('NEOCP_GEO')?.color || '');

// Tracklets: identified catalog targets' astrometry from their chunk payloads, plus each requested NEOCP object's
// (TrackletLayer entries [key, {catalog: {id}, tracklets}]).
const catalogTracklets = [], requested = new Map(), missing = [];
const trackletEntries = () => [...catalogTracklets, ...requested.values()];
const trackletsOf = id => trackletEntries().find(([key, o]) => (o.catalog?.id || key) === id)?.[1].tracklets || [];
const requestedTargets = () => W.targets.filter(T => T.source === 'request' && !T.removed);
let renderer, scene, camera, controls, camTween, sky, bodies, trails, markers, rings, lagrange, tracklets, chevrons, scaleBar, scaleRings, labels, list, lookup, marks;
let renderReady = false, dirty = true, paramsDirty = true, uiPending = true, forceUI = true, situationDirty = true, lastFrame = null, lastUI = 0, lastList = 0, lastSituation = 0, lastClock = 0, lastWallMin = NaN, readoutFor = undefined, camMoving = false;
let tab = 'situation', frameKey = '', countText = '', pendingSel = null, pendingFocus = null, restore = new Set();
const hidden = { left: false, right: false }, panels = { drawer: false, sheet: null, full: false };   // desktop columns; mid drawer; phone sheet
const lineMaterials = [], focusKm = new Float64Array(3), occupancy = new Occupancy();
const tween = { start: 0, dur: 0, fromId: null, from: new Float64Array(3), active: false };
let lagVis = LAGRANGE.map(() => true);

async function loadData() {
  const chunks = catalog.chunks || [...new Set(catalog.targets.map(t => t.chunk).filter(Boolean))];
  await Promise.all(chunks.map(c => inject(`${base}/${c}.js`)));
  for (const c of chunks) {
    const p = registered(c);
    if (!p) { missing.push(c); continue; }
    for (const [id, payload] of Object.entries(p.targets)) {
      if (W.attach(id, payload) && payload.tracklets?.length) catalogTracklets.push([id, { catalog: { id }, tracklets: payload.tracklets }]);
    }
  }
}

// ---------- requested NEOCP objects (spec A4) ----------
// The helper's `object` becomes target 'req-<desig>' (exact case: ZTF10Gu and ZTF10GU are different objects). The track is
// validated before World changes; re-requesting updates the same target; removal is soft, because target indices are
// baked into trail texels, marker slots and the trail picker. intent ({select, auto} from NeocpCore): a user request
// (select) moves the clock into the new track's coverage (the lookup then selects it); a background refit (auto, or ↻
// with select false) keeps the visibility, selection, clock and playback the user has; a restored one (URL /
// localStorage) only takes back the URL's selection and focus.
function addRequested(desig, obj, intent = {}) {
  let track = null;
  try { track = decodeTrack(obj?.track); } catch { /* malformed rv: the lookup shows the failure */ }
  if (!track?.segments.length) return null;
  const id = `req-${desig}`, prev = W.byId.get(id), fresh = !prev || prev.removed;
  const T = W.addTarget({ ...obj.catalog, id, name: desig, label: desig, group: 'NEOCP · requested', regime: obj.catalog?.regime || 'NEOCP_GEO', tier: 1, draw: true }, 'request');
  W.attach(id, obj);
  Object.assign(T, { request: { ...obj.request, fit: obj.fit, desig, listing: lookup?.listing?.(desig) ?? null }, removed: false });
  if (fresh) Object.assign(T, { visible: true, userSet: true });           // new, or re-requested after a removal
  requested.set(id, [desig, { ...obj, catalog: { ...obj.catalog, id } }]);
  if (state.selected === id) readoutFor = undefined;                       // its NEOCP REQUEST card changed
  rebuildLayers();
  const restored = restore.delete(desig), user = !restored && (intent.select ?? fresh);
  if (user && !W.stateAt(T, clock.t, new Float64Array(6))) setTime(Math.max(track.t0_ms, Math.min(track.t1_ms, Date.now())));
  if (restored && pendingSel === id) select(id);
  if (restored && pendingFocus === id) { W.evaluate(clock.t); focusOn(id, !url.cam, null, null, 0); }   // its position at this epoch first
  return T;
}

function removeRequested(id) {
  const T = W.byId.get(id) || W.byId.get(`req-${id}`);
  if (T?.source !== 'request' || T.removed) return;
  Object.assign(T, { track: null, seg: null, has: false, visible: false, removed: true });
  requested.delete(T.id);
  if (state.focus === T.id) focusOn('earth', false);
  if (state.selected === T.id) select(null);
  rebuildLayers();
}

// After the target set changed: trails first (it grows the style texture), then marker slots, then every texel.
function rebuildLayers() {
  if (renderReady) { trails.rebuild(state.frame); ensureMarkers(); tracklets.rebuild(state.frame, trackletEntries()); }
  paramsDirty = dirty = forceUI = situationDirty = true; ticks();
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
  pendingFocus = null;                                                      // a focus chosen now replaces a restored one
  if (W.byId.has(id) && !W.byId.get(id).visible) setVisible(id, true);
  tween.from.set(focusKm); tween.fromId = tween.active ? null : state.focus; state.focus = id; state.view = null;   // interrupted: from the eased point
  trails.focus(W.byId.get(id) || null); paramsDirty = true;                 // §9.2 anchor for the focused trail
  viewUI();
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
  state.view = key; viewUI();
  dirty = true; saveURL();
}

// The preset buttons (≥ 1281 px) and the native select (narrower) show the same view; a hand-moved camera has none.
function viewUI() {
  for (const b of $('views').querySelectorAll('button')) { const on = b.dataset.view === state.view; b.classList.toggle('active', on); b.setAttribute('aria-pressed', String(on)); }
  $('view-select').value = state.view || '';
}

// ---------- selection, visibility, frames ----------
// Selecting shows the TARGET tab and deselecting SITUATION; a tab picked by hand holds until the selection changes.
function select(id) {
  const T = id && W.byId.get(id), prev = state.selected;
  state.selected = T && !T.removed ? id : null; pendingSel = null;
  if (state.selected && !T.visible && T.draw) setVisible(id, true);
  trails?.setIdleDim(!state.selected);                                       // V2: the HEO tangle idles dim while nothing is selected
  if (prev !== state.selected) setTab(state.selected ? 'target' : 'situation');
  paramsDirty = dirty = forceUI = true; readoutFor = undefined; list?.refresh(clock.t); saveURL();
}
function setVisible(id, on) {
  const T = W.byId.get(id);
  if (!T) return;
  T.visible = on && T.draw && !T.removed; T.userSet = true;
  if (id === state.selected) readoutFor = undefined;
  paramsDirty = dirty = forceUI = situationDirty = true; list?.refresh(clock.t);
}
function setGroupVisible(group, on) {
  for (const T of W.targets) if (T.group === group && T.draw && T.source !== 'request') T.visible = on;
  readoutFor = undefined; paramsDirty = dirty = forceUI = situationDirty = true; list.refresh(clock.t);
}
// Tier chips reset their tier's visibility; requested NEOCP objects are not in any tier.
function setChip(key, on) {
  chips[key] = on;
  for (const T of W.targets) if (T.source !== 'request' && (key === 't3' ? T.tier >= 3 : T.tier === Number(key[1]))) T.visible = on && defaultOn(T);
  for (const b of $('chips').querySelectorAll('[data-chip]')) b.setAttribute('aria-pressed', String(chips[b.dataset.chip]));
  readoutFor = undefined; paramsDirty = dirty = forceUI = situationDirty = true; list.render(); saveURL();
}
// [ ] and ‹ ›: the next visible target in list order.
function step(dir) {
  const ids = [...list.rows.keys()].filter(id => W.byId.get(id)?.visible), i = ids.indexOf(state.selected);
  if (ids.length) select(ids[i < 0 ? (dir > 0 ? 0 : ids.length - 1) : (i + dir + ids.length) % ids.length]);
}

function setFrame(key) {
  if (!FRAMES[key] || key === state.frame) return;
  const oldS = Float64Array.from(W.S), oldR = Float64Array.from(W.R);
  state.frame = W.frame = key; W.evaluate(clock.t);
  // Keep the view continuous: rotate the camera offset and a running camera tween's end points by S_new · S_oldᵀ (scene),
  // and the focus tween's start point by R_new · R_oldᵀ (frame km).
  const rot = (A, B, v) => { const o = [B[0] * v.x + B[3] * v.y + B[6] * v.z, B[1] * v.x + B[4] * v.y + B[7] * v.z, B[2] * v.x + B[5] * v.y + B[8] * v.z];
    v.x = A[0] * o[0] + A[1] * o[1] + A[2] * o[2]; v.y = A[3] * o[0] + A[4] * o[1] + A[5] * o[2]; v.z = A[6] * o[0] + A[7] * o[1] + A[8] * o[2]; };
  rot(W.S, oldS, camera.position);
  if (camTween.active) { rot(W.S, oldS, camTween.from); rot(W.S, oldS, camTween.to); }
  if (tween.active) { const f = { x: tween.from[0], y: tween.from[1], z: tween.from[2] }; rot(W.R, oldR, f); tween.from.set([f.x, f.y, f.z]); }
  trails.rebuild(key); tracklets.rebuild(key, trackletEntries());
  frameUI(); paramsDirty = dirty = true; saveURL();
}

// Frame buttons and the stage head's view title ("Sun–Earth rotating · focus Earth", short frame name ≤ 900 px).
function frameUI() {
  for (const b of $('frames').querySelectorAll('button')) b.setAttribute('aria-pressed', String(b.dataset.frame === state.frame));
  const F = FRAMES[state.frame], focus = state.focus === 'earth' ? 'Earth' : state.focus === 'moon' ? 'Moon' : W.byId.get(state.focus)?.label || state.focus;
  if (frameKey === `${state.frame}|${focus}`) return;
  frameKey = `${state.frame}|${focus}`;
  $('view-title').innerHTML = `<span class="lng">${F.label}</span><span class="sht">${SHORT_FRAME[state.frame]}</span> · focus ${escapeHtml(focus)}`;
  $('view-title').title = `${F.label} · focus ${focus}`;
  $('view-description').textContent = $('view-description').title = F.note;
}

// The scale bar's corner is the only chrome on the render (title, legend, credit and banner sit outside it); labels and
// chevrons keep clear of it. Measured once per layout change.
let keepOut = null, barPx = 0;
function keepOutBoxes() {
  const r = $('scene').getBoundingClientRect(), b = $('foot-right').getBoundingClientRect();
  return [{ x: b.left - r.left, y: b.top - r.top, width: b.width, height: b.height }];
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
  if (markers) { markers.points.removeFromParent(); markers.points.geometry.dispose(); markers.material.dispose(); }
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
  bodies.update(clock.t, focusKm);
  trails.update(clock.t, focusKm, T => (T ? isVisible(T) && layers.trails : layers.trails && layers.moonTrail), res);
  for (const m of lineMaterials) m.resolution.copy(res);
  let n = 0;
  for (const T of W.targets) {
    const on = isVisible(T) && T.has;
    T.scene = on ? sv(T.pos, T.scene || new THREE.Vector3()) : null;
    if (!on) { markers.set(n++, [0, 0, 0], T.color, 0, 0, false, 0); continue; }
    // V4 (markers.targetStyle): hollow = stale or extrapolated; derelict and GP dimmer. Staleness is §6.4's isStale.
    markers.setTarget(n++, T.scene.toArray(), T, { selected: state.selected === T.id, hovered: state.hovered === T.id, stale: readout.isStale(T, clock.t) });
  }
  const earthPx = pixelRadius(bodies.earth.position, 1), moonPx = pixelRadius(bodies.moon.position, RADIUS.moon);
  // Earth under 8 px (5.2 px in the opening view, 2.4 px on a phone, smaller than any marker): a ring 3 px outside the limb;
  // under 2 px the filled 7 px dot.
  markers.set(n++, bodies.earth.position.toArray(), EARTH_C, earthPx < 2 ? 7 : 2 * earthPx + 6, SHAPE.circle, earthPx >= 2, earthPx < 8 ? .8 : 0);
  markers.set(n++, bodies.moon.position.toArray(), MOON_C, 6, SHAPE.circle, false, moonPx < 2 ? 1 : 0);
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
  if (Math.abs(bar.px - barPx) > 40) { barPx = bar.px; keepOut = null; }        // the scale bar changed width
  for (const b of keepOut ||= keepOutBoxes()) occupancy.add(b, true);
  occupancy.add(scaleBar.box($('scene')), true);
  const cands = [];
  // r: the body's projected radius (px), so its label clears the disc (labels.js)
  const add = (key, text, p, priority, cls, always = false, free = false, r = 0) => {
    const s = project(p, w, h);
    if (!s || occluded(p, occluders.filter(o => o.position !== p))) return null;
    if (layers.labels || always) cands.push({ key, text, x: s.x, y: s.y, priority: priority - s.z, cls, always, free, r });
    return s;
  };
  if (state.earthPx < 150) add('earth', 'Earth', bodies.earth.position, 950, 'body', true, false, state.earthPx);
  add('moon', 'Moon', bodies.moon.position, 940, 'body', true, false, state.moonPx);
  for (const T of W.targets) {
    T.screen = null;
    if (!T.scene) continue;
    const s = project(T.scene, w, h);
    if (!s || occluded(T.scene, occluders)) continue;
    T.screen = s;
    const sel = state.selected === T.id, focus = state.focus === T.id, hov = state.hovered === T.id, req = T.source === 'request';
    if (!layers.labels && !sel && !focus) continue;
    // requested NEOCP objects carry their range: "P22rAEs 1.21 LD"
    cands.push({ key: T.id, text: req ? `${T.label} ${rangeText(Math.hypot(T.state[0], T.state[1], T.state[2]))}` : T.label, x: s.x, y: s.y, cls: sel ? 'sel' : T.tier > 1 ? 'dim' : '', always: sel || focus,
      priority: (focus ? 1000 : sel ? 900 : hov ? 800 : 0) + (T.tier === 1 ? 300 : 150) + (T.shape === 0 ? 30 : 0) + (req ? 20 : 0) - s.z });
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
// SE-L1/L2 and requested NEOCP objects (flybys often sit outside the Earth–Moon framing), with the range from the focus,
// clear of the scale bar and of `avoid` (the Sun glyph, on-screen markers, glyphs and bodies). Returns their boxes.
function edgeMarkers(w, h, avoid) {
  const S = state.selected && W.byId.get(state.selected), items = [];
  if (S?.scene) items.push({ id: S.id, label: S.label, color: S.css, p: S.scene });
  items.push({ id: 'earth', label: 'Earth', color: 'var(--ink)', p: bodies.earth.position }, { id: 'moon', label: 'Moon', color: 'var(--moon)', p: bodies.moon.position });
  for (const i of [0, 1]) if (lagrange.shown[i]) {                          // "SE-L1 · 8 · 1.49 Mkm": 8 shown targets of that regime off screen
    const key = LAGRANGE[i].key, n = W.targets.filter(T => T.scene && !T.screen && T.regime.key === (i ? 'SE_L2' : 'SE_L1')).length;
    items.push({ id: key, label: n ? `${key} · ${n}` : key, title: n ? `${n} ${key} target${n > 1 ? 's' : ''} off screen; click to fly there` : '', color: 'var(--lagrange)', p: lagrange.scene[i] });
  }
  for (const T of W.targets) if (T.source === 'request' && T.scene && T !== S) items.push({ id: T.id, label: T.label, color: T.css, p: T.scene });
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
    el.innerHTML = readout.targetTip(T, clock.t);
  }
  el.hidden = false;
  const w = $('scene').clientWidth;
  el.style.left = `${Math.min(x + 14, w - 290)}px`; el.style.top = `${y + 14}px`;
}

// ---------- UI ----------
// UI refreshes run after the next draw, so the readouts and Situation see the new epoch.
function setTime(t) { clock.set(t); clock.playing = false; dirty = forceUI = true; saveURL(); }
function ui(force = false) {
  banner(); clockUI(); playUI();
  const t = clock.t;
  $('timeline').value = String((t - W.t0) / 1000); $('timeline').setAttribute('aria-valuetext', `${utc(t)} UTC`);
  $('track').style.setProperty('--p', ((t - W.t0) / (W.t1 - W.t0)).toFixed(4));
  inspectorUI(force);
  if (force || performance.now() - lastList > 500) { list.refresh(t); lookup.refresh(); countsUI(); lastList = performance.now(); }
  peekUI(); frameUI();
}

// Δ between the epoch and the wall clock (or the build epoch): "0 s", "−45 m", "−23 h 04 m", "+2 d 04 h".
function deltaText(ms) {
  const s = Math.round(Math.abs(ms) / 1000), sign = ms < 0 ? '−' : '+', p = n => String(n).padStart(2, '0');
  if (s < 60) return `${s ? sign : ''}${s} s`;
  if (s < 3600) return `${sign}${Math.floor(s / 60)} m`;
  if (s < 2 * 86400) return `${sign}${Math.floor(s / 3600)} h ${p(Math.floor(s % 3600 / 60))} m`;
  return `${sign}${Math.floor(s / 86400)} d ${p(Math.floor(s % 86400 / 3600))} h`;
}

// The one epoch clock (title bar), the LIVE / PAUSED / rate pill (also the LIVE toggle), Δ now, ● Now and the rate.
function clockUI() {
  const t = clock.t, u = utc(t), wall = Date.now(), inWindow = clock.liveAvailable, pill = $('live');
  text($('utc-date'), u.slice(0, 10)); text($('utc-time'), u.slice(11, 19)); $('utc-clock').dateTime = iso(t);
  pill.className = `pill${clock.live ? ' live' : clock.playing ? ' play' : ''}`;
  text($('live-badge'), clock.live ? 'LIVE' : clock.playing ? `${clock.dir < 0 ? '◀◀' : '▶'} ${RATE[clock.rate] || ''}` : '❚❚ PAUSED');
  pill.setAttribute('aria-pressed', String(clock.live)); pill.setAttribute('aria-disabled', String(!inWindow));
  pill.title = !inWindow ? 'Wall clock outside the data window' : clock.live ? 'Following the wall clock (L)' : 'Follow the wall clock (L)';
  const delta = `Δ ${inWindow ? 'now' : 'build'} <b>${clock.live ? '0 s' : deltaText(t - (inWindow ? wall : W.now))}</b>`;
  if ($('elapsed').innerHTML !== delta) { $('elapsed').innerHTML = delta; $('elapsed').title = `Epoch minus the ${inWindow ? 'wall clock' : 'build epoch (the wall clock is outside the data window)'}`; }
  $('now').setAttribute('aria-pressed', String(clock.live));
  if ($('speed').disabled !== clock.live) { $('speed').disabled = clock.live; $('speed').value = clock.live ? 'live' : String(clock.rate); }
  const minute = inWindow ? Math.floor(wall / 60000) : -1;                  // the timeline's wall-clock line moves once a minute
  if (minute !== lastWallMin) { lastWallMin = minute; marksUI(); }
}

// LIVE runs too: the button reads Pause, and a click (or Space) pauses at the current epoch.
function playUI() {
  const p = clock.playing || clock.live, b = $('play');
  text(b.firstElementChild, p ? '❚❚' : '▶'); text(b.lastElementChild, p ? ' Pause' : ' Play'); b.setAttribute('aria-label', p ? 'Pause' : 'Play');
  $('reverse').setAttribute('aria-pressed', String(clock.dir < 0));
}

function countsUI() {
  const c = list.counts?.();
  if (!c) return;
  const html = `<b>${c.shown}</b> shown · <b>${c.drawable}</b> drawable · <b>${c.listed}</b> listed`;
  if (html !== countText) {
    countText = html; $('target-count').innerHTML = html; text($('ptab-count'), String(c.shown));
    $('target-count').title = `shown: drawn at this epoch · drawable: has an ephemeris in this build (Situation's "live" counts those with data at this epoch) · listed: every catalog target`;
  }
}

// TARGET renders on a selection change and updates in place; SITUATION renders when the target set or visibility
// changed and refreshes its numbers at most once a second while shown (review: no per-second rebuild).
const situationApp = { isVisible, requested: requestedTargets, mpcNote: () => lookup?.mpcNote?.() || '' };
function inspectorUI(force) {
  const T = state.selected && W.byId.get(state.selected), t = clock.t, now = performance.now();
  if (readoutFor !== (T ? T.id : null)) {
    // A re-render (◉, ↻ Refetch, a refit) keeps keyboard focus on the same action button, or on the tab.
    const ro = $('readout'), a = document.activeElement, act = ro.contains(a) ? a.dataset?.act || '' : null;
    readoutFor = T ? T.id : null;
    if (T) readout.renderTarget(ro, T, W, tracklets); else ro.replaceChildren();
    if (act !== null) { const b = act && ro.querySelector(`[data-act="${act}"]`); (b && !b.disabled && !ro.hidden ? b : $(TABS[tab][0])).focus(); }
    tabsUI(); ticks();
  }
  if (T) readout.updateTarget(T, W, t);
  if (situationDirty) { readout.renderSituation($('situation'), W, situationApp, t); situationDirty = false; lastSituation = now; }
  else if (tab === 'situation' && (force || now - lastSituation > 1000)) { readout.updateSituation?.($('situation'), W, situationApp, t); lastSituation = now; }
}

const TABS = { target: ['itab-target', 'readout'], situation: ['itab-situation', 'situation'], about: ['itab-about', 'about-panel'] };
function setTab(name, focus = false) {
  if (name === 'target' && !state.selected) name = 'situation';
  if (name === 'situation' && tab !== name) lastSituation = 0;
  tab = name;
  const a = document.activeElement;
  let lost = false;                                                         // focus inside a panel that is hidden now
  for (const [key, [tabId, panelId]] of Object.entries(TABS)) {
    const on = key === name;
    if (!on && !$(panelId).hidden && $(panelId).contains(a)) lost = true;
    $(tabId).setAttribute('aria-selected', String(on)); $(tabId).tabIndex = on ? 0 : -1; $(panelId).hidden = !on;
  }
  if (focus || lost) $(TABS[name][0]).focus();
  tabsUI(); forceUI = true;
}
function tabsUI() {
  const on = !!state.selected;
  $('itab-target').disabled = !on; $('itab-target').lastElementChild.hidden = on;          // "TARGET none" while nothing is selected
  $('tab-nav').hidden = !on || tab !== 'target';
}

// Phone: the selection's peek row above the transport, and a family dot on the Inspector tab.
function peekUI() {
  const T = state.selected && W.byId.get(state.selected), el = $('peek'), ptab = $('ptab-inspector');
  el.hidden = !T; ptab.classList.toggle('has-sel', !!T);
  if (!T) return;
  const g = el.firstElementChild, cls = glyphClass(T, readout.isStale(T, clock.t) || T.seg?.quality === 'extrap');
  if (g.className !== cls) g.className = cls;
  g.style.setProperty('--c', T.css); ptab.style.setProperty('--c', T.css);
  text(el.querySelector('.nm'), T.cat.name || T.label);
  text(el.querySelector('.rg'), T.has ? rangeText(Math.hypot(T.state[0], T.state[1], T.state[2])) : '—');
}

// Legend entries (ui/legend.js) follow their layer: tracklets, sight lines, GEO · Moon distance rings.
function legendUI() { legendRows($('legend'), layers); }
function legendOpen(open) {
  $('legend').classList.toggle('open', open); $('legend-toggle').setAttribute('aria-expanded', String(open));
  $('legend-toggle').textContent = open ? 'Legend ▾' : 'Legend ▴';
}
function layersUI() {
  const keys = Object.keys(LAYERS), on = keys.filter(k => layers[k]).length;
  $('layer-count').textContent = `${on}/${keys.length}`; $('layer-on').textContent = `${on} of ${keys.length} on`;
}

// '?' help (C9): keys and glyphs, modal over the scene; Esc, × or a click on the scrim closes it and focus returns.
const FAMILY = { LUNAR_ORBIT: 'lunar', EM_L1: 'Earth–Moon / translunar', CISLUNAR_HEO: 'cislunar HEO', SE_L1: 'SE-L1', SE_L2: 'SE-L2',
  EARTH_NEAR_HELIO: 'heliocentric', NEOCP_GEO: 'NEOCP', OTHER: 'other' };
let helpOpener = null;
function helpUI(open = $('help').hidden) {
  if (open && $('help').hidden) {                  // colour = regime family, from catalog.js (src/cle/catalog.py FAMILY)
    const fam = new Map();
    for (const r of [...W.regimes.values()].sort((a, b) => a.order - b.order)) if (!fam.has(r.color)) fam.set(r.color, FAMILY[r.key] || r.label);
    $('help-colours').innerHTML = 'colour = regime family: ' + [...fam].map(([c, name]) => `<span class="nw"><i class="swatch" style="--c:${c}"></i>${escapeHtml(name)}</span>`).join(' · ');
    helpOpener = document.activeElement;
  }
  $('help').hidden = !open; $('help-toggle').setAttribute('aria-pressed', String(open));
  if (open) $('help-close').focus();
  else if (helpOpener?.isConnected) { helpOpener.focus(); helpOpener = null; }
}

// Timeline marks (ui/timeline.js) under the slider: the wall-clock line, the selected target's coverage band and
// tracklet epochs, and the observation bars of every requested object.
function marksUI() {
  if (!marks || !lookup) return;
  const T = state.selected && W.byId.get(state.selected);
  const coverage = (T?.track?.segments || []).map(s => ({ t0: Math.max(W.t0, s.t0_ms), t1: Math.min(W.t1, s.t1_ms), kind: s.quality === 'extrap' ? 'extrap' : 'data', color: T.css })).filter(c => c.t1 > c.t0);
  const obs = requestedTargets().flatMap(R => lookup.obsTimes(R.id).map(t => ({ t, selected: R.id === state.selected })));
  marks.update({ wall: clock.liveAvailable ? Date.now() : null, coverage, obs, trackletTimes: T ? trackletsOf(T.id).map(tk => tk.t_ms[0]) : [] });
}
const obsOf = T => T.source === 'request' ? lookup?.obsTimes(T.id) || [] : trackletsOf(T.id).map(tk => tk.t_ms[0]);

// The dock's right-hand key: the window, or what the timeline shows for the selection.
function timeInfoUI() {
  const T = state.selected && W.byId.get(state.selected), segs = T?.track?.segments || [];
  let html = `±${Math.round(W.window_s / DAY / 2)} d window · build ${utc(W.now).slice(5, 16)}`;
  if (segs.length) {
    const fit = segs.some(s => s.quality === 'fit' || s.quality === 'extrap');
    html = `<i class="ti" style="--c:${T.css}"></i><b>${escapeHtml(T.label)}</b> ${fit ? 'arc' : 'data'}`
      + (segs.some(s => s.quality === 'extrap') ? '<i class="ti ex"></i>extrap' : '')
      + (obsOf(T).length ? `<i class="ti obs" style="--c:var(${T.source === 'request' ? '--neocp' : '--ink'})"></i>obs` : '');
  }
  $('time-info').innerHTML = html;
}

// Slider snap points (§10): the build epoch, the selected target's segment joins and observation epochs (the
// selection's, or every requested object's); then the marks and the dock key.
function ticks() {
  const T = state.selected && W.byId.get(state.selected), v = new Set([W.now]);
  for (const s of T?.track?.segments.slice(1) || []) v.add(s.t0_ms);
  const own = T ? obsOf(T) : [];
  for (const t of own.length ? own : requestedTargets().flatMap(obsOf)) v.add(t);
  $('ticks').innerHTML = [...v].filter(t => t >= W.t0 && t <= W.t1).sort((a, b) => a - b).map(t => `<option value="${Math.round((t - W.t0) / 1000)}"></option>`).join('');
  marksUI(); timeInfoUI();
}

function banner() {
  const msgs = [];
  if (W.t1 - Date.now() < DAY * 1000 && W.t1 > Date.now() - 30 * DAY * 1000) msgs.push(`Data window ends ${utc(W.t1).slice(0, 16)} UTC — run make daily`);
  if (Date.now() > W.t1 || Date.now() < W.t0) msgs.push(`Wall clock outside the data window; showing build epoch ${utc(W.now).slice(0, 16)} UTC`);
  if (missing.length) msgs.push(`Missing data chunks: ${missing.join(', ')}`);
  const msg = msgs.join(' · ');
  if ($('banner').hidden !== !msgs.length || $('banner').textContent !== msg) { $('banner').hidden = !msgs.length; $('banner').textContent = $('banner').title = msg; }
}

// A preset view travels as `view` (the camera is implied); a hand-moved camera as `cam`; requested designations as
// `neocp` (a selection of one still being re-requested is kept).
function saveURL() {
  const c = camera?.position;
  writeState({ t: clock.live ? null : iso(clock.t), frame: state.frame, focus: state.focus === 'earth' ? pendingFocus : state.focus, sel: state.selected ?? pendingSel,
    layers: Object.keys(LAYERS).filter(k => layers[k]), tiers: Object.keys(chips).filter(k => chips[k]), trail: state.trail === 'auto' ? null : state.trail,
    view: state.view, cam: c && !state.view ? [c.x, c.y, c.z].map(x => +x.toPrecision(5)) : null, neocp: lookup?.designations.length ? lookup.designations : null,
    helper: url.helper });                                                 // ?helper= (spec A3) survives the rewrite
}

// ---------- panels: desktop columns, the mid drawer, phone sheets ----------
// What a phone sheet covers is inert (out of the tab order, so focus never lands under it): the whole stage under the
// Targets / NEOCP sheet and the full-height Inspector, the time dock under the half-height Inspector. The mid drawer
// closes instead when focus leaves it for something it covers (wireUI).
function layout() {
  const ph = phone.matches, mid = narrow.matches && !ph, left = mid ? panels.drawer : ph && (panels.sheet === 'targets' || panels.sheet === 'neocp');
  const insp = ph && panels.sheet === 'inspector';
  document.body.classList.toggle('no-left', !narrow.matches && hidden.left);
  document.body.classList.toggle('no-right', !ph && hidden.right);
  $('controls-panel').classList.toggle('open', left);
  $('state-panel').classList.toggle('open', insp); $('state-panel').classList.toggle('full', ph && panels.full);
  document.querySelector('.stage').inert = ph && (left || insp && panels.full);
  $('time-dock').inert = insp && !panels.full;
  $('expand-info').setAttribute('aria-pressed', String(panels.full));
  $('toggle-controls').setAttribute('aria-pressed', String(narrow.matches ? left : !hidden.left));
  $('toggle-info').setAttribute('aria-pressed', String(!hidden.right));
  for (const key of ['targets', 'neocp', 'inspector']) $(`ptab-${key}`).setAttribute('aria-expanded', String(panels.sheet === key));
  if (labels) labels.cap = ph ? 16 : 40;                                                // V13
  dirty = true; keepOut = null;
}
// Closing returns focus that was inside the closed panel to what opens it: the phone tab of the sheet, or ◧.
function closePanels() {
  const a = document.activeElement, inLeft = $('controls-panel').contains(a), inRight = $('state-panel').contains(a), { sheet, drawer } = panels;
  panels.drawer = false; panels.sheet = null; layout();
  if (phone.matches && sheet && (inLeft || inRight)) $(`ptab-${sheet}`).focus();
  else if (drawer && inLeft) $('toggle-controls').focus();
}
const sheetDone = () => { if (phone.matches && panels.sheet) closePanels(); };          // a pick from a phone sheet shows the scene
function openSheet(name) {
  const a = document.activeElement, from = panels.sheet && ($('controls-panel').contains(a) || $('state-panel').contains(a));
  panels.sheet = panels.sheet === name ? null : name; layout();
  if (from && !panels.sheet) $(`ptab-${name}`).focus();
}
// Show the Targets column / drawer / sheet (keys / and N, Situation's regime rows).
function revealLeft(sheet = 'targets') {
  if (phone.matches) panels.sheet = sheet; else if (narrow.matches) panels.drawer = true; else hidden.left = false;
  layout();
}
// The NEOCP dock (Situation's "NEOCP requested" row): its first requested row, else the designation field.
function showLookup() {
  revealLeft('neocp');
  const row = $('neocp-requested')?.querySelector('.rq');
  if (row) { row.scrollIntoView({ block: 'nearest' }); row.focus(); } else lookup.focus();
}

function wireUI() {
  $('timeline').max = String(W.window_s);
  const end = t => `${utc(t).slice(5, 10)}<span class="hm"> ${utc(t).slice(11, 16)}</span>`;
  $('window-start').innerHTML = end(W.t0); $('window-stop').innerHTML = end(W.t1);
  $('speed').value = String(clock.rate);
  $('speed').addEventListener('change', () => { if ($('speed').value !== 'live') clock.rate = Number($('speed').value); forceUI = true; });
  $('play').addEventListener('click', () => {
    if (clock.live) { clock.live = false; clock.playing = false; }              // LIVE reads Pause: pause where it is
    else {
      if (!clock.playing && (clock.t >= W.t1 && clock.dir > 0 || clock.t <= W.t0 && clock.dir < 0)) clock.t = clock.dir > 0 ? W.t0 : W.t1;
      clock.playing = !clock.playing;
    }
    dirty = forceUI = true; saveURL();
  });
  $('reverse').addEventListener('click', () => { clock.dir *= -1; dirty = forceUI = true; });
  $('live').addEventListener('click', () => { if (!clock.liveAvailable) return; clock.setLive(!clock.live); dirty = forceUI = true; saveURL(); });
  $('timeline').addEventListener('input', () => setTime(W.t0 + Number($('timeline').value) * 1000));
  for (const [id, dt] of [['minus-day', -DAY], ['minus-hour', -3600], ['plus-hour', 3600], ['plus-day', DAY]]) $(id).addEventListener('click', () => setTime(clock.t + dt * 1000));
  $('now').addEventListener('click', () => { if (clock.liveAvailable) { clock.setLive(true); dirty = forceUI = true; saveURL(); } else setTime(W.now); });
  // Tier chips with their catalog counts; Tier 3 only when the catalog has tier-3 targets.
  for (const b of $('chips').querySelectorAll('[data-chip]')) {
    const k = b.dataset.chip, n = W.targets.filter(T => T.source === 'catalog' && (k === 't3' ? T.tier >= 3 : T.tier === Number(k[1]))).length;
    b.lastElementChild.textContent = n; b.hidden = k === 't3' && !n;
    b.setAttribute('aria-pressed', String(chips[k]));
    b.addEventListener('click', () => setChip(k, !chips[k]));
  }
  $('frames').addEventListener('click', e => { const f = e.target.closest('button')?.dataset.frame; if (f) setFrame(f); });
  $('views').addEventListener('click', e => { const v = e.target.closest('button')?.dataset.view; if (v) preset(v); });
  $('view-select').addEventListener('change', () => { if ($('view-select').value) preset($('view-select').value); });
  for (const el of document.querySelectorAll('[data-layer]')) {
    el.checked = !!layers[el.dataset.layer];
    el.addEventListener('change', () => { layers[el.dataset.layer] = el.checked; legendUI(); layersUI(); paramsDirty = dirty = true; saveURL(); });
  }
  $('trail-length').value = state.trail;
  $('trail-length').addEventListener('change', () => { state.trail = $('trail-length').value; paramsDirty = dirty = true; saveURL(); });
  $('star-gain').addEventListener('input', () => { layers.starGain = Number($('star-gain').value); $('star-gain-out').value = layers.starGain.toFixed(2); dirty = true; });
  const closeLayers = () => { $('layers-panel').open = false; $('layers-panel').firstElementChild.focus(); };
  $('layers-close').addEventListener('click', closeLayers);
  document.addEventListener('pointerdown', e => { if ($('layers-panel').open && !$('layers-panel').contains(e.target)) $('layers-panel').open = false; });
  $('search').addEventListener('input', () => { list.query = $('search').value.trim().toLowerCase(); list.render(); });
  // Inspector: tabs (←/→ between them), identity actions [data-act], Situation rows [data-id] / [data-regime].
  for (const [key, [tabId]] of Object.entries(TABS)) $(tabId).addEventListener('click', () => setTab(key));
  $('itab-target').parentElement.addEventListener('keydown', e => {
    const order = Object.keys(TABS).filter(k => !$(TABS[k][0]).disabled), i = order.indexOf(tab);
    const to = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: order.length - 1 }[e.key];
    if (to === undefined) return;
    e.preventDefault(); setTab(order[(to + order.length) % order.length], true);
  });
  $('state-panel').addEventListener('click', e => {
    const act = e.target.closest('[data-act]'), regime = e.target.closest('[data-regime]')?.dataset.regime, id = e.target.closest('[data-id]')?.dataset.id;
    if (act) inspectorAction(act.dataset.act, act.dataset.id || state.selected);
    else if (e.target.closest('[data-neocp]')) showLookup();                // "NEOCP requested": the lookup dock, not a list group
    else if (regime) { revealLeft(); list.scrollToRegime(regime); }
    else if (id && W.byId.has(id)) { select(id); sheetDone(); }
  });
  $('state-panel').addEventListener('keydown', e => {                       // Situation rows that are not buttons
    const row = e.target.closest?.('[data-id],[data-regime],[data-neocp]');
    if ((e.key === 'Enter' || e.key === ' ') && row && !row.matches('button,a')) { e.preventDefault(); row.click(); }
  });
  $('legend-toggle').addEventListener('click', () => legendOpen(!$('legend').classList.contains('open')));
  $('help-toggle').addEventListener('click', () => helpUI());
  $('help-close').addEventListener('click', () => helpUI(false));
  $('help').addEventListener('click', e => { if (e.target === $('help')) helpUI(false); });
  $('help').addEventListener('keydown', e => {                              // focus stays in the dialog
    if (e.key !== 'Tab') return;
    const f = [...$('help').querySelectorAll('button,a[href]')], i = f.indexOf(document.activeElement);
    e.preventDefault(); f[(i + (e.shiftKey ? -1 : 1) + f.length) % f.length].focus();
  });
  $('toggle-controls').addEventListener('click', () => { if (narrow.matches) panels.drawer = !panels.drawer; else hidden.left = !hidden.left; layout(); });
  $('toggle-info').addEventListener('click', () => { hidden.right = !hidden.right; layout(); });
  for (const id of ['close-controls', 'close-info']) $(id).addEventListener('click', closePanels);
  $('expand-info').addEventListener('click', () => { panels.full = !panels.full; layout(); });
  $('ptab-targets').addEventListener('click', () => openSheet('targets'));
  $('ptab-neocp').addEventListener('click', () => { openSheet('neocp'); if (panels.sheet) lookup.focus(); });
  $('ptab-inspector').addEventListener('click', () => openSheet('inspector'));
  $('peek').addEventListener('click', e => {
    const act = e.target.closest('[data-peek]')?.dataset.peek;
    if (act === 'info') { panels.sheet = 'inspector'; setTab('target'); layout(); } else if (act === 'close') select(null);
  });
  $('scene').addEventListener('pointerdown', () => { if (panels.drawer && !phone.matches) { panels.drawer = false; layout(); } });
  $('controls-panel').addEventListener('focusout', e => {                  // Tab out of the mid drawer: it closes, so focus is seen
    const to = e.relatedTarget;
    if (!panels.drawer || phone.matches || !to || $('controls-panel').contains(to) || to === $('toggle-controls')) return;
    panels.drawer = false; layout();
  });
  for (const mq of [narrow, phone]) mq.addEventListener('change', () => { panels.drawer = false; panels.sheet = null; panels.full = false; legendOpen(false); layout(); });
  layout();
  addEventListener('keydown', e => {
    if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey) return;
    const el = e.target instanceof Element ? e.target : document.body, k = e.key;
    if (!$('help').hidden && k !== 'Escape' && k !== '?') return;          // the modal help keeps every other key
    if (el.closest('input,select,textarea')) {
      if (k !== 'Escape') return;
      if ($('layers-panel').contains(el)) closeLayers();
      else if ((panels.drawer || panels.sheet) && el.closest('#controls-panel,#state-panel')) $(panels.sheet ? `ptab-${panels.sheet}` : 'toggle-controls').focus();   // to what opened it; a 2nd Esc closes
      else el.blur();
      return;
    }
    if ((e.code === 'Space' || k === 'Enter') && el.closest('button,a[href],summary,[role=button],[role=tab],[role=option],[tabindex]')) return;   // the focused control acts
    if (e.code === 'Space') { e.preventDefault(); $('play').click(); }
    else if (e.code === 'ArrowRight' || e.code === 'ArrowLeft') setTime(clock.t + (e.code === 'ArrowRight' ? 1 : -1) * (e.shiftKey ? DAY : 3600) * 1000);
    else if (k === 'l' || k === 'L') $('live').click();
    else if (k === '1' || k === '2' || k === '3') setFrame(KEY_FRAMES[k - 1]);
    else if ((k === 'f' || k === 'F' || k === 'Enter') && state.selected) focusOn(state.selected);
    else if (k === 'e' || k === 'E') focusOn('earth');
    else if (k === 'm' || k === 'M') focusOn('moon');
    else if (k === 'n' || k === 'N') { e.preventDefault(); revealLeft('neocp'); lookup.focus(); }
    else if (k === '/') { e.preventDefault(); revealLeft(); $('search').focus(); }
    else if (k === '?') { e.preventDefault(); helpUI(); }
    else if (k === '[' || k === ']') step(k === ']' ? 1 : -1);
    else if (k === 'Escape') {                                             // innermost first: help, layers, legend, panels, selection
      if (!$('help').hidden) helpUI(false);
      else if ($('layers-panel').open) closeLayers();
      else if ($('legend').classList.contains('open')) legendOpen(false);
      else if (panels.drawer || panels.sheet) closePanels();
      else select(null);
    }
  });
  document.addEventListener('visibilitychange', () => { if (document.hidden) clock.playing = false; });
}

function inspectorAction(act, id) {
  const T = W.byId.get(id);
  if (act === 'prev' || act === 'next') step(act === 'next' ? 1 : -1);
  else if (act === 'deselect') select(null);
  else if (!T) return;
  else if (act === 'fly') { focusOn(id); sheetDone(); }
  else if (act === 'hide') setVisible(id, !T.visible);
  else if (act === 'refetch' && T.request) lookup.request(T.request.desig);
  else if (act === 'remove' && T.request) lookup.remove(T.request.desig);
}

// NEOCP lookup dock (ui/neocp.js, spec A1–A4). Designations come back from the URL's neocp=, else from localStorage
// cle.neocp (which the lookup mirrors; a JSON array or CSV); the helper is probed at load only when the page is served
// locally or something is to be restored (A3).
function storedDesigs() {
  const s = store?.getItem('cle.neocp') || '';
  try { const v = JSON.parse(s); if (Array.isArray(v)) return v.map(String); } catch { /* CSV */ }
  return s.split(',');
}
// Each MPC neocp.json read: the requested objects' listing rows (TARGET's NEOCP REQUEST card) and Situation's MPC time.
function listingUI() {
  for (const T of requestedTargets()) {
    const row = lookup.listing(T.request.desig);
    if (JSON.stringify(row) === JSON.stringify(T.request.listing)) continue;
    T.request.listing = row;
    if (state.selected === T.id) readoutFor = undefined;
  }
  uiPending = forceUI = true;                                              // a paused, idle page repaints too (no scene redraw)
}
function startLookup() {
  const desigs = (url.neocp ?? storedDesigs()).map(d => d.trim()).filter(d => neocpUI.DESIG_RE.test(d));
  const local = /^https?:$/.test(location.protocol) && /^(127\.0\.0\.1|localhost)$/.test(location.hostname);
  const helper = new URLSearchParams(location.search).has('helper') || typeof window.CLE_HELPER !== 'string' ? neocpUI.helperBase() : window.CLE_HELPER;
  restore = new Set(desigs);
  lookup = new neocpUI.NeocpLookup($('neocp-dock'), {
    base: helper, window: { t0: W.t0, t1: W.t1, now: W.now }, store, probeAtLoad: local || desigs.length > 0,
    onDrawn: addRequested, onRemove: removeRequested, setVisible,
    // A fetch that finishes later (async) closes only the NEOCP sheet it was started from, never one opened since.
    onSelect: (id, o = {}) => { select(id); if (!o.async) sheetDone(); else if (phone.matches && panels.sheet === 'neocp') closePanels(); },
    onFly: id => { focusOn(id); sheetDone(); },
    onChange: () => { situationDirty = uiPending = forceUI = paramsDirty = dirty = true; ticks(); saveURL(); },
    onList: listingUI,
    rangeOf: id => { const T = W.byId.get(id); return T?.has ? Math.hypot(T.state[0], T.state[1], T.state[2]) : NaN; },
    selected: () => state.selected,
  });
  lookup.init(desigs);
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
  controls.addEventListener('start', () => { if (state.view) { state.view = null; viewUI(); } });
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
  tracklets = new TrackletLayer(W, scene); tracklets.rebuild(state.frame, trackletEntries());
  chevrons = new Chevrons($('scene'), id => focusOn(id)); scaleBar = new ScaleBar($('foot-right')); scaleBar.el.id = 'scale-bar';
  W.evaluate(clock.t);
  new ResizeObserver(resize).observe($('scene')); resize();
  // Opening camera (V1): ?cam wins, then ?view (which implies its focus), else the Earth–Moon framing; a ?focus without
  // ?cam or ?view flies there.
  const urlView = !url.cam && VIEWS.includes(url.view);
  if (url.cam) camera.position.copy(new THREE.Vector3(...url.cam)); else preset(urlView ? url.view : 'em', 0);
  camera.lookAt(0, 0, 0);
  // A requested object's focus waits for its request to be redrawn (addRequested), like its selection (pendingSel).
  if (url.focus && !urlView && !focusOn(url.focus, !url.cam) && url.focus.startsWith('req-')) pendingFocus = url.focus;
  trails.setIdleDim(true);                                                 // select() undims
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
  if (clock.tick(dt)) dirty = true;
  if (tween.active) dirty = true;
  if (camTween.active) { camTween.step(now); dirty = true; } else if (camMoving) { camMoving = false; saveURL(); }
  if (controls.update()) dirty = true;
  if (dirty) { dirty = false; uiPending = true; draw(); }
  if (uiPending && (forceUI || now - lastUI > 80)) { ui(forceUI); lastUI = lastClock = now; uiPending = forceUI = false; }
  else if (now - lastClock > 1000) { clockUI(); lastClock = now; }        // Δ now and LIVE availability while nothing moves
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
  list = new listUI.TargetList($('target-list'), W, { setVisible, setGroupVisible, setChip, chipOn, store,
    select: id => { select(id); sheetDone(); }, focus: id => { focusOn(id); sheetDone(); }, get selected() { return state.selected; } });
  renderLegend($('legend')); wireUI();
  try { setupScene(); } catch (error) {
    $('loading').hidden = true; $('render-error').hidden = false; $('render-error-detail').textContent = error.message; console.error('cislunar-eye renderer:', error);
  }
  marks = new timelineUI.TimelineMarks($('timeline-marks'), { t0: W.t0, t1: W.t1, now: W.now });
  pendingSel = url.sel?.startsWith('req-') && !W.byId.has(url.sel) ? url.sel : null;   // taken back when its request is redrawn
  startLookup();
  list.render();
  if (url.sel && !pendingSel) select(url.sel);
  legendUI(); layersUI(); viewUI(); frameUI(); ticks(); ui(true);
  $('loading').hidden = true;
  requestAnimationFrame(frame);
}

window.__cle = { state, layers, chips, clock, world: W, select, setFrame, setChip, setVisible, focusOn, preset, setTime, setTab, isVisible, probe, pick, screenAt, helpUI,
  addRequested, removeRequested, overlays: { lagrangeVisibility },                // test hooks for the camera, overlay and request contracts
  cam: { focusDistance: (id, frame = state.frame) => cam.focusDistance(W.byId.get(id) || id, frame, view()), tweenTo: (dir, dist, ms) => camTween.tweenTo(dir, dist, ms), get tween() { return camTween; } },
  get ready() { return renderReady && !!list; }, get camera() { return camera; }, get controls() { return controls; }, get sky() { return sky; },
  get trails() { return trails; }, get markers() { return markers; }, get bodies() { return bodies; }, get lagrange() { return lagrange; }, get labels() { return labels; },
  get chevrons() { return chevrons; }, get scaleBar() { return scaleBar; }, get scaleRings() { return scaleRings; }, get camTween() { return camTween; },
  get tracklets() { return tracklets; }, get requested() { return requested; }, get lookup() { return lookup; }, get marks() { return marks; }, get tab() { return tab; },
  get panels() { return panels; }, get missing() { return missing; }, get focusKm() { return focusKm; },
  labelsShown: () => [...document.querySelectorAll('.label-layer:not(.sky-labels) .body-label')].filter(e => !e.hidden).map(e => e.textContent),
  edgesShown: () => [...document.querySelectorAll('.edge-marker')].filter(e => !e.hidden).map(e => e.dataset.id),
  screenOf: id => W.byId.get(id)?.screen || null };
main();
