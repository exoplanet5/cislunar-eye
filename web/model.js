// Targets from catalog.js + targets-*.js + neocp.js, evaluated each frame in the active display frame.
import * as THREE from 'three';
import { decode, decodeTrack, parseSource } from './loader.js';
import { sampleTrack, sampleSegment, RE_KM, GM_EARTH, GM_MOON } from './states.js';
import { Frames, apply, sceneRotation } from './frames.js';
import { makeLagrange, LAGRANGE, MU_EM, MU_SE } from './lagrange.js';

export const DAY = 86400;
export const QUALITY = {
  ref: 'Agency ephemeris (reconstruction)', pred: 'Agency ephemeris (prediction)', fit: 'Orbit fit inside its data arc',
  extrap: 'Extrapolated from the latest orbit', gp: 'Space-Track GP (SDP4 / GP-state integration)', conic: 'Osculating conic (indicative)',
};
// Fallback only: catalog.js regimes (src/cle/catalog.py FAMILY, review V3) override these colours.
const PALETTE = { LUNAR_ORBIT: '#4f81ff', EM_L1: '#d1a9ff', EM_L2: '#d1a9ff', EM_DRO: '#d1a9ff', EM_OTHER: '#d1a9ff', TRANSLUNAR: '#d1a9ff',
  CISLUNAR_HEO: '#4db99a', HEO: '#4db99a', SE_L1: '#e4bb1b', SE_L2: '#e66c04', EARTH_NEAR_HELIO: '#d13ec7', NEOCP_GEO: '#d15a62', OTHER: '#90a4ae' };
const ORDER = Object.keys(PALETTE);

export function shapeOf(cat) {
  const t = (cat.obj_type || '').toLowerCase();
  return t === 'payload' ? 0 : t === 'rocket_body' ? 1 : t === 'natural' ? 3 : 2;
}

// Regime default trail lengths (§9.4; HEO half a period back, as cle.catalog.trail), seconds.
export function defaultTrail(cat, window_s) {
  if (cat.trail?.past_s != null) return { past: cat.trail.past_s, future: cat.trail.future_s ?? cat.trail.past_s / 2 };
  const P = (cat.elements?.period_d || 0) * DAY, r = cat.regime || '';
  if (r === 'LUNAR_ORBIT') { const p = Math.min(DAY, P || DAY); return { past: p, future: .25 * p }; }
  if (r.startsWith('EM_')) return { past: 14 * DAY, future: 7 * DAY };
  if (r === 'TRANSLUNAR') return { past: window_s, future: 3 * DAY };
  if (r === 'CISLUNAR_HEO' || r === 'HEO' || r === 'NEOCP_GEO') return P ? { past: .5 * Math.min(14 * DAY, P), future: Math.min(1.5 * DAY, .25 * P) } : { past: 5 * DAY, future: 1.5 * DAY };
  return { past: 30 * DAY, future: 30 * DAY };
}

export class World {
  constructor(core, catalog) {
    const m = core.meta;
    this.meta = m;
    this.t0 = Date.parse(m.window.start_utc); this.t1 = Date.parse(m.window.stop_utc); this.now = Date.parse(m.now_utc);
    this.window_s = (this.t1 - this.t0) / 1000;
    this.moonTrack = decodeTrack(core.moon.track); this.sunTrack = decodeTrack(core.sun.track);
    this.moonRadius = core.moon.radius_km || 1737.4; this.earthRadius = core.earth.radius_km || RE_KM;
    const q = core.earth.quat;
    this.quat = { t0: q.t0_ms, step: q.step_s * 1000, n: q.n, xyzw: decode(q.xyzw, 'f64') };
    this.frames = new Frames(this.sunTrack, this.moonTrack);
    this.lagrange = makeLagrange(m.mu?.se ?? MU_SE, m.mu?.em ?? MU_EM);
    this.regimes = new Map(ORDER.map((k, i) => [k, { key: k, label: k, order: i, color: PALETTE[k] }]));
    for (const r of catalog.regimes || []) this.regimes.set(r.key, { ...r, order: r.order ?? this.regimes.get(r.key)?.order ?? 99 });
    this.groupOrder = new Map((catalog.groups || []).map((g, i) => [g.key, g.order ?? i]));
    this.targets = []; this.byId = new Map();
    for (const cat of catalog.targets) this.addTarget(cat);
    this.frame = 'se';
    this.R = new Float64Array(9); this.S = new Float64Array(9);
    this.sun = new Float64Array(6); this.moon = new Float64Array(6);
    this.sunF = new Float64Array(3); this.moonF = new Float64Array(3);
    this.t = this.now;
  }

