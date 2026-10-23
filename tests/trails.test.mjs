// Node tests of the trail batches and marker styles (web/scene/{trails,trailMaterial,markers}.js) on a synthetic world:
// the vendored three r170 is resolved for the bare 'three' specifier the bundler maps, no WebGL involved.
import test from 'node:test';
import assert from 'node:assert/strict';
import { register } from 'node:module';
import { pathToFileURL } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const three = pathToFileURL(path.join(root, 'web/vendor/three/three.module.min.js')).href;
register(`data:text/javascript,export function resolve(s, c, n) { if (s === 'three') return { url: ${JSON.stringify(three)}, shortCircuit: true }; return n(s, c); }`);
const THREE = await import('three');
const { Trails, GAIN, FAR_CAP } = await import('../web/scene/trails.js');
const { DASH_PX, DASH, dashUnit, pxPerUnit, MIN_FORESHORTEN, STYLE } = await import('../web/scene/trailMaterial.js');
const { targetStyle, extent, spriteSize, SHAPE, SIZE, SDF } = await import('../web/scene/markers.js');
const { defaultTrail } = await import('../web/model.js');
const { sampleTrack, RE_KM } = await import('../web/states.js');

const DAY = 86400, T0 = Date.parse('2026-10-07T12:00:00Z'), T1 = T0 + 30 * DAY * 1000, NOW = T0 + 15 * DAY * 1000;
const circle = (t0, n, step, radius, period, tilt = 0, join = 'start') => {
  const w = 2 * Math.PI / period, rv = new Float64Array(n * 6);
  for (let i = 0; i < n; i++) {
    const a = w * i * step, c = Math.cos(tilt), s = Math.sin(tilt);
    rv.set([radius * Math.cos(a), radius * Math.sin(a) * c, radius * Math.sin(a) * s, -radius * w * Math.sin(a), radius * w * Math.cos(a) * c, radius * w * Math.cos(a) * s], i * 6);
  }
  return { t0_ms: t0, t1_ms: t0 + (n - 1) * step * 1000, step_s: step, n, rv, quality: 'ref', join, src: 'test' };
};
const track = (center, segs) => ({ center, t0_ms: segs[0].t0_ms, t1_ms: segs.at(-1).t1_ms, segments: segs });

// A world with an identity frame: two HEO targets (one derelict), an SE-L2 target and the Moon track.
function world() {
  const w = { t0: T0, t1: T1, now: NOW, window_s: 30 * DAY, frames: { matrix: (f, t, out) => out.set([1, 0, 0, 0, 1, 0, 0, 0, 1]), matrixNear: (f, t, tRef, out) => out.set([1, 0, 0, 0, 1, 0, 0, 0, 1]) },
    moonTrack: track('earth', [circle(T0, 721, 3600, 384400, 27.3 * DAY)]), moon: new Float64Array(6), moonF: new Float64Array(3), targets: [] };
  const add = (id, regime, period_d, status, segs, quality = 'ref') => {
    const cat = { id, regime, status, elements: { period_d }, obj_type: 'payload' };
    const T = { id, index: w.targets.length, cat, track: track('earth', segs), draw: true, color: new THREE.Color('#4db6ac'), shape: 0,
      trail: defaultTrail(cat, w.window_s), rel: new Float64Array(6), pos: new Float64Array(3), seg: null, has: false };
    for (const s of T.track.segments) s.quality = quality;
    w.targets.push(T); return T;
  };
  add('heo', 'CISLUNAR_HEO', 2, 'active', [circle(T0, 721, 3600, 2e5, 2 * DAY, .3)]);
  add('wreck', 'CISLUNAR_HEO', 4, 'derelict', [circle(T0, 361, 7200, 3e5, 4 * DAY, .5)], 'gp');
  add('slow', 'CISLUNAR_HEO', 28, 'derelict', [circle(T0, 121, 6 * 3600, 5e5, 28 * DAY, .2)], 'gp');
  add('halo', 'SE_L2', 180, 'active', [circle(T0, 121, 6 * 3600, 1.5e6, 180 * DAY)]);
  // Two continuous segments that share no boundary sample (one step apart): the bridge keeps the arc running.
  add('split', 'CISLUNAR_HEO', 1, 'active', [circle(NOW - 2 * DAY * 1000, 25, 3600, 1e5, DAY), circle(NOW - 23 * 3600e3, 48, 3600, 1e5, DAY, 0, 'continuous')]);
  w.evaluate = t => { for (const T of w.targets) { T.seg = sampleTrack(T.track, t, T.rel); T.has = !!T.seg; T.pos.set(T.rel.subarray(0, 3)); } };
  return w;
}

