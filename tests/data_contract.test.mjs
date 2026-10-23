// Field-level contract between dist/data/*.js and the page (REVIEW DW-12), checked without a browser: every field
// that web/loader.js, web/model.js, web/app.js and web/ui/*.js read from the cle/*@1 sidecars. Skips when dist/data
// is not built (make build); the viewer fixture has a looser catalog and is covered by tests/browser.mjs.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { decode, decodeTrack, parseSource } from '../web/loader.js';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..'), dir = path.join(root, 'dist/data');
const skip = existsSync(path.join(dir, 'core.js')) ? false : 'dist/data not built (make build)';
const ISO = /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$/;
const QUALITY = new Set(['ref', 'pred', 'fit', 'extrap', 'gp', 'conic']), JOIN = new Set(['start', 'continuous', 'splice', 'gap', 'source']);
const isIso = (s, what) => assert.ok(typeof s === 'string' && ISO.test(s), `${what}: ISO UTC expected, got ${JSON.stringify(s)}`);
const isStr = (s, what) => assert.equal(typeof s, 'string', what);
const isNum = (x, what) => assert.ok(typeof x === 'number' && Number.isFinite(x), `${what}: number expected, got ${JSON.stringify(x)}`);
const isBool = (x, what) => assert.equal(typeof x, 'boolean', what);

function load(name) {              // CLE.register("<name>", {...}); — the classic-script sidecar, evaluated with a stub
  const text = readFileSync(path.join(dir, `${name}.js`), 'utf8');
  let got = null;
  new Function('CLE', text)({ register(n, payload) { assert.equal(n, name, `${name}.js registers ${n}`); got = payload; } });
  assert.ok(got && typeof got === 'object', `${name}.js registered nothing`);
  return got;
}
const files = skip ? [] : readdirSync(dir).filter(f => f.endsWith('.js') && !f.startsWith('.')).sort();
const D = skip ? {} : Object.fromEntries(files.map(f => [f.slice(0, -3), load(f.slice(0, -3))]));
const win = skip ? null : { t0: Date.parse(D.core.meta.window.start_utc), t1: Date.parse(D.core.meta.window.stop_utc) };

// A track as decodeTrack() and sampleTrack() read it: ordered uniform segments, rv of n·6 finite values, src as an
// index into track.srcs, no ISO t0, sigma_km / jump_km only when there is one (EFF-6).
function checkTrack(track, what) {
  assert.ok(track && ['earth', 'moon'].includes(track.center), `${what}: center`);
  assert.ok(['f32', 'f64'].includes(track.dtype), `${what}: dtype`);
  assert.ok(Array.isArray(track.srcs) && track.srcs.length > 0 && track.srcs.every(x => typeof x === 'string' && x.includes(':')), `${what}: srcs`);
  assert.equal(new Set(track.srcs).size, track.srcs.length, `${what}: srcs not distinct`);
  assert.ok(Array.isArray(track.segments) && track.segments.length > 0, `${what}: segments`);
  let end = -Infinity;
  const used = new Set();
  track.segments.forEach((s, k) => {
    assert.equal(s.t0, undefined, `${what}[${k}]: ISO t0 is gone (t0_ms)`); isNum(s.t0_ms, `${what}[${k}].t0_ms`);
    assert.ok(Number.isInteger(s.t0_ms / 1000), `${what}[${k}]: t0_ms on a whole second`);
    assert.ok(s.step_s > 0 && Number.isInteger(s.n) && s.n >= 2, `${what}[${k}]: step/n`);
    assert.ok(QUALITY.has(s.quality) && JOIN.has(s.join_before), `${what}[${k}]: quality/join ${s.quality}/${s.join_before}`);
    if (k === 0) assert.equal(s.join_before, 'start', `${what}: first join`);
    assert.ok(Number.isInteger(s.src) && s.src >= 0 && s.src < track.srcs.length, `${what}[${k}].src: index into srcs, got ${JSON.stringify(s.src)}`); used.add(s.src);
    assert.ok(s.jump_km === undefined || (typeof s.jump_km === 'number' && s.jump_km !== 0), `${what}[${k}].jump_km`);
    assert.ok(s.sigma_km === undefined || typeof s.sigma_km === 'string', `${what}[${k}].sigma_km`);
    assert.ok(s.t0_ms >= end - 1, `${what}[${k}]: segments out of order`); end = s.t0_ms + (s.n - 1) * s.step_s * 1000;
  });
  assert.equal(used.size, track.srcs.length, `${what}: every srcs entry is used`);
  const d = decodeTrack(track);    // throws when rv.length !== n·6
  d.segments.forEach((s, k) => {
    assert.equal(s.src, track.srcs[track.segments[k].src], `${what}[${k}]: decoded src`);
    assert.ok(s.rv.every(Number.isFinite), `${what}[${k}]: non-finite state`);
    if (s.sigma) assert.equal(s.sigma.length, s.n, `${what}[${k}]: sigma length`);
    assert.equal(s.jump_km, track.segments[k].jump_km || 0, `${what}[${k}]: decoded jump_km`);
  });
  return d;
}

