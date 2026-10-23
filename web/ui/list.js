// Target list (ARCHITECTURE §10, AM3): grouped by catalog group, tier/natural chips, search, per-row and per-group
// visibility, "listed, not drawn" badge for draw=N rows, "left NEOCP <date>" chip when the catalog entry carries
// left_neocp (ISO date; DW-06), caveat tooltip, live range / source / age cells.
import { escapeHtml, rangeText, badgeOf, durationText } from './format.js';
import { dataAge, isStale, notes } from './readout.js';

const STATUS = { active: '●', derelict: '○', returning: '↩', lost: '?', unknown: '·' };

export function searchText(T) {
  const c = T.cat;
  return [c.id, c.name, c.label, ...(Array.isArray(c.aliases) ? c.aliases : String(c.aliases || '').split(';')), c.norad, c.cospar, c.horizons_id]
    .filter(x => x != null && x !== '').join(' ').toLowerCase();
}

export class TargetList {
  constructor(el, world, app) {
    this.el = el; this.w = world; this.app = app; this.query = ''; this.collapsed = new Set(); this.rows = new Map();
    el.addEventListener('click', e => {
      const row = e.target.closest('[data-id]'), head = e.target.closest('[data-group]');
      if (e.target.closest('.eye') && row) { const T = world.byId.get(row.dataset.id); app.setVisible(T, !T.visible); return; }
      if (e.target.closest('.eye') && head) { app.setGroupVisible(head.dataset.group, !this.groupVisible(head.dataset.group)); return; }
      if (e.target.closest('.focus') && row) { app.focus(row.dataset.id); return; }
      if (row) { app.select(row.dataset.id); return; }
      if (head) { const g = head.dataset.group; this.collapsed.has(g) ? this.collapsed.delete(g) : this.collapsed.add(g); this.render(); }
    });
    el.addEventListener('dblclick', e => { const row = e.target.closest('[data-id]'); if (row) app.focus(row.dataset.id); });
  }

  listed(T) {
    if (this.query) return searchText(T).includes(this.query);
    return T.tier <= 2 || T.natural || this.app.chipOn(T) || T.visible;
  }

  groupVisible(g) { return this.w.targets.some(T => T.group === g && T.visible); }

  render() {
    const groups = new Map();
    for (const T of this.w.targets) if (this.listed(T)) {
      const g = T.group;
      if (!groups.has(g)) groups.set(g, []);
      groups.get(g).push(T);
    }
    const rank = ([g, list]) => this.w.groupOrder.get(g) ?? 100 + list.reduce((m, T) => Math.min(m, T.regime.order), 99);
    const order = [...groups.entries()].sort((a, b) => rank(a) - rank(b) || a[0].localeCompare(b[0]));
    const html = [];
    for (const [g, list] of order) {
      list.sort((a, b) => a.tier - b.tier || a.regime.order - b.regime.order || (a.label > b.label ? 1 : -1));
      const shown = list.filter(T => T.visible).length, open = !this.collapsed.has(g);
      html.push(`<div class="group-head" data-group="${escapeHtml(g)}" aria-expanded="${open}"><button class="eye" aria-pressed="${shown > 0}" title="Show or hide the group">${shown ? '◉' : '○'}</button><span class="gname">${open ? '▾' : '▸'} ${escapeHtml(g)}</span><span class="count">${shown}/${list.length}</span></div>`);
      if (!open) continue;
      for (const T of list) {
        const c = T.cat, sel = this.app.selected === T.id, left = c.left_neocp ? String(c.left_neocp).slice(0, 10) : '', n = notes(T, this.w.t);
        const title = [c.name, left && `left the NEOCP ${left}`, n.epoch && `${n.stale ? '⚠ ' : ''}${n.epoch}`, n.caveat && `⚠ ${n.caveat}`, n.note, T.qualityNote, T.draw && !T.track && T.why].filter(Boolean).join(' — ');
        const notrack = T.draw && !T.track && this.app.loaded;
        html.push(`<div class="row-target${T.visible ? '' : ' off'}${sel ? ' selected' : ''}${T.draw ? '' : ' nodraw'}${notrack ? ' notrack' : ''}" data-id="${escapeHtml(T.id)}" title="${escapeHtml(title)}">`
          + `<button class="eye" aria-pressed="${T.visible}" title="Show or hide"${T.draw ? '' : ' disabled'}>${T.visible ? '◉' : '○'}</button>`
          + `<i class="swatch" style="background:${T.css}"></i>`
          + `<span class="name">${escapeHtml(T.label)}<span class="st" title="${escapeHtml(c.status || '')}">${STATUS[c.status] || ''}</span>`
          + `${T.tier > 1 ? `<span class="tier">T${T.tier}</span>` : ''}${T.natural ? '<span class="tier nat">nat</span>' : ''}`
          + `${left ? `<span class="tier left" title="no longer in the NEOCP listings (last listed ${escapeHtml(left)})">left NEOCP ${escapeHtml(left)}</span>` : ''}</span>`
          + (notrack ? `<span class="nd" title="${escapeHtml(T.why || 'drawable, but this build has no track for it')}">${T.why ? 'not drawn' : 'no track'}</span>`
            : T.draw ? `<span class="badges"><b class="src"></b><span class="q"></span><span class="warn"></span></span><span class="range"></span>`
            : '<span class="nd">listed, not drawn</span>')
          + `<button class="focus" title="Focus">◎</button></div>`);
      }
    }
    this.el.innerHTML = html.join('') || '<p class="fine">No targets match.</p>';
    this.rows.clear();
    for (const row of this.el.querySelectorAll('[data-id]')) this.rows.set(row.dataset.id, row);
    this.refresh(this.w.t);
  }

  refresh(t) {
    for (const [id, row] of this.rows) {
      const T = this.w.byId.get(id);
      if (!T.draw || !row.querySelector('.range')) continue;
      const seg = T.seg, r = T.has ? Math.hypot(T.state[0], T.state[1], T.state[2]) : NaN;
      row.querySelector('.range').textContent = T.has ? rangeText(r) : (T.track ? 'no data' : 'loading');
      row.querySelector('.src').textContent = seg ? badgeOf(seg.src) : '';
      const q = row.querySelector('.q'); q.textContent = seg ? seg.quality : ''; q.className = `q q-${seg?.quality || 'none'}`;
      const age = dataAge(T, t), stale = isStale(T, t);
      row.querySelector('.warn').textContent = stale ? '⚠' : '';
      row.querySelector('.warn').title = stale ? `stale${age ? ` · ${age.label} ${durationText(age.ms)}` : ''}` : '';
    }
  }
}
