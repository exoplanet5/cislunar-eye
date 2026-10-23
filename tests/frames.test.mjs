import test from 'node:test';
import assert from 'node:assert/strict';
import { rotatingBasis, sceneRotation, apply, Frames, IDENTITY } from '../web/frames.js';
import { collinear, MU_SE } from '../web/lagrange.js';

const AU = 1.495978707e8;
const mul = (A, B) => Array.from({ length: 9 }, (_, i) => [0, 1, 2].reduce((s, k) => s + A[Math.floor(i / 3) * 3 + k] * B[k * 3 + i % 3], 0));
const T = A => [A[0], A[3], A[6], A[1], A[4], A[7], A[2], A[5], A[8]];
const det = A => A[0] * (A[4] * A[8] - A[5] * A[7]) - A[1] * (A[3] * A[8] - A[5] * A[6]) + A[2] * (A[3] * A[7] - A[4] * A[6]);
const close = (a, b, tol) => a.every((x, i) => Math.abs(x - b[i]) < tol);
function orthonormal(R, tol = 1e-13) {
  assert.ok(close(mul(R, T(R)), [...IDENTITY], tol), 'R Rᵀ = I');
  assert.ok(Math.abs(det(R) - 1) < tol, 'det R = +1');
}

// Geocentric Sun on an inclined circle (ecliptic-like, ICRF axes), 1 h samples.
function sunTrack(t0, n) {
  const eps = 23.4392911 * Math.PI / 180, w = 2 * Math.PI / (365.25 * 86400), rv = new Float64Array(n * 6);
  for (let i = 0; i < n; i++) {
    const L = 3.6 + w * i * 3600, x = AU * Math.cos(L), y = AU * Math.sin(L), vx = -AU * w * Math.sin(L), vy = AU * w * Math.cos(L);
    rv.set([x, y * Math.cos(eps), y * Math.sin(eps), vx, vy * Math.cos(eps), vy * Math.sin(eps)], i * 6);
  }
  return { center: 'earth', t0_ms: t0, t1_ms: t0 + (n - 1) * 3600e3, segments: [{ t0_ms: t0, t1_ms: t0 + (n - 1) * 3600e3, step_s: 3600, n, rv }] };
}

test('rotating bases are orthonormal and right handed', () => {
  let seed = 7;
  const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647 - .5) * 2;
  for (let k = 0; k < 200; k++) {
    const r = [rnd() * 4e5, rnd() * 4e5, rnd() * 4e5], v = [rnd(), rnd(), rnd()];
    for (const sign of [1, -1]) {
      const R = [...rotatingBasis(r, v, sign)];
      orthonormal(R);
      orthonormal([...sceneRotation(R)]);
      const x = apply(R, r);
      assert.ok(Math.abs(x[0] - sign * Math.hypot(...r)) < 1e-9 && Math.abs(x[1]) < 1e-9 && Math.abs(x[2]) < 1e-9);
    }
  }
});

test('sceneRotation is M R Mᵀ with the MMX mapping M = (x, z, −y)', () => {
  const R = [...rotatingBasis([1e5, 3e5, -2e5], [.3, -.8, .2], 1)], M = [1, 0, 0, 0, 0, 1, 0, -1, 0];
  assert.ok(close([...sceneRotation(R)], mul(mul(M, R), T(M)), 1e-15));
});

test('SE rotating: Sun on −x, SE-L2 on +x, ẑ along the Earth orbit normal (AM1)', () => {
  const t0 = Date.parse('2026-10-06T13:00:00Z'), F = new Frames(sunTrack(t0, 721), sunTrack(t0, 721));
  const { g1, g2 } = collinear(MU_SE);
  for (const t of [t0, t0 + 5.5 * 3600e3, t0 + 15 * 86400e3, t0 + 30 * 86400e3, t0 + 40 * 86400e3]) {
    const R = [...F.matrix('se', t)], s = F.context(t).sunState;
    orthonormal(R, 1e-12);
    const sun = apply(R, s), l2 = apply(R, [...s].slice(0, 3).map(c => -g2 * c)), l1 = apply(R, [...s].slice(0, 3).map(c => g1 * c));
    assert.ok(sun[0] < -.98 * AU && Math.hypot(sun[1], sun[2]) < 1e-3);
    assert.ok(l2[0] > 1.49e6 && l2[0] < 1.52e6 && Math.hypot(l2[1], l2[2]) < 1e-6);
    assert.ok(l1[0] < -1.48e6 && Math.hypot(l1[1], l1[2]) < 1e-6);
    // ẑ = unit((r⊕ − r☉) × (v⊕ − v☉)) = unit(r☉ × v☉): ecliptic north, about 23.44° from ICRF +Z
    const zdec = Math.asin(R[8]) * 180 / Math.PI;
    assert.ok(Math.abs(zdec - (90 - 23.4392911)) < 1e-6, `ẑ dec ${zdec}`);
  }
  assert.deepEqual([...F.matrix('eci', t0)], [...IDENTITY]);
});

test('EM rotating: Moon on +x', () => {
  const t0 = Date.parse('2026-10-06T13:00:00Z'), n = 49, w = 2 * Math.PI / (27.32 * 86400), rv = new Float64Array(n * 6), i5 = 5.14 * Math.PI / 180;
  for (let i = 0; i < n; i++) {
    const L = w * i * 3600, d = 3.844e5;
    rv.set([d * Math.cos(L), d * Math.sin(L) * Math.cos(i5), d * Math.sin(L) * Math.sin(i5), -d * w * Math.sin(L), d * w * Math.cos(L) * Math.cos(i5), d * w * Math.cos(L) * Math.sin(i5)], i * 6);
  }
  const moon = { center: 'earth', t0_ms: t0, t1_ms: t0 + (n - 1) * 3600e3, segments: [{ t0_ms: t0, t1_ms: t0 + (n - 1) * 3600e3, step_s: 3600, n, rv }] };
  const F = new Frames(sunTrack(t0, n), moon);
  for (const t of [t0, t0 + 7.25 * 3600e3, t0 + 47 * 3600e3]) {
    const R = [...F.matrix('em', t)], m = apply(R, F.context(t).moonState);
    orthonormal(R, 1e-12);
    assert.ok(Math.abs(m[0] - 3.844e5) < 1e-3 && Math.hypot(m[1], m[2]) < 1e-6, `${m}`);
  }
});
