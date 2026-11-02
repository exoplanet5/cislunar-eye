// Inspector (ARCHITECTURE §10, design §4): the TARGET cards of the selected target, or the SITUATION overview. Both are
// rendered on change (selection, membership) and refreshed in place: updateTarget() per UI tick, updateSituation() ≤ 1 Hz.
import { equatorial, osculating, GM_EARTH, GM_MOON, RE_KM, RM_KM } from '../states.js';
import { QUALITY } from '../model.js';
import { fmt, angularText, radiiText, utc, hm, dayText, durationParts, durationText, escapeHtml, rangeText, badgeOf, glyphClass, groupName, SOURCE_LABEL, REGIME_SHORT } from './format.js';
import { STATION_COLORS } from './theme.js';

const DAY_MS = 86400000, C_KMS = 299792.458, ARCSEC = 648000 / Math.PI, POINTING_KEY = 'cle.pointing';
const EPOCH_AT = /@(\d{4}-\d\d-\d\d[T\d:.]*Z?)/;                                      // "gp:3954@2026-08-05T15:12:15Z"
const srcEpoch = src => Date.parse(EPOCH_AT.exec(src || '')?.[1]);
const primary = c => (c.sources || []).find(s => s.primary) || (c.sources || [])[0];

// Age of the data behind the current segment: Horizons retrieval, GP epoch, or end of the fitted arc (of the TLEs for a
// Bill Gray TLE extrapolation; of the state vector for hpop). Sources such as "gp+nbody:59228@2026-10-12T19:06:43Z" or
// "hpop:DRO-A.txt@2026-10-25T00:00:00Z" carry their epoch after '@'. A
// requested NEOCP object's arc ends at its last observation (fit.obs_last): its source range has dates only.
export function dataAge(T, t) {
  const seg = T.seg || T.track?.segments.at(-1), c = T.cat;
  if (!seg) return null;
  const parse = x => (x ? Date.parse(x) : NaN), prim = primary(c), gp = seg.quality === 'gp';
  if (seg.quality === 'ref' || seg.quality === 'pred') {
    const r = parse(c.data?.retrieved_utc);
    return Number.isFinite(r) ? { label: 'retrieved', ms: t - r, epoch: r } : null;
  }
  const last = T.source === 'request' ? parse(T.request?.fit?.obs_last) : NaN;
  if (Number.isFinite(last)) return { label: 'arc end', ms: t - last, epoch: last };
  const epoch = srcEpoch(seg.src) || (gp ? parse(c.elements?.epoch) || parse(prim?.end) : parse(prim?.end) || parse(c.elements?.epoch));
  const label = gp ? 'GP epoch' : /(^|:)bgtle/.test(seg.src) ? 'TLE end' : /^hpop:/.test(seg.src) ? 'state epoch' : 'arc end';
  return Number.isFinite(epoch) ? { label, ms: t - epoch, epoch } : null;
}

// Stale (§6.4): the catalog's own flag first (catalog.js `staleness.flag`: 'gp-stale' = Space-Track GP 30–120 d old,
// 'extrap' = latest-orbit extrapolation; build_master.py decides, so list, page and TARGETS.md agree), then what the
// catalog cannot know: an fo extrapolation running > 30 d past its arc at this epoch.
export function isStale(T, t) {
  if (T.cat.staleness?.flag) return true;
  if (/stale/i.test(T.caveat || '') || /^STALE/.test(T.cat.primary_ephem || '')) return true;
  if (T.seg?.quality !== 'extrap') return false;
  const age = dataAge(T, t);
  return !!age && age.ms > 30 * DAY_MS;
}

// One GP-epoch line (review V11). For a GP segment the catalog caveat ("Space-Track GP 2026-10-13 (age 9 d)", frozen at
// the master build), the build note ("Space-Track GP (SGP4/SDP4), newest epoch <= now …, age 77.9 d") and the source
// ("gp:3954@2026-08-05T15:12:15Z") all restate the epoch that dataAge() carries live: those clauses are dropped (what
// follows a ';' is kept), `src` loses its '@epoch' and `epoch` says it once.
const GP_CAVEAT = /^(stale: )?Space-Track GP [^;]*/, GP_NOTE = /^Space-Track GP \(SGP4\/SDP4\), newest epoch[^;]*/, AT = /@[^\s,;]*/;
export function notes(T, t) {
  const age = dataAge(T, t), stale = isStale(T, t), rest = (s, re) => (s || '').replace(re, '').replace(/^;\s*/, ''), src = T.seg?.src || '';
  if (T.seg?.quality !== 'gp' || !age) return { caveat: T.caveat || '', note: T.buildNote || '', epoch: '', stale, src };
  return { caveat: rest(T.caveat, GP_CAVEAT), note: rest(T.buildNote, GP_NOTE), stale, src: src.replace(AT, ''),
    epoch: `GP epoch ${utc(age.epoch).slice(0, 10)} · ${durationText(age.ms)}${age.ms < 0 ? ' ahead' : ''}${stale ? ' · stale' : ''}` };
}

