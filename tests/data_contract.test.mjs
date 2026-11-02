// Field-level contract between dist/data/*.js and the page (REVIEW DW-12), checked without a browser: every field
// that web/loader.js, web/model.js, web/app.js and web/ui/*.js read from the cle/*@1 sidecars. Skips when dist/data
// is not built (make build); the viewer fixture has a looser catalog and is covered by tests/browser.mjs. The last test
// checks the fixture's canned answer of the on-request NEOCP helper (build/fixture/request/C46FX01.json, npm run fixture).
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

// Astrometry tracklets as web/scene/overlays.js TrackletLayer and the readout read them (§7, cle.tracklets.build):
// per station and night, light-time epochs t_ms, geocentric ICRF stn_km / dot_km as base64 f64 n×3.
function checkTracklets(list, w) {
  assert.ok(Array.isArray(list), `${w}.tracklets`);
  for (const tk of list) {
    isStr(tk.stn, `${w}: tracklet stn`); assert.ok(Number.isInteger(tk.n) && tk.n >= 1 && tk.t_ms.length === tk.n, `${w}: tracklet n/t_ms`);
    tk.t_ms.forEach(t => isNum(t, `${w}: tracklet t_ms`));
    for (const k of ['ra_deg', 'dec_deg', 'mag', 'range_km', 'resid_arcsec']) assert.equal(tk[k].length, tk.n, `${w}: tracklet ${k}`);
    assert.equal(decode(tk.dot_km, 'f64').length, tk.n * 3, `${w}: dot_km`); assert.equal(decode(tk.stn_km, 'f64').length, tk.n * 3, `${w}: stn_km`);
  }
}

// The master-list groups the viewer pipeline leaves out (src/cle/catalog.py VIEWER_EXCLUDED_GROUPS).
const EXCLUDED = new Set(['NEOCP live', 'NEOCP archive (unidentified apparitions)', 'Natural companions (minimoons, quasi-satellites)']);

test('manifest.js lists every sidecar with its size and sha256', { skip }, () => {
  const m = D.manifest;
  for (const f of ['neocp.js', 'targets-neocp.js']) assert.ok(!files.includes(f) && !(f in m.files), `${f}: retired (NEOCP is on request)`);
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
  for (const g of c.groups) { isStr(g.key, 'group.key'); isNum(g.order, 'group.order'); assert.ok(!EXCLUDED.has(g.key), `group ${g.key}: excluded from the viewer`); }
  assert.ok(Array.isArray(c.chunks) && c.chunks.length > 0);
  for (const ch of c.chunks) assert.ok(ch.startsWith('targets-') && D[ch], `chunk ${ch} has a file`);
  const ids = new Set();
  for (const t of c.targets) {
    const w = `target ${t.id}`;
    isStr(t.id, w); assert.ok(!ids.has(t.id), `${w}: duplicate id`); ids.add(t.id);
    isStr(t.name, `${w}.name`); isStr(t.label, `${w}.label`); isStr(t.group, `${w}.group`); isStr(t.obj_type, `${w}.obj_type`);
    assert.ok(regimes.has(t.regime), `${w}: regime ${t.regime}`);
    assert.ok(!EXCLUDED.has(t.group) && t.obj_type !== 'natural', `${w}: ${t.group} / ${t.obj_type} is excluded from the viewer`);
    assert.equal(t.natural, undefined, `${w}: natural is gone`); isBool(t.draw, `${w}.draw`); isBool(t.drawn, `${w}.drawn`); isBool(t.default_visible, `${w}.default_visible`);
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
      assert.ok(t.draw && c.chunks.includes(t.chunk), `${w}: drawn needs draw=Y and a chunk`);
      assert.ok(D[t.chunk].targets[t.id], `${w}: no payload in ${t.chunk}.js`);
      assert.equal(t.not_drawn_reason, undefined, `${w}: drawn with a reason`);
      isIso(t.data.start_utc, `${w}.data.start_utc`); isIso(t.data.end_utc, `${w}.data.end_utc`);
      assert.ok(Array.isArray(t.data.quality) && Array.isArray(t.data.src) && typeof t.data.retrieved_utc === 'string', `${w}.data`);
    } else {
      assert.equal(t.chunk, null, `${w}: not drawn but chunk ${t.chunk}`); assert.ok(!t.default_visible, `${w}: hidden when not drawn`);
      if (t.draw) assert.ok(typeof t.not_drawn_reason === 'string' && t.not_drawn_reason.length > 0, `${w}: draw=Y, not drawn, no reason`);
    }
  }
  const drawn = c.targets.filter(t => t.drawn).length;
  const payloads = c.chunks.reduce((n, ch) => n + Object.keys(D[ch].targets).length, 0);
  assert.equal(payloads, drawn, 'one chunk payload per drawn target');
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
      if ('tracklets' in t) {           // NEOCP / neocpsat astrometry of an identified target (build/tracklets.json)
        assert.ok(t.tracklets.length > 0, `${w}: an empty tracklets key is omitted`);
        checkTracklets(t.tracklets, w);
        for (const tk of t.tracklets) tk.t_ms.forEach(x => assert.ok(x >= win.t0 - 86400e3 && x <= win.t1, `${w}: tracklet epoch outside the window`));
      }
    }
  }
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