test('manifest.js lists every sidecar with its size and sha256', { skip }, () => {
  const m = D.manifest;
  assert.equal(m.schema, 'cle/manifest@1'); isIso(m.built_utc, 'manifest.built_utc'); isIso(m.now_utc, 'manifest.now_utc');
  assert.deepEqual(Object.keys(m.files).sort(), files.filter(f => f !== 'manifest.js'));
  for (const [f, e] of Object.entries(m.files)) {
    const bytes = readFileSync(path.join(dir, f));
    assert.equal(e.bytes, statSync(path.join(dir, f)).size, `${f}: bytes`);
    assert.equal(e.sha256, createHash('sha256').update(bytes).digest('hex'), `${f}: sha256`);
  }
});

test('core.js: window, Earth orientation, Moon and Sun context tracks', { skip }, () => {
  const c = D.core, m = c.meta;
  assert.equal(c.schema, 'cle/core@1');
  isIso(m.built_utc, 'built_utc'); isIso(m.now_utc, 'now_utc'); isIso(m.window.start_utc, 'start'); isIso(m.window.stop_utc, 'stop');
  assert.equal(m.now_ms, Date.parse(m.now_utc)); assert.equal(m.window.t0_ms, win.t0); assert.equal(m.window.t1_ms, win.t1);
  assert.ok(win.t0 < m.now_ms && m.now_ms < win.t1 && win.t1 - win.t0 === 30 * 86400e3, 'now ± 15 d');
  assert.equal(m.now_utc, D.manifest.now_utc); assert.equal(m.now_utc, D.catalog.now_utc);
  isNum(m.mu.em, 'mu.em'); isNum(m.mu.se, 'mu.se'); assert.ok(Math.abs(m.mu.em - 0.0121506) < 1e-5 && m.mu.se < 1e-5);
  assert.ok(Math.abs(c.earth.radius_km - 6378.137) < 1e-6 && Math.abs(c.moon.radius_km - 1737.4) < 1);
  const q = c.earth.quat, xyzw = decode(q.xyzw, 'f64');
  isNum(q.t0_ms, 'quat.t0_ms'); isNum(q.step_s, 'quat.step_s'); assert.equal(xyzw.length, q.n * 4);
  assert.ok(q.t0_ms <= win.t0 && q.t0_ms + (q.n - 1) * q.step_s * 1000 >= win.t1, 'quat covers the window');
  for (let i = 0; i < q.n; i++) assert.ok(Math.abs(Math.hypot(xyzw[4 * i], xyzw[4 * i + 1], xyzw[4 * i + 2], xyzw[4 * i + 3]) - 1) < 1e-9, 'unit quaternion');
  for (const [name, range] of [['moon', [3.5e5, 4.1e5]], ['sun', [1.46e8, 1.53e8]]]) {
    const t = checkTrack(c[name].track, `core.${name}`);
    assert.equal(c[name].track.center, 'earth'); assert.equal(c[name].track.dtype, 'f64');
    assert.ok(t.t0_ms <= win.t0 && t.t1_ms >= win.t1, `${name} track covers the window`);
    const rv = t.segments[0].rv;
    for (let i = 0; i < t.segments[0].n; i++) {
      const r = Math.hypot(rv[6 * i], rv[6 * i + 1], rv[6 * i + 2]);
      assert.ok(r > range[0] && r < range[1], `${name} distance ${r.toFixed(0)} km`);
    }
  }
});

