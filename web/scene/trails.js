// Batched trails (ARCHITECTURE §9.4): one instanced batch per anchor (Earth; Moon for Moon-centred tracks), each
// vertex rotated with R(tᵢ) of its own sample time, rebuilt only when the frame or the data change. Intervals with a
// high angular rate (2 h lunar orbits at 5 min, perigees) are subdivided with the same Hermite the markers use. Every
// instance carries the arc length along its trail, the phase of the screen-space dash (C1).
// Head segments join the bracketing (sub)samples to the interpolated position each frame; the straddling one is culled.
// §9.2: the focused Earth-anchored target's own trail is re-emitted relative to an anchor A at its position (f64
// subtraction before the f32 store) in a group at A − f, so it stays on its marker at 1 km range; its shared copy is hidden.
// Its arcs are re-based at A the same way, so the dash phase keeps its precision at that range.
// Per-target styling (V2, V8): app.js hands over colour, visibility, lengths and selection; restyle() applies the idle,
// hover, derelict and far-trail rules before the next draw and writes the texels.
import * as THREE from 'three';
import { trailMaterial, trailGeometry, markDirty, STYLE, DASH_PX, dashUnit, pxPerUnit } from './trailMaterial.js';
import { sceneRotation } from '../frames.js';
import { hermiteInto, RE_KM } from '../states.js';

const FRAME_RATE = { eci: 0, se: 2.0e-7, em: 2.66e-6 }, MAX_ANGLE = .035, MAX_SUB = 12, REANCHOR_KM = 1000, ZERO = new Float64Array(3), DAY = 86400;
// V2: crowded Earth-orbit regimes idle at 0.25·P and 45 % (of the 95 % peak) when nothing is selected; derelicts at 70 %.
// V8: in the Earth–Moon frame the 30-day SE-L1/L2 and heliocentric trails are capped (≈ 100 px arcs instead of ≈ 880).
export const GAIN = { idle: .45 / .95, derelict: .7 }, IDLE = new Set(['CISLUNAR_HEO', 'HEO']);
export const FAR = new Set(['SE_L1', 'SE_L2', 'EARTH_NEAR_HELIO']), FAR_CAP = { past: .25 * DAY, future: .1 * DAY };

// Subdivision count per sample interval for one frame, prefix offsets, and the arc length (scene units, cumulative along
// the track, f64) at every (sub)vertex — filled by push(); cached on the segment.
function subdivisions(seg, frame) {
  if (seg.sub?.frame === frame) return seg.sub;
  const n = Math.max(0, seg.n - 1), k = new Uint8Array(n), off = new Uint32Array(n + 1), rv = seg.rv, rate = r => {
    const a = r * 6, x = rv[a], y = rv[a + 1], z = rv[a + 2], u = rv[a + 3], v = rv[a + 4], w = rv[a + 5];
    return Math.hypot(y * w - z * v, z * u - x * w, x * v - y * u) / (x * x + y * y + z * z);
  };
  // Path turning over the interval (velocity direction change): near an eccentric apogee it is several times the
  // angular rate about the centre, and it sets the chord sagitta.
  const turn = i => {
    const a = i * 6 + 3, b = a + 6, n = Math.hypot(rv[a], rv[a + 1], rv[a + 2]) * Math.hypot(rv[b], rv[b + 1], rv[b + 2]);
    return n ? Math.acos(Math.max(-1, Math.min(1, (rv[a] * rv[b] + rv[a + 1] * rv[b + 1] + rv[a + 2] * rv[b + 2]) / n))) : 0;
  };
  for (let i = 0; i < n; i++) {
    const swept = Math.max(Math.max(rate(i), rate(i + 1)) * seg.step_s, turn(i)) + FRAME_RATE[frame] * seg.step_s;
    k[i] = Math.min(MAX_SUB, Math.max(1, Math.ceil(swept / MAX_ANGLE)));
    off[i + 1] = off[i] + k[i];
  }
  return seg.sub = { frame, k, off, arcs: new Float64Array(off[n] + 1) };
}

// The (sub)interval of seg that holds time t (ms): interval i, sub-step j of k.
function locate(seg, frame, t) {
  const sub = subdivisions(seg, frame), x = (t - seg.t0_ms) / 1000, i = Math.max(0, Math.min(seg.n - 2, Math.floor(x / seg.step_s))), k = sub.k[i];
  return { sub, i, k, j: Math.max(0, Math.min(k - 1, Math.floor((x - i * seg.step_s) / (seg.step_s / k)))) };
}