const epochLine = n => (n.stale ? '⚠ ' : '') + n.epoch;

// Scene tooltip of a target (app.js tooltip()): name, regime · range · quality · source (the quality only when the source
// does not already start with it: "gp:3954", not "gp · gp:3954"), then the notes() lines.
export function targetTip(T, t) {
  const n = notes(T, t), r = T.has ? Math.hypot(T.state[0], T.state[1], T.state[2]) : NaN, q = T.seg?.quality || 'no data';
  return `<b style="color:${T.css}">${escapeHtml(T.cat.name || T.label)}</b><p class="fine">${escapeHtml([T.regime.label, rangeText(r), n.src.startsWith(q) ? '' : q, n.src].filter(Boolean).join(' · '))}</p>`
    + (n.epoch ? `<p class="fine${n.stale ? ' warn' : ''}">${escapeHtml(epochLine(n))}</p>` : '')
    + (n.caveat ? `<p class="fine warn">⚠ ${escapeHtml(n.caveat)}</p>` : '') + (n.note ? `<p class="fine">${escapeHtml(n.note)}</p>` : '')
    + (T.draw && !T.track ? `<p class="fine">no track in this build${T.why ? `: ${escapeHtml(T.why)}` : ''}</p>` : '');
}

// ---------- TARGET ----------
// Cards stack as `.card`s; a `.kv` row is label | value | unit, wrapped in a display:contents <div> so it hides as one.
const card = (title, aside, body, cls = '') => `<section class="card${cls}"><h4>${title}${aside ? `<span>${aside}</span>` : ''}</h4>${body}</section>`;
const kv = (label, id, unit = '', cls = '') => `<div><dt>${label}</dt><dd id="${id}"${cls ? ` class="${cls}"` : ''}>—</dd><dd class="u">${unit}</dd></div>`;
const txt = (label, id, html = '—') => `<div><dt>${label}</dt><dd${id ? ` id="${id}"` : ''} class="txt">${html}</dd></div>`;
const SE_POINT = { SE_L1: 0, SE_L2: 1 };
const MODE = { fo: 'find_orb', fo_daily: 'find_orb', page_sv: 'neocp2 vector', page_table: 'neocp2 table' };
let root = null;                                                  // the #readout element renderTarget() last filled
const cache = new WeakMap();                                      // innerHTML written per element, so unchanged HTML is not rewritten

function identity(T, n) {
  const c = T.cat, id = escapeHtml(T.id), name = escapeHtml(c.name || T.label), req = T.source === 'request';
  const status = c.status && c.status !== 'unknown' ? [c.status.replaceAll('_', ' '), c.status_date].filter(Boolean).join(' · ') : '';
  const type = (c.obj_type || '').toLowerCase().replaceAll('_', ' ');
  const tags = [`<span class="tag fam" style="--c:${T.css}">${escapeHtml(T.regime.label)}</span>`, ...[type !== 'other' && type, status,
    c.left_neocp && `left NEOCP ${String(c.left_neocp).slice(0, 10)}`, groupName(T.group) !== T.regime.label && T.group].filter(Boolean).map(s => `<span class="tag">${escapeHtml(s)}</span>`)];
  const ids = [['NORAD', c.norad], ['COSPAR', c.cospar], ['Horizons', c.horizons_id], ['Tier', T.tier]]
    .map(([k, v]) => `<dt>${k}</dt><dd${v == null || v === '' ? ' class="muted">—' : `>${escapeHtml(v)}`}</dd>`).join('');
  return `<section class="card ident"><div class="idrow"><i class="${glyphClass(T)}" style="--c:${T.css}"></i><h3 title="${name}">${name}</h3><span class="acts">`
    + `<button type="button" data-act="fly" data-id="${id}" title="Fly to (F)"${T.draw && T.track ? '' : ' disabled'}>⌖ Fly to</button>`
    + `<button type="button" data-act="hide" data-id="${id}" aria-label="Show ${name}" title="Show or hide"${T.draw ? '' : ' disabled'}>◉</button>`
    + `<button type="button" data-act="deselect" data-id="${id}" aria-label="Deselect" title="Deselect (Esc)">×</button></span></div>`
    + `<div class="tags">${tags.join('')}</div>${req ? '' : `<dl class="kv ids">${ids}</dl>`}`
    + `<p id="ro-epoch" class="${n.stale ? 'caveat' : 'note'}"${n.epoch ? '' : ' hidden'}>${escapeHtml(epochLine(n))}</p>`
    + (n.caveat ? `<p class="caveat">⚠ ${escapeHtml(n.caveat)}</p>` : '') + (n.note ? `<p class="note">${escapeHtml(n.note)}</p>` : '')
    + (T.qualityNote ? `<p class="note">${escapeHtml(T.qualityNote)}</p>` : '') + '</section>';
}

// Fit strength of a requested object: weak below a 1 d arc or 4 obs, good from a 3 d arc with rms < 0.5″, else fair.
function fitWord(f) {
  const n = f.n_used ?? f.n_obs;
  return !(f.arc_d >= 1) || !(n >= 4) ? 'weak' : f.arc_d >= 3 && f.rms_arcsec < .5 ? 'good' : 'fair';
}

