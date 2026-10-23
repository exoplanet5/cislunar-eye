// Visual-pass modules that run without three: frames.matrixNear (EFF-10, AM1), lagrangeVisibility (V5), camera.js
// (V7 focus distances, V14 slerp with the pole guard, the tween) and the scale-bar step (V12).
import test from 'node:test';
import assert from 'node:assert/strict';
import { Frames, IDENTITY, rotateZ, apply } from '../web/frames.js';
import { LAGRANGE, lagrangeVisibility } from '../web/lagrange.js';
import { fitDistance, focusDistance, focusView, planeView, slerp, CameraTween, scaleStep, scaleLabel, ease, edgePoint, placeEdgeBox } from '../web/camera.js';
import { LD_KM, RE_KM } from '../web/states.js';
import { rangeText } from '../web/ui/format.js';

const AU = 1.495978707e8, DEG = Math.PI / 180;
const mul = (A, B) => Array.from({ length: 9 }, (_, i) => [0, 1, 2].reduce((s, k) => s + A[Math.floor(i / 3) * 3 + k] * B[k * 3 + i % 3], 0));
const T = A => [A[0], A[3], A[6], A[1], A[4], A[7], A[2], A[5], A[8]];
const close = (a, b, tol) => a.every((x, i) => Math.abs(x - b[i]) < tol);
const orthonormal = (R, tol = 1e-12) => assert.ok(close(mul([...R], T([...R])), [...IDENTITY], tol), 'R Rᵀ = I');

// Geocentric Sun on an inclined circle (as frames.test.mjs) and an elliptic Moon (e, 1 h samples, Kepler-solved).
function track(t0, n, rv) { return { center: 'earth', t0_ms: t0, t1_ms: t0 + (n - 1) * 3600e3, segments: [{ t0_ms: t0, t1_ms: t0 + (n - 1) * 3600e3, step_s: 3600, n, rv }] }; }
function sunTrack(t0, n) {
  const eps = 23.4392911 * DEG, w = 2 * Math.PI / (365.25 * 86400), rv = new Float64Array(n * 6);
  for (let i = 0; i < n; i++) {
    const L = 3.6 + w * i * 3600, x = AU * Math.cos(L), y = AU * Math.sin(L), vx = -AU * w * Math.sin(L), vy = AU * w * Math.cos(L);
    rv.set([x, y * Math.cos(eps), y * Math.sin(eps), vx, vy * Math.cos(eps), vy * Math.sin(eps)], i * 6);
  }
  return track(t0, n, rv);
}
function moonTrack(t0, n, e = 0) {
  const mu = 403503.2, a = 3.844e5, nn = Math.sqrt(mu / a ** 3), i5 = 5.14 * DEG, rv = new Float64Array(n * 6);
  for (let i = 0; i < n; i++) {
    const M = nn * i * 3600; let E = M; for (let k = 0; k < 30; k++) E -= (E - e * Math.sin(E) - M) / (1 - e * Math.cos(E));
    const r = a * (1 - e * Math.cos(E)), x = a * (Math.cos(E) - e), y = a * Math.sqrt(1 - e * e) * Math.sin(E), f = nn * a * a / r;
    const vx = -f * Math.sin(E), vy = f * Math.sqrt(1 - e * e) * Math.cos(E);
    rv.set([x, y * Math.cos(i5), y * Math.sin(i5), vx, vy * Math.cos(i5), vy * Math.sin(i5)], i * 6);
  }
  return track(t0, n, rv);
}

test('IDENTITY is typed and rotateZ keeps orthonormality', () => {
  assert.ok(IDENTITY instanceof Float64Array);
  const t0 = Date.parse('2026-10-06T13:00:00Z'), F = new Frames(sunTrack(t0, 49), moonTrack(t0, 49));
  assert.deepEqual([...F.matrix('eci', t0)], [1, 0, 0, 0, 1, 0, 0, 0, 1]);
  const R = F.matrix('em', t0 + 5e6);
  for (const th of [0, .3, -2.1, 3.14]) orthonormal(rotateZ(R, th));
  assert.ok(close([...rotateZ(R, 0)], [...R], 1e-15));
  // Rz(θ) turns the frame's x̂ toward ŷ: a point that was at angle θ ahead of x̂ lands on +x.
  const th = .4, p = [R[0] * Math.cos(th) + R[3] * Math.sin(th), R[1] * Math.cos(th) + R[4] * Math.sin(th), R[2] * Math.cos(th) + R[5] * Math.sin(th)];
  const q = apply(rotateZ(R, th), p);
  assert.ok(Math.abs(q[0] - 1) < 1e-12 && Math.abs(q[1]) < 1e-12 && Math.abs(q[2]) < 1e-12, `${q}`);
});