const segmentCount = (tracks, frame) => tracks.reduce((n, tr) => n + tr.segments.reduce((m, s) => m + subdivisions(s, frame).off.at(-1) + 1, 0), 0);

export class Trails {
  constructor(world, scene) {
    this.w = world;
    this.earthGroup = new THREE.Group(); this.moonGroup = new THREE.Group(); this.focusGroup = new THREE.Group();
    scene.add(this.earthGroup, this.moonGroup, this.focusGroup);
    this.width = 0; this.texture = null; this.material = null; this.batches = {}; this.heads = {};
    this.R = new Float64Array(9); this.S = new Float64Array(9); this.h = new Float64Array(6);
    this.focusT = null; this.A = new Float64Array(3); this.As = new Float64Array(3); this.s0 = 0; this.focusBatch = null; this.focusHeads = null;
    this.base = []; this.hovered = null; this.idleDim = true; this.farCap = true; this.styleDirty = true;
    this.dashPx = DASH_PX; this.camera = null;
  }

  ensureTexture() {
    const w = this.w.targets.length + 2;
    if (this.texture && this.width >= w) return;
    this.width = Math.max(w + 8, 16);
    this.data = new Float32Array(this.width * 2 * 4);
    this.texture?.dispose();
    this.texture = new THREE.DataTexture(this.data, this.width, 2, THREE.RGBAFormat, THREE.FloatType);
    this.texture.magFilter = this.texture.minFilter = THREE.NearestFilter;
    this.texture.needsUpdate = true;
    if (this.material) this.material.uniforms.uTargets.value = this.texture;
    else this.material = trailMaterial(this.texture);
    this.moonIndex = this.width - 1; this.focusIndex = this.width - 2;
    this.base = []; this.styleDirty = true;                       // texel indices moved; the caller re-runs setParams
  }

  // Base parameters per texel index (the Moon uses the last slot): colour, visibility, past/future lengths (s) and
  // selection. The texels are written by restyle() before the next draw, after the hover / idle / far rules.
  setParams(i, color, visible, past, future, highlight) {
    this.base[i] = { r: color.r, g: color.g, b: color.b, visible: !!visible, past, future, sel: !!highlight };
    this.styleDirty = true;
  }

  // Hover emphasis (V2): the hovered target's trail gets its full length and alpha and the highlight style (width, alpha
  // floor, exempt from the selection dimming).
  setHover(id) {
    id ||= null;
    if (id !== this.hovered) { this.hovered = id; this.styleDirty = true; }
  }

  // Idle dimming of the crowded regimes (V2) and the EM-frame cap of far trails (V8) can be switched off by the UI.
  setIdleDim(on) { if (!!on !== this.idleDim) { this.idleDim = !!on; this.styleDirty = true; } }
  setFarCap(on) { if (!!on !== this.farCap) { this.farCap = !!on; this.styleDirty = true; } }

  // Per-target style from the base parameters: a selected or hovered target keeps what app.js asked for; otherwise HEO
  // trails idle at half their default length (0.25·P, only when app.js passed the default) and GAIN.idle while nothing
  // is selected, derelicts are dimmed, and SE/heliocentric trails are capped in the EM frame. The focused target's own
  // copy is hidden, the focus slot shown.
  restyle() {
    const w = this.w, base = this.base, hov = this.hovered, anySel = w.targets.some(T => base[T.index]?.sel && T.track);
    for (let i = 0; i < base.length; i++) {
      const b = base[i];
      if (!b) continue;
      const T = w.targets[i];
      let { past, future } = b, gain = 1;
      const hl = b.sel || (!!T && T.id === hov);
      if (T && !hl) {
        const r = T.cat.regime;
        if (this.idleDim && !anySel && IDLE.has(r)) {
          if (past === T.trail.past) past *= .5;                  // half the ½·min(P, 14 d) default: 0.25·P, ≤ 3.5 d
          gain *= GAIN.idle;
        }
        if (this.farCap && this.frame === 'em' && FAR.has(r)) { past = Math.min(past, FAR_CAP.past); future = Math.min(future, FAR_CAP.future); }
        if (T.cat.status === 'derelict') gain *= GAIN.derelict;
      }
      const own = this.focusT?.index === i;
      this.texel(i, b, b.visible && !own, past, future, hl, gain);
      if (own) this.texel(this.focusIndex, b, b.visible, past, future, hl, gain);
    }
    this.material.uniforms.uAnySel.value = anySel ? 1 : 0;
    this.styleDirty = false;
  }

