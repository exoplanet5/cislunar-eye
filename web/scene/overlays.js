// Lagrange markers (AM4, hollow diamonds with a centre dot) and NEOCP tracklets (§7: dots coloured by station, one
// hairline per tracklet, optional sight lines from the station). Tracklet vertices are rotated with R(t_obs) per frame
// switch and grouped per target in time order, so hidden targets hide their lines too and only the tracklets inside the
// target's drawn trail window are drawn (one draw range; a tracklet spans minutes); the focused target's group is
// re-emitted relative to an anchor at its position (§9.2), like its trail.
import * as THREE from 'three';
import { PointLayer, SHAPE } from './markers.js';
import { decode } from '../loader.js';
import { sceneRotation } from '../frames.js';
import { RE_KM } from '../states.js';
import { LAGRANGE, lagrangeVisibility } from '../lagrange.js';
import { token, STATION_COLORS } from '../ui/theme.js';
import { FAR } from './trails.js';

export { lagrangeVisibility };
export { ScaleBar, ScaleRings, Chevrons, edgePoint } from './scalecues.js';

const REANCHOR_KM = 1000;          // colours: web/theme.css tokens (--lagrange, --hairline, --sightline) and the bead cycle (V3, V10)

export class LagrangeLayer {
  constructor(world, scene) {
    this.w = world; this.layer = new PointLayer(LAGRANGE.length, { renderOrder: 4 });
    this.color = new THREE.Color(token('--lagrange')); scene.add(this.layer.points);
    this.scene = LAGRANGE.map(() => new THREE.Vector3()); this.frame = LAGRANGE.map(() => new Float64Array(3));
    this.shown = LAGRANGE.map(() => false);
  }

  // mask: per-point visibility from lagrangeVisibility() (V5); shown[i] is what the label, dot-box and pick loops consult.
  update(focusKm, visible, pixelRatio, mask = null) {
    const w = this.w, R = w.R, f = focusKm;
    LAGRANGE.forEach((L, i) => {
      const p = w.lagrange.out[i], q = this.frame[i], on = this.shown[i] = !!visible && (!mask || mask[i]);
      for (let k = 0; k < 3; k++) q[k] = R[k * 3] * p[0] + R[k * 3 + 1] * p[1] + R[k * 3 + 2] * p[2];
      this.scene[i].set((q[0] - f[0]) / RE_KM, (q[2] - f[2]) / RE_KM, -(q[1] - f[1]) / RE_KM);
      this.layer.set(i, this.scene[i].toArray(), this.color, 10, SHAPE.lagrange, true, on ? .85 : 0);
    });
    this.layer.commit(LAGRANGE.length, pixelRatio);
  }
}

export class TrackletLayer {
  constructor(world, scene) {
    this.w = world; this.group = new THREE.Group(); scene.add(this.group);
    this.items = []; this.by = new Map(); this.layers = []; this.anchorId = null;
  }

  rebuild(frame, objects) {
    for (const L of this.layers) this.dispose(L);
    this.items = []; this.by = new Map(); this.anchorId = null; this.frame = frame;
    const w = this.w, R = new Float64Array(9), S = new Float64Array(9), stations = new Map(), per = new Map();
    const sc = (v, o, out) => { const x = v[o] / RE_KM, y = v[o + 2] / RE_KM, z = -v[o + 1] / RE_KM; out.push(S[0] * x + S[1] * y + S[2] * z, S[3] * x + S[4] * y + S[5] * z, S[6] * x + S[7] * y + S[8] * z); };
    for (const [desig, obj] of objects) for (const tk of obj.tracklets || []) {          // stations keep their first-seen colours
      const id = obj.catalog?.id || desig;
      if (!stations.has(tk.stn)) stations.set(tk.stn, new THREE.Color(STATION_COLORS[stations.size % STATION_COLORS.length]));
      if (!per.has(id)) per.set(id, []);
      per.get(id).push(tk); this.items.push({ id, stn: tk.stn, n: tk.n });
    }
    // Per target, tracklets by first epoch; tk[j] holds its epochs and its dot / hairline / sight-line vertex ranges.
    for (const [id, list] of per) {
      const a = { dots: [], colors: [], hair: [], sight: [], tk: [] };
      for (const tk of list.sort((x, y) => Math.min(...x.t_ms) - Math.min(...y.t_ms))) {
        const dot = decode(tk.dot_km, 'f64'), stn = decode(tk.stn_km, 'f64'), pd = [], ps = [];
        const r = { t0: Math.min(...tk.t_ms), t1: Math.max(...tk.t_ms), d0: a.colors.length, h0: a.hair.length / 3, s0: a.sight.length / 3 };
        for (let i = 0; i < tk.n; i++) {
          w.frames.matrix(frame, tk.t_ms[i], R); sceneRotation(R, S);
          sc(dot, i * 3, pd); sc(stn, i * 3, ps);
        }
        for (let i = 0; i < tk.n; i++) {
          a.dots.push(...pd.slice(i * 3, i * 3 + 3)); a.colors.push(stations.get(tk.stn));
          a.sight.push(...ps.slice(i * 3, i * 3 + 3), ...pd.slice(i * 3, i * 3 + 3));
          if (i) a.hair.push(...pd.slice(i * 3 - 3, i * 3), ...pd.slice(i * 3, i * 3 + 3));
        }
        a.tk.push(Object.assign(r, { d1: a.colors.length, h1: a.hair.length / 3, s1: a.sight.length / 3 }));
      }
      this.by.set(id, a);
    }
    this.layers = [...this.by.keys()].map(id => this.build(id));
    this.stations = [...stations.keys()];
  }