// The object's row in MPC neocp.json at the last read (NeocpLookup.listing): "Score 97 · V 19.8 · Updated Oct. 21.52 UT ·
// not seen 2.08 d".
function listingText(L) {
  const n = (x, d) => (Number.isFinite(x) ? x.toFixed(d) : '—');
  return [`Score ${n(L.score, 0)}`, `V ${n(L.v, 1)}`, L.updated, `not seen ${n(L.seen, 2)} d`].filter(Boolean).map(escapeHtml).join(' · ');
}

function requestCard(T, w) {
  const r = T.request, f = r.fit || {}, id = escapeHtml(T.id), word = fitWord(f), n = f.n_used ?? f.n_obs ?? r.n_obs_used;
  const at = x => (x ? `${hm(Date.parse(x))} UTC` : '—'), first = Date.parse(f.obs_first), last = Date.parse(f.obs_last);
  const seen = Number.isFinite(first) && Number.isFinite(last)
    ? `${dayText(first, w.t0)} ${hm(first)} → ${utc(first).slice(0, 10) === utc(last).slice(0, 10) ? '' : `${dayText(last, w.t0)} `}${hm(last)}` : '';
  const page = /^https?:\/\//.test(f.pluto_url || '') ? `<a href="${escapeHtml(f.pluto_url)}" target="_blank" rel="noopener">neocp2 pseudo-MPEC ↗</a>` : '';
  // Requested: when the helper answered (served_utc; a result-cache hit says when MPC and neocp2 were read)
  const served = r.served_utc || r.fetched_utc, read = Date.parse(r.fetched_utc);
  const from = r.cached && Math.abs(Date.parse(served) - read) >= 60e3 ? ` · cached, read ${hm(read)}` : '';
  return card('NEOCP request', `MPC + neocp2 → ${escapeHtml(MODE[r.mode] || r.mode || 'fit')}`, `<dl class="kv">`
    + txt('Requested', '', at(served) + from)
    + txt('NEOCP', '', r.on_neocp === false ? '<span class="wn">left the NEOCP · drawn from neocp2</span>' : `listed · MPC ${escapeHtml(r.n_obs_mpc ?? '—')} obs`)
    + (r.listing ? txt('MPC listing', 'ro-listing', listingText(r.listing)) : '')
    + txt('neocp2 page', '', at(r.page_created_utc))
    + txt('Fit', '', `<b class="fit-${word}">${word}</b> · rms ${fmt(f.rms_arcsec, 2)}″`)
    + txt('Arc', '', `${fmt(f.arc_d, 2)} d · ${escapeHtml(n ?? '—')} obs${f.stations?.length ? ` · ${escapeHtml(f.stations.join(' '))}` : ''}`)
    + (seen ? txt('Observed', '', seen) : '')
    + `</dl>${page ? `<p class="note">${page}</p>` : ''}<div class="acts"><button type="button" data-act="refetch" data-id="${id}">↻ Refetch</button>`
    + `<button type="button" data-act="remove" data-id="${id}">Remove</button></div>`, ' request');
}

function stateCard() {
  return card('Geocentric state', 'ICRF · geometric', '<dl class="kv"><div><dt>Earth range</dt><dd id="ro-range" class="big">—</dd><dd class="u">km</dd><dd id="ro-range-units" class="sub">—</dd></div>'
    + kv('Speed', 'ro-speed', 'km/s') + kv('Moon distance', 'ro-moon', 'km') + kv('RA', 'ro-ra') + kv('Dec', 'ro-dec') + kv('Sun–Earth–object', 'ro-elong', '°')
    + txt('Selenocentric', 'ro-seleno') + '</dl>');
}

// Sky motion from the track at t ± 60 s (the Hermite interpolator), geocentric: topocentric RA/Dec differ by up to the
// parallax bound asin(R⊕ / r).
function pointingCard() {
  let open = false;
  try { open = globalThis.localStorage?.getItem(POINTING_KEY) === '1'; } catch { /* storage denied */ }
  return `<details class="card pointing"${open ? ' open' : ''}><summary><h4>Pointing<span>geocentric · not topocentric</span></h4></summary><dl class="kv">`
    + txt('Sky rate', 'ro-rate') + kv('Range rate', 'ro-rdot', 'km/s') + kv('Light time', 'ro-light', 's') + kv('1σ on sky', 'ro-sigsky', '″') + kv('Parallax bound', 'ro-parallax', '°')
    + '</dl><p class="note">Topocentric RA/Dec differ from these by up to the parallax bound.</p></details>';
}

