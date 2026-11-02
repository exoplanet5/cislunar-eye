// NEOCP lookup (web/ui/neocp.js) without a browser: the pure helpers, and NeocpCore (state + network) driven through an
// injected fetch, store, clock and timers. Covers the §A3 network policy (nothing at construction, MPC list after the first
// frame, helper probed only per the rule), one request in flight, × abort, the offline/waiting retry, the 10-min poll
// (refit on a changed "Updated", "left the NEOCP"), persistence, and that network failures never throw.
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  DESIG_RE, MPC_LIST, DEFAULT_HELPER, checkDesig, helperBase, parseUpdated, parseList, sortRows, matchRows, interpret, statusText,
  rowTip, checklist, metaText, reqId, NeocpCore,
} from '../web/ui/neocp.js';

const NOW = Date.parse('2026-11-01T13:00:00Z'), H = 3600e3, D = 24 * H;
const WIN = { t0: NOW - 15 * D, t1: NOW + 15 * D, now: NOW - D };
const plain = s => s.replaceAll(' ', ' ');
const LIST = [
  { Temp_Desig: 'X91537', Score: 100, V: 19.5, Updated: 'Added Nov. 1.50 UT', NObs: 3, Arc: 0.01, Not_Seen_dys: 0.936 },
  { Temp_Desig: 'P22rAEs', Score: 100, V: 19.4, Updated: 'Added Nov. 1.43 UT', NObs: 3, Arc: 0.02, Not_Seen_dys: 0.227 },
  { Temp_Desig: 'CEXJ3W2', Score: 100, V: 99.9, Updated: 'Updated Nov. 1.24 UT', NObs: 18, Arc: 0.05, Not_Seen_dys: 3.103 },
  { Temp_Desig: 'A11ISeO', Score: 10, V: 17.6, Updated: 'Updated Oct. 31.78 UT', NObs: 56, Arc: 7.15, Not_Seen_dys: 0.764 },
  { Temp_Desig: 'ZTF10Gu', Score: 87, V: 21.3, Updated: 'Updated Nov. 1.24 UT', NObs: 3, Arc: 0.02, Not_Seen_dys: 0.2 },
  { Temp_Desig: 'ZTF10GU', Score: 98, V: 20.9, Updated: 'Updated Nov. 1.24 UT', NObs: 4, Arc: 0.03, Not_Seen_dys: 0.3 },
  { Temp_Desig: 'bad desig!', Score: 1 },
];

// ---------- pure helpers ----------

test('designation rule: 5–8 letters and digits after trim, case kept', () => {
  for (const d of ['ZTF10Gu', 'X91537', 'C46FX01', 'P22rAEs', 'abcde', 'ABCDEFGH']) assert.ok(DESIG_RE.test(d), d);
  for (const d of ['ZTF1', 'ABCDEFGHI', 'P22r-Es', '', 'P22 rAE']) assert.ok(!DESIG_RE.test(d), d);
  assert.deepEqual(checkDesig('  ZTF10Gu '), { desig: 'ZTF10Gu', ok: true, bad: false });
  assert.deepEqual(checkDesig('ZTF'), { desig: 'ZTF', ok: false, bad: false }, 'a short prefix is not yet an error');
  assert.equal(checkDesig('ZTF-1').bad, true);
  assert.equal(checkDesig('ABCDEFGHI').bad, true);
  assert.equal(reqId('ZTF10Gu'), 'req-ZTF10Gu');
  assert.notEqual(reqId('ZTF10Gu'), reqId('ZTF10GU'));
});

