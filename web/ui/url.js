// URL state (ARCHITECTURE §10): ?t=<ISO>&frame=&focus=&sel=&layers=&tiers=&trail=&view=&cam=, written with replaceState
// (throttled); on file:// where a query rewrite is refused the hash carries the same parameters.
export function readState(loc = location) {
  const q = new URLSearchParams(loc.search), h = new URLSearchParams(loc.hash.replace(/^#/, ''));
  const get = k => h.get(k) ?? q.get(k);
  const csv = k => (get(k) == null ? null : get(k).split(',').filter(Boolean));
  const cam = (get('cam') || '').split(',').map(Number);
  return { t: get('t'), frame: get('frame'), focus: get('focus'), sel: get('sel'), layers: csv('layers'), tiers: csv('tiers'),
    trail: get('trail'), view: get('view'), cam: cam.length === 3 && cam.every(Number.isFinite) ? cam : null };
}

let timer = null, pending = null, useHash = false;
export function writeState(params) {
  pending = params;
  if (timer) return;
  timer = setTimeout(() => {
    timer = null;
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(pending)) if (v != null && v !== '') q.set(k, Array.isArray(v) ? v.join(',') : v);
    const text = q.toString().replaceAll('%2C', ',').replaceAll('%3A', ':');
    if (!useHash) {
      try { history.replaceState(null, '', `${location.pathname}?${text}`); return; } catch { useHash = true; }
    }
    try { history.replaceState(null, '', `${location.pathname}${location.search}#${text}`); } catch { /* read-only location */ }
  }, 500);
}