  texel(i, c, visible, past, future, highlight, gain) {
    const d = this.data, a = i * 4, b = (this.width + i) * 4;
    d[a] = c.r; d[a + 1] = c.g; d[a + 2] = c.b; d[a + 3] = visible ? 1 : 0;
    d[b] = past; d[b + 1] = future; d[b + 2] = highlight ? 1 : 0; d[b + 3] = gain;
    this.texture.needsUpdate = true;
  }

  // Frame-rotated scene vector (R⊕) of an anchor-relative ICRF position r[ro..ro+2] at time t; sub-samples pass their
  // native sample's epoch as tRef, so one exact R per sample serves them (frames.matrixNear, EFF-10).
  vertex(frame, t, r, ro, out, o, tRef = t) {
    const S = this.S;
    this.w.frames.matrixNear(frame, t, tRef, this.R); sceneRotation(this.R, S);
    const x = r[ro] / RE_KM, y = r[ro + 2] / RE_KM, z = -r[ro + 1] / RE_KM;          // MMX mapping, then the frame rotation
    out[o] = S[0] * x + S[1] * y + S[2] * z; out[o + 1] = S[3] * x + S[4] * y + S[5] * z; out[o + 2] = S[6] * x + S[7] * y + S[8] * z;
  }

  // Vertex j/k of interval i of a segment: the native sample at j = 0, Hermite otherwise.
  subVertex(frame, seg, i, j, k, out, o) {
    if (j === 0) return this.vertex(frame, seg.t0_ms + i * seg.step_s * 1000, seg.rv, i * 6, out, o);
    if (j === k) return this.vertex(frame, seg.t0_ms + (i + 1) * seg.step_s * 1000, seg.rv, i * 6 + 6, out, o);
    hermiteInto(seg.rv, i, j / k, seg.step_s, this.h, 0);
    this.vertex(frame, seg.t0_ms + (i + j / k) * seg.step_s * 1000, this.h, 0, out, o, seg.t0_ms + i * seg.step_s * 1000);
  }

  // Instance writer into a batch: f64 positions minus the scene offset o (R⊕) and arcs minus s0 are stored as f32; s is
  // the arc at pa.
  writer(b, o = ZERO, head = 0, s0 = 0) {
    const { start, end, time, info, arc } = b.buf, t0 = this.w.t0;
    const wr = { n: 0, emit(pa, pb, ta, tb, index, style, s) {
      const n = wr.n;
      for (let k = 0; k < 3; k++) { start[n * 3 + k] = pa[k] - o[k]; end[n * 3 + k] = pb[k] - o[k]; }
      time[n * 2] = (ta - t0) / 1000; time[n * 2 + 1] = (tb - t0) / 1000; info[n * 2] = index; info[n * 2 + 1] = style + head; arc[n] = s - s0; wr.n++;
    } };
    return wr;
  }

  // Every (sub)sample interval of a track; continuous joins whose segments do not share their boundary are bridged.
  // The arc runs over the whole track (bridges included) and is stored per (sub)vertex for the head segments.
  push(wr, track, index, quality) {
    const frame = this.frame, p = new Float64Array(6), len = () => Math.hypot(p[3] - p[0], p[4] - p[1], p[5] - p[2]);
    let prev = null, s = 0;
    for (const seg of track.segments) {
      const style = quality ?? STYLE[seg.quality] ?? 0, { k, off, arcs } = subdivisions(seg, frame), ms = seg.step_s * 1000;
      if (prev && seg.join === 'continuous' && seg.t0_ms > prev.t && seg.t0_ms - prev.t <= 2 * ms) {
        p.set(prev.p); this.vertex(frame, seg.t0_ms, seg.rv, 0, p, 3);
        wr.emit(p, p.subarray(3), prev.t, seg.t0_ms, index, style, s); s += len();
      }
      arcs[0] = s;
      for (let i = 0; i < seg.n - 1; i++) {
        const kk = k[i];
        this.subVertex(frame, seg, i, 0, kk, p, 0);
        for (let j = 1; j <= kk; j++) {
          this.subVertex(frame, seg, i, j, kk, p, 3);
          wr.emit(p, p.subarray(3), seg.t0_ms + i * ms + (j - 1) / kk * ms, seg.t0_ms + i * ms + j / kk * ms, index, style, s);
          s += len(); arcs[off[i] + j] = s;
          p.copyWithin(0, 3, 6);
        }
      }
      prev = { t: seg.t1_ms, p: Float64Array.from(p.subarray(0, 3)) };
    }
  }