test('helperBase (§A3): ?helper= > CLE_HELPER > same origin on 127.0.0.1/localhost > default port', () => {
  const at = (href, extra) => { const u = new URL(href); return helperBase({ protocol: u.protocol, hostname: u.hostname, search: u.search }, extra); };
  assert.equal(at('file:///Users/x/dist/index.html'), DEFAULT_HELPER);
  assert.equal(at('http://127.0.0.1:8765/'), '');
  assert.equal(at('http://localhost:9000/index.html'), '');
  assert.equal(at('https://example.org/cle/'), DEFAULT_HELPER);
  assert.equal(at('file:///x/index.html?helper=http://127.0.0.1:9999/'), 'http://127.0.0.1:9999');
  assert.equal(at('file:///x/index.html?helper=javascript:alert(1)'), DEFAULT_HELPER, 'only http(s) URLs are taken');
  globalThis.CLE_HELPER = 'http://10.0.0.2:8765';
  try {
    assert.equal(at('file:///x/index.html'), 'http://10.0.0.2:8765');
    assert.equal(at('file:///x/index.html?helper=http://127.0.0.1:1'), 'http://127.0.0.1:1', '?helper= wins');
    globalThis.CLE_HELPER = '';
    assert.equal(at('file:///x/index.html'), '', "CLE_HELPER '' = same origin");
  } finally { delete globalThis.CLE_HELPER; }
});

test('neocp.json: "Updated" dates, rows, sort (geo first only with flags) and case-sensitive-first prefix filter', () => {
  assert.equal(parseUpdated('Added Nov. 1.50 UT', NOW), Date.parse('2026-11-01T12:00:00Z'));
  assert.equal(parseUpdated('Updated Oct. 31.75 UT', NOW), Date.parse('2026-10-31T18:00:00Z'));
  assert.equal(parseUpdated('Updated Dec. 31.50 UT', Date.parse('2027-01-02T00:00:00Z')), Date.parse('2026-12-31T12:00:00Z'));
  assert.equal(parseUpdated('Updated Sept. 3.00 UT', NOW), Date.parse('2026-09-03T00:00:00Z'));
  assert.ok(Number.isNaN(parseUpdated('soon', NOW)));
  assert.equal(parseList({ not: 'a list' }), null);
  const rows = parseList(LIST, NOW);
  assert.deepEqual(rows.map(r => r.desig), ['X91537', 'P22rAEs', 'CEXJ3W2', 'A11ISeO', 'ZTF10Gu', 'ZTF10GU'], 'invalid designations dropped');
  assert.ok(Number.isNaN(rows[2].v), 'V 99.9 = unknown');
  assert.deepEqual(sortRows(rows).map(r => r.desig), ['X91537', 'P22rAEs', 'CEXJ3W2', 'ZTF10GU', 'ZTF10Gu', 'A11ISeO'], 'score, then the most recent Updated');
  const flags = { ZTF10Gu: { geo: true }, A11ISeO: { geo: true }, X91537: { geo: false } };
  assert.deepEqual(sortRows(rows, flags).map(r => r.desig).slice(0, 2), ['ZTF10Gu', 'A11ISeO'], 'neocp2-geocentric first');
  assert.deepEqual(matchRows(rows, 'ZTF10Gu').map(r => r.desig), ['ZTF10Gu', 'ZTF10GU'], 'exact case first, then the other case');
  assert.deepEqual(matchRows(rows, 'ztf').map(r => r.desig), ['ZTF10Gu', 'ZTF10GU']);
  assert.deepEqual(matchRows(rows, 'Q'), []);
  assert.equal(matchRows(rows, ''), rows);
});

test('helper answers: success, verbatim error messages, retry except bad_designation, non-JSON bodies', () => {
  const object = { track: {}, catalog: { id: 'c46fx01' } };
  assert.deepEqual(interpret(200, { ok: true, object }), { object });
  assert.deepEqual(interpret(404, { ok: false, error: 'not_found', message: 'not on the NEOCP and no neocp2 page' }),
    { error: { code: 'not_found', message: 'not on the NEOCP and no neocp2 page', retry: true } });
  assert.equal(interpret(400, { ok: false, error: 'bad_designation', message: 'x' }).error.retry, false);
  assert.deepEqual(interpret(500, null).error, { code: 'internal', message: 'helper answered HTTP 500', retry: true });
  assert.equal(interpret(200, { ok: false }).error.code, 'bad_response');
  assert.equal(interpret(200, { ok: true, object: { catalog: {} } }).error.message, 'helper answered without a track');
});

