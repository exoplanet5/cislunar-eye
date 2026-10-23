// Right panel (ARCHITECTURE §10): readouts for the selected target, or the Situation summary.
import { equatorial, osculating, GM_EARTH, GM_MOON, RE_KM, RM_KM } from '../states.js';
import { QUALITY } from '../model.js';
import { fmt, angularText, kmUnits, utc, durationText, escapeHtml, rangeText, badgeOf } from './format.js';
import { STATION_COLORS } from './theme.js';

const DAY_MS = 86400000;

// Age of the data behind the current segment: Horizons retrieval, GP epoch, or end of the fitted arc. Sources such as
// "gp+nbody:59228@2026-10-12T19:06:43Z" or "fo:X@2026-10-19" carry their epoch after '@'.
export function dataAge(T, t) {
  const seg = T.seg || T.track?.segments.at(-1), c = T.cat;
  if (!seg) return null;
  const prim = (c.sources || []).find(s => s.primary) || (c.sources || [])[0];
  const parse = x => (x ? Date.parse(x) : NaN), at = parse((/@(\d{4}-\d\d-\d\d[T\d:.]*Z?)/.exec(seg.src) || [])[1]);
  if (seg.quality === 'ref' || seg.quality === 'pred') {
    const r = parse(c.data?.retrieved_utc) || parse(T.provenance?.find(p => p.retrieved_utc)?.retrieved_utc);
    return Number.isFinite(r) ? { label: 'retrieved', ms: t - r, epoch: r } : null;
  }
  const epoch = at || (seg.quality === 'gp' ? parse(c.elements?.epoch) || parse(prim?.end) : parse(prim?.end) || parse(c.elements?.epoch));
  return Number.isFinite(epoch) ? { label: seg.quality === 'gp' ? 'GP epoch' : 'since arc end', ms: t - epoch, epoch } : null;
}

