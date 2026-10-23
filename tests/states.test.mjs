import test from 'node:test';
import assert from 'node:assert/strict';
import { hermite, hermiteInto, segmentAt, sampleTrack, sceneVector, osculating, solarTerminator, GM_MOON } from '../web/states.js';
import { decode, decodeTrack } from '../web/loader.js';
import { starAppearance, SKY_STYLE } from '../web/sky-style.js';

const b64 = (values, Type) => Buffer.from(new Uint8Array(Type.from(values).buffer)).toString('base64');
const circular = (r, w) => t => [r * Math.cos(w * t), r * Math.sin(w * t), 0, -r * w * Math.sin(w * t), r * w * Math.cos(w * t), 0];

test('Hermite reproduces native samples and matches the MMX reference form', () => {
  const rv = Float64Array.from({ length: 30 }, (_, i) => Math.sin(i * 1.7) * 4e5 + i), out = new Float64Array(6);
  for (let i = 0; i < 4; i++) {
    hermiteInto(rv, i, 0, 60, out);
    for (let k = 0; k < 6; k++) assert.ok(Math.abs(out[k] - rv[i * 6 + k]) < 1e-8);
    hermiteInto(rv, i, 1, 60, out);
    for (let k = 0; k < 6; k++) assert.ok(Math.abs(out[k] - rv[i * 6 + 6 + k]) < 1e-8);
    hermiteInto(rv, i, .37, 60, out);
    const ref = hermite(rv.slice(i * 6, i * 6 + 6), rv.slice(i * 6 + 6, i * 6 + 12), .37, 60);
    for (let k = 0; k < 6; k++) assert.ok(Math.abs(out[k] - ref[k]) < 1e-6 * Math.max(1, Math.abs(ref[k])));
  }
});

test('Hermite is exact for a cubic trajectory', () => {
  const p = t => [1 + 2 * t - 3e-3 * t * t + 4e-6 * t ** 3, -5 + 1e-2 * t ** 2, 7e-7 * t ** 3], v = t => [2 - 6e-3 * t + 12e-6 * t * t, 2e-2 * t, 21e-7 * t * t];
  const dt = 300, rv = Float64Array.from([...p(0), ...v(0), ...p(dt), ...v(dt)]), out = new Float64Array(6);
  for (const u of [.1, .5, .83]) {
    hermiteInto(rv, 0, u, dt, out);
    p(u * dt).forEach((x, k) => assert.ok(Math.abs(out[k] - x) < 1e-9));
    v(u * dt).forEach((x, k) => assert.ok(Math.abs(out[k + 3] - x) < 1e-12));
  }
});

test('2 h lunar orbit at 5 min stays inside the analytic bound r(ωh)⁴/384', () => {
  const T = 7200, r = Math.cbrt(GM_MOON * (T / 2 / Math.PI) ** 2), w = 2 * Math.PI / T, h = 300, f = circular(r, w);
  const rv = Float64Array.from([...f(0), ...f(h)]), out = new Float64Array(6);
  let err = 0;
  for (let u = 0; u <= 1; u += 1 / 64) { hermiteInto(rv, 0, u, h, out); const e = f(u * h); err = Math.max(err, Math.hypot(out[0] - e[0], out[1] - e[1], out[2] - e[2])); }
  const bound = r * (w * h) ** 4 / 384;
  assert.ok(err <= bound * 1.05, `${err} km > ${bound} km`);
  assert.ok(err < .03);
});

test('base64 decoding is little-endian for f32 and f64, zero-copy', () => {
  const v = [1.5, -2.25, 6378.137, 1e-7, 4.2e5, -0.0];
  assert.deepEqual([...decode(b64(v, Float64Array), 'f64')], v);
  assert.deepEqual([...decode(b64(v, Float32Array), 'f32')], [...Float32Array.from(v)]);
  const raw = new Uint8Array(new Float64Array([1]).buffer);
  assert.deepEqual([...raw], [0, 0, 0, 0, 0, 0, 0xf0, 0x3f]);
  assert.equal(decode(null, 'f64'), null);
});