const drawnEntry = (request = {}, fit = {}) => ({ desig: 'C46FX01', requested: NOW - 120e3, built: NOW - 60e3, job: null, error: null, left: null,
  object: { request: { n_obs_mpc: 4, on_neocp: true, mode: 'fo', page_created_utc: '2026-11-01T12:35:06Z', ...request },
    fit: { n_obs: 4, rms_arcsec: 0.384, arc_d: 0.0213, stations: ['F52'], obs_first: '2026-11-01T07:05:00Z', obs_last: '2026-11-01T07:21:00Z', ...fit } } });

test('row status line follows the request lifecycle (§A1); atoms never break inside', () => {
  const t = e => plain(statusText(e, NOW).text);
  assert.equal(t({ job: 'queued' }), 'queued');
  assert.equal(t({ job: 'busy', started: NOW - 4200 }), '◌ MPC + neocp2 + fit · 4 s');
  assert.equal(t({ job: 'busy', started: NOW - 9000, auto: 'update' }), '◌ updated on the NEOCP · refit · 9 s');
  // a drawn row is one line (design §3.1); the fetch steps and the fit mode go to the row tooltip (checklist)
  assert.equal(t(drawnEntry()), 'drawn · 4 obs · 0.02 d · rms 0.38″');
  assert.equal(t(drawnEntry({ mode: 'page_sv' })), 'drawn · 4 obs · 0.02 d · rms 0.38″');
  assert.equal(checklist(drawnEntry()), '✓ MPC 4 obs ✓ neocp2 ✓ find_orb');
  assert.equal(checklist(drawnEntry({ mode: 'page_sv' })), '✓ MPC 4 obs ✓ neocp2 ✓ neocp2 vector');
  assert.equal(checklist(drawnEntry({ mode: 'page_table' })).includes('✓ neocp2 table'), true);
  const left = statusText(drawnEntry({ on_neocp: false, n_obs_mpc: 0 }), NOW);
  assert.equal(plain(left.text), 'left the NEOCP · drawn from neocp2 · 0.02 d · rms 0.38″'); assert.equal(left.warn, true);
  assert.equal(plain(statusText({ ...drawnEntry(), left: Date.parse('2026-11-01T12:58:00Z') }, NOW).text), 'left the NEOCP · last listed 12:58');
  const err = statusText({ error: { code: 'not_found', message: 'not on the NEOCP and no neocp2 page', retry: true } }, NOW);
  assert.deepEqual([plain(err.text), err.warn, err.retry], ['not on the NEOCP and no neocp2 page', true, true]);
  const s = statusText(drawnEntry(), NOW).text;
  assert.ok(!/MPC 4|rms 0|0\.02 d/.test(s), 'no plain space inside an atom'); assert.match(s, /4 obs/);
  assert.equal(rowTip(drawnEntry()), 'C46FX01 · requested 12:58 UTC · neocp2 12:35 · fit 12:59 · 4 obs F52 11-01 07:05–07:21 · ✓ MPC 4 obs ✓ neocp2 ✓ find_orb');
});

test('meta line: loading, failed, loaded, no match', () => {
  assert.equal(metaText({ state: 'loading', rows: null }, 0, '').text, 'reading MPC neocp.json …');
  assert.deepEqual([metaText({ state: 'failed', rows: null }, 0, '').text, metaText({ state: 'failed', rows: null }, 0, '').warn], ['MPC list unavailable', true]);
  const L = { state: 'ok', rows: parseList(LIST, NOW), at: Date.parse('2026-11-01T12:58:07Z') };
  assert.equal(metaText(L, 6, '').text, '6 on NEOCP · MPC 12:58');
  assert.equal(metaText(L, 0, 'QQZZ999').text, 'no match · Fetch anyway');
  assert.equal(metaText(L, 0, 'QQ', false).text, 'no match', 'no Fetch hint for an invalid designation');
  assert.equal(metaText({ ...L, state: 'failed', failedAt: NOW }, 6, '').warn, true, 'a failed refresh keeps the rows, in --warn');
});