  // Tracklets [j0, j1) of a target inside its drawn trail window [t − past, t + future] (windowOf(id) → {past, future (s),
  // emph}), so no bead floats where no trail is drawn; in the EM frame none for SE-L1/L2 and heliocentric targets unless
  // selected, hovered or focused (their trails are capped there, V8). Without t or windowOf: all.
  range(id, t, windowOf) {
    const tk = this.by.get(id).tk, win = t != null && windowOf ? windowOf(id) : null;
    if (!win) return [0, tk.length];
    if (this.frame === 'em' && !win.emph && FAR.has(this.w.byId.get(id)?.cat.regime)) return [0, 0];
    const lo = t - win.past * 1000, hi = t + win.future * 1000;
    let j0 = 0, j1 = tk.length;
    while (j0 < j1 && tk[j0].t1 < lo) j0++;
    while (j1 > j0 && tk[j1 - 1].t0 > hi) j1--;
    return [j0, j1];
  }

  // One target's dots, hairlines and sight lines, as f32 offsets from o (scene R⊕, Earth-relative; f64 subtraction).
  build(id, o = new Float64Array(3)) {
    const a = this.by.get(id), g = new THREE.Group(), n = a.colors.length, sub = arr => arr.map((x, i) => x - o[i % 3]);
    const dots = new PointLayer(Math.max(1, n), { renderOrder: 5 });
    for (let i = 0; i < n; i++) dots.set(i, [a.dots[i * 3] - o[0], a.dots[i * 3 + 1] - o[1], a.dots[i * 3 + 2] - o[2]], a.colors[i], 4, SHAPE.circle, false, 1);
    dots.commit(n, 1);
    const lines = (arr, color, opacity) => {
      const geo = new THREE.BufferGeometry(); geo.setAttribute('position', new THREE.Float32BufferAttribute(sub(arr), 3));
      const l = new THREE.LineSegments(geo, new THREE.LineBasicMaterial({ color, transparent: true, opacity, depthWrite: false }));
      l.frustumCulled = false; return l;
    };
    const L = { id, g, dots, o, hair: lines(a.hair, token('--hairline'), .55), sight: lines(a.sight, token('--sightline'), .22), shown: [0, a.tk.length] };
    g.add(dots.points, L.hair, L.sight); this.group.add(g);
    return L;
  }

  dispose(L) {
    L.g.removeFromParent();
    for (const c of [L.dots.points, L.hair, L.sight]) { c.geometry.dispose(); c.material.dispose(); }
  }

  // Rebuild one target's group relative to p (frame km), or Earth-relative when p is null.
  reanchor(id, p) {
    const i = this.layers.findIndex(L => L.id === id);
    if (i < 0) return;
    this.dispose(this.layers[i]);
    this.layers[i] = this.build(id, p ? Float64Array.of(p[0] / RE_KM, p[2] / RE_KM, -p[1] / RE_KM) : undefined);
  }

  // anchor: the focused target {id, pos (frame km)} or null; t (ms) and windowOf(id): the drawn trail window (range()).
  update(focusKm, layers, isVisible, pixelRatio, anchor = null, t = null, windowOf = null) {
    const f = focusKm, on = layers.tracklets, id = anchor && this.by.has(anchor.id) ? anchor.id : null;
    if (id !== this.anchorId || (id && this.far(anchor.pos))) {
      if (id !== this.anchorId) this.reanchor(this.anchorId, null);
      this.reanchor(id, anchor?.pos);
      this.anchorId = id;
    }
    for (const L of this.layers) {
      const r = on && isVisible(L.id) ? this.range(L.id, t, windowOf) : [0, 0], v = r[1] > r[0];
      if (v && (r[0] !== L.shown[0] || r[1] !== L.shown[1])) {
        const tk = this.by.get(L.id).tk, a = tk[r[0]], b = tk[r[1] - 1];
        L.dots.points.geometry.setDrawRange(a.d0, b.d1 - a.d0); L.hair.geometry.setDrawRange(a.h0, b.h1 - a.h0); L.sight.geometry.setDrawRange(a.s0, b.s1 - a.s0);
      }
      L.shown = r; L.g.visible = v; L.sight.visible = v && layers.sightlines;
      L.g.position.set(L.o[0] - f[0] / RE_KM, L.o[1] - f[2] / RE_KM, L.o[2] + f[1] / RE_KM);
      L.dots.material.uniforms.pixelRatio.value = pixelRatio;
    }
  }

  far(p) {
    const L = this.layers.find(x => x.id === this.anchorId);
    return !!L && Math.hypot(p[0] / RE_KM - L.o[0], p[2] / RE_KM - L.o[1], -p[1] / RE_KM - L.o[2]) * RE_KM > REANCHOR_KM;
  }

  get dotCount() { return this.layers.reduce((n, L) => n + L.dots.count, 0); }
  // Beads drawn now (visible groups, inside their windows).
  get shownCount() { return this.layers.reduce((n, L) => n + (L.g.visible ? this.by.get(L.id).tk.slice(...L.shown).reduce((m, x) => m + x.d1 - x.d0, 0) : 0), 0); }
}
