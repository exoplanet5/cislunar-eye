// Palette gates (review V3) on the colours the page really uses: src/cle/catalog.py REGIMES (→ catalog.js) and the
// web/theme.css tokens. ΔE is OKLab ×100; CVD is the Machado–Oliveira–Fernandes 2009 simulation at severity 1, as the
// dataviz skill's validate_palette.js; "adjacent" is the order the list shows the families in.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { register } from 'node:module';
import path from 'node:path';
import { STATION_COLORS, TOKENS } from '../web/ui/theme.js';
import { SKY_STYLE } from '../web/sky-style.js';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SURFACE = '#04060c';
const MACHADO = {
  protan: [[0.152286, 1.052583, -0.204868], [0.114503, 0.786281, 0.099216], [-0.003882, -0.048116, 1.051998]],
  deutan: [[0.367322, 0.860646, -0.227968], [0.280085, 0.672501, 0.047413], [-0.011820, 0.042940, 0.968881]],
};
const s2l = c => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
const lin = h => [0, 2, 4].map(i => s2l(parseInt(h.slice(1 + i, 3 + i), 16) / 255));
const oklab = ([r, g, b]) => {
  const l = Math.cbrt(.4122214708 * r + .5363325363 * g + .0514459929 * b), m = Math.cbrt(.2119034982 * r + .6806995451 * g + .1073969566 * b), s = Math.cbrt(.0883024619 * r + .2817188376 * g + .6299787005 * b);
  return [.2104542553 * l + .793617785 * m - .0040720468 * s, 1.9779984951 * l - 2.428592205 * m + .4505937099 * s, .0259040371 * l + .7827717662 * m - .808675766 * s];
};
const sim = (v, k) => MACHADO[k].map(r => Math.max(0, Math.min(1, r[0] * v[0] + r[1] * v[1] + r[2] * v[2])));
const dE = (a, b, k) => { const x = oklab(k ? sim(lin(a), k) : lin(a)), y = oklab(k ? sim(lin(b), k) : lin(b)); return 100 * Math.hypot(x[0] - y[0], x[1] - y[1], x[2] - y[2]); };
const dCVD = (a, b) => Math.min(dE(a, b, 'protan'), dE(a, b, 'deutan'));
const chroma = h => { const [, a, b] = oklab(lin(h)); return Math.hypot(a, b); };
const lum = h => { const [r, g, b] = lin(h); return .2126 * r + .7152 * g + .0722 * b; };
const contrast = (a, b) => { const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x); return (hi + .05) / (lo + .05); };

const py = readFileSync(path.join(root, 'src/cle/catalog.py'), 'utf8');
const family = Object.fromEntries([...py.matchAll(/"([A-Z_0-9]+)": "(#[0-9a-f]{6})"/g)].map(m => [m[1], m[2]]));
const css = readFileSync(path.join(root, 'web/theme.css'), 'utf8');
const token = n => (css.match(new RegExp(`--${n}:(#[0-9a-f]{6})`)) || [])[1];
const ORDER = ['LUNAR', 'EM', 'HEO', 'SE_L1', 'SE_L2', 'HELIO', 'NEOCP'];       // list / Situation order of the families
const series = ORDER.map(k => family[k]);

test('catalog.py: seven colour families, chroma ≥ 0.1, contrast ≥ 3:1 on the scene surface', () => {
  assert.deepEqual(Object.keys(family), [...ORDER, 'OTHER']);
  for (const k of ORDER) assert.ok(chroma(family[k]) >= 0.1, `${k} ${family[k]} chroma ${chroma(family[k]).toFixed(3)} < 0.1 (reads grey)`);
  for (const k of ORDER) assert.ok(contrast(family[k], SURFACE) >= 3, `${k} contrast ${contrast(family[k], SURFACE).toFixed(2)}`);
  assert.equal(new Set(series).size, 7);
});

test('families: adjacent pairs in list order ΔE ≥ 13 under CVD and ≥ 15 normal; all pairs ≥ 8 CVD', () => {
  for (let i = 0; i + 1 < series.length; i++) {
    const [a, b] = [series[i], series[i + 1]], cvd = dCVD(a, b), nor = dE(a, b);
    assert.ok(cvd >= 13, `${ORDER[i]}↔${ORDER[i + 1]} CVD ΔE ${cvd.toFixed(1)} < 13`);
    assert.ok(nor >= 15, `${ORDER[i]}↔${ORDER[i + 1]} normal ΔE ${nor.toFixed(1)} < 15`);
  }
  for (let i = 0; i < series.length; i++) for (let j = i + 1; j < series.length; j++)
    assert.ok(dCVD(series[i], series[j]) >= 8, `${ORDER[i]}↔${ORDER[j]} CVD ΔE ${dCVD(series[i], series[j]).toFixed(1)} < 8`);
});