// ---------- NeocpCore with fakes ----------

function harness({ store = new Map(), probeAtLoad = false, routes = {}, onDrawn } = {}) {
  let now = NOW, seq = 0;
  const timers = new Map(), calls = [], drawn = [], intents = [], removed = [], selected = [], events = [];
  const clock = {
    setTimeout: (f, ms) => { timers.set(++seq, { f, at: now + ms }); return seq; },
    clearTimeout: id => timers.delete(id),
    setInterval: (f, ms) => { timers.set(++seq, { f, at: now + ms, every: ms }); return seq; },
    clearInterval: id => timers.delete(id),
  };
  const advance = async ms => {                                     // run due timers in time order, letting promises settle
    const end = now + ms;
    for (;;) {
      await settle();
      const due = [...timers].filter(([, t]) => t.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
      if (!due) break;
      const [id, t] = due; now = t.at;
      if (t.every) t.at += t.every; else timers.delete(id);
      t.f();
    }
    now = end; await settle();
  };
  const fetch = (url, init) => {
    calls.push(url);
    const route = Object.entries(routes).find(([k]) => url.startsWith(k) || url.includes(k));
    if (!route) return Promise.reject(new TypeError('Failed to fetch'));
    const r = route[1](url, init);
    if (r === 'hang') return new Promise((_, rej) => init.signal.addEventListener('abort', () => rej(new DOMException('aborted', 'AbortError'))));
    if (r instanceof Promise) return r;
    if (r instanceof Error) return Promise.reject(r);
    const [status, body, headers = {}] = r;
    return Promise.resolve({ ok: status >= 200 && status < 300, status, headers: { get: k => headers[k] ?? null },
      json: () => (typeof body === 'string' ? Promise.reject(new SyntaxError('not JSON')) : Promise.resolve(body)) });
  };
  const storage = { getItem: k => store.get(k) ?? null, setItem: (k, v) => store.set(k, String(v)), removeItem: k => store.delete(k) };
  const core = new NeocpCore({ fetch, store: storage, now: () => now, ...clock, raf: f => clock.setTimeout(f, 16), base: 'http://127.0.0.1:8765',
    window: WIN, probeAtLoad,
    onDrawn: onDrawn ?? ((desig, o, intent) => { const T = { id: reqId(desig), visible: true, o }; drawn.push(desig); intents.push([desig, intent]); return T; }),
    onRemove: id => removed.push(id), onSelect: (id, o) => selected.push(o?.async ? id : `sync:${id}`), onChange: () => events.push('change'), onEvent: k => events.push(k) });
  return { core, calls, drawn, intents, removed, selected, events, store, advance, timers, get now() { return now; }, set now(v) { now = v; } };
}
const settle = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); await new Promise(r => setImmediate(r)); };
const HEALTH = () => [200, { ok: true, service: 'cislunar-eye', api: 1, fo: true, now_utc: '2026-11-01T13:00:00Z' }];
const answer = desig => () => [200, { ok: true, schema: 'cle/neocp-request@1', desig, built_utc: '2026-11-01T13:00:05Z', window: {},
  object: { cid: desig.toLowerCase(), catalog: { id: desig.toLowerCase(), name: desig }, track: { segments: [] },
    fit: { n_obs: 4, rms_arcsec: 0.38, arc_d: 0.02, stations: ['F52'] },
    tracklets: [{ stn: 'F52', n: 2, t_ms: [NOW - 6 * H, NOW - 5.9 * H] }, { stn: 'T05', n: 1, t_ms: [NOW - 2 * H] }],
    request: { desig, n_obs_mpc: 4, on_neocp: true, mode: 'fo', fetched_utc: '2026-11-01T13:00:00Z' } } }];
const listRoute = (rows = LIST) => () => [200, rows, { 'last-modified': 'Sun, 01 Nov 2026 12:58:07 GMT' }];