  mesh(geometry, group) {
    const m = new THREE.Mesh(geometry, this.material); m.frustumCulled = false; m.renderOrder = 1; group.add(m);
    m.onBeforeRender = (renderer, scene, camera) => { this.camera = camera; };
    return m;
  }

  // C1 check hook: the future-dash cycle on screen (CSS px) along the trail at a target's marker, as last drawn: the
  // shader's dash unit for the (sub)interval that holds the current time times its projected px per scene unit.
  dashCyclePx(id) {
    const T = this.w.targets.find(x => x.id === id), cam = this.camera, res = this.material?.uniforms.resolution.value;
    if (!T?.has || !T.track || !cam || !this.frame || this.t == null) return null;
    const focus = this.focusT === T, g = focus ? this.focusGroup : T.track.center === 'moon' ? this.moonGroup : this.earthGroup;
    const { i, j, k } = locate(T.seg, this.frame, this.t), p = new Float64Array(6);
    this.subVertex(this.frame, T.seg, i, j, k, p, 0); this.subVertex(this.frame, T.seg, i, j + 1, k, p, 3);
    if (focus) for (let n = 0; n < 6; n++) p[n] -= this.As[n % 3];
    g.updateMatrixWorld(); cam.updateMatrixWorld();
    const M = new THREE.Matrix4().multiplyMatrices(cam.matrixWorldInverse, g.matrixWorld);
    const a = new THREE.Vector3(p[0], p[1], p[2]).applyMatrix4(M), b = new THREE.Vector3(p[3], p[4], p[5]).applyMatrix4(M);
    const A = a.clone().applyMatrix4(cam.projectionMatrix), B = b.clone().applyMatrix4(cam.projectionMatrix);
    const kScreen = Math.hypot((B.x - A.x) * res.x / 2, (B.y - A.y) * res.y / 2) / a.distanceTo(b);
    return dashUnit(pxPerUnit(a.toArray(), b.toArray(), res.y, cam.projectionMatrix.elements[5])) * kScreen;
  }

  rebuild(frame) {
    this.ensureTexture();
    this.frame = frame; this.styleDirty = true;
    const w = this.w, lists = { earth: [], moon: [] };
    for (const T of w.targets) if (T.track && T.draw) lists[T.track.center === 'moon' ? 'moon' : 'earth'].push(T);
    const sizes = { earth: segmentCount([...lists.earth.map(T => T.track), w.moonTrack], frame), moon: segmentCount(lists.moon.map(T => T.track), frame) };
    for (const anchor of ['earth', 'moon']) {
      let b = this.batches[anchor];
      const group = anchor === 'moon' ? this.moonGroup : this.earthGroup;
      if (!b || b.capacity < sizes[anchor]) {
        if (b) { b.mesh.removeFromParent(); b.geometry.dispose(); }
        b = this.batches[anchor] = trailGeometry(sizes[anchor] + 64);
        b.mesh = this.mesh(b.geometry, group);
      }
      const wr = this.writer(b);
      for (const T of lists[anchor]) this.push(wr, T.track, T.index);
      if (anchor === 'earth') this.push(wr, w.moonTrack, this.moonIndex, STYLE.ref);
      markDirty(b.geometry, wr.n);
      const heads = (anchor === 'earth' ? lists.earth.length + 1 : lists.moon.length) * 2 + 2;
      if (!this.heads[anchor] || this.heads[anchor].capacity < heads) {
        this.heads[anchor]?.mesh.removeFromParent();
        const h = this.heads[anchor] = trailGeometry(heads + 16);
        h.mesh = this.mesh(h.geometry, group);
      }
      this.heads[anchor].list = lists[anchor];
    }
    this.count = { earth: this.batches.earth.geometry.instanceCount, moon: this.batches.moon.geometry.instanceCount };
    this.anchor();
  }

  // Focus target (Earth-anchored, with a track) or null; the caller re-runs setParams.
  focus(T) {
    this.focusT = T && T.track && T.draw && T.track.center !== 'moon' ? T : null;
    this.styleDirty = true;
    this.anchor();
    return this.focusT;
  }