// The on-request NEOCP helper's answer (scripts/serve.py GET /api/neocp/<DESIG>, cle.request.respond) as the fixture
// writes it for tests/browser.mjs to route-fulfil: the track and tracklet contract above, the catalog entry of a
// requested object, and the fit / request fields web/ui/neocp.js shows. tests/test_request.py checks that its key sets
// match a real helper answer.
const reqFile = path.join(root, 'build/fixture/request/C46FX01.json');
test('fixture helper answer (cle/neocp-request@1): track, tracklets, catalog, fit, request', { skip: existsSync(reqFile) ? false : 'fixture not built (npm run fixture)' }, () => {
  const r = JSON.parse(readFileSync(reqFile, 'utf8')), o = r.object, w = `request ${r.desig}`;
  assert.equal(r.ok, true); assert.equal(r.schema, 'cle/neocp-request@1'); assert.match(r.desig, /^[A-Za-z0-9]{5,8}$/);
  isIso(r.built_utc, `${w}.built_utc`); isIso(r.window.start_utc, `${w}.window.start_utc`); isIso(r.window.stop_utc, `${w}.window.stop_utc`);
  const t0 = Date.parse(r.window.start_utc), t1 = Date.parse(r.window.stop_utc);
  assert.ok(t0 < t1 && t1 - t0 <= 45 * 86400e3, `${w}: window`);
  for (const k of ['cid', 'catalog', 'track', 'fit', 'caveat', 'tracklets', 'rms_arcsec', 'request']) assert.ok(k in o, `${w}: object.${k}`);
  const d = checkTrack(o.track, `${w}.track`);
  assert.ok(d.t0_ms >= t0 - 1 && d.t1_ms <= t1 + 1, `${w}: track outside the window`);
  assert.ok(o.tracklets.length > 0, `${w}: tracklets`); checkTracklets(o.tracklets, w);
  for (const tk of o.tracklets) tk.t_ms.forEach(x => assert.ok(x >= t0 && x <= t1, `${w}: tracklet epoch outside the window`));
  isStr(o.caveat, `${w}.caveat`); isNum(o.rms_arcsec, `${w}.rms_arcsec`);
  const c = o.catalog;
  assert.equal(o.cid, r.desig.toLowerCase()); assert.equal(c.id, o.cid); assert.equal(c.name, r.desig); assert.equal(c.label, r.desig);
  assert.equal(c.group, 'NEOCP · requested'); assert.equal(c.chunk, null, `${w}: the track travels in the answer`);
  assert.ok(c.draw === true && c.drawn === true && c.default_visible === true && c.tier === 1, `${w}: drawn, visible, tier 1`);
  assert.equal(c.natural, undefined); isStr(c.regime, `${w}.regime`); isStr(c.status, `${w}.status`); isStr(c.caveat, `${w}.catalog.caveat`);
  const sources = c.sources.map(s => { isStr(s, `${w}.sources`); return parseSource(s); });
  assert.ok(sources[0].primary && sources[0].kind === 'pluto_mpec' && sources[0].ref.endsWith(`/${r.desig}.htm`), `${w}: primary pluto_mpec source`);
  assert.ok(sources.some(s => s.kind === 'neocp_obs'), `${w}: neocp_obs source`);
  assert.ok(Array.isArray(c.links) && c.links.every(l => typeof l.label === 'string' && /^https:\/\//.test(l.url)), `${w}.links`);
  assert.ok(c.trail && Number.isFinite(c.trail.past_s) && Number.isFinite(c.trail.future_s), `${w}.trail`);
  for (const k of ['a_km', 'e', 'i_deg', 'period_d', 'peri_km', 'apo_km']) assert.ok(c.elements[k] === null || typeof c.elements[k] === 'number', `${w}.elements.${k}`);
  const f = o.fit;
  assert.ok(['fo', 'fo_daily', 'page_sv', 'page_table'].includes(f.mode), `${w}: fit.mode ${f.mode}`);
  assert.ok(Number.isInteger(f.n_obs) && f.n_obs > 0, `${w}.fit.n_obs`); isNum(f.arc_d, `${w}.fit.arc_d`); isNum(f.rms_arcsec, `${w}.fit.rms_arcsec`);
  isIso(f.obs_first, `${w}.fit.obs_first`); isIso(f.obs_last, `${w}.fit.obs_last`);
  assert.ok(Array.isArray(f.stations) && f.stations.length > 0 && f.stations.every(s => /^[0-9A-Z]{3}$/.test(s)), `${w}.fit.stations`);
  assert.deepEqual([...new Set(o.tracklets.map(t => t.stn))].sort(), [...f.stations].sort(), `${w}: tracklet stations = fit.stations`);
  const q = o.request;
  assert.equal(q.desig, r.desig); assert.equal(q.mode, f.mode); isIso(q.fetched_utc, `${w}.request.fetched_utc`); isIso(q.page_created_utc, `${w}.request.page_created_utc`);
  for (const k of ['n_obs_mpc', 'n_obs_page', 'n_obs_used']) assert.ok(Number.isInteger(q[k]) && q[k] >= 0, `${w}.request.${k}`);
  isBool(q.on_neocp, `${w}.request.on_neocp`); isBool(q.cached, `${w}.request.cached`); isBool(q.find_orb, `${w}.request.find_orb`);
  isNum(q.seconds, `${w}.request.seconds`); assert.ok(Array.isArray(q.warnings), `${w}.request.warnings`);
  assert.ok(o.tracklets.reduce((n, t) => n + t.n, 0) <= q.n_obs_used, `${w}: more tracklet dots than observations used`);
});