test('segments: no extrapolation, no interpolation across a gap, later segment wins at a shared boundary', () => {
  const t0 = Date.parse('2026-10-06T13:00:00Z'), seg = (start, n, x, join) => ({ t0_ms: start, step_s: 3600, n, rv: b64(Array.from({ length: n * 6 }, (_, i) => (i % 6 === 0 ? x + i : 0)), Float64Array),
    sigma_km: null, src: 'test', quality: 'ref', join_before: join, jump_km: 0 });
  const tr = decodeTrack({ center: 'earth', dtype: 'f64', segments: [seg(t0, 5, 100, 'start'), seg(t0 + 4 * 3600e3, 3, 900, 'continuous'), seg(t0 + 10 * 3600e3, 3, 5000, 'gap')] });
  assert.equal(tr.segments[0].t1_ms, t0 + 4 * 3600e3);
  assert.equal(segmentAt(tr, t0 + 4 * 3600e3), tr.segments[1]);
  assert.equal(segmentAt(tr, t0 + 8 * 3600e3), null);
  assert.equal(segmentAt(tr, t0 - 1), null);
  assert.equal(segmentAt(tr, t0 + 12 * 3600e3 + 1), null);
  const out = new Float64Array(6);
  assert.equal(sampleTrack(tr, t0 + 10 * 3600e3, out), tr.segments[2]);
  assert.equal(out[0], 5000);
  assert.throws(() => decodeTrack({ center: 'earth', dtype: 'f64', segments: [{ ...seg(t0, 5, 0, 'start'), n: 6 }] }), /values for n=6/);
});

test('sceneVector is MMX (x, z, −y)/R: right handed and norm preserving', () => {
  const m = v => sceneVector(v, 1), cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  assert.deepEqual(m([1, 2, 3]), [1, 3, -2]);
  const x = [1, 0, 0], y = [0, 1, 0];
  assert.deepEqual(cross(m(x), m(y)).map(c => c + 0), m(cross(x, y)).map(c => c + 0));
  assert.ok(Math.abs(Math.hypot(...sceneVector([3e5, -4e5, 1.2e5], 6378.137)) * 6378.137 / Math.hypot(3e5, -4e5, 1.2e5) - 1) < 1e-15);
});

test('osculating conic and terminator', () => {
  const r = 1860, v = Math.sqrt(GM_MOON / r), o = osculating([r, 0, 0, 0, v, 0], GM_MOON);
  assert.ok(o.e < 1e-12 && Math.abs(o.a - r) < 1e-9 && Math.abs(o.inclination) < 1e-9);
  const t = solarTerminator([23455, 0, 0], 1);
  assert.ok(Math.abs(t.center[0] - 1 / 23455) < 1e-15 && Math.abs(t.radius ** 2 + t.center[0] ** 2 - 1) < 1e-12);
});

test('starAppearance: real but subdued constants (ARCHITECTURE §9.8)', () => {
  const rows = [[-1.44, 6.1, .60], [0, 5.0, .60], [3, 2.9, .39], [5, 1.4, .24], [6, .66, .17]];
  for (const [V, d, a] of rows) {
    const s = starAppearance(V);
    assert.ok(Math.abs(s.diameter - d) < .06, `V=${V}: ${s.diameter}`);
    assert.ok(Math.abs(s.opacity - a) < .006, `V=${V}: ${s.opacity}`);
    assert.equal(s.ringDiameter, 0);
  }
  assert.equal(starAppearance(9).diameter, .6); assert.equal(starAppearance(9).opacity, .12);
  assert.deepEqual([SKY_STYLE.raStepDegrees, SKY_STYLE.decStepDegrees, SKY_STYLE.gridOpacity, SKY_STYLE.constellationOpacity, SKY_STYLE.background], [30, 30, .32, .20, '#04060c']);
});
