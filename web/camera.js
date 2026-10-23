// Camera framing and transitions (V7, V14, V12): focus distances per regime with the HEO rule that keeps Earth in view,
// plane views, a direction slerp + log-distance tween that OrbitControls follows (it re-derives its spherical from
// camera.position each frame), the scale-bar step and the screen-edge geometry of the chevrons. Three-free: vectors are
// plain {x, y, z} (a THREE.Vector3 is one), so node tests run without the bundle.
import { apply } from './frames.js';
import { RE_KM, LD_KM } from './states.js';

export const ease = s => (s < .5 ? 2 * s * s : 1 - (-2 * s + 2) ** 2 / 2);
const HEO = /^(HEO|CISLUNAR_HEO)$/, DEG = Math.PI / 180, POLE_DEG = 3;
const dot = (a, b) => a.x * b.x + a.y * b.y + a.z * b.z;
const vec = (x = 0, y = 0, z = 0) => ({ x, y, z });
const cross = (a, b) => vec(a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x);
const scale = (a, k) => vec(a.x * k, a.y * k, a.z * k);
const add = (a, b) => vec(a.x + b.x, a.y + b.y, a.z + b.z);
const norm = a => { const n = Math.hypot(a.x, a.y, a.z) || 1; return vec(a.x / n, a.y / n, a.z / n); };

// Distance (R⊕) that fits ±half R⊕ across the view and 0.35·half up and down (the 30° plane views). view: {fov (deg),
// aspect, maxDistance}.
export function fitDistance(half, view) {
  const t = Math.tan(view.fov * DEG / 2);
  return Math.min(view.maxDistance ?? Infinity, 1.06 * Math.max(half / (t * (view.aspect || 1)), .35 * half / t));
}

// Camera distance (R⊕) for a focus: 'earth' | 'moon' | a Lagrange key | a target {id, cat, pos (frame km)}. HEO and
// CISLUNAR_HEO targets get at least 1.3·r / (tan(fov/2)·min(1, aspect)) with r = max(|r|, apogee), so Earth stays in view
// while following them around the whole orbit, not only at the epoch of the fly-to (V7: Vela 5A 123 R⊕ instead of 36);
// EM_*, lunar and translunar targets keep their regime framing (the same rule would send DRO-A to 211 R⊕). The display
// frame does not change the distance (every frame is Earth-centred); it is taken for symmetry with focusView.
export function focusDistance(target, frame, view) {
  const id = typeof target === 'string' ? target : target.id, cat = target.cat || {}, r = cat.regime || '';
  if (id === 'earth') return 12;
  if (id === 'moon') return 2.0;
  if (id.startsWith('SE-')) return fitDistance(150, view);
  if (id.startsWith('EM-')) return 30;
  if (r === 'LUNAR_ORBIT') return .9;
  if (r.startsWith('EM_')) return 25;
  if (r.startsWith('SE_')) return 140;
  if (r === 'EARTH_NEAR_HELIO') return 200;
  const apo = Number(cat.elements?.apo_km) || 0, d = Math.max(8, Math.min(120, 1.1 * (apo || 2e5) / RE_KM));
  if (!HEO.test(r) || !target.pos) return d;
  const t = Math.tan(view.fov * DEG / 2) * Math.min(1, view.aspect || 1), far = Math.max(apo, Math.hypot(target.pos[0], target.pos[1], target.pos[2]));
  return Math.min(view.maxDistance ?? Infinity, Math.max(d, 1.3 * far / RE_KM / t));
}

// Camera direction `elev`° above the plane with normal ẑ of `system` ('se' ecliptic, 'em' lunar orbit) in the display
// frame, the Sun (ref 'sun') or the Moon (ref 'moon') on the left/right. world: {R, frames, sunF, moonF}.
export function planeView(world, t, system, ref, elev = 30) {
  const R = world.frames.matrix(system, t), z = apply(world.R, [R[6], R[7], R[8]]), n = norm(vec(z[0], z[2], -z[1]));
  const s = ref === 'moon' ? world.moonF : [-world.sunF[0], -world.sunF[1], -world.sunF[2]];
  let a = vec(s[0], s[2], -s[1]);
  a = norm(add(a, scale(n, -dot(a, n))));
  const b = cross(n, a), e = elev * DEG;
  return norm(add(scale(b, -Math.cos(e)), scale(n, Math.sin(e))));
}

// Distance and direction for a focus (V7): HEO targets zoomed to are seen from the frame's own plane view (the SE plane
// with the Sun on the left in the SE and ECI frames, the lunar orbit plane with the Moon on the right in the EM frame), so
// the ellipse reads around Earth; everything else keeps the camera direction (dir null; §10 fly-to).
export function focusView(target, frame, world, t, view) {
  const r = typeof target === 'object' && target.cat?.regime || '', dir = HEO.test(r) ? planeView(world, t, frame === 'em' ? 'em' : 'se', frame === 'em' ? 'moon' : 'sun') : null;
  return { dist: focusDistance(target, frame, view), dir };
}