const instances = (b, index) => {
  const { start, end, time, info, arc } = b.buf, out = [];
  for (let i = 0; i < b.geometry.instanceCount; i++) if (info[i * 2] === index)
    out.push({ s: Array.from(start.subarray(i * 3, i * 3 + 3)), e: Array.from(end.subarray(i * 3, i * 3 + 3)), ta: time[i * 2], tb: time[i * 2 + 1], arc: arc[i], style: info[i * 2 + 1] });
  return out;
};
const len = (a, b) => Math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]);
const setup = (frame = 'se') => {
  const w = world(), tr = new Trails(w, new THREE.Scene());
  tr.ensureTexture(); tr.rebuild(frame); w.evaluate(NOW);
  for (const T of w.targets) tr.setParams(T.index, T.color, true, T.trail.past, T.trail.future, false);
  tr.setParams(tr.moonIndex, new THREE.Color('#c1c7ce'), true, 7 * DAY, 0, false);
  return { w, tr };
};
const texel = (tr, i) => { const d = tr.data, b = (tr.width + i) * 4; return { visible: d[i * 4 + 3], past: d[b], future: d[b + 1], highlight: d[b + 2], gain: d[b + 3] }; };
const update = (tr, t = NOW) => tr.update(t, new Float64Array(3), () => true, new THREE.Vector2(1440, 900));

test('instance arcs accumulate the chord lengths along one trail, through continuous bridges', () => {
  const { w, tr } = setup();
  for (const id of ['heo', 'split']) {
    const T = w.targets.find(x => x.id === id), inst = instances(tr.batches.earth, T.index);
    assert.ok(inst.length > 100, `${id}: ${inst.length} instances`);
    assert.equal(inst[0].arc, 0);
    let s = 0, worst = 0;
    for (const x of inst) { worst = Math.max(worst, Math.abs(x.arc - s) / Math.max(1e-6, s)); s += len(x.s, x.e); assert.ok(x.tb > x.ta); }
    assert.ok(worst < 1e-5, `${id}: arc drift ${worst}`);
    const total = inst.reduce((a, x) => a + len(x.s, x.e), 0), r = Math.hypot(...inst[0].s), turns = (T.track.t1_ms - T.track.t0_ms) / (T.cat.elements.period_d * DAY * 1000);
    assert.ok(Math.abs(total / (2 * Math.PI * r) / turns - 1) < .02, `${id}: ${total / (2 * Math.PI * r)} turns of ${turns}`);
  }
  // The bridge is the one un-subdivided instance (a full step long) between the split segments, at the boundary.
  const T = w.targets.find(x => x.id === 'split'), split = instances(tr.batches.earth, T.index), bridges = split.filter(x => x.tb - x.ta === 3600);
  assert.equal(bridges.length, 1);
  assert.equal(bridges[0].ta, (T.track.segments[0].t1_ms - T0) / 1000);
  assert.equal(bridges[0].tb, (T.track.segments[1].t0_ms - T0) / 1000);
});

test('head segments continue the arc at the bracketing sub-sample', () => {
  const { w, tr } = setup();
  const t = NOW + 1234e3; w.evaluate(t); update(tr, t);
  for (const T of w.targets) {
    const heads = instances(tr.heads.earth, T.index);
    assert.equal(heads.length, 2, `${T.id}: two head instances`);
    const [a, b] = heads;
    assert.ok(a.style >= STYLE.HEAD && b.style >= STYLE.HEAD);
    assert.ok(Math.abs(b.arc - (a.arc + len(a.s, a.e))) < 1e-4 * Math.max(1, a.arc), `${T.id}: head arc ${a.arc} → ${b.arc}`);
    const trail = instances(tr.batches.earth, T.index), prev = trail.filter(x => x.tb <= a.ta).at(-1);
    assert.ok(prev && Math.abs(prev.arc + len(prev.s, prev.e) - a.arc) < 1e-4 * Math.max(1, a.arc), `${T.id}: head starts where the trail's sub-sample ends`);
    assert.ok(Math.abs(a.tb - (t - T0) / 1000) < 1e-6 && Math.abs(b.ta - (t - T0) / 1000) < 1e-6);
  }
});