// Osculating conic, or for SE-L1/L2 targets the offset from their L point in the Sun–Earth frame; none for heliocentric.
function orbitCard(T) {
  const c = T.cat, lunar = c.regime === 'LUNAR_ORBIT';
  if (c.regime in SE_POINT) {
    const L = c.regime.replace('_', '-');
    return card(`Distance from ${L}`, 'SE rotating', `<dl class="kv">${kv('Distance', 'ro-ld', 'km', 'big')}${kv('x · anti-Sun', 'ro-sx', 'km')}${kv('y', 'ro-sy', 'km')}${kv('z · ecliptic normal', 'ro-sz', 'km')}</dl>`
      + `<p class="note">Offset from ${L}, x anti-sunward; a two-body Earth conic means nothing here.</p>`);
  }
  if (c.regime === 'EARTH_NEAR_HELIO') return '';
  return card('Osculating orbit', `${lunar ? 'Moon' : 'Earth'} · i to equator`, '<div class="two">'
    + `<dl class="kv">${kv('a', 'ro-a', 'km')}${kv(lunar ? 'Perilune alt.' : 'Perigee', 'ro-peri', 'km')}${kv(lunar ? 'Apolune alt.' : 'Apogee', 'ro-apo', 'km')}</dl>`
    + `<dl class="kv">${kv('e', 'ro-e')}${kv('i', 'ro-i', '°')}${kv('Period', 'ro-p', 'd')}</dl></div><dl class="kv">${kv('C3', 'ro-c3', 'km²/s²')}</dl>`
    + `<p class="note">Two-body ${lunar ? 'Moon' : 'Earth'} conic through the state at this epoch.</p>`);
}

function elementsNote(c, w) {
  const e = c.elements, t = Date.parse(e?.epoch || '');
  if (!e || !Number.isFinite(e.a_km)) return '';
  if (!(t > w.t0 - 365 * DAY_MS)) return `<p class="note">Elements from ${Number.isFinite(t) ? new Date(t).getUTCFullYear() : 'an unknown epoch'} predate the current orbit.</p>`;
  return `<p class="note">Last known elements (${escapeHtml(e.epoch)}, ${escapeHtml(e.center || 'earth')}-centred): a ${fmt(e.a_km)} km · e ${fmt(e.e, 3)} · i ${fmt(e.i_deg, 1)}° · P ${fmt(e.period_d, 2)} d</p>`;
}

// Coverage over the data window: one bar per segment (family colour for data, at 55 % for GP, an apricot hatch for
// extrapolation, nothing over gaps) and the epoch tick that updateTarget() moves.
const pct = (x, w) => Math.max(0, Math.min(100, (x - w.t0) / (w.t1 - w.t0) * 100));
function coverage(T, w) {
  const segs = T.track?.segments || [], age = dataAge(T, w.t);
  const span = segs.length ? `${utc(segs[0].t0_ms).slice(0, 16)} → ${utc(segs.at(-1).t1_ms).slice(0, 16)} · ${segs.length} segment${segs.length > 1 ? 's' : ''}` : 'no track';
  const mid = [age && age.epoch >= w.t0 && age.epoch <= w.t1 && `${age.label} ${dayText(age.epoch, w.t0)}`, segs.some(s => s.join === 'gap') && 'gap', segs.length ? '▮ epoch' : 'no track'];
  const bars = segs.map(s => `<i class="${s.quality === 'extrap' ? 'x' : s.quality === 'gp' ? 'd gp' : 'd'}" style="left:${pct(s.t0_ms, w).toFixed(2)}%;width:${(pct(s.t1_ms, w) - pct(s.t0_ms, w)).toFixed(2)}%"></i>`);
  return (segs.length ? `<div class="covbar" id="ro-covbar" style="--c:${T.css}" title="${escapeHtml(span)}">${bars.join('')}<i class="ep"></i></div>` : '')
    + `<div class="covlab" id="ro-cov"><span>${dayText(w.t0, w.t0)}</span><span>${escapeHtml(mid.filter(Boolean).join(' · '))}</span><span>${dayText(w.t1, w.t0)}</span></div>`;
}

// Catalog notes are the build's per-source notes, "[news] … | [lunarmission] … | …" (scripts/build_master.py, each cut
// at 180 characters): one line per source without the [source] tag, without the bookkeeping fragments (catalog.csv row
// lists, TLE line lists), "…" where the build cut it. The raw text stays the paragraph's title.
const NOTE_CUT = 180, BOOKKEEPING = [/^catalog\.csv \d+ rows? \(lines [^)]*\);?\s*/, /^tle_list L[\d,]+$/];
export function noteLines(s) {
  const blocks = [];
  for (const p of String(s || '').split(' | ')) {
    const m = /^\[[a-z_]+\] ?([\s\S]*)$/.exec(p);
    if (m) blocks.push([m[1]]); else if (blocks.length) blocks.at(-1).push(p); else blocks.push([p]);
  }
  return blocks.map(b => {
    const parts = b.map(x => BOOKKEEPING.reduce((y, re) => y.replace(re, ''), x).trim()).filter(Boolean);
    return parts.length ? parts.join(' · ') + (b.join(' | ').length >= NOTE_CUT ? '…' : '') : '';
  }).filter(Boolean);
}