// Stale (§6.4): the catalog's own flag first (catalog.js `staleness.flag`: 'gp-stale' = Space-Track GP 30–120 d old,
// 'extrap' = latest-orbit extrapolation; build_master.py decides, so list, page and TARGETS.md agree), then the
// live conditions the catalog cannot know: an old NEOCP snapshot and an fo extrapolation running > 30 d past its arc.
export function isStale(T, t) {
  const seg = T.seg, age = dataAge(T, t);
  if (T.snapshotStale) return true;                       // neocp.js snapshot older than 3 h (§6.4)
  if (T.cat.staleness?.flag) return true;
  if (/stale/i.test(T.caveat || '') || /^STALE/.test(T.cat.primary_ephem || '')) return true;
  if (!seg) return false;
  if (seg.quality === 'extrap') return !!age && age.ms > 30 * DAY_MS;
  return false;
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

const SE_POINT = { SE_L1: 0, SE_L2: 1 };
const datum = (label, id, unit = '', kind = '') => `<div class="datum${kind ? ' ' + kind : ''}"><dt>${label}</dt><dd><span id="${id}">—</span>${unit ? `<small>${unit}</small>` : ''}</dd></div>`;
const block = (title, rows, note = '') => `<section class="metric"><h2>${title}</h2><dl class="data-list">${rows}</dl>${note}</section>`;

// Osculating conic, or for SE-L1/L2 targets the offset from their L point in the Sun–Earth frame; none for heliocentric.
function orbitBlock(T) {
  const c = T.cat, lunar = c.regime === 'LUNAR_ORBIT';
  if (c.regime in SE_POINT) {
    const L = c.regime.replace('_', '-');
    return block(`Distance from ${L}`, datum(`${L} distance`, 'ro-ld', 'km', 'hero wide') + datum('x · anti-Sun', 'ro-sx', 'km') + datum('y', 'ro-sy', 'km')
      + datum('z · ecliptic normal', 'ro-sz', 'km'), `<p class="fine">${L} offset in the Sun–Earth rotating frame (x anti-sunward); a two-body Earth conic means nothing here.</p>`);
  }
  if (c.regime === 'EARTH_NEAR_HELIO') return '';
  return block(`Osculating orbit · ${lunar ? 'Moon' : 'Earth'}`, datum('a', 'ro-a', 'km') + datum('e', 'ro-e') + datum('i', 'ro-i', '° eq.')
    + datum('Period', 'ro-p', 'd') + datum(lunar ? 'Perilune alt.' : 'Perigee', 'ro-peri', 'km') + datum(lunar ? 'Apolune alt.' : 'Apogee', 'ro-apo', 'km')
    + datum('C3', 'ro-c3', 'km²/s²', 'wide'), `<p class="fine">Two-body ${lunar ? 'Moon' : 'Earth'} conic through the state at the selected epoch.</p>`);
}

function elementsNote(c, w) {
  const e = c.elements, t = Date.parse(e?.epoch || '');
  if (!e || !Number.isFinite(e.a_km)) return '';
  if (!(t > w.t0 - 365 * DAY_MS)) return `<p class="fine">Elements from ${Number.isFinite(t) ? new Date(t).getUTCFullYear() : 'an unknown epoch'} predate the current orbit.</p>`;
  return `<p class="fine">Last known elements (${escapeHtml(e.epoch)}, ${escapeHtml(e.center || 'earth')}-centred): a ${fmt(e.a_km)} km · e ${fmt(e.e, 3)} · i ${fmt(e.i_deg, 1)}° · P ${fmt(e.period_d, 2)} d</p>`;
}

// Station chips (review V10): the station code as text, the bead colour only as a grouping hint. `tracklets` is the
// TrackletLayer (items {id, stn, n}; stations in first-seen order, the same cycle the beads use).
function stationChips(T, tracklets) {
  const items = (tracklets?.items || []).filter(x => x.id === T.id);
  if (!items.length) return '';
  const by = new Map();
  for (const x of items) by.set(x.stn, (by.get(x.stn) || 0) + x.n);
  const color = stn => STATION_COLORS[Math.max(0, (tracklets.stations || []).indexOf(stn)) % STATION_COLORS.length];
  return `<p class="fine">Tracklet beads · ${items.length} tracklet${items.length > 1 ? 's' : ''}, ${items.reduce((s, x) => s + x.n, 0)} obs, by station</p>`
    + `<div class="stations">${[...by].map(([stn, n]) => `<span class="stn" style="--c:${color(stn)}"><i></i>${escapeHtml(stn)}<small>${n}</small></span>`).join('')}</div>`;
}

export function renderTarget(el, T, w, tracklets = null) {
  const c = T.cat, n = notes(T, w.t), ids = [c.norad && `NORAD ${c.norad}`, c.cospar, c.horizons_id && `Horizons ${c.horizons_id}`].filter(Boolean).join(' · ');
  el.innerHTML = `<section class="metric ident"><h2>Selected</h2><div class="sel-name"><i class="swatch" style="background:${T.css}"></i>${escapeHtml(c.name || T.label)}</div>`
    + `<p class="fine">${escapeHtml([T.regime.label, c.obj_type?.replaceAll('_', ' '), c.status && `${c.status}${c.status_date ? ` (${c.status_date})` : ''}`, `tier ${T.tier}`].filter(Boolean).join(' · '))}</p>`
    + (ids ? `<p class="fine">${escapeHtml(ids)}</p>` : '') + (c.group && c.group !== T.regime.label ? `<p class="fine">${escapeHtml(c.group)}</p>` : '')
    + `<p id="ro-epoch" class="${n.stale ? 'caveat' : 'fine'}"${n.epoch ? '' : ' hidden'}>${escapeHtml(epochLine(n))}</p>`
    + (n.caveat ? `<p class="caveat">⚠ ${escapeHtml(n.caveat)}</p>` : '') + (n.note ? `<p class="fine">${escapeHtml(n.note)}</p>` : '')
    + (T.snapshotStale ? '<p class="caveat">⚠ NEOCP snapshot older than 3 h: this fit may be out of date.</p>' : '')
    + (T.qualityNote ? `<p class="fine">${escapeHtml(T.qualityNote)}</p>` : '') + '</section>'
    + (T.draw && T.track ? block('Geocentric state · ICRF', datum('Earth range', 'ro-range', 'km', 'hero wide') + `<div class="datum wide"><dd class="aside" id="ro-range-units">—</dd></div>`
        + datum('Speed', 'ro-speed', 'km/s') + datum('Moon distance', 'ro-moon', 'km')
        + '<div class="datum wide"><dt>RA · Dec · geometric</dt><dd class="radec"><span id="ro-ra">—</span>  <span id="ro-dec">—</span></dd></div>'
        + datum('Sun–Earth–object', 'ro-elong', '°') + datum('Selenocentric', 'ro-seleno')) + orbitBlock(T)
      : `<section class="metric"><h2>Listed, not drawn</h2><p class="fine">${T.draw ? `This build has no track for it${T.why ? `: ${escapeHtml(T.why)}` : '.'}` : 'No public ephemeris covers the window.'}</p>${elementsNote(c, w)}</section>`)
    + `<section class="metric"><h2>Data</h2><dl class="data-list">${datum('Source', 'ro-src', '', 'wide')}${datum('Quality', 'ro-q', '', 'wide')}${datum('Age', 'ro-age')}${datum('σ', 'ro-sigma', 'km')}${datum('Coverage', 'ro-cov', '', 'wide')}</dl>`
    + stationChips(T, tracklets) + `${c.notes ? `<p class="fine notes">${escapeHtml(String(c.notes).slice(0, 400))}</p>` : ''}</section>`;
}

export function updateTarget(T, w, t) {
  const $ = id => document.getElementById(id), set = (id, text) => { const e = $(id); if (e) e.textContent = text; };
  const seg = T.seg;
  if (T.draw && T.track) {
    if (T.has) {
      const s = T.state, r = Math.hypot(s[0], s[1], s[2]), m = w.moon, sun = w.sun;
      const dm = Math.hypot(s[0] - m[0], s[1] - m[1], s[2] - m[2]), eq = equatorial(s);
      const elong = Math.acos(Math.max(-1, Math.min(1, (s[0] * sun[0] + s[1] * sun[1] + s[2] * sun[2]) / (r * Math.hypot(sun[0], sun[1], sun[2]))))) * 180 / Math.PI;
      set('ro-range', fmt(r)); set('ro-range-units', kmUnits(r)); set('ro-speed', fmt(Math.hypot(s[3], s[4], s[5]), 3)); set('ro-moon', fmt(dm));
      set('ro-ra', angularText(eq.ra_deg, true)); set('ro-dec', angularText(eq.dec_deg)); set('ro-elong', fmt(elong, 1));
      const lunar = T.cat.regime === 'LUNAR_ORBIT', rel = lunar || dm < 66000 ? [0, 1, 2, 3, 4, 5].map(k => s[k] - m[k]) : null;
      set('ro-seleno', rel ? `${fmt(Math.hypot(rel[0], rel[1], rel[2]))} km · ${fmt(Math.hypot(rel[3], rel[4], rel[5]), 3)} km/s` : '—');
      if (T.cat.regime in SE_POINT) {
        const L = w.lagrange.out[SE_POINT[T.cat.regime]], d = [0, 1, 2].map(k => s[k] - L[k]), R = w.frames.matrix('se', t, new Float64Array(9));
        set('ro-ld', fmt(Math.hypot(...d))); ['ro-sx', 'ro-sy', 'ro-sz'].forEach((id, k) => set(id, fmt(R[k * 3] * d[0] + R[k * 3 + 1] * d[1] + R[k * 3 + 2] * d[2])));
      }
      const o = osculating(lunar ? rel : s, lunar ? GM_MOON : GM_EARTH), R0 = lunar ? RM_KM : RE_KM;
      set('ro-a', o.e < 1 ? fmt(o.a) : '—'); set('ro-e', fmt(o.e, 4)); set('ro-i', fmt(o.inclination, 2)); set('ro-p', Number.isFinite(o.period) ? fmt(o.period / 86400, 3) : '—');
      set('ro-peri', fmt(o.periapsis - (lunar ? R0 : 0))); set('ro-apo', Number.isFinite(o.apoapsis) ? fmt(o.apoapsis - (lunar ? R0 : 0)) : '—'); set('ro-c3', fmt(o.c3, 3));
    } else for (const id of ['ro-range', 'ro-speed', 'ro-moon', 'ro-ra', 'ro-dec', 'ro-elong', 'ro-seleno', 'ro-a', 'ro-e', 'ro-i', 'ro-p', 'ro-peri', 'ro-apo', 'ro-c3',
      'ro-ld', 'ro-sx', 'ro-sy', 'ro-sz']) set(id, '—');
    if (!T.has) set('ro-range-units', T.track ? 'No data at this epoch (outside coverage or in a gap).' : 'Track not loaded.');
  }
  const age = dataAge(T, t), segs = T.track?.segments || [], n = notes(T, t);
  if ($('ro-epoch')) { set('ro-epoch', epochLine(n)); $('ro-epoch').className = n.stale ? 'caveat' : 'fine'; $('ro-epoch').hidden = !n.epoch; }
  if ($('ro-age')) $('ro-age').closest('.datum').hidden = !!n.epoch;     // the epoch line above already gives the GP age
  const srcs = (T.cat.sources || []).map(s => `${s.kind}:${s.ref}`);
  set('ro-src', seg ? `${n.src} [${badgeOf(seg.src)}]` : srcs.slice(0, 3).join(', ') + (srcs.length > 3 ? ` +${srcs.length - 3} more` : '') || '—');
  set('ro-q', seg ? `${seg.quality} · ${QUALITY[seg.quality] || ''}` : '—');
  set('ro-age', age ? `${durationText(age.ms)} (${age.label})` : '—');
  const i = seg ? Math.max(0, Math.min(seg.n - 1, Math.round((t - seg.t0_ms) / 1000 / seg.step_s))) : -1;
  set('ro-sigma', seg?.sigma ? fmt(seg.sigma[i], 1) : '—');
  set('ro-cov', segs.length ? `${utc(segs[0].t0_ms).slice(0, 16)} → ${utc(segs.at(-1).t1_ms).slice(0, 16)} · ${segs.length} segment${segs.length > 1 ? 's' : ''}${segs.some(s => s.join === 'gap') ? ' · gap' : ''}` : '—');
}

export function renderSituation(el, w, app, t) {
  const counts = new Map(), live = T => T.group === 'NEOCP live', neocpColor = w.regimes.get('NEOCP_GEO')?.color;
  for (const T of w.targets) {
    // Live NEOCP objects get their own row whatever their regime (CEW2VG2 is heliocentric).
    const r = live(T) ? { key: 'neocp-live', label: 'NEOCP live', color: neocpColor, order: w.regimes.get('NEOCP_GEO').order - .5 } : T.regime;
    const c = counts.get(r.key) || { label: r.label, color: r.color, order: r.order, shown: 0, live: 0, total: 0 };
    c.total++; if (T.has) c.live++; if (T.has && app.isVisible(T)) c.shown++;
    counts.set(r.key, c);
  }
  const rows = [...counts.values()].sort((a, b) => a.order - b.order)
    .map(c => `<tr><td><i class="swatch" style="background:${c.color}"></i>${escapeHtml(c.label)}</td><td>${c.shown}</td><td>${c.live}</td><td>${c.total}</td></tr>`).join('');
  const neocp = w.targets.filter(T => T.source === 'neocp' || (T.cat.regime === 'NEOCP_GEO' || /^NEOCP live/.test(T.group)) && T.tier === 1);
  const stale = w.targets.filter(T => T.draw && app.isVisible(T) && isStale(T, t));
  const ending = w.targets.filter(T => T.track && Math.abs(T.track.t1_ms - t) < 7 * DAY_MS && T.track.t1_ms < w.t1);
  const list = (arr, f) => arr.length ? `<ul class="mini">${arr.map(f).join('')}</ul>` : '<p class="fine">None.</p>';
  // §6.4: the snapshot-age alarm lives here, not in the scene (review V11).
  const snapshotOld = neocp.some(T => T.snapshotStale) ? '<p class="caveat">⚠ NEOCP snapshot older than 3 h: tracklets and fits marked stale</p>' : '';
  el.innerHTML = `<section class="metric"><h2>Situation</h2><table class="counts"><thead><tr><th>Regime</th><th title="drawn now">shown</th><th title="data at this epoch">live</th><th>all</th></tr></thead><tbody>${rows}</tbody></table>`
    + '<p class="fine">Click a marker or a list row to select it; double-click to fly to it.</p></section>'
    + `<section class="metric"><h2>NEOCP</h2>${snapshotOld}${list(neocp, T => `<li data-id="${escapeHtml(T.id)}">${T.snapshotStale ? '⚠ ' : ''}${escapeHtml(T.label)} · ${T.has ? rangeText(Math.hypot(T.state[0], T.state[1], T.state[2])) : 'no data'}${app.isVisible(T) ? '' : ' <span class="muted">(hidden)</span>'}</li>`)}${app.neocpNote || ''}</section>`
    + `<section class="metric"><h2>Stale or extrapolated</h2>${list(stale, T => `<li data-id="${escapeHtml(T.id)}" title="${escapeHtml(T.caveat || '')}">⚠ ${escapeHtml(T.label)} · ${escapeHtml(T.seg?.quality || '')}</li>`)}</section>`
    + `<section class="metric"><h2>Data ending within ±7 d</h2>${list(ending, T => `<li data-id="${escapeHtml(T.id)}">${escapeHtml(T.label)} · ${utc(T.track.t1_ms).slice(0, 16)}</li>`)}</section>`;
}