test('status and reference colours do not impersonate a series or a star', () => {
  const sun = token('sun'), cyan = token('cyan'), warn = token('warn'), live = token('live'), lagrange = token('lagrange'), star = token('star');
  assert.deepEqual([sun, cyan], ['#ffd54f', '#4fc3f7'], 'terminator / twilight convention');
  const core = SKY_STYLE.starCore;
  assert.ok(core[0] === core[1] && core[1] === core[2], `star core is pure grey: ${core}`);
  assert.equal('#' + core.map(c => Math.round(c * 255).toString(16).padStart(2, '0')).join(''), star);
  const near = (h, ref) => Math.min(dE(h, ref), dCVD(h, ref));
  // Stars, the Sun glyph/terminator and the Lagrange glyphs share the scene with every family: ≥ 8 normal and CVD. The
  // −12° ring (on the globe, off by default) and the sight lines (station → bead) are drawn only at the Earth: ≥ 8
  // normal for every family, CVD as well for the families drawn around the Earth. No light EM violet clears cyan under
  // CVD next to the lunar blue (tests/palette search, review V3 second pass), so that pair is separated by place.
  const AT_EARTH = new Set(['HEO', 'NEOCP']), sight = token('sightline');
  for (const [k, c] of ORDER.map((k, i) => [k, series[i]])) {
    for (const [name, ref] of [['sun', sun], ['star', star], ['lagrange', lagrange]]) assert.ok(near(c, ref) >= 8, `${k} ${c} vs ${name} ΔE ${near(c, ref).toFixed(1)}`);
    for (const [name, ref] of [['cyan', cyan], ['sight line', sight]]) {
      const d = AT_EARTH.has(k) ? near(c, ref) : dE(c, ref);
      assert.ok(d >= 8, `${k} ${c} vs ${name} ΔE ${d.toFixed(1)}`);
    }
  }
  assert.ok(near(warn, family.SE_L1) >= 8 && near(warn, sun) >= 8, `--warn vs SE-L1 ${near(warn, family.SE_L1).toFixed(1)} / sun ${near(warn, sun).toFixed(1)}`);
  assert.ok(Math.abs(((Math.atan2(oklab(lin(warn))[2], oklab(lin(warn))[1]) * 180 / Math.PI) + 360) % 360 - 90) > 15, '--warn is not yellow');
  assert.ok(near(live, family.NEOCP) >= 15, `--live vs NEOCP ${near(live, family.NEOCP).toFixed(1)}`);
  assert.ok(near(lagrange, star) >= 8 && near(lagrange, family.SE_L1) >= 8, `--lagrange vs star ${near(lagrange, star).toFixed(1)} / SE-L1 ${near(lagrange, family.SE_L1).toFixed(1)}`);
  assert.ok(contrast(warn, '#171c22') >= 4.5, 'warn text contrast on the panel');
});

test('station bead cycle: six colours, none the hairline, each ≥ 8 ΔE from it; the hairline is no second yellow', () => {
  const hair = token('hairline');
  assert.equal(STATION_COLORS.length, 6);
  assert.ok(!STATION_COLORS.includes(hair), 'the hairline colour is not a bead colour');
  for (const c of STATION_COLORS) assert.ok(dE(c, hair) >= 8, `${c} vs hairline ${dE(c, hair).toFixed(1)}`);
  // legend neighbours: terminator (--sun) and sight line; the NEOCP rose trail it is drawn over
  for (const [name, ref] of [['sun', token('sun')], ['sight line', token('sightline')], ['NEOCP', family.NEOCP]]) assert.ok(dE(hair, ref) >= 10, `hairline vs ${name} ΔE ${dE(hair, ref).toFixed(1)}`);
});

