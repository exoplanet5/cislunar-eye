// Derived from MMX web/states.js: Hermite, RA/Dec, terminator, scene mapping, osculating conic,
// generalised to per-target segmented tracks (ARCHITECTURE §5.1, §11) with allocation-free sampling.
export const RE_KM = 6378.137, RM_KM = 1737.4, LD_KM = 384400, GM_EARTH = 398600.435507, GM_MOON = 4902.800118;

export function hermite(a, b, u, dt) {
  const u2 = u * u, u3 = u2 * u;
  const state = new Array(6);
  for (let k = 0; k < 3; k++) {
    state[k] = (2 * u3 - 3 * u2 + 1) * a[k] + (u3 - 2 * u2 + u) * dt * a[k + 3]
      + (-2 * u3 + 3 * u2) * b[k] + (u3 - u2) * dt * b[k + 3];
    state[k + 3] = ((6 * u2 - 6 * u) * a[k] + (3 * u2 - 4 * u + 1) * dt * a[k + 3]
      + (-6 * u2 + 6 * u) * b[k] + (3 * u2 - 2 * u) * dt * b[k + 3]) / dt;
  }
  return state;
}

// Same polynomial on a flat n×6 array: samples i, i+1 → out[o..o+5].
export function hermiteInto(rv, i, u, dt, out, o = 0) {
  const u2 = u * u, u3 = u2 * u, a = i * 6, b = a + 6;
  const h00 = 2 * u3 - 3 * u2 + 1, h10 = (u3 - 2 * u2 + u) * dt, h01 = -2 * u3 + 3 * u2, h11 = (u3 - u2) * dt;
  const d00 = (6 * u2 - 6 * u) / dt, d10 = 3 * u2 - 4 * u + 1, d01 = (-6 * u2 + 6 * u) / dt, d11 = 3 * u2 - 2 * u;
  for (let k = 0; k < 3; k++) {
    out[o + k] = h00 * rv[a + k] + h10 * rv[a + k + 3] + h01 * rv[b + k] + h11 * rv[b + k + 3];
    out[o + k + 3] = d00 * rv[a + k] + d10 * rv[a + k + 3] + d01 * rv[b + k] + d11 * rv[b + k + 3];
  }
  return out;
}

// Segment covering t (ms), preferring the later one at a shared boundary; never extrapolates.
export function segmentAt(track, t) {
  const s = track.segments;
  for (let j = s.length - 1; j >= 0; j--) if (t >= s[j].t0_ms && t <= s[j].t1_ms) return s[j];
  return null;
}

export function sampleSegment(seg, t, out, o = 0) {
  const x = (t - seg.t0_ms) / 1000, i = Math.max(0, Math.min(seg.n - 2, Math.floor(x / seg.step_s)));
  return hermiteInto(seg.rv, i, (x - i * seg.step_s) / seg.step_s, seg.step_s, out, o);
}

// Track state relative to its own centre; null outside coverage or inside a gap.
export function sampleTrack(track, t, out, o = 0) {
  const seg = segmentAt(track, t);
  if (!seg) return null;
  sampleSegment(seg, t, out, o);
  return seg;
}

export function equatorial(position) {
  const [x, y, z] = position;
  return { ra_deg: (Math.atan2(y, x) * 180 / Math.PI + 360) % 360,
    dec_deg: Math.atan2(z, Math.hypot(x, y)) * 180 / Math.PI };
}

export function solarTerminator(sun, radius) {
  const distance = Math.hypot(sun[0], sun[1], sun[2]);
  if (!(radius > 0 && Number.isFinite(distance) && distance > radius)) throw new RangeError('Sun must lie outside the globe');
  const normal = [sun[0] / distance, sun[1] / distance, sun[2] / distance];
  // At the geometric horizon, r · (s − r) = 0, hence r · s = R².
  const offset = radius * radius / distance;
  return { normal, center: normal.map(value => value * offset), radius: Math.sqrt(radius * radius - offset * offset) };
}

export function sceneVector(vector, radius) {
  return [vector[0] / radius, vector[2] / radius, -vector[1] / radius];
}

const vdot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const vcross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
export const OBLIQUITY_J2000_DEG = 23.4392911;

// Instantaneous two-body conic through a state (MMX states.js:80-89).
export function osculating(state, mu, ecliptic = false) {
  const r = [state[0], state[1], state[2]], v = [state[3], state[4], state[5]], rn = Math.hypot(...r), v2 = vdot(v, v), h = vcross(r, v), hn = Math.hypot(...h);
  const ev = r.map((x, k) => ((v2 - mu / rn) * x - vdot(r, v) * v[k]) / mu), e = Math.hypot(...ev);
  const energy = v2 / 2 - mu / rn, p = hn * hn / mu, P = e > 1e-10 ? ev.map(x => x / e) : r.map(x => x / rn);
  const Q = vcross(h, P).map(x => x / hn), eps = OBLIQUITY_J2000_DEG * Math.PI / 180;
  const hz = ecliptic ? -h[1] * Math.sin(eps) + h[2] * Math.cos(eps) : h[2];
  return { e, p, P, Q, c3: 2 * energy, a: -mu / (2 * energy), inclination: Math.acos(Math.max(-1, Math.min(1, hz / hn))) * 180 / Math.PI,
    periapsis: p / (1 + e), apoapsis: e < 1 ? p / (1 - e) : Infinity, period: e < 1 ? 2 * Math.PI * Math.sqrt((-mu / (2 * energy)) ** 3 / mu) : Infinity,
    trueAnomaly: Math.atan2(vdot(r, Q), vdot(r, P)) };
}
