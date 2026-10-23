// Display frames (ARCHITECTURE AM1). All are Earth-centred; R(t) rows are the frame axes in ICRF.
//   se : x̂ = unit(r⊕ − r☉) (anti-sunward, SE-L2 on +x), ẑ = unit((r⊕ − r☉) × (v⊕ − v☉)), ŷ = ẑ × x̂   (default)
//   eci: identity (MMX ICRF frame)
//   em : x̂ = unit(r☾), ẑ = unit(r☾ × v☾), ŷ = ẑ × x̂
// Scene mapping after the rotation is MMX's (x, z, −y) / R⊕, so screen-up is +ẑ.
import { sampleTrack } from './states.js';

export const FRAMES = {
  se: { label: 'Sun–Earth rotating', short: 'SE rotating', note: 'Earth-centred · Sun fixed on −x · ẑ ecliptic normal' },
  eci: { label: 'ECI · ICRF', short: 'ECI', note: 'Earth-centred inertial · ICRF axes · geometric' },
  em: { label: 'Earth–Moon rotating', short: 'EM rotating', note: 'Earth-centred · Moon fixed on +x · ẑ lunar orbit normal' },
};
export const IDENTITY = new Float64Array([1, 0, 0, 0, 1, 0, 0, 0, 1]);   // typed: out.set() 12× faster than from a frozen Array (EFF-10)

// Rows x̂ = sign·unit(r), ẑ = unit(r × v), ŷ = ẑ × x̂ into out[9].
export function rotatingBasis(r, v, sign, out = new Float64Array(9)) {
  const rn = Math.hypot(r[0], r[1], r[2]);
  const hx = r[1] * v[2] - r[2] * v[1], hy = r[2] * v[0] - r[0] * v[2], hz = r[0] * v[1] - r[1] * v[0], hn = Math.hypot(hx, hy, hz);
  const x0 = sign * r[0] / rn, x1 = sign * r[1] / rn, x2 = sign * r[2] / rn, z0 = hx / hn, z1 = hy / hn, z2 = hz / hn;
  out[0] = x0; out[1] = x1; out[2] = x2;
  out[3] = z1 * x2 - z2 * x1; out[4] = z2 * x0 - z0 * x2; out[5] = z0 * x1 - z1 * x0;
  out[6] = z0; out[7] = z1; out[8] = z2;
  return out;
}

// Rz(θ)·R: the frame advanced by θ about its own ẑ (x̂ and ŷ rows mixed, ẑ kept); orthonormal for any θ.
export function rotateZ(R, theta, out = new Float64Array(9)) {
  const c = Math.cos(theta), s = Math.sin(theta), a0 = R[0], a1 = R[1], a2 = R[2], b0 = R[3], b1 = R[4], b2 = R[5];
  out[0] = c * a0 + s * b0; out[1] = c * a1 + s * b1; out[2] = c * a2 + s * b2;
  out[3] = c * b0 - s * a0; out[4] = c * b1 - s * a1; out[5] = c * b2 - s * a2;
  out[6] = R[6]; out[7] = R[7]; out[8] = R[8];
  return out;
}

export const apply = (R, v, out = new Float64Array(3), o = 0, vo = 0) => {
  const x = v[vo], y = v[vo + 1], z = v[vo + 2];
  out[o] = R[0] * x + R[1] * y + R[2] * z; out[o + 1] = R[3] * x + R[4] * y + R[5] * z; out[o + 2] = R[6] * x + R[7] * y + R[8] * z;
  return out;
};

// M·R·Mᵀ with M = (x, z, −y): the frame rotation acting on scene (three.js) vectors, row-major 3×3.
export function sceneRotation(R, out = new Float64Array(9)) {
  const r = (i, j) => R[i * 3 + j];
  out[0] = r(0, 0); out[1] = r(0, 2); out[2] = -r(0, 1);
  out[3] = r(2, 0); out[4] = r(2, 2); out[5] = -r(2, 1);
  out[6] = -r(1, 0); out[7] = -r(1, 2); out[8] = r(1, 1);
  return out;
}

export class Frames {
  constructor(sunTrack, moonTrack) {
    this.sun = sunTrack; this.moon = moonTrack;
    this.sunState = new Float64Array(6); this.moonState = new Float64Array(6);
    this.sv = new Float64Array(6);
    this.near = { key: null, t: NaN, R: new Float64Array(9), omega: 0 };
  }

  // Angular rate |r × v| / r² (rad/s) of the state last sampled by matrix(): the frame's rotation rate about ẑ.
  rate() {
    const s = this.sv, hx = s[1] * s[5] - s[2] * s[4], hy = s[2] * s[3] - s[0] * s[5], hz = s[0] * s[4] - s[1] * s[3];
    return Math.hypot(hx, hy, hz) / (s[0] * s[0] + s[1] * s[1] + s[2] * s[2]);
  }

  // Interpolated geocentric Sun and Moon at t (clamped to the core track).
  context(t) {
    const clamp = (tr, x) => Math.max(tr.t0_ms, Math.min(tr.t1_ms, x));
    sampleTrack(this.sun, clamp(this.sun, t), this.sunState);
    sampleTrack(this.moon, clamp(this.moon, t), this.moonState);
    return this;
  }

  matrix(key, t, out = new Float64Array(9)) {
    if (key === 'eci') { out.set(IDENTITY); return out; }
    const tr = key === 'se' ? this.sun : this.moon, s = this.sv;
    sampleTrack(tr, Math.max(tr.t0_ms, Math.min(tr.t1_ms, t)), s);
    return rotatingBasis(s, s.subarray(3), key === 'se' ? -1 : 1, out);
  }

  // R(t) as the exact R(tRef) advanced about ẑ by ω·(t − tRef), ω the frame body's angular rate at tRef (EFF-10): one
  // exact matrix per native trail sample serves its sub-samples. The ω·δ term keeps AM1 (without it a 1 h EM sub-sample
  // sits ≈ 1,900 km off); the angle is exact, so what remains is the change of ω and ẑ over δ — on the 12:00 build ≤ 0.1 km
  // in SE, ≤ 110 km in EM for the 6-h SE-L targets at 1.5 Mkm (7e-5 of r, < 0.1 px in the Earth–Moon framing).
  matrixNear(key, t, tRef, out = new Float64Array(9)) {
    const n = this.near;
    if (key === 'eci') { out.set(IDENTITY); return out; }
    if (n.key !== key || n.t !== tRef) { this.matrix(key, tRef, n.R); n.key = key; n.t = tRef; n.omega = this.rate(); }
    if (!n.omega || t === tRef) { out.set(n.R); return out; }
    const tr = key === 'se' ? this.sun : this.moon, c = x => Math.max(tr.t0_ms, Math.min(tr.t1_ms, x));     // matrix() clamps too
    return rotateZ(n.R, n.omega * (c(t) - c(tRef)) / 1000, out);
  }
}
