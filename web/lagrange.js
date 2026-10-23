// Lagrange markers (ARCHITECTURE AM4): CR3BP collinear roots solved once per μ, scaled each frame by the
// instantaneous primary–secondary distance. Positions returned are geocentric ICRF km.
export const MU_EM = 0.0121506, MU_SE = 3.0404e-6;

function newton(f, df, g) {
  for (let k = 0; k < 100; k++) { const d = f(g) / df(g); g -= d; if (Math.abs(d) < 1e-16) break; }
  return g;
}

// γ1, γ2 from the secondary (L1 inside, L2 beyond); γ3 from the primary (L3 opposite the secondary).
export function collinear(mu) {
  const g0 = Math.cbrt(mu / 3);
  const l1 = newton(g => g ** 5 - (3 - mu) * g ** 4 + (3 - 2 * mu) * g ** 3 - mu * g ** 2 + 2 * mu * g - mu,
    g => 5 * g ** 4 - 4 * (3 - mu) * g ** 3 + 3 * (3 - 2 * mu) * g ** 2 - 2 * mu * g + 2 * mu, g0);
  const l2 = newton(g => g ** 5 + (3 - mu) * g ** 4 + (3 - 2 * mu) * g ** 3 - mu * g ** 2 - 2 * mu * g - mu,
    g => 5 * g ** 4 + 4 * (3 - mu) * g ** 3 + 3 * (3 - 2 * mu) * g ** 2 - 2 * mu * g - 2 * mu, g0);
  const l3 = newton(g => g ** 5 + (2 + mu) * g ** 4 + (1 + 2 * mu) * g ** 3 - (1 - mu) * g ** 2 - 2 * (1 - mu) * g - (1 - mu),
    g => 5 * g ** 4 + 4 * (2 + mu) * g ** 3 + 3 * (1 + 2 * mu) * g ** 2 - 2 * (1 - mu) * g - 2 * (1 - mu), 1 - 7 * mu / 12);
  // Barycentric x in units of the separation: primary at −μ, secondary at 1 − μ.
  return { mu, g1: l1, g2: l2, g3: l3, x1: 1 - mu - l1, x2: 1 - mu + l2, x3: -mu - l3 };
}

export const LAGRANGE = [
  { key: 'SE-L1', system: 'se' }, { key: 'SE-L2', system: 'se' },
  { key: 'EM-L1', system: 'em' }, { key: 'EM-L2', system: 'em' }, { key: 'EM-L3', system: 'em' },
  { key: 'EM-L4', system: 'em' }, { key: 'EM-L5', system: 'em' },
];

// V5 (decision 4): which LAGRANGE entries to draw. SE-L1/L2 always; EM-L1…L5 only in the EM frame or zoomed to the Moon,
// i.e. with the Moon, an EM point or a Moon-bound target (EM_*, LUNAR_ORBIT) focused — not in the SE-frame Earth–Moon
// opening view, where they sat among the targets. moonDistPx (the Moon's screen distance from Earth) is kept in the
// signature for the overlays contract and no longer decides. focus: an id string or a target {id, cat: {regime}}.
export function lagrangeVisibility(frame, moonDistPx, focus) {
  const id = (typeof focus === 'string' ? focus : focus?.id) || '', regime = (typeof focus === 'object' && focus?.cat?.regime) || '';
  const em = frame === 'em' || id === 'moon' || id.startsWith('EM-') || /^(EM_|LUNAR_ORBIT)/.test(regime);
  return LAGRANGE.map(L => L.system === 'se' || em);
}

export function makeLagrange(muSE = MU_SE, muEM = MU_EM) {
  const se = collinear(muSE), em = collinear(muEM), out = LAGRANGE.map(() => new Float64Array(3));
  // sun, moon: geocentric ICRF states [x, y, z, vx, vy, vz].
  return { se, em, out, update(sun, moon) {
    const D = Math.hypot(sun[0], sun[1], sun[2]);
    for (let k = 0; k < 3; k++) { out[0][k] = se.g1 * sun[k]; out[1][k] = -se.g2 * sun[k]; }
    const d = Math.hypot(moon[0], moon[1], moon[2]), u = [moon[0] / d, moon[1] / d, moon[2] / d];
    const h = [moon[1] * moon[5] - moon[2] * moon[4], moon[2] * moon[3] - moon[0] * moon[5], moon[0] * moon[4] - moon[1] * moon[3]];
    const hn = Math.hypot(...h), z = h.map(x => x / hn), y = [z[1] * u[2] - z[2] * u[1], z[2] * u[0] - z[0] * u[2], z[0] * u[1] - z[1] * u[0]];
    const c = Math.cos(Math.PI / 3), s = Math.sin(Math.PI / 3);
    // Geocentric = barycentric + μ·d along the Moon direction.
    for (let k = 0; k < 3; k++) {
      out[2][k] = (em.x1 + em.mu) * d * u[k]; out[3][k] = (em.x2 + em.mu) * d * u[k]; out[4][k] = (em.x3 + em.mu) * d * u[k];
      out[5][k] = d * (c * u[k] + s * y[k]); out[6][k] = d * (c * u[k] - s * y[k]);
    }
    return { D, d };
  } };
}