test('matrixNear: one exact R per native sample + ω·δ keeps AM1 to km level; without the term the sub-sample is off by ω·δ·r', () => {
  const t0 = Date.parse('2026-10-06T13:00:00Z'), r = 4e5, cases = [];
  for (const e of [0, .055]) {
    const F = new Frames(sunTrack(t0, 721), moonTrack(t0, 721, e)), p = [r, 0, 0];
    for (const [key, ref, bodyR] of [['em', t0 + 36 * 3600e3, r], ['se', t0 + 200 * 3600e3, 1.5e6]]) {
      for (const d of [300, 900, 1800, 3600, key === 'se' ? 21600 : 3600]) {
        const t = ref + d * 1000, exact = F.matrix(key, t), near = F.matrixNear(key, t, ref), raw = F.matrix(key, ref);
        orthonormal(near);
        assert.equal(F.near.t, ref);
        const pt = [bodyR, 0, 0], miss = (A, B) => { const a = apply(A, pt), b = apply(B, pt); return Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]); };
        cases.push({ e, key, d, near: miss(exact, near), raw: miss(exact, raw) });
      }
    }
    assert.deepEqual([...F.matrixNear('eci', t0 + 7e5, t0)], [1, 0, 0, 0, 1, 0, 0, 0, 1]);
    // past the end of the Sun/Moon track matrix() holds R(t1); matrixNear rotates only up to t1, not beyond
    const t1 = F.moon.t1_ms, beyond = F.matrixNear('em', t1 + 6 * 3600e3, t1 - 1800e3), atEnd = F.matrix('em', t1);
    assert.ok(close([...beyond], [...atEnd], 1e-6), 'clamped like matrix()');
  }
  const em1h = cases.filter(c => c.key === 'em' && c.d === 3600), se6h = cases.filter(c => c.key === 'se' && c.d === 21600);
  assert.ok(em1h.every(c => c.near < 10 && c.raw > 3000), JSON.stringify(em1h));             // review: ≈ 5 km vs ≈ 1,900–3,800 km
  assert.ok(se6h.every(c => c.near < 20 && c.raw > 5000), JSON.stringify(se6h));
  assert.ok(cases.every(c => c.near < c.raw / 50), JSON.stringify(cases.filter(c => !(c.near < c.raw / 50))));
});

test('lagrangeVisibility (V5, decision 4): EM points only in the EM frame or with a Moon-bound focus', () => {
  const se = LAGRANGE.map(L => L.system === 'se'), all = LAGRANGE.map(() => true);
  assert.deepEqual(lagrangeVisibility('se', 66, 'earth'), se);
  assert.deepEqual(lagrangeVisibility('eci', 104, 'SE-L2'), se);
  assert.deepEqual(lagrangeVisibility('se', 66, { id: 'vela-5a', cat: { regime: 'CISLUNAR_HEO' } }), se);
  assert.deepEqual(lagrangeVisibility('em', 66, 'earth'), all);
  assert.deepEqual(lagrangeVisibility('se', 268, 'earth'), se);                 // the SE-frame Earth–Moon opening view
  assert.deepEqual(lagrangeVisibility('se', 66, 'moon'), all);
  assert.deepEqual(lagrangeVisibility('se', 66, 'EM-L2'), all);
  assert.deepEqual(lagrangeVisibility('se', 66, { id: 'dro-a', cat: { regime: 'EM_OTHER' } }), all);
  assert.deepEqual(lagrangeVisibility('se', 66, { id: 'lro', cat: { regime: 'LUNAR_ORBIT' } }), all);
  assert.deepEqual(lagrangeVisibility('se', 66, null), se);
});