// Unit-vector slerp a → b into out. When the great circle would pass within POLE_DEG of the controls' pole (±y) between
// the endpoints, the path bends through a waypoint lifted out of the a–b plane, so OrbitControls' azimuth never flips (V14).
export function slerp(a, b, s, out = vec()) {
  const w = waypoint(a, b);
  if (w) return s < .5 ? slerp(a, w, s * 2, out) : slerp(w, b, s * 2 - 1, out);
  const d = Math.max(-1, Math.min(1, dot(a, b))), th = Math.acos(d);
  let k0 = 1 - s, k1 = s;
  if (th > 1e-6) { const sn = Math.sin(th); k0 = Math.sin((1 - s) * th) / sn; k1 = Math.sin(s * th) / sn; }
  const n = norm(vec(a.x * k0 + b.x * k1, a.y * k0 + b.y * k1, a.z * k0 + b.z * k1));
  out.x = n.x; out.y = n.y; out.z = n.z;
  return out;
}

function waypoint(a, b) {
  const n = cross(a, b), nn = Math.hypot(n.x, n.y, n.z);
  if (nn < 1e-9) return null;                                                       // same or opposite direction: plain slerp
  const nh = scale(n, 1 / nn), th = Math.acos(Math.max(-1, Math.min(1, dot(a, b))));
  if (Math.asin(Math.min(1, Math.abs(nh.y))) > POLE_DEG * DEG) return null;          // the circle stays clear of the pole
  const p = vec(0, Math.sign(a.y + b.y) || 1, 0), f = norm(add(p, scale(nh, -dot(p, nh))));    // foot of the pole on the circle
  const inside = Math.acos(Math.max(-1, Math.min(1, dot(a, f)))) + Math.acos(Math.max(-1, Math.min(1, dot(f, b)))) < th + 1e-6;
  return inside ? norm(add(scale(f, Math.cos(15 * DEG)), scale(nh, Math.sin(15 * DEG)))) : null;
}

// Direction slerp and log-distance tween of the camera offset about the focus (V14, V7). step() returns the eased
// progress so the caller can move the focus point with the same curve.
export class CameraTween {
  constructor(camera) { this.camera = camera; this.from = vec(0, 0, 1); this.to = vec(0, 0, 1); this.d0 = this.d1 = 1; this.start = 0; this.dur = 0; this.active = false; }

  // Move to direction `dir` (unit, from the focus; null keeps the current one) and distance `dist` (R⊕; null keeps the
  // current one) over ms (0 = at once), starting from the camera's current offset.
  tweenTo(dir, dist, ms, now = performance.now()) {
    const p = this.camera.position, d = Math.hypot(p.x, p.y, p.z);
    this.to = dir ? norm(dir) : d > 0 ? norm(p) : this.to;
    this.from = d > 0 ? norm(p) : this.to;                                   // an unset camera (at the focus) jumps
    this.d1 = dist > 0 ? dist : d > 0 ? d : this.d1; this.d0 = d > 0 ? d : this.d1;
    this.start = now; this.dur = ms; this.active = true;
    if (!ms) this.step(now);
    return this;
  }

  step(now = performance.now()) {
    if (!this.active) return 1;
    const s = this.dur ? Math.min(1, (now - this.start) / this.dur) : 1, e = ease(s), p = this.camera.position;
    const d = slerp(this.from, this.to, e), k = Math.exp(Math.log(this.d0) + (Math.log(this.d1) - Math.log(this.d0)) * e);
    p.set(d.x * k, d.y * k, d.z * k);
    if (s >= 1) this.active = false;
    return e;
  }
}

// Point on the scene edge in screen direction a (rad, y down), clear of the title above and the legend strip below (edge
// chevrons and the Sun indicator).
export function edgePoint(a, w, h) {
  const m = 28, cx = w / 2, cy = h / 2, sa = Math.sin(a);
  const k = Math.min((cx - m) / Math.abs(Math.cos(a) || 1e-9), sa > 0 ? (h - 72 - cy) / sa : sa < 0 ? (cy - 64) / -sa : Infinity);
  return { x: cx + Math.cos(a) * k - 6, y: cy + Math.sin(a) * k - 6, right: Math.cos(a) > .3 };
}

// Moves box b {x, y, width, height} (integer px) vertically to the nearest free 16-px slot: +1, −1, +2, −2 … steps from its
// edge point, toward the scene centre first, inside [4, h − 20]; `taken` are boxes already placed and keep-outs. Returns b.
export function placeEdgeBox(b, taken, h) {
  const y0 = b.y, dir = y0 > h / 2 ? -16 : 16;
  const hit = () => b.y < 4 || b.y > h - 20 || taken.some(o => b.x < o.x + o.width && b.x + b.width > o.x && b.y < o.y + o.height && b.y + b.height > o.y);
  for (let i = 1; i <= 60 && hit(); i++) b.y = y0 + dir * Math.ceil(i / 2) * (i % 2 ? 1 : -1);
  if (hit()) b.y = y0;
  return b;
}

// Scale-bar length (km) for a pixel scale at the focus plane: the longest of a 1–2–5 series (1 m … 500 Mkm) plus 1 LD that
// is ≤ maxPx, so the bar stays ≤ 180 px down to the 1.28 km close focus (§9.2).
const STEPS = [...[1e-3, .01, .1, 1, 10, 100, 1e3, 1e4, 1e5, 1e6, 1e7, 1e8].flatMap(d => [d, 2 * d, 5 * d]), LD_KM].sort((a, b) => a - b);
export function scaleStep(pxPerKm, maxPx = 180) {
  let km = STEPS[0];
  for (const s of STEPS) if (s * pxPerKm <= maxPx) km = s;
  return km;
}
export const scaleLabel = km => km === LD_KM ? '1 LD' : km >= 1e6 ? `${km / 1e6} Mkm` : km < 1 ? `${+(km * 1e3).toPrecision(3)} m` : `${km.toLocaleString('en-US')} km`;