  addTarget(cat, source = 'catalog') {
    let T = this.byId.get(cat.id);
    if (!T) {
      T = { id: cat.id, index: this.targets.length, track: null, state: new Float64Array(6), rel: new Float64Array(6), pos: new Float64Array(3),
        seg: null, has: false, visible: false, userSet: false, events: [] };
      this.targets.push(T); this.byId.set(cat.id, T);
    }
    cat.sources = (cat.sources || []).map(parseSource);      // compact catalog.js strings → {kind, ref, start, end, primary}
    T.cat = cat; T.source = source;
    T.regime = this.regimes.get(cat.regime) || this.regimes.get('OTHER');
    T.color = new THREE.Color(T.regime.color); T.css = T.regime.color;
    T.natural = cat.natural === true || (cat.obj_type || '').toLowerCase() === 'natural';
    T.tier = cat.tier ?? 1; T.draw = cat.draw !== false && cat.draw !== 'N'; T.why = cat.not_drawn_reason || '';
    T.shape = T.natural ? 3 : shapeOf(cat);
    T.trail = defaultTrail(cat, this.window_s);
    T.label = cat.label || cat.name || cat.id;
    T.caveat = cat.caveat || ''; T.buildNote = T.payloadCaveat && T.payloadCaveat !== T.caveat ? T.payloadCaveat : '';
    T.group = cat.group || T.regime.label;
    return T;
  }

  attach(id, payload) {
    const T = this.byId.get(id);
    if (!T) return null;
    T.track = decodeTrack(payload.track); T.events = payload.events || []; T.validation = payload.validation;
    T.payloadCaveat = payload.caveat || ''; T.caveat = T.cat.caveat || ''; T.buildNote = T.payloadCaveat !== T.caveat ? T.payloadCaveat : '';
    T.qualityNote = payload.quality_note || '';
    return T;
  }

  // Interpolate the context and every attached target at t (ms) and transform into the active frame.
  evaluate(t) {
    this.t = t;
    const F = this.frames.context(t);
    this.sun.set(F.sunState); this.moon.set(F.moonState);
    this.frames.matrix(this.frame, t, this.R); sceneRotation(this.R, this.S);
    apply(this.R, this.sun, this.sunF); apply(this.R, this.moon, this.moonF);
    for (const T of this.targets) {
      T.seg = T.track && T.draw ? sampleTrack(T.track, t, T.rel) : null;
      T.has = !!T.seg;
      if (!T.has) continue;
      for (let k = 0; k < 6; k++) T.state[k] = T.rel[k] + (T.track.center === 'moon' ? this.moon[k] : 0);
      apply(this.R, T.state, T.pos);
    }
    this.lagrange.update(this.sun, this.moon);
  }

  // Frame position (km) of a focusable body.
  focusPosition(id, out) {
    if (id === 'earth') { out[0] = out[1] = out[2] = 0; return true; }
    if (id === 'moon') { out.set(this.moonF); return true; }
    const L = LAGRANGE.findIndex(x => x.key === id);
    if (L >= 0) { apply(this.R, this.lagrange.out[L], out); return true; }
    const T = this.byId.get(id);
    if (!T?.has) return false;
    out.set(T.pos); return true;
  }

  // Earth orientation (scene quaternion, xyzw) slerped on the core 1 h track (MMX app.js:333).
  earthQuaternion(t, q, q0, q1) {
    const Q = this.quat, x = Math.max(0, Math.min((Q.n - 1) * Q.step, t - Q.t0)), i = Math.min(Q.n - 2, Math.floor(x / Q.step));
    q0.fromArray(Q.xyzw, i * 4); q1.fromArray(Q.xyzw, i * 4 + 4);
    return q.copy(q0).slerp(q1, (x - i * Q.step) / Q.step);
  }

  // Sample a target track at an arbitrary epoch (geocentric ICRF), for readouts and tracklet checks.
  stateAt(T, t, out) {
    const seg = T.track && sampleTrack(T.track, t, out);
    if (!seg) return null;
    if (T.track.center === 'moon') {
      const m = new Float64Array(6);
      sampleTrack(this.moonTrack, Math.max(this.moonTrack.t0_ms, Math.min(this.moonTrack.t1_ms, t)), m);
      for (let k = 0; k < 6; k++) out[k] += m[k];
    }
    return seg;
  }

  centralMu(T) { return T.cat.regime === 'LUNAR_ORBIT' ? GM_MOON : GM_EARTH; }
}

export { sampleSegment };