test('focusDistance (V7): regime framing, the HEO rule keeps Earth in view, EM targets untouched', () => {
  const view = { fov: 38, aspect: 1440 / 900, maxDistance: 6000 }, t = Math.tan(19 * DEG);
  assert.equal(fitDistance(150, view), Math.min(6000, 1.06 * Math.max(150 / (t * view.aspect), .35 * 150 / t)));
  assert.equal(focusDistance('earth', 'se', view), 12); assert.equal(focusDistance('moon', 'se', view), 2);
  assert.equal(focusDistance('SE-L2', 'se', view), fitDistance(150, view)); assert.equal(focusDistance('EM-L4', 'em', view), 30);
  const target = (regime, pos, apo_km) => ({ id: 'x', cat: { regime, elements: { apo_km } }, pos });
  assert.equal(focusDistance(target('LUNAR_ORBIT', [3.8e5, 0, 0]), 'se', view), .9);
  assert.equal(focusDistance(target('EM_DRO', [4.2e5, 0, 0]), 'em', view), 25);
  assert.equal(focusDistance(target('SE_L2', [1.5e6, 0, 0]), 'se', view), 140);
  assert.equal(focusDistance(target('EARTH_NEAR_HELIO', [5e6, 0, 0]), 'se', view), 200);
  const vela = target('CISLUNAR_HEO', [207451, 0, 0], 115000), d = focusDistance(vela, 'se', view);
  assert.ok(Math.abs(d - 1.3 * 207451 / RE_KM / t) < 1e-9 && d > 1.1 * 115000 / RE_KM, `${d}`);
  assert.ok(Math.abs(focusDistance(vela, 'se', { ...view, aspect: .5 }) - d / .5) < 1e-9, 'portrait: aspect-limited');
  // Vela 5A as catalogued (apogee 207,451 km): 122.8 R⊕ at apogee and at perigee, so Earth stays in view while following
  const vela5a = pos => focusDistance(target('CISLUNAR_HEO', pos, 207450.8), 'se', view);
  assert.ok(Math.abs(vela5a([207450.8, 0, 0]) - 122.8) < .05 && vela5a([28011, 0, 0]) === vela5a([207450.8, 0, 0]), `${vela5a([28011, 0, 0])}`);
  // Earth's offset from the view centre stays inside the half-height over the whole orbit (camera ⟂ to the offset)
  for (const r of [28011, 1e5, 207450.8]) assert.ok(r / RE_KM / vela5a([r, 0, 0]) < t / 1.29, `${r} km`);
  assert.equal(focusDistance(target('HEO', [2e4, 0, 0], 2e6), 'se', view), Math.min(6000, 1.3 * 2e6 / RE_KM / t), 'apogee rule, capped by maxDistance');
  assert.equal(focusDistance(target('HEO', [2e4, 0, 0], 2e7), 'se', view), 6000);
  assert.equal(focusDistance(target('HEO', [5e4, 0, 0], null), 'se', view), Math.max(Math.max(8, Math.min(120, 1.1 * 2e5 / RE_KM)), 1.3 * 5e4 / RE_KM / t), 'no apogee: |r| alone');
  assert.equal(focusDistance({ id: 'y', cat: { regime: 'CISLUNAR_HEO' } }, 'se', view), Math.max(8, Math.min(120, 1.1 * 2e5 / RE_KM)), 'no position: no HEO rule');
});

test('planeView and focusView: 30° above the frame plane with the reference body to the side; direction only for HEO', () => {
  const t0 = Date.parse('2026-10-06T13:00:00Z'), F = new Frames(sunTrack(t0, 49), moonTrack(t0, 49)), t = t0 + 7 * 3600e3;
  const W = { frames: F, R: new Float64Array(9), sunF: new Float64Array(3), moonF: new Float64Array(3) };
  for (const frame of ['se', 'em', 'eci']) {
    F.matrix(frame, t, W.R); F.context(t); apply(W.R, F.sunState, W.sunF); apply(W.R, F.moonState, W.moonF);
    for (const [system, ref] of [['se', 'sun'], ['em', 'moon'], ['se', 'moon']]) {
      const dir = planeView(W, t, system, ref), R = F.matrix(system, t), z = apply(W.R, [R[6], R[7], R[8]]), n = [z[0], z[2], -z[1]];
      const s = ref === 'moon' ? W.moonF : W.sunF.map(x => -x), a0 = [s[0], s[2], -s[1]], k = a0[0] * n[0] + a0[1] * n[1] + a0[2] * n[2];
      const a = a0.map((x, i) => x - k * n[i]), an = Math.hypot(...a);                      // the reference body projected onto the plane
      assert.ok(Math.abs(Math.hypot(dir.x, dir.y, dir.z) - 1) < 1e-12);
      assert.ok(Math.abs(dir.x * n[0] + dir.y * n[1] + dir.z * n[2] - Math.sin(30 * DEG)) < 1e-9, `${frame} ${system} elevation`);
      assert.ok(Math.abs(dir.x * a[0] + dir.y * a[1] + dir.z * a[2]) / an < 1e-9, `${frame} ${system}: ${ref} to the side`);
    }
    const view = { fov: 38, aspect: 1.6, maxDistance: 6000 }, heo = { id: 'v', cat: { regime: 'HEO' }, pos: [2e5, 0, 0] };
    const fv = focusView(heo, frame, W, t, view), ref = planeView(W, t, frame === 'em' ? 'em' : 'se', frame === 'em' ? 'moon' : 'sun');
    assert.equal(fv.dist, focusDistance(heo, frame, view)); assert.deepEqual(fv.dir, ref);
    assert.equal(focusView({ id: 'd', cat: { regime: 'EM_DRO' }, pos: [4e5, 0, 0] }, frame, W, t, view).dir, null);
    assert.equal(focusView('moon', frame, W, t, view).dir, null);
  }
});

