import { LD_KM, RE_KM } from '../states.js';

export const fmt = (value, digits = 0) => Number.isFinite(value)
  ? value.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits }) : '—';

export function angularText(degrees, rightAscension = false) {
  const sign = degrees < 0 ? '−' : '+';
  let total = Math.round(Math.abs(degrees) / (rightAscension ? 15 : 1) * 36000) / 10;
  if (rightAscension) total %= 86400;
  const major = Math.floor(total / 3600), minute = Math.floor(total / 60) % 60, second = (total % 60).toFixed(1).padStart(4, '0');
  return rightAscension ? `${String(major).padStart(2, '0')}h${String(minute).padStart(2, '0')}m${second}s`
    : `${sign}${String(major).padStart(2, '0')}°${String(minute).padStart(2, '0')}′${second}″`;
}

export const utc = t => new Date(t).toISOString().slice(0, 19).replace('T', ' ');
export const iso = t => new Date(t).toISOString().replace(/\.\d{3}Z$/, 'Z');
export const hm = t => utc(t).slice(11, 16);
// Calendar day of t: "10-15" in the year of ref (the data window), "2025-12-31" in any other year.
export const dayText = (t, ref) => { const d = utc(t).slice(0, 10); return d.slice(0, 4) === utc(ref).slice(0, 4) ? d.slice(5) : d; };

// km below 100,000 km, LD below 1 Mkm (≈ 2.6 LD), Mkm beyond: one unit per list or chevron set from 1 Mkm out.
export function rangeText(km) {
  if (!Number.isFinite(km)) return '—';
  if (km < 1e5) return `${fmt(km)} km`;
  if (km < 1e6) return `${fmt(km / LD_KM, 2)} LD`;
  return `${fmt(km / 1e6, 2)} Mkm`;
}

// [value, unit] of a duration: minutes below 1 h, hours below 2 d, days beyond (whole days from 30 d).
export function durationParts(ms) {
  const s = Math.abs(ms) / 1000;
  if (s < 3600) return [String(Math.round(s / 60)), 'min'];
  if (s < 2 * 86400) return [fmt(s / 3600, 1), 'h'];
  return [fmt(s / 86400, s < 30 * 86400 ? 1 : 0), 'd'];
}
export const durationText = ms => durationParts(ms).join(' ');

export const radiiText = km => `${fmt(km / RE_KM, 2)} R⊕ · ${fmt(km / LD_KM, 3)} LD`;

export const SOURCE_BADGE = { horizons: 'H', fo: 'F', bgtle: 'T', gp: 'G', hpop: 'S', extrap: 'X', neocp: 'N', pluto: 'N', de440s: 'D' };
export const SOURCE_LABEL = { H: 'Horizons', G: 'Space-Track GP', F: 'find_orb fit', T: 'Bill Gray TLE', S: 'state vector + HPOP', N: 'MPC + neocp2', X: 'extrapolated', D: 'DE440s' };
export const badgeOf = src => SOURCE_BADGE[(src || '').split(/[:+_]/)[0]] || '·';      // gp+nbody → G, pluto_mpec → N

// Marker glyph classes (list rows, inspector, NEOCP rows; the scene's markers.targetStyle): shape = object type
// (model.shapeOf: ● payload, ■ rocket body, ◆ debris and other), hollow = extrapolated or stale, dimmed = derelict,
// dashed = listed but not drawn. The colour arrives as an inline --c (the regime family).
const SHAPE_CLASS = ['pl', 'rb', 'db'];
export const glyphClass = (T, hollow = false) => `g ${SHAPE_CLASS[T.shape] || 'db'}${hollow ? ' ho' : ''}${T.cat?.status === 'derelict' ? ' dr' : ''}${T.draw ? '' : ' nd'}`;

// A catalog group without its trailing qualifier: "Cislunar HEO (apogee ≥ 100 000 km)" → "Cislunar HEO".
export const groupName = g => String(g).replace(/\s*\([^)]*\)$/, '');
// The target list's group heads (design mockup): the two long catalog groups by a short name; the full name is the title.
const GROUP_SHORT = { 'Earth–Moon libration, DRO & resonant': 'EM libration · DRO', 'Earth-return / near-Earth heliocentric': 'Near-Earth heliocentric' };
export const groupShort = g => GROUP_SHORT[g] || groupName(g);
// Regime labels too long for the 280 / 272 px Inspector's Situation table (≤ 1180 px swaps them in).
export const REGIME_SHORT = { EARTH_NEAR_HELIO: 'Near-Earth helio.' };

export const escapeHtml = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