function dataCard(T, w) {
  const c = T.cat, links = (c.links || []).filter(l => /^https?:\/\//.test(l.url || '')), lines = noteLines(c.notes);
  return `<section class="card data"><h4>Data<span id="ro-kind"></span></h4><dl class="kv">${txt('Source', 'ro-src')}${txt('Quality', 'ro-q')}`
    + '<div><dt>Data age</dt><dd id="ro-age">—</dd><dd class="u"></dd><dd class="sub"></dd></div>' + `${kv('σ position', 'ro-sigma', 'km')}</dl>${coverage(T, w)}`
    + (lines.length ? `<p class="note notes" title="${escapeHtml(c.notes)}">${lines.map(l => `<span>${escapeHtml(l)}</span>`).join('')}</p>` : '')
    + (links.length ? `<p class="note">${links.map(l => `<a href="${escapeHtml(l.url)}" target="_blank" rel="noopener">${escapeHtml(l.label || 'link')} ↗</a>`).join(' · ')}</p>` : '') + '</section>';
}

// Station chips (review V10): the station code as text, the bead colour only as a grouping hint; the busiest station
// first (ties in first-seen order). `tracklets` is the TrackletLayer (items {id, stn, n}; stations in first-seen order,
// the same cycle the beads use).
function trackletCard(T, tracklets) {
  const items = (tracklets?.items || []).filter(x => x.id === T.id);
  if (!items.length) return '';
  const by = new Map(), color = stn => STATION_COLORS[Math.max(0, (tracklets.stations || []).indexOf(stn)) % STATION_COLORS.length];
  for (const x of items) by.set(x.stn, (by.get(x.stn) || 0) + x.n);
  return card('Tracklets', `${items.length} · ${items.reduce((s, x) => s + x.n, 0)} obs · by station`,
    `<div class="stations">${[...by].sort((a, b) => b[1] - a[1]).map(([stn, n]) => `<span class="stn" data-station="${escapeHtml(stn)}" style="--c:${color(stn)}"><i></i>${escapeHtml(stn)}<small>${n}</small></span>`).join('')}</div>`);
}

export function renderTarget(el, T, w, tracklets = null) {
  const n = notes(T, w.t), drawn = T.draw && T.track;
  el.innerHTML = identity(T, n) + (T.source === 'request' && T.request ? requestCard(T, w) : '')
    + (drawn ? stateCard() + pointingCard() + orbitCard(T)
      : card(T.draw ? 'No track' : 'Listed, not drawn', '', `<p class="note">${T.draw ? `This build has no track for it${T.why ? `: ${escapeHtml(T.why)}` : '.'}` : 'No public ephemeris covers the window.'}</p>${elementsNote(T.cat, w)}`))
    + dataCard(T, w) + trackletCard(T, tracklets);
  root = el;
  el.querySelector('details.pointing')?.addEventListener('toggle', e => {
    try { globalThis.localStorage?.setItem(POINTING_KEY, e.target.open ? '1' : '0'); } catch { /* storage denied */ }
    if (e.target.open) pointing(T, w, w.t);
  });
}

const $ = id => document.getElementById(id);
const signed = (v, digits) => fmt(v, digits).replace('-', '−');
const put = (e, s) => { if (e && e.textContent !== s) e.textContent = s; }, set = (id, s) => put($(id), s);
const html = (e, s) => { if (e && cache.get(e) !== s) { cache.set(e, s); e.innerHTML = s; } };
const DASH = ['ro-range', 'ro-speed', 'ro-moon', 'ro-ra', 'ro-dec', 'ro-elong', 'ro-seleno', 'ro-a', 'ro-e', 'ro-i', 'ro-p', 'ro-peri', 'ro-apo', 'ro-c3', 'ro-ld', 'ro-sx', 'ro-sy', 'ro-sz',
  'ro-rate', 'ro-rdot', 'ro-light', 'ro-sigsky', 'ro-parallax'];

// Sky rate and position angle (east of north) of the geocentric direction between t − 60 s and t + 60 s (one-sided at a
// coverage edge), range rate over the same step; light time, 1σ on the sky and the parallax bound at t.
function pointing(T, w, t) {
  if (!T.has) { for (const id of DASH.slice(-5)) set(id, '—'); return; }
  const a = new Float64Array(6), b = new Float64Array(6), s = T.state, r = Math.hypot(s[0], s[1], s[2]);
  let t0 = t - 60e3, t1 = t + 60e3;
  if (!w.stateAt(T, t0, a)) { a.set(s); t0 = t; }
  if (!w.stateAt(T, t1, b)) { b.set(s); t1 = t; }
  const ra = Math.hypot(a[0], a[1], a[2]), rb = Math.hypot(b[0], b[1], b[2]), dt = (t1 - t0) / 1000;
  const u = [a[0] / ra, a[1] / ra, a[2] / ra], v = [b[0] / rb, b[1] / rb, b[2] / rb], d = [v[0] - u[0], v[1] - u[1], v[2] - u[2]];
  const ang = Math.atan2(Math.hypot(u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]), u[0] * v[0] + u[1] * v[1] + u[2] * v[2]);
  const al = Math.atan2(u[1] + v[1], u[0] + v[0]), de = Math.atan2(u[2] + v[2], Math.hypot(u[0] + v[0], u[1] + v[1]));
  const east = -Math.sin(al) * d[0] + Math.cos(al) * d[1], north = -Math.sin(de) * (Math.cos(al) * d[0] + Math.sin(al) * d[1]) + Math.cos(de) * d[2];
  const rate = dt ? ang / dt * 60 * ARCSEC : NaN, pa = (Math.atan2(east, north) * 180 / Math.PI + 360) % 360, rdot = dt ? (rb - ra) / dt : NaN;
  set('ro-rate', !Number.isFinite(rate) ? '—' : `${rate < 3600 ? `${fmt(rate, rate < 10 ? 2 : rate < 100 ? 1 : 0)}″` : `${fmt(rate / 3600, 2)}°`}/min · PA ${fmt(pa)}°`);
  set('ro-rdot', Number.isFinite(rdot) ? `${rdot < 0 ? '−' : '+'}${fmt(Math.abs(rdot), 3)}` : '—');
  set('ro-light', fmt(r / C_KMS, 2));
  const seg = T.seg, sigma = seg?.sigma?.[Math.max(0, Math.min(seg.n - 1, Math.round((t - seg.t0_ms) / 1000 / seg.step_s)))], sky = sigma / r * ARCSEC;
  set('ro-sigsky', Number.isFinite(sky) ? fmt(sky, sky < 100 ? 1 : 0) : '—');
  const p = Math.asin(Math.min(1, RE_KM / r)) * 180 / Math.PI;
  set('ro-parallax', `≤ ${fmt(p, p < 1 ? 2 : 1)}`);
}

