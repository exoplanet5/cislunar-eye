// NEOCP lookup dock (design §3.1; front-end spec §A1–A4). A temporary designation, typed or picked from MPC's
// neocp.json, goes to the local helper (scripts/serve.py: MPC observations + Bill Gray's neocp2 orbit → find_orb fit →
// track and tracklets over the data window); app.js draws the answer as a requested target (opts.onDrawn).
// Network policy (§A3): nothing at construction; MPC neocp.json once after the first frame (+500 ms), then every 10 min;
// the helper's /api/health at load only when the page is its own (same origin) or designations were persisted, else on
// the first use of the dock; neocp2 flags only from a ready helper. One request in flight; × aborts it. Network failures
// become dock text: nothing here throws or writes to the console.
// NeocpCore holds the state and the network (DOM-free, tests/neocp.test.mjs); NeocpLookup renders it and takes input.
import { escapeHtml, rangeText, utc } from './format.js';

export const DESIG_RE = /^[A-Za-z0-9]{5,8}$/;
export const MPC_LIST = 'https://www.minorplanetcenter.net/Extended_Files/neocp.json';
export const DEFAULT_HELPER = 'http://127.0.0.1:8765';
export const HELPER_DOWN = { code: 'helper', message: 'helper not running: run make\u00a0serve', retry: true };
const POLL_MS = 600e3, PROBE_EVERY_MS = 15e3, LIST_DELAY_MS = 500, LIST_TIMEOUT_MS = 20e3, PROBE_TIMEOUT_MS = 4e3,
  REQUEST_TIMEOUT_MS = 150e3, FLASH_MS = 600, KEEP = 12;
const STORE = 'cle.neocp', SHORT = 'cle.neocp.short';
const MODE = { fo: 'find_orb', fo_daily: 'find_orb', page_sv: 'neocp2 vector', page_table: 'neocp2 table' };
const MONTHS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'];
const NB = ' ', SEP = `${NB}· `;                  // status atoms never break inside; lines break after a '·'

export const reqId = desig => `req-${desig}`;
const hhmm = ms => (Number.isFinite(ms) ? utc(ms).slice(11, 16) : '—');
const atoms = (...parts) => parts.filter(Boolean).map(p => p.replaceAll(' ', NB)).join(SEP);
const num = x => (x === null || x === undefined || x === '' ? NaN : Number(x));
export const days = x => (!Number.isFinite(x) ? '—' : x < 10 ? x.toFixed(2) : x < 100 ? x.toFixed(1) : String(Math.round(x)));

// ---------- pure helpers ----------

export const servedLocally = loc => /^https?:$/.test(loc?.protocol || '') && /^(127\.0\.0\.1|localhost|\[::1\])$/.test(loc?.hostname || '');