test('idle HEO trails: 0.25·P at GAIN.idle while nothing is selected; hover and selection restore them', () => {
  const { w, tr } = setup();
  update(tr);
  const [heo, wreck, , halo] = w.targets;
  let x = texel(tr, heo.index);
  assert.deepEqual([x.past, x.future, x.highlight, x.visible], [.25 * 2 * DAY, heo.trail.future, 0, 1]);
  assert.ok(Math.abs(x.gain - GAIN.idle) < 1e-6);
  x = texel(tr, wreck.index);
  assert.equal(x.past, .25 * 4 * DAY); assert.ok(Math.abs(x.gain - GAIN.idle * GAIN.derelict) < 1e-6, 'derelict × idle');
  const slow = w.targets.find(T => T.id === 'slow');
  assert.deepEqual([slow.trail.past, texel(tr, slow.index).past], [7 * DAY, 3.5 * DAY], 'P > 14 d: half the 7 d cap, not 0.25·P');
  x = texel(tr, halo.index);
  assert.deepEqual([x.past, x.future, x.gain], [30 * DAY, 30 * DAY, 1], 'SE-L2 untouched in the SE frame');
  assert.equal(tr.material.uniforms.uAnySel.value, 0);
  // Hover: full default length and gain, the highlight style, no dimming of the rest.
  tr.setHover('heo'); update(tr);
  x = texel(tr, heo.index);
  assert.deepEqual([x.past, x.future, x.gain, x.highlight], [heo.trail.past, heo.trail.future, 1, 1]);
  assert.equal(texel(tr, wreck.index).past, DAY, 'others stay idle');
  assert.equal(tr.material.uniforms.uAnySel.value, 0, 'hover does not dim the rest');
  tr.setHover(null); update(tr);
  assert.deepEqual([texel(tr, heo.index).past, texel(tr, heo.index).highlight], [.5 * 2 * DAY * .5, 0]);
  // A user trail preset (past ≠ default) keeps its length, only the gain drops.
  tr.setParams(heo.index, heo.color, true, 3 * DAY, 1.5 * DAY, false); update(tr);
  x = texel(tr, heo.index); assert.equal(x.past, 3 * DAY); assert.ok(Math.abs(x.gain - GAIN.idle) < 1e-6);
  // Selection: the selected trail as asked, everyone else at their full length with uAnySel for the shader's 30 %.
  tr.setParams(heo.index, heo.color, true, w.window_s, w.window_s, true); update(tr);
  x = texel(tr, heo.index); assert.deepEqual([x.past, x.highlight, x.gain], [w.window_s, 1, 1]);
  assert.equal(tr.material.uniforms.uAnySel.value, 1);
  x = texel(tr, wreck.index); assert.equal(x.past, wreck.trail.past); assert.ok(Math.abs(x.gain - GAIN.derelict) < 1e-6);
  // setIdleDim(false): defaults everywhere.
  tr.setParams(heo.index, heo.color, true, heo.trail.past, heo.trail.future, false); tr.setIdleDim(false); update(tr);
  x = texel(tr, heo.index); assert.deepEqual([x.past, x.gain], [heo.trail.past, 1]);
  assert.ok(Math.abs(texel(tr, wreck.index).gain - GAIN.derelict) < 1e-6);
});

test('EM frame caps SE/helio trails at 0.25 d / 0.1 d unless hovered, selected or switched off', () => {
  const { w, tr } = setup('em');
  update(tr);
  const halo = w.targets.find(T => T.id === 'halo');
  let x = texel(tr, halo.index);
  assert.deepEqual([x.past, x.future], [FAR_CAP.past, FAR_CAP.future]);
  assert.deepEqual([FAR_CAP.past, FAR_CAP.future], [.25 * DAY, .1 * DAY]);
  tr.setHover('halo'); update(tr); x = texel(tr, halo.index); assert.deepEqual([x.past, x.future], [30 * DAY, 30 * DAY]);
  tr.setHover(null); tr.setFarCap(false); update(tr); assert.equal(texel(tr, halo.index).past, 30 * DAY);
  tr.setFarCap(true); tr.rebuild('se'); update(tr); assert.equal(texel(tr, halo.index).past, 30 * DAY, 'SE frame: no cap');
});