// Quality word in its colour, then what it rests on: the fit arc (the TLE span for a Bill Gray TLE, the epoch of a
// hand-supplied state vector), else QUALITY.
function qualityHtml(T, seg, w) {
  const q = escapeHtml(seg.quality), p = primary(T.cat), s = Date.parse(p?.start), e = Date.parse(p?.end);
  if (!((q === 'fit' || q === 'extrap') && Number.isFinite(s) && Number.isFinite(e))) return `<span class="q q-${q}">${q}</span>${QUALITY[q] ? ` · ${QUALITY[q]}` : ''}`;
  const short = dayText(s, w.t0).length < 6 && dayText(e, w.t0).length < 6, day = x => `<span class="nw">${short ? dayText(x, w.t0) : utc(x).slice(0, 10)}</span>`;
  if (p.kind === 'statevec') return `<span class="q q-${q}">${q}</span> · state vector of ${day(s)}`;
  return `<span class="q q-${q}">${q}</span> · ${p.kind === 'bgtle' ? 'TLEs' : 'fit arc'} ${day(s)} → ${day(e)}`;
}

export function updateTarget(T, w, t) {
  const seg = T.seg, n = notes(T, t), age = dataAge(T, t), view = root?.isConnected ? root : null;
  const g = view?.querySelector('.idrow .g'), hide = view?.querySelector('[data-act="hide"]');
  if (g) g.className = glyphClass(T, isStale(T, t) || seg?.quality === 'extrap');
  if (hide) { hide.textContent = T.visible ? '◉' : '○'; hide.setAttribute('aria-pressed', T.visible); }
  if (T.draw && T.track) {
    if (T.has) {
      const s = T.state, r = Math.hypot(s[0], s[1], s[2]), m = w.moon, sun = w.sun;
      const dm = Math.hypot(s[0] - m[0], s[1] - m[1], s[2] - m[2]), eq = equatorial(s);
      const elong = Math.acos(Math.max(-1, Math.min(1, (s[0] * sun[0] + s[1] * sun[1] + s[2] * sun[2]) / (r * Math.hypot(sun[0], sun[1], sun[2]))))) * 180 / Math.PI;
      set('ro-range', fmt(r)); set('ro-range-units', radiiText(r)); set('ro-speed', fmt(Math.hypot(s[3], s[4], s[5]), 3)); set('ro-moon', fmt(dm));
      set('ro-ra', angularText(eq.ra_deg, true)); set('ro-dec', angularText(eq.dec_deg)); set('ro-elong', fmt(elong, 1));
      const lunar = T.cat.regime === 'LUNAR_ORBIT', rel = lunar || dm < 66000 ? [0, 1, 2, 3, 4, 5].map(k => s[k] - m[k]) : null;
      set('ro-seleno', rel ? `${fmt(Math.hypot(rel[0], rel[1], rel[2]))} km · ${fmt(Math.hypot(rel[3], rel[4], rel[5]), 3)} km/s` : '— no SOI');
      if (T.cat.regime in SE_POINT) {
        const L = w.lagrange.out[SE_POINT[T.cat.regime]], d = [0, 1, 2].map(k => s[k] - L[k]), R = w.frames.matrix('se', t, new Float64Array(9));
        set('ro-ld', fmt(Math.hypot(...d))); ['ro-sx', 'ro-sy', 'ro-sz'].forEach((id, k) => set(id, signed(R[k * 3] * d[0] + R[k * 3 + 1] * d[1] + R[k * 3 + 2] * d[2])));
      }
      const o = osculating(lunar ? rel : s, lunar ? GM_MOON : GM_EARTH), R0 = lunar ? RM_KM : RE_KM;
      set('ro-a', o.e < 1 ? fmt(o.a) : '—'); set('ro-e', fmt(o.e, 4)); set('ro-i', fmt(o.inclination, 2)); set('ro-p', Number.isFinite(o.period) ? fmt(o.period / 86400, 3) : '—');
      set('ro-peri', fmt(o.periapsis - (lunar ? R0 : 0))); set('ro-apo', Number.isFinite(o.apoapsis) ? fmt(o.apoapsis - (lunar ? R0 : 0)) : '—'); set('ro-c3', signed(o.c3, 3));
      if ($('ro-rate')?.closest('details').open) pointing(T, w, t);
    } else for (const id of DASH) set(id, '—');
    if (!T.has) set('ro-range-units', 'no data at this epoch');
  }
  const epoch = $('ro-epoch');
  if (epoch) { set('ro-epoch', epochLine(n)); epoch.className = n.stale ? 'caveat' : 'note'; epoch.hidden = !n.epoch; }
  const srcs = (T.cat.sources || []).map(s => `${s.kind}:${s.ref}`), badge = seg ? badgeOf(seg.src) : '';
  set('ro-src', seg ? n.src : srcs.slice(0, 3).join(', ') + (srcs.length > 3 ? ` +${srcs.length - 3} more` : '') || '—');
  set('ro-kind', badge ? `${badge} · ${SOURCE_LABEL[badge] || 'other'}` : '');
  html($('ro-q'), seg ? qualityHtml(T, seg, w) : '—');
  const ageEl = $('ro-age');
  if (ageEl) {
    const [value, unit] = age ? durationParts(age.ms) : ['—', ''];
    ageEl.parentElement.hidden = !!n.epoch;                         // the GP-epoch line above already gives the GP age
    put(ageEl, value); put(ageEl.nextElementSibling, unit); put(ageEl.parentElement.lastElementChild, age ? `since ${age.label}` : '');
  }
  const i = seg ? Math.max(0, Math.min(seg.n - 1, Math.round((t - seg.t0_ms) / 1000 / seg.step_s))) : -1;
  set('ro-sigma', seg?.sigma ? fmt(seg.sigma[i], 1) : '—');
  const tick = $('ro-covbar')?.querySelector('.ep');
  if (tick) tick.style.left = `${pct(t, w).toFixed(2)}%`;
}