test('§A3: no request at construction; MPC list after the first frame + 500 ms, then every 10 min; no helper probe on file://', async () => {
  const h = harness({ routes: { [MPC_LIST]: listRoute() } });
  assert.deepEqual(h.calls, [], 'constructor');
  h.core.start([]);
  await h.advance(400);
  assert.deepEqual(h.calls, [], 'nothing before the first frame + 500 ms');
  await h.advance(200);
  assert.deepEqual(h.calls, [MPC_LIST]);
  assert.equal(h.core.list.state, 'ok'); assert.equal(h.core.list.rows.length, 6);
  assert.equal(h.core.list.at, Date.parse('2026-11-01T12:58:07Z'), 'Last-Modified is the list time');
  await h.advance(10 * 60e3);
  assert.deepEqual(h.calls, [MPC_LIST, MPC_LIST], 'polled after 10 min; the helper was never probed');
  assert.equal(h.core.helper, 'unknown');
});

test('§A3: the helper is probed on first use (and at load only when asked); flags only from a ready helper', async () => {
  const h = harness({ routes: { '/api/health': HEALTH, '/api/neocp/summary': () => [200, { ok: true, objects: { ZTF10Gu: { geo: true } } }], [MPC_LIST]: listRoute() } });
  h.core.start([]); h.core.touch(); h.core.touch();
  await h.advance(0);
  assert.deepEqual(h.calls, ['http://127.0.0.1:8765/api/health', 'http://127.0.0.1:8765/api/neocp/summary'], 'one probe, then the flags');
  assert.equal(h.core.helper, 'ready'); assert.deepEqual(h.core.flags, { ZTF10Gu: { geo: true } });
  const g = harness({ probeAtLoad: true, routes: { '/api/health': () => [200, { ok: true, service: 'something-else' }] } });
  g.core.start([]);
  await g.advance(0);
  assert.deepEqual(g.calls, ['http://127.0.0.1:8765/api/health']); assert.equal(g.core.helper, 'offline', 'another service on the port is not the helper');
  assert.equal(g.core.flags, null);
});

test('one request in flight; the URL carries the window and the wall clock; drawn rows give obs epochs; × aborts a running job', async () => {
  let release;
  const h = harness({ routes: { '/api/health': HEALTH, '/api/neocp/summary': () => [502, { ok: false, error: 'upstream' }],
    '/api/neocp/ZTF10Gu': () => (release ? answer('ZTF10Gu')() : new Promise(r => { release = r; })), '/api/neocp/P22rAEs': answer('P22rAEs'), '/api/neocp/C46FX01': () => 'hang' } });
  const pA = h.core.request('ZTF10Gu'), pB = h.core.request('P22rAEs');
  assert.equal(h.core.request('ZTF10Gu'), pA, 'a pending request is shared');
  await h.advance(0);
  const req = h.calls.filter(u => u.includes('/api/neocp/') && !u.includes('summary'));
  assert.deepEqual(req, [`http://127.0.0.1:8765/api/neocp/ZTF10Gu?t0=${WIN.t0}&t1=${WIN.t1}&now=${NOW}`], 'the second waits for the first');
  assert.deepEqual([h.core.entries.get('ZTF10Gu').job, h.core.entries.get('P22rAEs').job], ['busy', 'queued']);
  assert.deepEqual(h.core.designations, ['P22rAEs', 'ZTF10Gu'], 'newest first, pending included');
  release({ ok: true, status: 200, headers: { get: () => null }, json: () => Promise.resolve(answer('ZTF10Gu')()[1]) });
  assert.equal((await pA).id, 'req-ZTF10Gu');
  assert.equal((await pB).id, 'req-P22rAEs');
  assert.deepEqual(h.drawn, ['ZTF10Gu', 'P22rAEs']); assert.deepEqual(h.selected, ['req-ZTF10Gu', 'req-P22rAEs'], 'first requests select once drawn');
  assert.deepEqual(h.core.byId('req-P22rAEs').object.tracklets.flatMap(t => t.t_ms).length, 3);
  // × on a running first request: the fetch is aborted and the row disappears; the queue moves on
  const pC = h.core.request('C46FX01'), pD = h.core.request('ZTF10Gu', { select: false });
  await h.advance(0);
  assert.equal(h.core.entries.get('C46FX01').job, 'busy');
  h.core.cancel('C46FX01');
  assert.equal(await pC, null); assert.equal(h.core.entries.has('C46FX01'), false);
  assert.equal((await pD).id, 'req-ZTF10Gu', 'the refetch behind it ran'); assert.equal(h.selected.length, 2, 'a refetch does not select');
  assert.equal(h.core.entries.get('ZTF10Gu').T.id, 'req-ZTF10Gu');
  // outside the window the request carries the build epoch instead of the wall clock
  h.now = WIN.t1 + D;
  assert.match(h.core.requestUrl('ZTF10Gu'), new RegExp(`now=${WIN.now}$`));
});

