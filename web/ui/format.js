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

// km below 100,000 km, LD below 1 Mkm (≈ 2.6 LD), Mkm beyond: one unit per list or chevron set from 1 Mkm out.
export function rangeText(km) {
  if (!Number.isFinite(km)) return '—';
  if (km < 1e5) return `${fmt(km)} km`;
  if (km < 1e6) return `${fmt(km / LD_KM, 2)} LD`;
  return `${fmt(km / 1e6, 2)} Mkm`;
}

export function durationText(ms) {
  const s = Math.abs(ms) / 1000;
  if (s < 3600) return `${Math.round(s / 60)} min`;
  if (s < 2 * 86400) return `${fmt(s / 3600, 1)} h`;
  return `${fmt(s / 86400, s < 30 * 86400 ? 1 : 0)} d`;
}

export const kmUnits = km => `${fmt(km)} km · ${fmt(km / RE_KM, 2)} R⊕ · ${fmt(km / LD_KM, 3)} LD`;

export const SOURCE_BADGE = { horizons: 'H', fo: 'F', bgtle: 'T', gp: 'G', extrap: 'X', neocp: 'N', pluto: 'N', de440s: 'D' };
export const badgeOf = src => SOURCE_BADGE[(src || '').split(/[:+_]/)[0]] || '·';      // gp+nbody → G, pluto_mpec → N

export const escapeHtml = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
