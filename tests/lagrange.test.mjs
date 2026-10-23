import test from 'node:test';
import assert from 'node:assert/strict';
import { collinear, makeLagrange, LAGRANGE, MU_EM, MU_SE } from '../web/lagrange.js';
import { Clock } from '../web/time.js';

// CR3BP x-acceleration on the rotating x axis (barycentric, unit separation); zero at L1, L2, L3.
const ax = (x, mu) => x - (1 - mu) * (x + mu) / Math.abs(x + mu) ** 3 - mu * (x - 1 + mu) / Math.abs(x - 1 + mu) ** 3;

test('EM collinear roots: L1 ≈ 0.8369, L2 ≈ 1.1557 from the barycentre (μ = 0.0121506)', () => {
  const em = collinear(MU_EM);
  assert.ok(Math.abs(em.x1 - 0.8369) < 1e-4 && Math.abs(em.x2 - 1.1557) < 1e-4, `${em.x1} ${em.x2}`);
  assert.ok(Math.abs(em.g1 - 0.15093) < 1e-5 && Math.abs(em.g2 - 0.16783) < 1e-5);
  assert.ok(Math.abs(em.x3 + 1.00506) < 1e-5);
  for (const x of [em.x1, em.x2, em.x3]) assert.ok(Math.abs(ax(x, MU_EM)) < 1e-12, `a(${x}) = ${ax(x, MU_EM)}`);
});

test('SE collinear roots: about 1.4976e6 and 1.5077e6 km from Earth at 1 AU (μ = 3.0404e-6)', () => {
  const se = collinear(MU_SE), AU = 1.495978707e8;
  assert.ok(Math.abs(se.g1 * AU - 1.4976e6) < 1e3 && Math.abs(se.g2 * AU - 1.5077e6) < 1e3, `${se.g1 * AU} ${se.g2 * AU}`);
  for (const x of [se.x1, se.x2]) assert.ok(Math.abs(ax(x, MU_SE)) < 1e-9);
});

test('makeLagrange: SE points on the Sun line, EM points co-move with the Moon, L4/L5 equilateral', () => {
  const L = makeLagrange(), sun = [1.2e8, -8e7, -3.5e7, 16, 22, 9.5], moon = [-2.1e5, 3.0e5, 1.1e5, -.8, -.5, -.2];
  const { D, d } = L.update(sun, moon), p = Object.fromEntries(LAGRANGE.map((x, i) => [x.key, L.out[i]]));
  const norm = v => Math.hypot(v[0], v[1], v[2]), dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
  const along = (v, u) => (v[0] * u[0] + v[1] * u[1] + v[2] * u[2]) / norm(u);
  assert.ok(Math.abs(along(p['SE-L1'], sun) - L.se.g1 * D) < 1e-6 && Math.abs(along(p['SE-L2'], sun) + L.se.g2 * D) < 1e-6);
  assert.ok(Math.abs(norm(p['SE-L1']) - L.se.g1 * D) < 1e-6);
  for (const [k, x] of [['EM-L1', L.em.x1], ['EM-L2', L.em.x2], ['EM-L3', L.em.x3]]) {
    assert.ok(Math.abs(along(p[k], moon) - (x + MU_EM) * d) < 1e-6 && Math.abs(Math.abs(along(p[k], moon)) - norm(p[k])) < 1e-6, k);
  }
  for (const k of ['EM-L4', 'EM-L5']) {
    assert.ok(Math.abs(norm(p[k]) - d) < 1e-6 && Math.abs(dist(p[k], moon) - d) < 1e-6, k);
    const h = [moon[1] * moon[5] - moon[2] * moon[4], moon[2] * moon[3] - moon[0] * moon[5], moon[0] * moon[4] - moon[1] * moon[3]];
    assert.ok(Math.abs(along(p[k], h)) < 1e-6, `${k} in the orbit plane`);
  }
  const lead = p['EM-L4'], h = [moon[1] * moon[5] - moon[2] * moon[4], moon[2] * moon[3] - moon[0] * moon[5], moon[0] * moon[4] - moon[1] * moon[3]];
  const c = [moon[1] * lead[2] - moon[2] * lead[1], moon[2] * lead[0] - moon[0] * lead[2], moon[0] * lead[1] - moon[1] * lead[0]];
  assert.ok(along(c, h) > 0, 'EM-L4 leads the Moon');
});

test('Clock clamps to the window and LIVE needs the wall clock inside it', () => {
  const t0 = Date.parse('2026-10-06T13:00:00Z'), t1 = Date.parse('2026-11-05T13:00:00Z'), now = Date.parse('2026-10-21T13:00:00Z');
  let wall = now + 3600e3;
  const c = new Clock(t0, t1, now, () => wall);
  assert.equal(c.setLive(true), true); assert.equal(c.t, wall);
  c.set(t1 + 86400e3); assert.equal(c.t, t1); assert.equal(c.live, false);
  c.set(NaN); assert.equal(c.t, now);
  c.playing = true; c.rate = 86400; c.set(t1 - 1000); c.playing = true;
  assert.equal(c.tick(.1), true); assert.equal(c.t, t1); assert.equal(c.playing, false);
  wall = t1 + 1; assert.equal(c.setLive(true), false);
});