test('helper down: the row waits ("run make serve"), the helper is re-probed every 15 s, and the row retries when it answers', async () => {
  let up = false;
  const h = harness({ routes: { '/api/health': () => (up ? HEALTH() : new TypeError('Failed to fetch')), '/api/neocp/summary': () => [200, { ok: true, objects: {} }],
    '/api/neocp/C46FX01': answer('C46FX01') } });
  const p = h.core.request('C46FX01');
  assert.equal(await p, null, 'a network failure resolves, never throws');
  const e = h.core.entries.get('C46FX01');
  assert.deepEqual([e.error.code, e.waiting, h.core.helper], ['helper', true, 'offline']);
  assert.equal(plain(statusText(e, NOW).text), 'helper not running: run make serve');
  assert.deepEqual(h.core.designations, ['C46FX01'], 'a waiting row is kept for the URL');
  await h.advance(15e3);
  assert.equal(h.calls.filter(u => u.endsWith('/api/health')).length, 2, 're-probed after 15 s');
  up = true;
  await h.advance(15e3);
  assert.equal(h.core.helper, 'ready'); assert.equal(e.T?.id, 'req-C46FX01'); assert.equal(e.waiting, false);
  assert.deepEqual(h.selected, ['req-C46FX01']);
  await h.advance(60e3);
  assert.equal(h.calls.filter(u => u.endsWith('/api/health')).length, 3, 'no probing once nothing waits');
});

test('errors become row text: helper error codes, a non-JSON 500, a viewer that cannot draw the track, a timeout', async () => {
  const h = harness({ routes: { '/api/health': HEALTH, '/api/neocp/summary': () => [200, { ok: true, objects: {} }],
    '/api/neocp/QQZZ999': () => [404, { ok: false, error: 'not_found', message: 'not on the NEOCP and no neocp2 page' }],
    '/api/neocp/ABCDE12': () => [500, '<html>oops</html>'], '/api/neocp/C46FX01': answer('C46FX01'), '/api/neocp/SLOW123': () => 'hang' },
    onDrawn: () => { throw new Error('bad track segments'); } });
  for (const d of ['QQZZ999', 'ABCDE12', 'C46FX01']) assert.equal(await h.core.request(d), null);
  const msg = d => h.core.entries.get(d).error.message;
  assert.equal(msg('QQZZ999'), 'not on the NEOCP and no neocp2 page');
  assert.equal(msg('ABCDE12'), 'helper answered HTTP 500');
  assert.equal(msg('C46FX01'), 'track not drawable: bad track segments');
  assert.deepEqual(h.core.designations, [], 'failed rows are not persisted');
  assert.equal(await h.core.request('nope!'), null, 'an invalid designation resolves null'); assert.equal(h.core.entries.has('nope!'), false);
  const slow = h.core.request('SLOW123');
  await h.advance(151e3);
  assert.equal(await slow, null); assert.equal(msg('SLOW123'), 'helper timed out');
});