test('slerp: exact endpoints, constant angular rate, and the pole guard bends a path through ±y (V14)', () => {
  const n = v => { const k = Math.hypot(v.x, v.y, v.z); return { x: v.x / k, y: v.y / k, z: v.z / k }; }, ang = (a, b) => Math.acos(Math.max(-1, Math.min(1, a.x * b.x + a.y * b.y + a.z * b.z)));
  const a = n({ x: 1, y: .3, z: .2 }), b = n({ x: -.4, y: .5, z: 1 }), th = ang(a, b);
  for (const s of [0, .25, .5, .75, 1]) {
    const p = slerp(a, b, s);
    assert.ok(Math.abs(Math.hypot(p.x, p.y, p.z) - 1) < 1e-12 && Math.abs(ang(a, p) - s * th) < 1e-9 && Math.abs(ang(p, b) - (1 - s) * th) < 1e-9, `s=${s}`);
  }
  assert.deepEqual(slerp(a, a, .5), a);
  const out = { x: 0, y: 0, z: 0 }; assert.equal(slerp(a, b, 1, out), out); assert.ok(ang(out, b) < 1e-9);
  // 20° either side of +y in the xz=0 plane: the plain great circle passes exactly through the pole.
  const c = { x: Math.cos(70 * DEG), y: Math.sin(70 * DEG), z: 0 }, d = { x: -Math.cos(70 * DEG), y: Math.sin(70 * DEG), z: 0 };
  let maxY = 0;
  for (let i = 0; i <= 100; i++) { const p = slerp(c, d, i / 100); maxY = Math.max(maxY, p.y); assert.ok(Math.abs(Math.hypot(p.x, p.y, p.z) - 1) < 1e-12); }
  assert.ok(maxY < Math.cos(10 * DEG) && maxY > Math.cos(20 * DEG), `closest approach to the pole: ${Math.acos(maxY) / DEG}°`);
  assert.ok(ang(slerp(c, d, 1), d) < 1e-9 && ang(slerp(c, d, 0), c) < 1e-9);
  // the same 40° arc 30° from the pole is left alone
  const e = n({ x: Math.cos(70 * DEG), y: Math.sin(70 * DEG), z: .6 }), f = n({ x: -Math.cos(70 * DEG), y: Math.sin(70 * DEG), z: .6 });
  assert.ok(Math.abs(ang(e, slerp(e, f, .5)) - ang(e, f) / 2) < 1e-9);
});

test('CameraTween: direction slerp with log-distance ease, immediate at 0 ms', () => {
  const cam = { position: { x: 0, y: 0, z: 10, set(x, y, z) { this.x = x; this.y = y; this.z = z; } } }, tw = new CameraTween(cam);
  tw.tweenTo({ x: 1, y: 0, z: 0 }, 40, 1000, 0);
  assert.equal(tw.step(0), 0); assert.ok(Math.abs(cam.position.z - 10) < 1e-12 && Math.abs(cam.position.x) < 1e-12);
  assert.equal(tw.step(500), ease(.5));
  const len = Math.hypot(cam.position.x, cam.position.y, cam.position.z);
  assert.ok(Math.abs(len - 20) < 1e-9 && Math.abs(cam.position.x - cam.position.z) < 1e-9, `half way: ${JSON.stringify(cam.position)}`);
  assert.equal(tw.step(1000), 1); assert.equal(tw.active, false);
  assert.ok(Math.abs(cam.position.x - 40) < 1e-9 && Math.abs(cam.position.z) < 1e-9);
  tw.tweenTo(null, 5, 0, 0);
  assert.equal(tw.active, false); assert.ok(Math.abs(cam.position.x - 5) < 1e-9, 'reduced motion: at once, direction kept');
  assert.equal(tw.step(7), 1);
  // an unset camera (position 0, the page's first preset) jumps to dir·dist without a log(0)
  cam.position.set(0, 0, 0); tw.tweenTo({ x: 0, y: 1, z: 0 }, 216.6, 0, 0);
  assert.ok(Math.abs(cam.position.y - 216.6) < 1e-9 && cam.position.x === 0 && cam.position.z === 0, JSON.stringify(cam.position));
  cam.position.set(0, 0, 0); tw.tweenTo({ x: 0, y: 1, z: 0 }, 50, 1000, 0); tw.step(500);
  assert.ok(Number.isFinite(cam.position.y) && Math.abs(cam.position.y - 50) < 1e-9, 'no distance to ease from: at the target distance throughout');
  cam.position.set(0, 0, 0); tw.tweenTo(null, null, 0, 0);
  assert.ok([cam.position.x, cam.position.y, cam.position.z].every(Number.isFinite), 'nothing to go on: finite');
});