test('focus copy: the own texel hides, the focus slot carries the style; the Moon slot is plain', () => {
  const { w, tr } = setup();
  const heo = w.targets[0];
  tr.focus(heo); update(tr);
  assert.equal(texel(tr, heo.index).visible, 0);
  const f = texel(tr, tr.focusIndex);
  assert.deepEqual([f.visible, f.past], [1, .25 * 2 * DAY]);
  assert.ok(tr.focusBatch.geometry.instanceCount > 100 && tr.focusHeads.geometry.instanceCount === 2);
  const m = texel(tr, tr.moonIndex); assert.deepEqual([m.visible, m.past, m.gain], [1, 7 * DAY, 1]);
  tr.focus(null); update(tr); assert.equal(texel(tr, heo.index).visible, 1);
});

test('focus copy: arcs re-based at the anchor, continuous with the focus heads', () => {
  const { w, tr } = setup();
  const heo = w.targets[0], t = NOW + 777e3;
  w.evaluate(t); update(tr, t); tr.focus(heo); update(tr, t);
  const shared = instances(tr.batches.earth, heo.index), own = instances(tr.focusBatch, tr.focusIndex);
  assert.equal(own.length, shared.length);
  const s0 = shared[0].arc - own[0].arc, now = (t - T0) / 1000;
  assert.ok(s0 > 1000, `re-based by ${s0} R⊕`);
  for (let i = 0; i < own.length; i += 37) assert.ok(Math.abs(own[i].arc + s0 - shared[i].arc) < 1e-3, 'same arcs less s0');
  const at = own.find(x => x.ta <= now && x.tb >= now) || own.find(x => x.tb >= now);
  assert.ok(Math.abs(at.arc) < .5, `arc ≈ 0 at the anchor (${at.arc} R⊕)`);
  const [a, b] = instances(tr.focusHeads, tr.focusIndex);
  assert.ok(Math.abs(a.arc - at.arc) < 1e-4 && Math.abs(b.arc - (a.arc + len(a.s, a.e))) < 1e-4, 'focus heads share the re-based arc');
});

test('dash unit (C1): px per scene unit from the projection, cycles within DASH_PX·2^±¼ on screen', () => {
  const cam = new THREE.PerspectiveCamera(38, 1.6, 1e-4, 1e5), H = 900, P = cam.projectionMatrix, projY = P.elements[5];
  const px = v => { const c = new THREE.Vector4(...v, 1).applyMatrix4(P); return [c.x / c.w * .5 * H * 1.6, c.y / c.w * .5 * H]; };
  let rnd = 7;
  const rand = () => (rnd = (rnd * 16807) % 2147483647) / 2147483647;
  for (let n = 0; n < 200; n++) {
    const depth = 10 ** (rand() * 6 - 3), m = [(rand() - .5) * depth, (rand() - .5) * depth * .6, -depth];
    const d = [rand() - .5, rand() - .5, rand() - .5], h = 1e-4 * depth, s = m.map((x, i) => x - d[i] * h), e = m.map((x, i) => x + d[i] * h);
    const [x0, y0] = px(s), [x1, y1] = px(e), screen = Math.hypot(x1 - x0, y1 - y0) / Math.hypot(...d) / (2 * h);
    const k = pxPerUnit(s, e, H, projY), floor = .5 * H * projY / depth * MIN_FORESHORTEN;   // end-on trails: k floor
    assert.ok(Math.abs(Math.max(screen, floor) / k - 1) < 2e-3, `projection ${screen} vs pxPerUnit ${k} (depth ${depth})`);
    const cycle = dashUnit(k) * k;
    assert.ok(cycle >= DASH_PX * 2 ** -.25 - 1e-9 && cycle <= DASH_PX * 2 ** .25 + 1e-9, `cycle ${cycle} px`);
  }
  // The page hook: the cycle along each trail at its marker, from a real camera, within the same bounds.
  const { w, tr } = setup(), cam2 = new THREE.PerspectiveCamera(38, 1.6, 1e-4, 1e5);
  update(tr);
  for (const dist of [120, 900]) {
    cam2.position.set(dist * .3, dist * .5, dist * .8); cam2.lookAt(0, 0, 0); cam2.updateMatrixWorld();
    tr.batches.earth.mesh.onBeforeRender(null, null, cam2);
    for (const T of w.targets) {
      const c = tr.dashCyclePx(T.id);
      assert.ok(c >= DASH_PX * 2 ** -.25 - 1e-6 && c <= DASH_PX * 2 ** .25 + 1e-6, `${T.id} at ${dist} R⊕: ${c} px`);
    }
  }
  // Neighbours with k inside one half octave share the unit (a continuous phase); the patterns' lit fractions.
  assert.equal(dashUnit(2 ** 6.26), dashUnit(2 ** 6.74));
  assert.ok(dashUnit(2 ** 6.76) < dashUnit(2 ** 6.74));
  assert.ok(DASH.future[1] > .5 && DASH.extrap[0] === .5 && DASH.extrap[1] / DASH.extrap[0] === .5);
});