test('10-min poll: a changed "Updated" refits automatically, a drawn object missing from the list has left the NEOCP', async () => {
  let rows = LIST;
  const h = harness({ routes: { [MPC_LIST]: () => [200, rows, { 'last-modified': 'Sun, 01 Nov 2026 12:58:07 GMT' }], '/api/health': HEALTH,
    '/api/neocp/summary': () => [200, { ok: true, objects: {} }], '/api/neocp/P22rAEs': answer('P22rAEs'), '/api/neocp/ZTF10Gu': answer('ZTF10Gu') } });
  h.core.start([]);
  await h.advance(600);
  await h.core.request('P22rAEs'); await h.core.request('ZTF10Gu');
  const n = () => h.calls.filter(u => u.includes('/api/neocp/P22rAEs')).length;
  assert.equal(n(), 1);
  rows = LIST.map(r => (r.Temp_Desig === 'P22rAEs' ? { ...r, Updated: 'Updated Nov. 1.55 UT', NObs: 5 } : r)).filter(r => r.Temp_Desig !== 'ZTF10Gu');
  await h.advance(10 * 60e3);
  assert.equal(n(), 2, 'refit after the Updated change');
  // the app learns which answers are the user's (select: moves the clock in) and which are background refits (keep the
  // user's selection, visibility, clock and playback)
  assert.deepEqual(h.intents, [['P22rAEs', { select: true, auto: null }], ['ZTF10Gu', { select: true, auto: null }], ['P22rAEs', { select: false, auto: 'update' }]]);
  assert.deepEqual(h.selected, ['req-P22rAEs', 'req-ZTF10Gu'], 'the refit selects nothing; a finished request selects asynchronously');
  const p = h.core.entries.get('P22rAEs'), z = h.core.entries.get('ZTF10Gu');
  assert.equal(p.job, null); assert.equal(p.left, null);
  assert.equal(z.left, Date.parse('2026-11-01T12:58:07Z'));
  assert.equal(plain(statusText(z, h.now).text), 'left the NEOCP · last listed 12:58');
  assert.ok(z.T, 'the drawn result is kept');
  rows = LIST;
  await h.advance(10 * 60e3);
  assert.equal(z.left, null, 'listed again');
});

test('persistence: cle.neocp is restored at load (quietly, probing the helper), written on change, and cleared', async () => {
  const store = new Map([['cle.neocp', 'P22rAEs,ZTF10Gu,bad!,P22rAEs']]);
  const h = harness({ store, routes: { '/api/health': HEALTH, '/api/neocp/summary': () => [200, { ok: true, objects: {} }],
    '/api/neocp/P22rAEs': answer('P22rAEs'), '/api/neocp/ZTF10Gu': answer('ZTF10Gu') } });
  h.core.start([]);
  assert.deepEqual(h.core.order, ['P22rAEs', 'ZTF10Gu'], 'order kept, invalid and duplicate names dropped');
  await h.advance(0);
  assert.equal(h.calls[0], 'http://127.0.0.1:8765/api/health', 'persisted designations probe the helper at load');
  assert.deepEqual(h.drawn, ['P22rAEs', 'ZTF10Gu']); assert.deepEqual(h.selected, [], 'restored objects are not selected');
  assert.equal(store.get('cle.neocp'), 'P22rAEs,ZTF10Gu');
  h.core.remove('P22rAEs');
  assert.deepEqual(h.removed, ['req-P22rAEs']); assert.equal(store.get('cle.neocp'), 'ZTF10Gu');
  h.core.clear();
  assert.equal(store.has('cle.neocp'), false); assert.deepEqual(h.removed, ['req-P22rAEs', 'req-ZTF10Gu']);
  const u = harness({ store: new Map([['cle.neocp', 'P22rAEs']]), routes: {} });
  u.core.start(['ZTF10Gu']);
  assert.deepEqual(u.core.order, ['ZTF10Gu'], 'URL designations win over localStorage');
  await u.advance(0);
  u.core.destroy();
  await u.advance(60 * 60e3);
  assert.equal(u.timers.size, 0, 'destroy() clears every timer');
});