test('scaleStep (V12): 1–2–5 series plus 1 LD, bar between 72 and 180 px', () => {
  const px = pxPerRE => pxPerRE / RE_KM;
  assert.equal(scaleStep(px(1.503)), 5e5);        // L1–L2 wide view
  assert.equal(scaleStep(px(5.739)), 2e5);        // Earth–Moon framing
  assert.equal(scaleStep(px(621.5)), 1e3);        // Moon close-up
  assert.equal(scaleStep(px(.3)), 2e6);           // near the 6,000 R⊕ limit
  assert.equal(scaleStep(.0004), LD_KM);          // 1 LD = 154 px beats 200,000 km
  for (const p of [1e-6, 3e-5, .0004, .002, .05, .7, 5]) { const l = scaleStep(p) * p; assert.ok(l > 72 && l <= 180, `${p}: ${l} px`); }
  assert.equal(scaleLabel(LD_KM), '1 LD'); assert.equal(scaleLabel(1e6), '1 Mkm'); assert.equal(scaleLabel(2e6), '2 Mkm'); assert.equal(scaleLabel(5e5), '500,000 km');
  // below 1 km (close focus, §9.2: 1.28 km minimum): metres, never a 1 km bar wider than the scene
  assert.equal(scaleStep(974), .1); assert.equal(scaleLabel(scaleStep(974)), '100 m');
  assert.equal(scaleLabel(scaleStep(779.5)), '200 m'); assert.equal(scaleLabel(scaleStep(194.9)), '500 m');   // JWST focus, camera 1.6 / 6.4 km
  for (const p of [30, 194.9, 779.5, 974, 5e4]) { const l = scaleStep(p) * p; assert.ok(l > 72 && l <= 180, `${p} px/km: ${l} px`); }
  assert.deepEqual([.001, .002, .005, .01, .5, 1, 2].map(scaleLabel), ['1 m', '2 m', '5 m', '10 m', '500 m', '1 km', '2 km']);
});

test('rangeText: km below 100,000 km, LD below 1 Mkm, Mkm from 1 Mkm out', () => {
  assert.deepEqual([42164, 384400, 999e3, 1e6, 1.494e6, 1.94e6, 5.09e6].map(rangeText),
    ['42,164 km', '1.00 LD', '2.60 LD', '1.00 Mkm', '1.49 Mkm', '1.94 Mkm', '5.09 Mkm']);
  assert.equal(rangeText(NaN), '—');
});

test('edge chevrons (V12): edge points stay inside the scene; placeEdgeBox finds a free slot without oscillating', () => {
  const w = 876, h = 856;
  for (let a = -Math.PI; a < Math.PI; a += .05) {
    const p = edgePoint(a, w, h);
    assert.ok(p.x >= 0 && p.x <= w && p.y >= 50 && p.y <= h - 70, `${a}: ${JSON.stringify(p)}`);
    assert.equal(p.right, Math.cos(a) > .3);
  }
  // SE-L2 preset: Earth and the Sun lie in the same direction; the Sun box sits on the Earth chevron's edge point, just
  // above the centre line (the old ±16 rule flipped direction every step there and ended on the Sun after 40 steps)
  const sun = { x: 20, y: 420, width: 46, height: 16 }, earth = placeEdgeBox({ x: 20, y: 420, width: 107, height: 16 }, [sun], h);
  assert.equal(earth.y, 436);
  const l1 = placeEdgeBox({ x: 20, y: 420, width: 113, height: 16 }, [sun, earth], h);
  assert.equal(l1.y, 404, 'next free slot on the other side');
  // below the centre the first try is toward the centre (up), clear of the legend strip
  assert.equal(placeEdgeBox({ x: 300, y: 770, width: 90, height: 16 }, [{ x: 0, y: 760, width: 600, height: 96 }], h).y, 738);
  // never above the scene top or below the foot; fully blocked: stays at its edge point
  assert.equal(placeEdgeBox({ x: 20, y: 6, width: 80, height: 16 }, [{ x: 0, y: 0, width: 200, height: 30 }], h).y, 38);
  assert.equal(placeEdgeBox({ x: 20, y: 500, width: 80, height: 16 }, [{ x: 0, y: 0, width: 2000, height: 2000 }], h).y, 500);
});