// §A3: ?helper=<url> first, then window.CLE_HELPER (template boot snippet; '' = same origin), else same origin when the
// page is served from 127.0.0.1/localhost, else the default port. Only http(s) URLs are taken.
export function helperBase(loc = globalThis.location, params = new URLSearchParams(loc?.search || '')) {
  for (const v of [params.get('helper'), globalThis.CLE_HELPER]) {
    if (typeof v === 'string' && /^https?:\/\/[^\s/?#]+/i.test(v.trim())) return v.trim().replace(/\/+$/, '');
  }
  if (globalThis.CLE_HELPER === '') return '';
  return servedLocally(loc) ? '' : DEFAULT_HELPER;
}

// Trimmed designation, its validity (§A2: 5–8 letters and digits, case kept) and whether it already holds a bad character.
export function checkDesig(raw) {
  const desig = String(raw ?? '').trim();
  return { desig, ok: DESIG_RE.test(desig), bad: /[^A-Za-z0-9]/.test(desig) || desig.length > 8 };
}

// neocp.json "Updated" → ms: "Added Nov. 1.50 UT" = Nov 1 12:00 UTC of the year that puts it at most 60 d ahead of ref.
export function parseUpdated(text, ref = Date.now()) {
  const m = /^\s*\S+\s+([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2}(?:\.\d+)?)/.exec(String(text ?? ''));
  const mon = m ? MONTHS.indexOf(m[1].toLowerCase()) : -1;
  if (mon < 0) return NaN;
  const at = y => Date.UTC(y, mon, 1) + (parseFloat(m[2]) - 1) * 86400e3, y = new Date(ref).getUTCFullYear();
  return at(y) > ref + 60 * 86400e3 ? at(y - 1) : at(y);
}

// MPC neocp.json → rows (V 99.9 = unknown); null when the payload is not a list.
export function parseList(json, ref = Date.now()) {
  if (!Array.isArray(json)) return null;
  const rows = [], seen = new Set();
  for (const r of json) {
    const desig = String(r?.Temp_Desig ?? '').trim(), v = num(r?.V);
    if (!DESIG_RE.test(desig) || seen.has(desig)) continue;
    seen.add(desig);
    rows.push({ desig, score: num(r.Score), v: v >= 99 ? NaN : v, updated: String(r.Updated ?? ''), t_upd: parseUpdated(r.Updated, ref),
      nobs: num(r.NObs), arc: num(r.Arc), seen: num(r.Not_Seen_dys) });
  }
  return rows;
}

// Shortlist order: neocp2-geocentric first (only with flags), then Score, then the most recent Updated, then name.
export function sortRows(rows, flags = null) {
  const geo = r => (flags?.[r.desig]?.geo === true ? 1 : 0), v = x => (Number.isFinite(x) ? x : -1e9);
  return [...rows].sort((a, b) => geo(b) - geo(a) || v(b.score) - v(a.score) || v(b.t_upd) - v(a.t_upd) || (a.desig < b.desig ? -1 : 1));
}

// Prefix filter: case-sensitive matches first, then the case-insensitive ones (ZTF10Gu and ZTF10GU are different objects).
export function matchRows(rows, q) {
  if (!q) return rows;
  const lo = q.toLowerCase(), exact = rows.filter(r => r.desig.startsWith(q));
  return [...exact, ...rows.filter(r => !r.desig.startsWith(q) && r.desig.toLowerCase().startsWith(lo))];
}

// Helper answer → {object} or {error:{code, message, retry}}; the helper's message is shown verbatim (§A1).
export function interpret(status, body) {
  const o = body?.object, ok2xx = status >= 200 && status < 300;
  if (ok2xx && body?.ok === true && o?.track && o?.catalog) return { object: o };
  const code = typeof body?.error === 'string' ? body.error : ok2xx ? 'bad_response' : status >= 500 ? 'internal' : 'http';
  const message = typeof body?.message === 'string' && body.message.trim() ? body.message.trim().slice(0, 120)
    : code === 'bad_response' ? 'helper answered without a track' : `helper answered HTTP ${status}`;
  return { error: { code, message, retry: code !== 'bad_designation' } };
}

// The fetch steps a drawn object went through: "✓ MPC 4 obs ✓ neocp2 ✓ find_orb" (row tooltip; the TARGET card says
// the same in its NEOCP REQUEST rows).
export function checklist(e) {
  const r = e.object?.request || {}, f = e.object?.fit || {};
  return `✓ MPC ${r.n_obs_mpc ?? f.n_obs ?? '?'} obs ✓ neocp2 ✓ ${MODE[r.mode] || r.mode || 'fit'}`;
}

// `.st` line of a requested row (§A1 lifecycle): {text, warn, retry}. Atoms are joined with no-break spaces so a
// 280 px panel wraps only between them; a drawn row is one line, "drawn · 4 obs · 0.02 d · rms 0.38″" (design §3.1),
// so four rows fit the 160 px box.
export function statusText(e, now = Date.now()) {
  if (e.job === 'queued') return { text: atoms(e.auto ? 'updated on the NEOCP' : '', 'queued') };
  if (e.job === 'busy') {
    const s = Math.max(0, Math.floor((now - e.started) / 1000));
    return { text: atoms(e.auto ? '◌ updated on the NEOCP' : '◌ MPC + neocp2 + fit', e.auto ? 'refit' : '', `${s} s`) };
  }
  if (e.error) return { text: e.error.message, warn: true, retry: e.error.retry };       // the helper's sentence wraps freely
  const r = e.object?.request || {}, f = e.object?.fit || {}, n = f.n_obs ?? r.n_obs_used ?? r.n_obs_mpc;
  const stats = [Number.isFinite(f.arc_d) && `${days(f.arc_d)} d`, Number.isFinite(f.rms_arcsec) && `rms ${f.rms_arcsec.toFixed(2)}″`];
  if (e.left != null) return { text: atoms('left the NEOCP', e.left > 0 && `last listed ${hhmm(e.left)}`), warn: true };
  if (r.on_neocp === false) return { text: atoms('left the NEOCP', 'drawn from neocp2', ...stats), warn: true };
  return { text: atoms('drawn', n != null && `${n} obs`, ...stats) };
}

// Row tooltip: "P22rAEs · requested 13:01 UTC · neocp2 12:35 · fit 13:02 · 3 obs F52 11-01 07:05–07:21 · ✓ MPC 3 obs
// ✓ neocp2 ✓ find_orb".
export function rowTip(e) {
  const r = e.object?.request || {}, f = e.object?.fit || {}, a = Date.parse(f.obs_first), b = Date.parse(f.obs_last);
  const span = Number.isFinite(a) && Number.isFinite(b) ? `${utc(a).slice(5, 16)}–${utc(b).slice(utc(a).slice(0, 10) === utc(b).slice(0, 10) ? 11 : 5, 16)}` : '';
  const obs = e.object && [`${f.n_obs ?? r.n_obs_used ?? '?'} obs`, (f.stations || []).join(' '), span].filter(Boolean).join(' ');
  return [e.desig, `requested ${hhmm(e.requested)} UTC`, r.page_created_utc && `neocp2 ${hhmm(Date.parse(r.page_created_utc))}`,
    e.object && `fit ${hhmm(e.built)}`, obs, e.object && checklist(e), e.object && e.error && `refetch failed: ${e.error.message}`].filter(Boolean).join(' · ');
}

// Meta line under the form: {text, warn, title}.
export function metaText(list, shown, query, valid = true) {
  if (!list.rows) {
    if (list.state === 'failed') return { text: 'MPC list unavailable', warn: true, title: 'MPC neocp.json could not be read; ↻ tries again. Fetch still works.' };
    return { text: 'reading MPC neocp.json …', title: 'MPC neocp.json, the objects on the NEOCP now' };
  }
  const title = `${list.rows.length} objects on the NEOCP · MPC neocp.json ${hhmm(list.at)} UTC`
    + (list.state === 'failed' ? ` · the refresh at ${hhmm(list.failedAt)} UTC failed` : '');
  if (query && !shown) return { text: valid ? 'no match · Fetch anyway' : 'no match', title: `no object on the NEOCP list starts with '${query}'` };
  return { text: `${list.rows.length} on NEOCP · MPC ${hhmm(list.at)}`, warn: list.state === 'failed', title };
}

export function helperText(state, host) {
  return {
    unknown: { text: 'helper', title: `local helper (make serve) on ${host}: not checked yet, used on Fetch` },
    checking: { text: 'helper …', title: `checking the local helper on ${host}` },
    ready: { text: 'helper ready', title: `local helper on ${host}` },
    offline: { text: 'helper offline', title: `no answer from the local helper on ${host}: run make serve` },
  }[state];
}

function localStore() { try { return globalThis.localStorage ?? null; } catch { return null; } }
const readKey = (store, k) => { try { return store?.getItem(k) ?? null; } catch { return null; } };
const writeKey = (store, k, v) => { try { v == null ? store?.removeItem(k) : store?.setItem(k, v); } catch { /* storage refused */ } };

// ---------- state and network ----------

export class NeocpCore {
  // o: fetch, store, now, setTimeout/clearTimeout/setInterval/clearInterval, raf, base, window {t0,t1,now},
  //    probeAtLoad, onDrawn(desig, object) → T|null, onRemove(id), onSelect(id), onChange(), onEvent(kind)
  constructor(o = {}) {
    const g = globalThis;
    this.o = o;
    this.fetch = o.fetch ?? (g.fetch ? (...a) => g.fetch(...a) : null);
    this.store = o.store !== undefined ? o.store : localStore();
    this.now = o.now ?? Date.now;
    this.st = o.setTimeout ?? ((f, ms) => g.setTimeout(f, ms)); this.ct = o.clearTimeout ?? (id => g.clearTimeout(id));
    this.si = o.setInterval ?? ((f, ms) => g.setInterval(f, ms)); this.ci = o.clearInterval ?? (id => g.clearInterval(id));
    this.raf = o.raf ?? (g.requestAnimationFrame ? f => g.requestAnimationFrame(f) : f => this.st(f, 0));
    this.base = o.base ?? helperBase();
    this.win = o.window ?? {};
    this.helper = 'unknown';                                    // unknown → checking → ready | offline
    this.entries = new Map(); this.order = [];                  // desig → entry; designations newest first
    this.list = { state: 'idle', rows: null, at: NaN, failedAt: NaN };
    this.flags = null;
    this.chain = Promise.resolve(); this.timers = {}; this.probing = null; this.probedAt = -Infinity; this.flagsBusy = false; this.dead = false;
  }

  emit(kind) { if (!this.dead) this.o.onEvent?.(kind); }
  changed() {
    if (this.dead) return;
    writeKey(this.store, STORE, this.designations.join(',') || null);
    this.emit('entries'); this.o.onChange?.();
  }
  ordered() { return this.order.map(d => this.entries.get(d)); }
  byId(id) { return this.ordered().find(e => e.id === id) || null; }
  get designations() { return this.order.filter(d => { const e = this.entries.get(d); return e.T || e.job || e.waiting; }); }
  get busy() { return this.ordered().some(e => e.job === 'busy'); }

  // Restore the persisted designations (URL first, else localStorage), schedule the MPC list, probe per §A3.
  start(desigs = []) {
    const given = desigs.length ? desigs : (readKey(this.store, STORE) || '').split(',');
    const keep = [...new Set(given.map(d => checkDesig(d)).filter(c => c.ok).map(c => c.desig))].slice(0, KEEP);
    for (const d of [...keep].reverse()) this.add(d);
    for (const d of keep) this.request(d, { quiet: true });
    if (!keep.length && this.o.probeAtLoad) this.probe();
    this.raf(() => {
      if (this.dead) return;
      this.timers.start = this.st(() => { this.loadList(); this.timers.poll = this.si(() => this.poll(), POLL_MS); }, LIST_DELAY_MS);
    });
  }

  poll() { this.loadList(); if (this.helper === 'ready') this.loadFlags(); }
  touch() { if (this.helper === 'unknown') this.probe(); }       // first use of the dock

  add(desig) {
    let e = this.entries.get(desig);
    if (!e) {
      e = { desig, id: reqId(desig), requested: this.now(), T: null, object: null, built: NaN, job: null, started: NaN, auto: null,
        ac: null, error: null, waiting: false, listed: this.listedNow(desig), left: null, select: false, promise: null, seq: 0 };
      this.entries.set(desig, e); this.order.unshift(desig);
    }
    return e;
  }

  listedNow(desig) {
    const r = this.list.rows?.find(x => x.desig === desig);
    return r ? { updated: r.updated, at: this.list.at } : null;
  }

  // Queue a (re)fetch; one runs at a time. A pending request for the same designation is shared. select: select the
  // object once drawn (default: a first, user-initiated request); quiet: restored at load; auto: 'update' (NEOCP poll).
  request(desig, { select, auto = null, quiet = false } = {}) {
    const c = checkDesig(desig);
    if (!c.ok || this.dead) return Promise.resolve(null);
    const e = this.add(c.desig);
    if (e.promise) return e.promise;
    Object.assign(e, { select: select ?? (!e.T && !quiet), auto, waiting: false, error: null, job: 'queued' });
    const seq = ++e.seq, p = this.chain.then(() => this.run(e, seq));
    this.chain = p; e.promise = p;
    p.then(() => { if (e.promise === p) e.promise = null; });
    this.changed();
    return p;
  }

  async run(e, seq) {
    if (this.dead || this.entries.get(e.desig) !== e || e.seq !== seq || e.job !== 'queued') return null;
    e.job = 'busy'; e.started = this.now(); this.ticking(); this.changed();
    try {
      if (!(await this.ready())) return this.fail(e, seq, HELPER_DOWN, true);
      if (e.seq !== seq) return null;
      const ac = e.ac = new AbortController();
      const r = await this.get(this.requestUrl(e.desig), REQUEST_TIMEOUT_MS, ac.signal);
      if (e.ac === ac) e.ac = null;
      if (r.error === 'cancel' || e.seq !== seq) return null;
      if (r.error === 'network') { this.setHelper('offline'); return this.fail(e, seq, HELPER_DOWN, true); }
      if (r.error === 'timeout') return this.fail(e, seq, { code: 'timeout', message: 'helper timed out', retry: true });
      const out = interpret(r.status, r.body);
      if (out.error) return this.fail(e, seq, out.error);
      let T = null, why = '';
      // intent: select = a user's request (the app moves the clock into the track); otherwise a refit (auto / ↻)
      try { T = this.o.onDrawn?.(e.desig, out.object, { select: e.select, auto: e.auto }) ?? null; } catch (err) { why = String(err?.message || err); }
      if (!T) return this.fail(e, seq, { code: 'draw', message: `track not drawable${why ? `: ${why}` : ''}`.slice(0, 80), retry: true });
      Object.assign(e, { T, object: out.object, built: Date.parse(r.body.built_utc) || this.now(), job: null, auto: null, error: null,
        waiting: false, left: null, listed: this.listedNow(e.desig) ?? e.listed });
      this.changed();
      if (e.select) this.o.onSelect?.(e.id, { async: true });   // the answer came later: the app keeps other sheets open
      return T;
    } catch (err) {                                              // a viewer callback failed: say so in the row
      return this.fail(e, seq, { code: 'internal', message: `viewer error: ${err?.message || err}`.slice(0, 80), retry: true });
    } finally { this.ticking(); }
  }

  fail(e, seq, error, waiting = false) {
    if (e.seq !== seq || this.entries.get(e.desig) !== e) return null;
    Object.assign(e, { job: null, auto: null, error, waiting });
    if (waiting) this.scheduleProbe();
    this.changed();
    return null;
  }

  // × on a running or queued row: abort; a first request disappears, a refetch keeps the drawing it had.
  cancel(desig) {
    const e = this.entries.get(desig);
    if (!e?.job) return;
    e.seq++; e.ac?.abort(); Object.assign(e, { ac: null, job: null, auto: null, promise: null });
    if (e.T) this.changed(); else this.drop([e]);
  }

  remove(desig) { const e = this.entries.get(desig); if (e) this.drop([e]); }
  clear() { this.drop(this.ordered()); }
  drop(list) {
    for (const e of list) {
      e.seq++; e.ac?.abort();
      this.entries.delete(e.desig); this.order = this.order.filter(d => d !== e.desig);
      if (e.T) this.o.onRemove?.(e.id);
    }
    this.changed();
  }

  requestUrl(desig) {
    const { t0, t1, now } = this.win, wall = this.now(), at = wall >= t0 && wall <= t1 ? wall : now;
    const q = [['t0', t0], ['t1', t1], ['now', at]].filter(([, v]) => Number.isFinite(v)).map(([k, v]) => `${k}=${Math.round(v)}`).join('&');
    return `${this.base}/api/neocp/${encodeURIComponent(desig)}${q ? `?${q}` : ''}`;
  }

  setHelper(s) {
    if (this.helper === s) return;
    this.helper = s;
    if (s === 'offline') this.flags = null;                      // no stale neocp2 flags while the helper is down
    this.emit('helper');
  }

  // Ready helper, or a fresh probe (a queue of rows behind a dead helper shares one probe).
  async ready() {
    if (this.helper === 'ready') return true;
    if (this.helper === 'offline' && this.now() - this.probedAt < 3e3) return false;
    return this.probe();
  }

  probe() {
    if (this.probing) return this.probing;
    if (this.helper === 'unknown') this.setHelper('checking');
    this.probing = this.get(`${this.base}/api/health`, PROBE_TIMEOUT_MS).then(r => {
      this.probing = null; this.probedAt = this.now();
      const ok = r.ok && r.body?.ok === true && r.body?.service === 'cislunar-eye';
      this.setHelper(ok ? 'ready' : 'offline');
      if (ok) { this.loadFlags(); this.retryWaiting(); } else this.scheduleProbe();
      return ok;
    });
    return this.probing;
  }

  // While a row waits for the helper, probe every 15 s; when it answers, the waiting rows go again.
  scheduleProbe() {
    if (this.dead || this.timers.probe || !this.ordered().some(e => e.waiting)) return;
    this.timers.probe = this.st(() => { this.timers.probe = null; if (this.ordered().some(e => e.waiting)) this.probe(); }, PROBE_EVERY_MS);
  }
  retryWaiting() { for (const e of this.ordered()) if (e.waiting && !e.job) this.request(e.desig, { select: e.select }); }

  // A 1 s tick while a request runs, for the elapsed seconds in its row.
  ticking() {
    const on = this.busy && !this.dead;
    if (on && !this.timers.tick) this.timers.tick = this.si(() => this.emit('tick'), 1000);
    else if (!on && this.timers.tick) { this.ci(this.timers.tick); this.timers.tick = null; }
  }

  async loadList() {
    if (this.dead || this.list.state === 'loading') return;
    const prev = this.list;
    this.list = { ...prev, state: 'loading' }; this.emit('list');
    const r = await this.get(MPC_LIST, LIST_TIMEOUT_MS, null, { cache: 'no-cache' }), now = this.now();
    const rows = r.ok ? parseList(r.body, now) : null;
    if (!rows) { this.list = { ...prev, state: 'failed', failedAt: now }; this.emit('list'); return; }
    const lm = Date.parse(r.modified || '');
    this.list = { state: 'ok', rows, at: Number.isFinite(lm) ? lm : now, failedAt: NaN };
    this.reconcile(rows); this.emit('list');
  }

  // Each list read: a changed "Updated" refits the object; a drawn object missing from the list has left the NEOCP.
  reconcile(rows) {
    const by = new Map(rows.map(r => [r.desig, r]));
    let dirty = false;
    for (const e of this.ordered()) {
      const r = by.get(e.desig);
      if (r) {
        const updated = e.listed && e.listed.updated !== r.updated;
        e.listed = { updated: r.updated, at: this.list.at };
        if (e.left != null) { e.left = null; dirty = true; }
        if (updated && !e.job && !e.waiting && (e.T || e.error?.retry)) this.request(e.desig, { auto: 'update', select: false });
      } else if (e.T && e.left == null && !e.job) { e.left = e.listed?.at ?? 0; dirty = true; }
    }
    if (dirty) this.changed();
  }

  async loadFlags() {
    if (this.dead || this.helper !== 'ready' || this.flagsBusy) return;
    this.flagsBusy = true;
    const r = await this.get(`${this.base}/api/neocp/summary`, LIST_TIMEOUT_MS);
    this.flagsBusy = false;
    if (r.error === 'network') { this.setHelper('offline'); return; }
    const objects = r.ok && r.body?.ok === true ? r.body.objects : null;
    if (objects && typeof objects === 'object' && this.helper === 'ready') { this.flags = objects; this.emit('flags'); }
  }

  // fetch that never throws: {ok, status, body, modified} or {error: 'network'|'timeout'|'cancel'}.
  async get(url, ms, outer = null, init = {}) {
    if (!this.fetch) return { ok: false, status: 0, body: null, error: 'network' };
    let timedOut = false;
    const ac = new AbortController(), stop = () => ac.abort(), timer = this.st(() => { timedOut = true; ac.abort(); }, ms);
    outer?.addEventListener('abort', stop);
    try {
      const res = await this.fetch(url, { ...init, signal: ac.signal, credentials: 'omit' });
      let body = null;
      try { body = await res.json(); } catch { /* not JSON: interpret() reports the status */ }
      if (ac.signal.aborted) throw new Error('aborted');
      return { ok: res.ok, status: res.status, body, modified: res.headers?.get?.('last-modified') ?? null };
    } catch {
      return { ok: false, status: 0, body: null, error: outer?.aborted ? 'cancel' : timedOut ? 'timeout' : 'network' };
    } finally { this.ct(timer); outer?.removeEventListener('abort', stop); }
  }

  destroy() {
    this.dead = true;
    for (const e of this.ordered()) { e.seq++; e.ac?.abort(); }
    this.ct(this.timers.start); this.ct(this.timers.probe); this.ci(this.timers.poll); this.ci(this.timers.tick);
    this.timers = {};
  }
}

// ---------- view ----------

const HEAD = ['<span class="sr">state</span>', 'desig', 'flag', 'scr', 'V', 'obs', 'arc', 'seen'];     // units in HEAD_TIP
// Columns (· desig flag scr V obs arc seen) of the shortlist table and the dropdown grid; FLAG only with neocp2 flags.
const KEYS = ['k-m', 'k-d', 'k-f', 'k-s num', 'k-v num', 'k-o num', 'k-a num', 'k-n num'];
const withFlag = flags => (_, i) => !!flags || i !== 2;
const HEAD_TIP = ['', 'temporary designation', 'neocp2: geocentric orbit fits best', 'NEOCP score', 'V magnitude', 'observations', 'arc, days', 'days since last seen'];
const DOCK = `<div class="ph"><h2 id="neocp-title"><i class="dot" aria-hidden="true"></i>NEOCP lookup</h2><span id="helper-status" class="helper" role="status"></span></div>
<form id="neocp-form" class="neo-form" novalidate>
<div class="field"><input id="neocp-input" type="text" role="combobox" aria-controls="neocp-suggest" aria-expanded="false" aria-autocomplete="list"
 aria-label="NEOCP temporary designation" aria-describedby="neocp-note" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false" maxlength="8"
 placeholder="desig., e.g. ZTF10Go"><button type="button" id="neocp-suggest-toggle" tabindex="-1" aria-label="Objects on the NEOCP now" aria-controls="neocp-suggest" aria-expanded="false">▾</button></div>
<button type="submit" id="neocp-fetch" class="primary" title="MPC observations + neocp2 orbit → find_orb fit, drawn over the data window (Enter)">Fetch</button>
</form>
<p id="neocp-note" class="neo-note" hidden></p>
<div id="neocp-meta" class="neo-meta"><span class="mt"></span><span class="mb"><button type="button" id="neocp-geo-only" class="chip" aria-pressed="false" title="Only objects whose neocp2 best fit is geocentric" hidden>geo only</button><button type="button" id="neocp-refresh" class="ghost" title="Re-read MPC neocp.json" aria-label="Re-read the MPC NEOCP list">↻</button><button type="button" id="neocp-short-toggle" class="ghost" aria-controls="neocp-short"></button></span></div>
<table id="neocp-short" class="sug"><caption class="sr">Objects on the NEOCP now: click to fill, double-click or Enter to fetch</caption><colgroup></colgroup><thead></thead><tbody></tbody>
<tfoot hidden><tr><td><button type="button" class="all" aria-controls="neocp-suggest"></button></td></tr></tfoot></table>
<div id="neocp-suggest" class="sug" role="listbox" aria-label="Objects on the NEOCP now" hidden></div>
<div class="req-h" hidden><span>Requested · <span id="neocp-req-count">0</span><span class="rq-more" hidden></span></span><button type="button" id="neocp-clear" class="ghost mini" title="Remove every requested object">clear all</button></div>
<div id="neocp-requested" class="req" role="list" aria-label="Requested NEOCP objects"></div>
<p id="neocp-live" class="sr" role="status" aria-live="polite"></p>`;

export class NeocpLookup {
  constructor(root, opts = {}) {
    const loc = globalThis.location;
    this.root = root; this.o = opts;
    this.base = opts.base ?? helperBase(loc);
    this.host = this.base ? this.base.replace(/^https?:\/\//, '') : loc?.host || '127.0.0.1:8765';
    this.core = new NeocpCore({ ...opts, base: this.base, probeAtLoad: opts.probeAtLoad ?? (this.base === '' && servedLocally(loc)),
      onEvent: kind => this.paint(kind) });
    // picked: the designation the field holds from the list (the dropdown's highlight follows it, not a row index, so a
    // re-sort under the user — neocp2 flags arriving, the 10-min re-read — never moves it); said: what #neocp-live told.
    this.query = ''; this.open = false; this.geoOnly = false; this.tried = false; this.picked = null; this.said = new Map(); this.shortCur = null;
    this.shortOpen = readKey(this.core.store, SHORT) !== 'closed';
    root.classList.add('neo');
    if (!root.hasAttribute('aria-label')) root.setAttribute('aria-labelledby', 'neocp-title');
    root.innerHTML = DOCK;
    const q = s => root.querySelector(s);
    this.el = { status: q('#helper-status'), form: q('#neocp-form'), field: q('.field'), input: q('#neocp-input'), toggle: q('#neocp-suggest-toggle'),
      fetch: q('#neocp-fetch'), note: q('#neocp-note'), meta: q('#neocp-meta .mt'), geo: q('#neocp-geo-only'), refresh: q('#neocp-refresh'),
      shortToggle: q('#neocp-short-toggle'), short: q('#neocp-short'), body: q('#neocp-short tbody'), foot: q('#neocp-short tfoot'),
      more: q('#neocp-short .all'), pop: q('#neocp-suggest'), reqH: q('.req-h'), count: q('#neocp-req-count'), clear: q('#neocp-clear'),
      req: q('#neocp-requested'), over: q('.rq-more'), live: q('#neocp-live') };
    this.outside = e => { if (!this.el.pop.contains(e.target) && !this.el.field.contains(e.target)) this.close(); };
    this.wire();
    this.paint('all');
  }

  init(desigs = []) { this.core.start(desigs); }
  request(desig) { return this.core.request(desig); }
  remove(desig) { this.core.remove(desig); }
  clear() { this.core.clear(); }
  focus() { this.el.input.focus(); this.el.input.select(); }
  get designations() { return this.core.designations; }
  // Situation's DATA WINDOW line ("NEOCP · on request · list 12:58", one line in the 272 px mid Inspector): when MPC
  // neocp.json was last read.
  mpcNote() {
    const L = this.core.list;
    if (L.rows) return `list ${hhmm(L.at)}${L.state === 'failed' ? ' · refresh failed' : ''}`;
    return L.state === 'failed' ? 'MPC list unavailable' : '';
  }
  // The object's row in the last MPC neocp.json read ({score, v, updated, nobs, arc, seen, …}), or null when unlisted.
  listing(desig) { return this.core.list.rows?.find(r => r.desig === desig) || null; }
  obsTimes(id) {
    const e = this.core.byId(id);
    return (e?.object?.tracklets || []).flatMap(t => (Array.isArray(t.t_ms) ? t.t_ms : [])).filter(Number.isFinite).sort((a, b) => a - b);
  }
  destroy() { this.core.destroy(); this.close(); this.root.innerHTML = ''; }

  wire() {
    const { form, input, toggle, pop, body, more, geo, refresh, shortToggle, clear, req } = this.el;
    form.addEventListener('submit', e => { e.preventDefault(); this.submit(input.value); });
    input.addEventListener('focus', () => this.core.touch());
    input.addEventListener('input', () => { this.query = input.value.trim(); this.picked = null; this.tried = false; this.paint('query'); });
    input.addEventListener('keydown', e => this.key(e));
    input.addEventListener('blur', () => this.close());
    toggle.addEventListener('mousedown', e => e.preventDefault());                  // keep the caret in the field
    toggle.addEventListener('click', () => { this.open ? this.close() : this.show(); input.focus(); });
    pop.addEventListener('mousedown', e => e.preventDefault());
    pop.addEventListener('click', e => { const o = e.target.closest('[data-desig]'); if (o) this.pick(o.dataset.desig); });
    pop.addEventListener('dblclick', e => { const o = e.target.closest('[data-desig]'); if (o) this.submit(o.dataset.desig); });
    body.addEventListener('click', e => { const r = e.target.closest('[data-desig]'); if (r) { this.core.touch(); this.pick(r.dataset.desig); } });
    body.addEventListener('dblclick', e => { const r = e.target.closest('[data-desig]'); if (r) this.submit(r.dataset.desig); });
    body.addEventListener('keydown', e => {                        // one tab stop: ↑ ↓ Home End move, Space fills, Enter fetches
      const r = e.target.closest('[data-desig]');
      if (!r || e.metaKey || e.ctrlKey || e.altKey) return;
      const rows = [...body.rows], i = rows.indexOf(r), to = { ArrowDown: i + 1, ArrowUp: i - 1, Home: 0, End: rows.length - 1 }[e.key];
      if (to !== undefined) { e.preventDefault(); e.stopPropagation(); const n = rows[Math.max(0, Math.min(rows.length - 1, to))]; this.shortCur = n.dataset.desig; this.roveShort(); n.focus(); }
      else if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); this.submit(r.dataset.desig); }
      else if (e.key === ' ') { e.preventDefault(); e.stopPropagation(); this.core.touch(); this.pick(r.dataset.desig); }
    });
    body.addEventListener('focusin', e => { const r = e.target.closest('[data-desig]'); if (r) { this.shortCur = r.dataset.desig; this.roveShort(); } });
    more.addEventListener('click', () => { this.show(); input.focus(); });
    geo.addEventListener('click', () => { this.geoOnly = !this.geoOnly; this.paint('flags'); });
    refresh.addEventListener('click', () => this.core.poll());
    shortToggle.addEventListener('click', () => {
      this.shortOpen = !this.shortOpen; writeKey(this.core.store, SHORT, this.shortOpen ? null : 'closed'); this.paint('list');
    });
    clear.addEventListener('click', () => this.core.clear());
    req.addEventListener('click', e => this.rowAction(e));
    // A click on a row selects without focusing it (as before rows were focusable), so Space after it still plays.
    req.addEventListener('mousedown', e => {
      if (!e.target.closest('.rq') || e.target.closest('button')) return;
      e.preventDefault();
      const a = document.activeElement;
      if (a && a !== document.body && !this.el.req.contains(a)) a.blur();
    });
    req.addEventListener('scroll', () => this.overflowCue(), { passive: true });
    req.addEventListener('dblclick', e => { const r = e.target.closest('.rq'); if (r && !e.target.closest('button')) this.fly(r.dataset.desig); });
    req.addEventListener('keydown', e => {
      const r = e.target.closest('.rq');
      if (!r || e.target !== r || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); this.select(r.dataset.desig); }
      else if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); this.core.remove(r.dataset.desig); }
    });
  }

  key(e) {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (!this.open) { this.show(); return; }
      const rows = this.rows(), cur = rows.findIndex(r => r.desig === this.picked);
      const i = Math.max(0, Math.min(rows.length - 1, cur + (e.key === 'ArrowDown' ? 1 : -1)));
      if (rows[i]) this.pick(rows[i].desig);                    // the field follows the highlighted option; the filter stays
    } else if (e.key === 'Enter' && this.open) {
      e.preventDefault(); this.submit(this.el.input.value);     // the field: the highlighted designation, or what was typed
    } else if (e.key === 'Escape' && this.open) {
      e.preventDefault(); e.stopPropagation(); this.close();
    }
  }

  show() {
    if (this.open) return;
    this.open = true;
    document.addEventListener('pointerdown', this.outside, true);
    this.paintPop();
  }

  close() {
    if (!this.open) return;
    this.open = false;
    document.removeEventListener('pointerdown', this.outside, true);
    this.paintPop();
  }

  pick(desig) {
    this.el.input.value = desig; this.picked = desig; this.tried = false;
    this.paint('query');
  }

  // Fetch from the form, the dropdown or the shortlist. An already requested designation adds no row: its row flashes
  // and, when drawn, is selected; a failed one is fetched again.
  submit(raw) {
    const c = checkDesig(raw);
    if (!c.ok) { this.tried = true; this.paint('query'); return; }
    this.close(); this.core.touch();
    const e = this.core.entries.get(c.desig);
    this.el.input.value = ''; this.query = ''; this.picked = null; this.tried = false;
    if (e && (e.T || e.job)) { this.flash(e.desig); if (e.T) this.o.onSelect?.(e.id); }
    else this.core.request(c.desig, { select: true });
    this.paint('query');
  }

  select(desig) { const e = this.core.entries.get(desig); if (e?.T) this.o.onSelect?.(e.id); }
  fly(desig) { const e = this.core.entries.get(desig); if (e?.T) this.o.onFly?.(e.id); }

  rowAction(ev) {
    const r = ev.target.closest('.rq'), d = r?.dataset.desig, e = d && this.core.entries.get(d);
    if (!e) return;
    const b = ev.target.closest('button');
    if (!b) { this.select(d); return; }
    if (b.classList.contains('x')) e.job ? this.core.cancel(d) : this.core.remove(d);
    else if (b.classList.contains('re')) this.core.request(d, { select: false });
    else if (b.classList.contains('eye') && e.T) {
      const on = !e.T.visible;
      if (this.o.setVisible) this.o.setVisible(e.id, on); else { e.T.visible = on; e.T.userSet = true; this.o.onChange?.(); }   // app: setVisible redraws
      this.refresh();
    }
  }

  flash(desig) {
    const row = this.rowEl(desig);
    if (!row) return;
    row.scrollIntoView?.({ block: 'nearest' });
    row.classList.add('flash');
    this.core.st(() => this.rowEl(desig)?.classList.remove('flash'), FLASH_MS);
  }

  rowEl(desig) { return [...this.el.req.children].find(r => r.dataset.desig === desig) || null; }

  flagsOn() { return this.core.helper === 'ready' && !!this.core.flags; }

  rows() {
    const L = this.core.list.rows;
    if (!L) return [];
    const flags = this.flagsOn() ? this.core.flags : null;
    let rows = sortRows(L, flags);
    if (flags && this.geoOnly) rows = rows.filter(r => flags[r.desig]?.geo === true);
    return matchRows(rows, this.query);
  }

  // ---- painting ----

  // kind: all | helper | list | flags | entries | query | tick (core events, plus the view's own 'all' and 'query').
  paint(kind) {
    if (kind === 'tick') { this.paintTick(); return; }
    if (kind === 'list') this.o.onList?.();                     // the app refreshes T.request.listing and Situation
    if (kind === 'all' || kind === 'helper') this.paintHelper();
    if (kind === 'all' || kind === 'helper' || kind === 'query') this.paintForm();
    this.paintList();
    if (kind === 'all' || kind === 'entries') { this.paintRequested(); this.announce(); }
  }

  // #neocp-live (polite): a request's outcome for screen readers: fetching, drawn with its numbers, the helper's message.
  announce() {
    const now = new Map(), msgs = [];
    for (const e of this.core.ordered()) {
      const st = e.job === 'busy' ? 'busy' : e.job ? '' : e.error ? `err:${e.error.message}` : e.T ? `drawn:${e.left ?? ''}:${e.built}` : '';
      now.set(e.desig, st);
      if (!st || this.said.get(e.desig) === st) continue;
      msgs.push(st === 'busy' ? `${e.desig}: fetching` : `${e.desig}: ${statusText(e, this.core.now()).text}`.replaceAll(NB, ' '));
    }
    for (const d of this.said.keys()) if (!now.has(d)) msgs.push(`${d} removed`);
    this.said = now;
    if (msgs.length) this.el.live.textContent = msgs.join('. ');
  }

  paintHelper() {
    const s = this.core.helper, h = helperText(s, this.host), el = this.el.status;
    el.className = `helper ${s}`; el.title = h.title;
    el.innerHTML = `<i aria-hidden="true"></i>${escapeHtml(h.text)}`;
  }

  paintForm() {
    const c = checkDesig(this.el.input.value), offline = this.core.helper === 'offline', invalid = c.desig !== '' && !c.ok;
    this.el.fetch.disabled = invalid;
    const note = this.el.note;
    if (invalid && (c.bad || this.tried)) {
      note.className = 'neo-note'; note.hidden = false; note.textContent = '5–8 letters or digits · case-sensitive';
    } else if (offline) {
      note.className = 'neo-note caveat'; note.hidden = false;
      note.innerHTML = '⚠ helper not running: run <kbd>make serve</kbd>, then Fetch again. The NEOCP list still loads from MPC.';
    } else note.hidden = true;
    this.el.input.setAttribute('aria-invalid', String(invalid && (c.bad || this.tried)));
  }

  cells(r, flags, tag) {
    const e = this.core.entries.get(r.desig), q = this.query;
    const mark = !e ? '' : e.job ? '◌' : e.T ? '✓' : e.error ? '!' : '';
    const n = q && r.desig.toLowerCase().startsWith(q.toLowerCase()) ? q.length : 0;
    const f = flags?.[r.desig], flag = f?.geo === true ? '<span class="geo">geo</span>' : `<span class="mut">${f ? 'helio' : '—'}</span>`;
    const c = [mark, `<b>${n ? `<u>${escapeHtml(r.desig.slice(0, n))}</u>` : ''}${escapeHtml(r.desig.slice(n))}</b>`, flags ? flag : '',
      Number.isFinite(r.score) ? Math.round(r.score) : '—', Number.isFinite(r.v) ? r.v.toFixed(1) : '—', Number.isFinite(r.nobs) ? r.nobs : '—',
      days(r.arc), Number.isFinite(r.seen) ? (r.seen < 100 ? r.seen.toFixed(1) : Math.round(r.seen)) : '—'];
    return c.map((v, i) => `<${tag} class="${KEYS[i]}${i === 0 && mark === '!' ? ' bad' : ''}">${v}</${tag}>`).filter(withFlag(flags)).join('');
  }

  paintList() {
    const L = this.core.list, rows = this.rows(), flags = this.flagsOn() ? this.core.flags : null;
    const m = metaText(L, rows.length, this.query, checkDesig(this.query).ok);
    this.el.meta.className = `mt${m.warn ? ' warn' : ''}`; this.el.meta.textContent = m.text; this.el.meta.title = m.title;
    this.el.geo.hidden = !flags; this.el.geo.setAttribute('aria-pressed', String(!!flags && this.geoOnly));
    const st = this.el.shortToggle;
    st.textContent = this.shortOpen ? '▾' : '▸'; st.setAttribute('aria-expanded', String(this.shortOpen));
    st.setAttribute('aria-label', this.shortOpen ? 'Hide the NEOCP shortlist' : 'Show the NEOCP shortlist'); st.title = st.getAttribute('aria-label');
    const table = this.el.short, loading = !L.rows && L.state !== 'failed', n = this.core.order.length ? 3 : 5;
    if (this.headFlags !== !!flags) {                           // the FLAG column comes and goes with the helper
      this.headFlags = !!flags;
      table.tHead.innerHTML = `<tr>${HEAD.map((h, i) => `<th scope="col" class="${KEYS[i]}"${HEAD_TIP[i] ? ` title="${HEAD_TIP[i]}"` : ''}>${h}</th>`).filter(withFlag(flags)).join('')}</tr>`;
      table.querySelector('colgroup').innerHTML = KEYS.map(k => `<col class="c-${k[2]}">`).filter(withFlag(flags)).join('');
      this.el.foot.rows[0].cells[0].colSpan = flags ? 8 : 7;   // a wider span would add a phantom column
    }
    table.hidden = !this.shortOpen || (!loading && !rows.length);
    if (loading) {
      this.el.body.innerHTML = `<tr class="skel-row"><td></td><td colspan="${flags ? 7 : 6}"><i class="skel"></i></td></tr>`.repeat(3);
      this.el.foot.hidden = true;
    } else {
      const had = this.el.body.contains(document.activeElement) ? document.activeElement.dataset.desig : null;
      this.el.body.innerHTML = rows.slice(0, n).map(r => `<tr class="sug-row${r.desig === this.picked ? ' hi' : ''}" data-desig="${r.desig}" tabindex="-1" aria-label="${escapeHtml(this.rowLabel(r, flags))}">${this.cells(r, flags, 'td')}</tr>`).join('');
      this.roveShort();
      if (had) [...this.el.body.rows].find(r => r.dataset.desig === had)?.focus();
      this.el.foot.hidden = rows.length <= n;
      this.el.more.textContent = `+ ${rows.length - n} more · ▾ all`;
    }
    this.paintPop();
  }

  // "C46FX01, geo, score 97, V 19.8, 10 obs, arc 1.13 d, seen 0.2 d: Space fills the field, Enter fetches"
  rowLabel(r, flags) {
    const f = flags?.[r.desig], n = (x, d) => (Number.isFinite(x) ? x.toFixed(d) : '—');
    return `${r.desig}${f ? `, ${f.geo === true ? 'geo' : 'helio'}` : ''}, score ${n(r.score, 0)}, V ${n(r.v, 1)}, ${Number.isFinite(r.nobs) ? r.nobs : '—'} obs, `
      + `arc ${days(r.arc)} d, seen ${n(r.seen, 1)} d: Space fills the field, Enter fetches`;
  }

  // The shortlist is one tab stop (roving tabindex): the row last moved to, else the first.
  roveShort() {
    const rows = [...this.el.body.rows].filter(r => r.dataset.desig);
    const cur = rows.find(r => r.dataset.desig === this.shortCur) || rows[0];
    for (const r of rows) r.tabIndex = r === cur ? 0 : -1;
  }

  paintPop() {
    const pop = this.el.pop, input = this.el.input;
    pop.hidden = !this.open;
    for (const b of [input, this.el.toggle]) b.setAttribute('aria-expanded', String(this.open));
    this.el.toggle.textContent = this.open ? '▴' : '▾';
    if (!this.open) { input.removeAttribute('aria-activedescendant'); return; }
    const L = this.core.list, rows = this.rows(), flags = this.flagsOn() ? this.core.flags : null;
    const total = L.rows?.length ?? 0, count = this.query || (flags && this.geoOnly) ? `${rows.length} of ${total} on the NEOCP` : `${total} on the NEOCP now`;
    const head = L.rows ? `<span>${count}</span><span>MPC ${hhmm(L.at)}</span>`
      : `<span class="${L.state === 'failed' ? 'warn' : ''}">${L.state === 'failed' ? 'MPC list unavailable · ↻' : 'reading MPC neocp.json …'}</span>`;
    const cols = HEAD.map((h, i) => `<span class="${KEYS[i]}">${i ? h : ''}</span>`).filter(withFlag(flags)).join('');
    pop.classList.toggle('noflag', !flags);
    const active = rows.findIndex(r => r.desig === this.picked);   // by designation: a re-sort keeps the user's row
    pop.innerHTML = `<div class="sug-h" aria-hidden="true">${head}</div>`
      + (rows.length ? `<div class="sug-row sug-cols" aria-hidden="true">${cols}</div>` : '')
      + rows.map((r, i) => `<div class="sug-row${i === active ? ' hi' : ''}" role="option" id="neocp-opt-${r.desig}" data-desig="${r.desig}" aria-selected="${i === active}" aria-label="${escapeHtml(this.rowLabel(r, flags).replace(/: .*$/, ''))}">${this.cells(r, flags, 'span')}</div>`).join('')
      + '<div class="sug-f" aria-hidden="true"><span><kbd>↑</kbd> <kbd>↓</kbd> choose · <kbd>Enter</kbd> fetch</span><kbd>Esc</kbd></div>';
    const act = rows[active];
    if (act) { input.setAttribute('aria-activedescendant', `neocp-opt-${act.desig}`); pop.querySelector('.sug-row.hi')?.scrollIntoView?.({ block: 'nearest' }); }
    else input.removeAttribute('aria-activedescendant');
  }

  rowHtml(e) {
    const st = statusText(e, this.core.now()), d = escapeHtml(e.desig), drawn = !!e.T, busy = !!e.job, vis = !!e.T?.visible;
    const kind = drawn ? `drawn${st.warn ? ' warn' : ''}` : busy ? 'busy' : 'err';
    const eye = drawn ? `<button type="button" class="eye" aria-pressed="${vis}" aria-label="Show ${d}">${vis ? '◉' : '○'}</button>`
      : `<span class="eye${busy ? ' spin' : ' bang'}" aria-hidden="true">${busy ? '◌' : '!'}</span>`;
    const x = `<button type="button" class="x" aria-label="${busy ? `Cancel the request for ${d}` : `Remove ${d}`}" title="${busy ? 'Cancel' : 'Remove'}">×</button>`;
    const re = st.retry ? `<button type="button" class="re" aria-label="Fetch ${d} again" title="Fetch again">↻</button>` : '';
    return `<div class="rq ${kind}" role="listitem" tabindex="0" data-id="${escapeHtml(e.id)}" data-desig="${d}" title="${escapeHtml(rowTip(e))}">`
      + `${eye}<i class="g pl${drawn ? '' : ' nd'}" aria-hidden="true"></i><span class="nm">${d}</span>`
      + `<span class="src">${drawn ? '<b>N</b> <span class="q"></span>' : ''}</span><span class="range"></span>${x}`
      + `<span class="st${st.warn ? ' warn' : ''}${re ? '' : ' wide'}">${escapeHtml(st.text)}</span>${re}</div>`;
  }

  // Focus on a row survives the rebuild; when its row went (× / Delete from the keyboard) it moves to the row now in that
  // place, else to the field.
  paintRequested() {
    const list = this.core.ordered(), a = document.activeElement, row = a?.closest?.('.rq'), mine = !!row && this.el.req.contains(row);
    const keep = mine ? [row.dataset.desig, ['x', 're', 'eye'].find(k => a.classList.contains(k))] : null, at = mine ? [...this.el.req.children].indexOf(row) : -1;
    const kbd = mine && a.matches(':focus-visible');
    this.el.reqH.hidden = !list.length; this.el.count.textContent = String(list.length);
    this.el.req.innerHTML = list.map(e => this.rowHtml(e)).join('');
    if (keep) {
      const r = this.rowEl(keep[0]), rows = this.el.req.children;
      if (r) (keep[1] && r.querySelector(`.${keep[1]}`) || r).focus();
      else if (kbd) (rows[Math.min(at, rows.length - 1)] || this.el.input).focus();
    }
    this.refresh();
  }

  // More rows than the 160 px box shows: "· ↓ 2 more" in the header and a fade at the box's foot.
  overflowCue() {
    const box = this.el.req, end = box.getBoundingClientRect().bottom;
    const n = box.clientHeight ? [...box.children].filter(r => r.getBoundingClientRect().bottom > end + 2).length : 0;
    const s = n ? ` · ↓ ${n} more` : '';
    if (this.el.over.textContent !== s) { this.el.over.textContent = s; this.el.over.hidden = !n; }
    box.classList.toggle('over', n > 0);
  }

  paintTick() {
    for (const e of this.core.ordered()) if (e.job === 'busy') {
      const st = this.rowEl(e.desig)?.querySelector('.st');
      if (st) st.textContent = statusText(e, this.core.now()).text;
    }
  }

  // Ranges, quality, visibility and selection of the drawn rows, in place (app.js calls it ≤ 2 Hz).
  refresh() {
    const sel = this.o.selected?.() ?? null;
    for (const row of this.el.req.children) {
      const e = this.core.entries.get(row.dataset.desig), T = e?.T;
      const on = row.dataset.id === sel;
      if (row.classList.contains('selected') !== on) { row.classList.toggle('selected', on); on ? row.setAttribute('aria-current', 'true') : row.removeAttribute('aria-current'); }
      if (!T) continue;
      const q = T.has ? T.seg?.quality || '' : '', rng = rangeText(this.o.rangeOf?.(e.id) ?? NaN);
      const set = (s, v) => { const el = row.querySelector(s); if (el && el.textContent !== v) el.textContent = v; };
      set('.range', rng); set('.q', q);
      const qe = row.querySelector('.q'); if (qe) qe.className = `q q-${q || 'none'}`;
      row.querySelector('.g').classList.toggle('ho', !T.has || q === 'extrap');
      row.classList.toggle('off', !T.visible);
      const eye = row.querySelector('button.eye');
      if (eye && eye.getAttribute('aria-pressed') !== String(!!T.visible)) { eye.textContent = T.visible ? '◉' : '○'; eye.setAttribute('aria-pressed', String(!!T.visible)); }
    }
    this.overflowCue();
  }
}