// ---------- SITUATION ----------
// Rows per regime (catalog order; on-request NEOCP objects in their own "NEOCP requested" row, always shown), the stale
// or extrapolated targets on view, and the data window with the freshness of each source. renderSituation() writes the
// HTML; updateSituation() rewrites it only when a row appears or goes, else patches the [data-v] numbers in place.
const shapes = new WeakMap();                                     // #situation → the row keys it shows

function situation(w, app, t) {
  const req = w.regimes.get('NEOCP_GEO'), rows = new Map([...w.regimes.values()].map(r => [r.key, { key: r.key, regime: r.key, label: r.label, color: r.color, order: r.order, shown: 0, live: 0, all: 0 }]));
  rows.set('request', { key: 'request', regime: 'NEOCP_GEO', label: 'NEOCP requested', color: req?.color, order: req?.order ?? 99, shown: 0, live: 0, all: 0 });
  for (const T of w.targets) {
    if (T.removed) continue;
    const r = rows.get(T.source === 'request' ? 'request' : T.regime.key);
    r.all++; if (T.has) { r.live++; if (app.isVisible(T)) r.shown++; }
  }
  const regimes = [...rows.values()].filter(r => r.all || r.key === 'request').sort((a, b) => a.order - b.order);
  const stale = w.targets.filter(T => !T.removed && T.draw && app.isVisible(T) && isStale(T, t)).map(T => ({ T, age: dataAge(T, t) }))
    .sort((a, b) => (b.age?.ms ?? -Infinity) - (a.age?.ms ?? -Infinity));
  const ending = w.targets.filter(T => !T.removed && T.track && Math.abs(T.track.t1_ms - t) < 7 * DAY_MS && T.track.t1_ms < w.t1);
  return { regimes, stale, ending, mpc: app.mpcNote?.() || '', key: [regimes.map(r => r.key), stale.map(x => x.T.id), ending.map(T => T.id)].join('|') };
}

// Newest data per source over the catalog targets: Horizons retrieval, GP epoch, end of the find_orb fit arcs.
function freshness(w) {
  let horizons = NaN, gp = NaN, fo = NaN;
  const max = (a, b) => (Number.isFinite(a) && !(a <= b) ? a : b);
  for (const T of w.targets) {
    if (T.source === 'request' || T.removed) continue;
    horizons = max(Date.parse(T.cat.data?.retrieved_utc), horizons);
    for (const s of T.track?.segments || []) {
      if (s.quality === 'gp') gp = max(srcEpoch(s.src) || Date.parse(T.cat.elements?.epoch), gp);
      else if (/^fo:/.test(s.src)) fo = max(srcEpoch(s.src) || Date.parse(primary(T.cat)?.end), fo);
    }
  }
  return { horizons, gp, fo };
}