test('catalog.js: every target carries what the list, readout and model read', { skip }, () => {
  const c = D.catalog;
  assert.equal(c.schema, 'cle/catalog@1'); isIso(c.built_utc, 'built_utc');
  const regimes = new Set(c.regimes.map(r => { isStr(r.key, 'regime.key'); isStr(r.label, 'regime.label'); assert.match(r.color, /^#[0-9a-f]{6}$/i); return r.key; }));
  for (const g of c.groups) { isStr(g.key, 'group.key'); isNum(g.order, 'group.order'); }
  assert.ok(Array.isArray(c.chunks) && c.chunks.length > 0);
  for (const ch of c.chunks) assert.ok(ch.startsWith('targets-') && D[ch], `chunk ${ch} has a file`);
  const ids = new Set();
  for (const t of c.targets) {
    const w = `target ${t.id}`;
    isStr(t.id, w); assert.ok(!ids.has(t.id), `${w}: duplicate id`); ids.add(t.id);
    isStr(t.name, `${w}.name`); isStr(t.label, `${w}.label`); isStr(t.group, `${w}.group`); isStr(t.obj_type, `${w}.obj_type`);
    assert.ok(regimes.has(t.regime), `${w}: regime ${t.regime}`);
    isBool(t.natural, `${w}.natural`); isBool(t.draw, `${w}.draw`); isBool(t.drawn, `${w}.drawn`); isBool(t.default_visible, `${w}.default_visible`);
    assert.ok(Number.isInteger(t.tier) && t.tier >= 1, `${w}.tier`); isStr(t.caveat, `${w}.caveat`); isStr(t.status, `${w}.status`);
    isStr(t.primary_ephem, `${w}.primary_ephem`); isStr(t.notes, `${w}.notes`); assert.ok(Array.isArray(t.aliases), `${w}.aliases`);
    assert.ok(t.norad === null || Number.isInteger(t.norad), `${w}.norad`);
    for (const k of ['cospar', 'horizons_id', 'status_date']) assert.ok(t[k] === null || typeof t[k] === 'string', `${w}.${k}`);
    assert.ok(Array.isArray(t.sources), `${w}.sources`); assert.equal(t.src_rows, undefined, `${w}: src_rows is gone`);
    const sources = t.sources.map(s => { isStr(s, `${w}.sources: compact string`); return parseSource(s); });    // what model.js gives readout.js
    for (const s of sources) { assert.ok(/^\w+$/.test(s.kind), `${w}.sources.kind ${JSON.stringify(s)}`); assert.ok(s.ref.length > 0, `${w}.sources.ref`); isBool(s.primary, `${w}.sources.primary`); }
    assert.ok(sources.filter(s => s.primary).length <= 1 && sources.findIndex(s => s.primary) <= 0, `${w}: at most one primary source, listed first`);
    assert.ok(t.elements && typeof t.elements === 'object', `${w}.elements`);
    for (const k of ['a_km', 'e', 'i_deg', 'period_d', 'peri_km', 'apo_km']) assert.ok(t.elements[k] === null || typeof t.elements[k] === 'number', `${w}.elements.${k}`);
    assert.ok(t.elements.epoch === null || typeof t.elements.epoch === 'string', `${w}.elements.epoch`);
    assert.ok(t.trail === null || (typeof t.trail === 'object' && (t.trail.past_s == null || typeof t.trail.past_s === 'number')), `${w}.trail`);
    if (t.drawn) {
      assert.ok(t.draw && (c.chunks.includes(t.chunk) || t.chunk === 'neocp'), `${w}: drawn needs draw=Y and a chunk`);
      if (t.chunk === 'neocp') {            // live NEOCP object: its track travels only in neocp.js (EFF-6)
        const live = Object.values(D.neocp.objects).find(o => o.cid === t.id);
        assert.ok(live && live.track, `${w}: chunk "neocp" but no neocp.js object with a track`);
        assert.ok(c.chunks.every(ch => !D[ch].targets[t.id]), `${w}: live NEOCP track duplicated in a chunk`);
      } else assert.ok(D[t.chunk].targets[t.id], `${w}: no payload in ${t.chunk}.js`);
      assert.equal(t.not_drawn_reason, undefined, `${w}: drawn with a reason`);
      isIso(t.data.start_utc, `${w}.data.start_utc`); isIso(t.data.end_utc, `${w}.data.end_utc`);
      assert.ok(Array.isArray(t.data.quality) && Array.isArray(t.data.src) && typeof t.data.retrieved_utc === 'string', `${w}.data`);
    } else {
      assert.equal(t.chunk, null, `${w}: not drawn but chunk ${t.chunk}`); assert.ok(!t.default_visible, `${w}: hidden when not drawn`);
      if (t.draw) assert.ok(typeof t.not_drawn_reason === 'string' && t.not_drawn_reason.length > 0, `${w}: draw=Y, not drawn, no reason`);
    }
  }
  const drawn = c.targets.filter(t => t.drawn).length, live = c.targets.filter(t => t.drawn && t.chunk === 'neocp').length;
  const payloads = c.chunks.reduce((n, ch) => n + Object.keys(D[ch].targets).length, 0);
  assert.equal(payloads + live, drawn, 'one payload per drawn target (chunk or neocp.js)');
  assert.ok(drawn > 0, 'at least one drawn target');
});

test('targets-*.js: one clipped, decodable track per drawn target', { skip }, () => {
  const byId = new Map(D.catalog.targets.map(t => [t.id, t]));
  for (const ch of D.catalog.chunks) {
    const p = D[ch];
    assert.equal(p.schema, 'cle/targets@1'); isIso(p.built_utc, `${ch}.built_utc`);
    for (const [id, t] of Object.entries(p.targets)) {
      const w = `${ch}:${id}`, cat = byId.get(id);
      assert.ok(cat && cat.drawn && cat.chunk === ch, `${w}: not a drawn catalog target of this chunk`);
      const d = checkTrack(t.track, w);
      assert.ok(d.t0_ms >= win.t0 - 1 && d.t1_ms <= win.t1 + 1, `${w}: track outside the window`);
      assert.equal(t.track.center, cat.centre === 'moon' ? 'moon' : 'earth', `${w}: centre`);
      assert.equal(Date.parse(cat.data.start_utc), d.t0_ms, `${w}: data.start_utc`); assert.equal(Date.parse(cat.data.end_utc), d.t1_ms, `${w}: data.end_utc`);
      assert.deepEqual(cat.data.quality, [...new Set(d.segments.map(s => s.quality))].sort(), `${w}: data.quality`);
      assert.deepEqual(cat.data.src, [...t.track.srcs].sort(), `${w}: data.src`);
      assert.ok(Array.isArray(t.events), `${w}.events`);
      for (const e of t.events) { isIso(e.t_utc, `${w}.events.t_utc`); isStr(e.kind, `${w}.events.kind`); isStr(e.note, `${w}.events.note`); }
      assert.equal(t.provenance, undefined, `${w}: provenance stays in build/states/<id>.json`);
      assert.ok(t.validation && typeof t.validation === 'object', `${w}.validation`);
      for (const k of ['midpoint_max_km', 'tol_km', 'regime_pass', 'regime_check', 'midpoint_method', 'moon_check_km']) assert.ok(k in t.validation, `${w}.validation.${k}`);
      isStr(t.caveat, `${w}.caveat`); isStr(t.quality_note, `${w}.quality_note`);
    }
  }
});

test('neocp.js: live objects, attached tracklets and the removed list', { skip }, () => {
  const n = D.neocp, ids = new Set(D.catalog.targets.map(t => t.id));
  assert.equal(n.schema, 'cle/neocp@1'); isIso(n.built_utc, 'neocp.built_utc');
  assert.ok(n.neocp_snapshot_utc === null || ISO.test(n.neocp_snapshot_utc), 'neocp_snapshot_utc');
  assert.ok(n.objects && typeof n.objects === 'object' && Array.isArray(n.removed), 'objects / removed');
  const checkTracklets = (list, w) => {
    assert.ok(Array.isArray(list), `${w}.tracklets`);
    for (const tk of list) {
      isStr(tk.stn, `${w}: tracklet stn`); assert.ok(Number.isInteger(tk.n) && tk.n >= 1 && tk.t_ms.length === tk.n, `${w}: tracklet n/t_ms`);
      tk.t_ms.forEach(t => isNum(t, `${w}: tracklet t_ms`));
      assert.equal(decode(tk.dot_km, 'f64').length, tk.n * 3, `${w}: dot_km`); assert.equal(decode(tk.stn_km, 'f64').length, tk.n * 3, `${w}: stn_km`);
    }
  };
  for (const [desig, o] of Object.entries(n.objects)) {
    const w = `neocp ${desig}`, cat = o.catalog;
    assert.ok(cat && typeof cat.id === 'string', `${w}: catalog.id`);
    if (o.cid != null) assert.equal(o.cid, cat.id, `${w}: cid`);
    for (const k of ['regime', 'group', 'obj_type', 'name', 'label']) isStr(cat[k], `${w}.catalog.${k}`);
    for (const s of cat.sources || []) assert.ok(typeof s === 'string' && parseSource(s).kind, `${w}.catalog.sources: compact string`);
    assert.equal(cat.src_rows, undefined, `${w}.catalog: src_rows is gone`);
    assert.ok(Number.isInteger(cat.tier), `${w}.catalog.tier`); assert.ok(cat.default_visible === undefined || typeof cat.default_visible === 'boolean', `${w}.catalog.default_visible`);
    if (o.track) {
      const d = checkTrack(o.track, w);
      assert.ok(d.t0_ms >= win.t0 - 1 && d.t1_ms <= win.t1 + 1, `${w}: track outside the window`);
    }
    if (o.fit) assert.ok(typeof o.fit === 'object', `${w}.fit`);
    if (o.caveat != null) isStr(o.caveat, `${w}.caveat`);
    checkTracklets(o.tracklets || [], w);
  }
  for (const [cid, a] of Object.entries(n.attached || {})) { assert.ok(ids.has(cid), `attached ${cid}: not a catalog target`); checkTracklets(a.tracklets, `attached ${cid}`); }
  for (const r of n.removed) { isStr(r.desig, 'removed.desig'); isIso(r.removed_utc, 'removed.removed_utc'); isStr(r.reason, 'removed.reason'); }
});

// web/assets/hipparcos.json as scripts/build_site.mjs inlines it and web/sky.js unpackStars() reads it (EFF-8): three
// base64 arrays, ra uint16 / dec int16 in units.deg, mag int16 in units.mag; 9,827 stars (tests/browser.mjs).
test('hipparcos.json: packed star catalog', () => {
  const h = JSON.parse(readFileSync(path.join(root, 'web/assets/hipparcos.json'), 'utf8')), S = h.stars;
  assert.ok(h.meta && typeof h.meta.packed === 'string' && S && !Array.isArray(S), 'packed shape');
  assert.equal(S.n, 9827); assert.equal(h.hip_ids.length, S.n);
  isNum(S.units.deg, 'units.deg'); isNum(S.units.mag, 'units.mag'); assert.ok(S.units.deg * 3600 < 30, 'LSB under 30 arcsec');
  const ra = decode(S.ra, 'u16'), dec = decode(S.dec, 'i16'), mag = decode(S.mag, 'i16');
  assert.ok(ra instanceof Uint16Array && dec instanceof Int16Array && mag instanceof Int16Array, 'typed views');
  assert.equal(ra.length, S.n); assert.equal(dec.length, S.n); assert.equal(mag.length, S.n);
  let bright = 0;
  for (let i = 0; i < S.n; i++) {
    const d = dec[i] * S.units.deg, v = mag[i] * S.units.mag;
    assert.ok(ra[i] * S.units.deg < 360 && Math.abs(d) <= 90 && v > -2 && v < 6.6, `star ${i}: ${ra[i]} ${d} ${v}`);
    bright += v < 1.6;
  }
  assert.ok(bright > 10 && bright < 30, `bright stars V<1.6: ${bright}`);
});