test('web/ui/theme.js fallbacks equal the web/theme.css tokens', () => {
  const root = css.slice(css.indexOf(':root{'), css.indexOf('}', css.indexOf(':root{')));
  const tokens = Object.fromEntries([...root.matchAll(/(--[a-z-]+):(#[0-9a-f]{6})/g)].map(m => [m[1], m[2]]));
  assert.deepEqual(TOKENS, tokens);
});

test('template keeps no colours: every var(--token) and token() used in web/ is defined in theme.css, no fallbacks', () => {
  const tpl = readFileSync(path.join(root, 'web/index.template.html'), 'utf8'), style = tpl.slice(tpl.indexOf('<style>'), tpl.indexOf('</style>'));
  const js = ['app.js', 'scene/overlays.js', 'scene/scalecues.js', 'scene/bodies.js', 'ui/legend.js', 'ui/readout.js', 'ui/list.js'].map(f => readFileSync(path.join(root, 'web', f), 'utf8')).join('\n');
  const defined = new Set([...css.matchAll(/(--[a-z-]+):/g)].map(m => m[1]).concat(['--mono', '--sans', '--left-w', '--right-w', '--c']));
  const used = new Set([...(style + js).matchAll(/var\((--[a-z-]+)/g), ...js.matchAll(/token\('(--[a-z-]+)'\)/g)].map(m => m[1]));
  assert.deepEqual([...used].filter(k => !defined.has(k)), [], 'tokens used but not defined in web/theme.css');
  assert.deepEqual(style.match(/var\(--[a-z-]+,[^)]*\)/g) || [], [], 'fallback colours in the template');
  assert.deepEqual(style.replace(/url\("?data:[^)]*\)/g, '').match(/#[0-9a-f]{3,8}\b/gi) || [], [], 'literal colours in the template <style>');
});

test('web/model.js fallback palette uses only catalog.py family colours', () => {
  const m = readFileSync(path.join(root, 'web/model.js'), 'utf8'), pal = m.slice(m.indexOf('const PALETTE'), m.indexOf('};', m.indexOf('const PALETTE')));
  for (const [, k, c] of pal.matchAll(/([A-Z_0-9]+): '(#[0-9a-f]{6})'/g)) assert.ok(Object.values(family).includes(c), `${k} ${c}`);
});

test('dist/data/catalog.js carries the catalog.py colours (skipped until make export)', { skip: !existsSync(path.join(root, 'dist/data/catalog.js')) }, () => {
  const js = readFileSync(path.join(root, 'dist/data/catalog.js'), 'utf8');
  const regimes = JSON.parse(js.slice(js.indexOf('{'), js.lastIndexOf('}') + 1)).regimes;
  for (const r of regimes) assert.ok(Object.values(family).includes(r.color), `${r.key} ${r.color} not a catalog.py family colour`);
  assert.equal(regimes.find(r => r.key === 'LUNAR_ORBIT').color, family.LUNAR);
});

// V10 / V11 text helpers (web/ui/readout.js, web/ui/legend.js); the vendored three resolves the bare 'three' of model.js.
const three = pathToFileURL(path.join(root, 'web/vendor/three/three.module.min.js')).href;
register(`data:text/javascript,export function resolve(s, c, n) { if (s === 'three') return { url: ${JSON.stringify(three)}, shortCircuit: true }; return n(s, c); }`);
const { notes, targetTip } = await import('../web/ui/readout.js');
const { renderLegend } = await import('../web/ui/legend.js');

test('readout/tooltip: one GP-epoch line instead of source + caveat + build note (V11)', () => {
  const t = Date.parse('2026-10-22T12:00:00Z');
  const T = { id: 'vela-5a', label: 'Vela 5A', css: family.HEO, has: false, draw: true, track: {}, regime: { label: 'Cislunar HEO' },
    seg: { quality: 'gp', src: 'gp:3954@2026-08-05T15:12:15Z' }, cat: { name: 'Vela 5A', staleness: { flag: 'gp-stale' }, sources: [], elements: {} },
    caveat: 'stale: Space-Track GP 78 d old (epoch 2026-08-05)',
    buildNote: 'Space-Track GP (SGP4/SDP4), newest epoch <= now 2026-08-05T15:12Z, age 77.9 d; consecutive GP sets disagree by 529 km' };
  const n = notes(T, t);
  assert.deepEqual(n, { caveat: '', note: 'consecutive GP sets disagree by 529 km', stale: true, src: 'gp:3954', epoch: 'GP epoch 2026-08-05 · 78 d · stale' });
  const tip = targetTip(T, t);
  assert.equal(tip.split('2026-08-05').length - 1, 1, tip);
  assert.match(tip, /class="fine warn">⚠ GP epoch 2026-08-05/);
  assert.match(tip, /Cislunar HEO · — · gp:3954</, 'the quality is not repeated before a source that starts with it');
  const X = { ...T, seg: { quality: 'extrap', src: 'fo:CAPSTONE+nbody@2026-09-11' }, cat: { sources: [] }, caveat: 'extrapolated', buildNote: 'n-body' };
  assert.deepEqual(notes(X, t), { caveat: 'extrapolated', note: 'n-body', epoch: '', stale: true, src: 'fo:CAPSTONE+nbody@2026-09-11' });   // 41 d past its arc
});

test('legend: two rows; tracklets (beads on a hairline) / sight line / rings keyed to their layers (V10)', () => {
  const el = {}; renderLegend(el);
  assert.equal(el.innerHTML.match(/class="row"/g).length, 2);
  for (const [key, cls, text] of [['tracklets', 'lg-trk', 'tracklets'], ['sightlines', 'lg-line sight', 'sight line'], ['rings', 'lg-ring', 'GEO · Moon distance']])
    assert.match(el.innerHTML, new RegExp(`data-legend="${key}"><i class="${cls}"[^>]*></i>${text}<`));
  assert.doesNotMatch(el.innerHTML, /hairline<|1 LD/, 'one tracklet entry; the ring is the instantaneous Moon distance, not 1 LD');
  assert.match(el.innerHTML, /○<\/b>extrap \/ stale/);
  assert.match(el.innerHTML, /<b>●<i class="dimb">●<\/i><\/b>derelict/, 'derelict: the same dot at full and reduced alpha');
});