test('marker styles (V4): hollow only for stale or extrapolated, derelict dim, sizes and ≥ 1.5 px shape extents', () => {
  const T = (quality, status) => ({ seg: { quality }, cat: { status }, shape: 0, color: new THREE.Color('#fff') });
  assert.deepEqual(targetStyle(T('ref', 'active')), { size: SIZE.idle, shape: 0, hollow: false, alpha: 1 });
  assert.deepEqual(targetStyle(T('gp', 'derelict')), { size: 8.5, shape: 0, hollow: false, alpha: .82 * .7 });
  assert.deepEqual(targetStyle(T('ref', 'derelict')), { size: 8.5, shape: 0, hollow: false, alpha: .7 });
  assert.deepEqual(targetStyle(T('extrap', 'active')), { size: 8.5, shape: 0, hollow: true, alpha: 1 });
  assert.deepEqual(targetStyle(T('gp', 'derelict'), { stale: true }), { size: 8.5, shape: 0, hollow: true, alpha: .6 }, 'stale: hollow at 60 %, not dimmed twice');
  assert.deepEqual(targetStyle(T('extrap', 'derelict'), { selected: true, stale: true }), { size: SIZE.selected, shape: 0, hollow: true, alpha: 1 });
  assert.deepEqual(targetStyle(T('gp', 'derelict'), { hovered: true }), { size: SIZE.hover, shape: 0, hollow: false, alpha: 1 });
  assert.ok(SIZE.idle === 8.5 && SIZE.hover > SIZE.idle && SIZE.selected > SIZE.hover);
  // The shader's SDFs (same constants), traced along 360 rays: every pair of the four target glyphs differs by ≥ 1.5 px
  // in some direction, the triangle has the circle's area (± 5 %), and glyph + 1.6 px halo fit the sprite at every size.
  const sdf = (shape, x, y, size) => {
    const r = size / 2;
    if (shape === SHAPE.square) return Math.max(Math.abs(x), Math.abs(y)) - SDF.square * r;
    if (shape === SHAPE.diamond) return (Math.abs(x) + Math.abs(y)) * .7071 - SDF.diamond * r;
    if (shape === SHAPE.triangle) { const qx = Math.abs(x), qy = y - .15 * r; return Math.max(qx * .866 - qy * .5, qy) - SDF.triangle * r; }
    return Math.hypot(x, y) - r;
  };
  const ray = (shape, a, size) => { let lo = 0, hi = size * 2; for (let i = 0; i < 50; i++) { const m = (lo + hi) / 2; if (sdf(shape, m * Math.cos(a), m * Math.sin(a), size) < 0) lo = m; else hi = m; } return lo; };
  const shapes = [SHAPE.circle, SHAPE.square, SHAPE.diamond, SHAPE.triangle], prof = shapes.map(s => Array.from({ length: 360 }, (_, i) => ray(s, i * Math.PI / 180, SIZE.idle)));
  for (let a = 0; a < 4; a++) for (let b = a + 1; b < 4; b++) {
    const diff = Math.max(...prof[a].map((x, i) => Math.abs(x - prof[b][i])));
    assert.ok(diff >= 1.5, `shapes ${shapes[a]}/${shapes[b]} differ by ${diff.toFixed(2)} px`);
  }
  const area = p => p.reduce((s, x) => s + x * x / 2 * Math.PI / 180, 0);
  assert.ok(Math.abs(area(prof[3]) / area(prof[0]) - 1) < .05, `triangle/circle area ${area(prof[3]) / area(prof[0])}`);
  for (const size of Object.values(SIZE)) for (const s of shapes) {
    const far = Math.max(...Array.from({ length: 360 }, (_, i) => ray(s, i * Math.PI / 180, size)));
    assert.ok(far <= extent(s, size) + 1e-3 && far > extent(s, size) - .15, `extent(${s}, ${size}) ${extent(s, size)} vs traced ${far}`);
    assert.ok(extent(s, size) + 1.6 <= spriteSize(size) / 2, `shape ${s} at ${size} px and its halo fit the sprite`);
  }
  assert.equal(SDF.stroke, 1.2);
});