  // Re-emit the focused target's trail relative to A = its current frame position.
  anchor() {
    const T = this.focusT?.track ? this.focusT : null;              // a NEOCP refresh can drop the focused track
    if (!this.frame) return;
    if (!T) { if (this.focusBatch) markDirty(this.focusBatch.geometry, 0); return; }
    if (T.has) {
      this.A.set(T.pos);
      const { sub, i, j } = locate(T.seg, this.frame, this.t ?? this.w.now);
      this.s0 = sub.arcs[sub.off[i] + j];
    }
    const A = this.A, As = this.As;
    As[0] = A[0] / RE_KM; As[1] = A[2] / RE_KM; As[2] = -A[1] / RE_KM;
    const need = segmentCount([T.track], this.frame);
    if (!this.focusBatch || this.focusBatch.capacity < need) {
      this.focusBatch?.mesh.removeFromParent(); this.focusBatch?.geometry.dispose();
      this.focusBatch = trailGeometry(need + 64); this.focusBatch.mesh = this.mesh(this.focusBatch.geometry, this.focusGroup);
    }
    this.focusHeads ||= trailGeometry(4); this.focusHeads.mesh ||= this.mesh(this.focusHeads.geometry, this.focusGroup);
    const wr = this.writer(this.focusBatch, As, 0, this.s0);
    this.push(wr, T.track, this.focusIndex);
    markDirty(this.focusBatch.geometry, wr.n);
  }

  // Per frame: pending styles, time uniform, anchors relative to the focus (R⊕), head segments for visible targets.
  update(t, focusKm, visible, resolution) {
    if (this.styleDirty) this.restyle();
    this.t = t;
    const w = this.w, m = this.material.uniforms, cur = new Float64Array(3), a = new Float64Array(6), f = focusKm;
    m.uNow.value = (t - w.t0) / 1000; m.resolution.value.copy(resolution);
    const sx = -f[0] / RE_KM, sy = -f[2] / RE_KM, sz = f[1] / RE_KM, mf = w.moonF;
    this.earthGroup.position.set(sx, sy, sz);
    this.moonGroup.position.set(mf[0] / RE_KM + sx, mf[2] / RE_KM + sy, -mf[1] / RE_KM + sz);
    const F = this.focusT, A = this.A;
    if (F?.has && Math.hypot(F.pos[0] - A[0], F.pos[1] - A[1], F.pos[2] - A[2]) > REANCHOR_KM) this.anchor();
    this.focusGroup.position.set((A[0] - f[0]) / RE_KM, (A[2] - f[2]) / RE_KM, -(A[1] - f[1]) / RE_KM);
    const head = (wr, seg, index, rel, style) => {
      const { sub: { off, arcs }, i, j, k } = locate(seg, this.frame, t);
      const ta = seg.t0_ms + i * seg.step_s * 1000 + j / k * seg.step_s * 1000, tb = seg.t0_ms + i * seg.step_s * 1000 + (j + 1) / k * seg.step_s * 1000;
      this.subVertex(this.frame, seg, i, j, k, a, 0); this.subVertex(this.frame, seg, i, j + 1, k, a, 3);
      this.vertex(this.frame, t, rel, 0, cur, 0);
      const s = arcs[off[i] + j], sc = s + Math.hypot(cur[0] - a[0], cur[1] - a[1], cur[2] - a[2]);
      wr.emit(a, cur, ta, t, index, style, s); wr.emit(cur, a.subarray(3), t, tb, index, style, sc);
    };
    for (const anchor of ['earth', 'moon']) {
      const h = this.heads[anchor];
      if (!h) continue;
      const wr = this.writer(h, ZERO, STYLE.HEAD);
      for (const T of h.list) if (T.has && visible(T)) head(wr, T.seg, T.index, T.rel, STYLE[T.seg.quality] ?? 0);
      if (anchor === 'earth' && visible(null)) {
        const seg = w.moonTrack.segments.find(s => t >= s.t0_ms && t <= s.t1_ms);
        if (seg) head(wr, seg, this.moonIndex, w.moon, STYLE.ref);
      }
      markDirty(h.geometry, wr.n);
    }
    if (this.focusHeads) {
      const wr = this.writer(this.focusHeads, this.As, STYLE.HEAD, this.s0);
      if (F?.has && visible(F)) head(wr, F.seg, this.focusIndex, F.rel, STYLE[F.seg.quality] ?? 0);
      markDirty(this.focusHeads.geometry, wr.n);
    }
  }
}