function situationHtml(m, w) {
  const z = n => `<td data-v${n ? '' : ' class="z"'}>${n}</td>`, tot = k => m.regimes.reduce((s, r) => s + r[k], 0), f = freshness(w), day = x => utc(x).slice(0, 10);
  const name = r => (REGIME_SHORT[r.key] ? `<span class="l">${escapeHtml(r.label)}</span><span class="s">${escapeHtml(REGIME_SHORT[r.key])}</span>` : escapeHtml(r.label));
  const regimes = m.regimes.map(r => `<tr ${r.key === 'request' ? 'data-neocp' : `data-regime="${escapeHtml(r.regime)}"`}><td><button type="button" class="lnk" title="${r.key === 'request' ? 'Open the NEOCP lookup' : `${escapeHtml(r.label)}: show its group in the target list`}"><i class="swatch" style="--c:${r.color}"></i>${name(r)}</button></td>${z(r.shown)}${z(r.live)}${z(r.all)}</tr>`).join('');
  const stale = m.stale.map(({ T, age }) => `<tr data-id="${escapeHtml(T.id)}"><td><button type="button" class="lnk">${escapeHtml(T.label)}</button></td><td>${escapeHtml(age?.label || T.seg?.quality || '')}</td><td data-v>${age ? durationText(age.ms) : '—'}</td></tr>`).join('');
  const ending = m.ending.map(T => `<button type="button" class="lnk" data-id="${escapeHtml(T.id)}">${escapeHtml(T.label)} ${dayText(T.track.t1_ms, w.t0)}</button>`).join(' · ');
  const key = Object.entries(SOURCE_LABEL).filter(([k]) => k !== 'D').map(([k, v]) => `<span class="nw"><b>${k}</b> ${v}</span>`).join('');   // a wrapping flex list (inspector.css)
  return card('Regimes', 'click a row to jump to its group', `<table class="counts"><thead><tr><th>regime</th><th title="drawn now">shown</th><th title="data at this epoch">live</th><th>all</th></tr></thead>`
      + `<tbody>${regimes}<tr class="tot"><td>Total</td>${z(tot('shown'))}${z(tot('live'))}${z(tot('all'))}</tr></tbody></table>`)
    + card(`Stale or extrapolated · ${m.stale.length}`, 'basis · age', m.stale.length ? `<table class="counts stale"><tbody>${stale}</tbody></table>` : '<p class="note">None on view.</p>')
    + card('Data window', `±${fmt((w.t1 - w.t0) / 2 / DAY_MS)} d`, '<dl class="kv">' + txt('From', '', `${utc(w.t0).slice(0, 16)} UTC`) + txt('To', '', `${utc(w.t1).slice(0, 16)} UTC`)
      + txt('Build', '', `${utc(w.now).slice(0, 16)} UTC`)
      + (Number.isFinite(f.horizons) ? txt('Horizons', '', `retrieved ${day(f.horizons)}`) : '') + (Number.isFinite(f.gp) ? txt('Space-Track GP', '', `newest epoch ${day(f.gp)}`) : '')
      + (Number.isFinite(f.fo) ? txt('find_orb fits', '', `arcs to ${day(f.fo)}`) : '')
      + `<div><dt>NEOCP</dt><dd class="txt" data-v title="list: when MPC's neocp.json (the objects on the NEOCP) was last read, UTC">${escapeHtml(['on request', m.mpc].filter(Boolean).join(' · '))}</dd></div>`
      + txt('Ends soon', '', ending || 'none') + `</dl><p class="note key">${key}</p>`, ' window');
}

// The [data-v] cells in document order, as situationHtml() writes them.
const values = m => [...m.regimes.flatMap(r => [r.shown, r.live, r.all]), ...['shown', 'live', 'all'].map(k => m.regimes.reduce((s, r) => s + r[k], 0)),
  ...m.stale.map(({ age }) => (age ? durationText(age.ms) : '—')), ['on request', m.mpc].filter(Boolean).join(' · ')].map(String);

const write = (el, m, w) => { el.innerHTML = situationHtml(m, w); shapes.set(el, m.key); };

export function renderSituation(el, w, app, t) { write(el, situation(w, app, t), w); }

export function updateSituation(el, w, app, t) {
  const m = situation(w, app, t);
  if (shapes.get(el) !== m.key) { write(el, m, w); return; }
  const v = values(m), cells = el.querySelectorAll('[data-v]');
  cells.forEach((e, i) => { if (e.textContent !== v[i]) { e.textContent = v[i]; if (e.tagName === 'TD' && !e.closest('.stale')) e.classList.toggle('z', v[i] === '0'); } });
}
