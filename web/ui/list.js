// Target list (ARCHITECTURE §10, design §3.2): catalog targets by catalog group (requested NEOCP objects live in the
// NEOCP dock), tier chips and search, per-row and per-group visibility, marker glyphs in the family colour, sticky group
// heads counting shown / all, "+ n Tier 3 · show" for rows a chip hides, live range / source / quality refreshed in place.
// Keyboard: the list is one tab stop (roving tabindex over rows and group heads); ↑ ↓ Home End move, Enter selects a row
// (on the selected row: flies there) or folds a group, Space shows or hides the row or the group. A mouse click selects
// without focusing the row (the page's Space still plays). Each option carries its whole state as its aria-label (the
// eye and ⌖ inside are mouse-only: aria-hidden, never tab stops; Space and F cover them).
import { escapeHtml, rangeText, badgeOf, durationText, glyphClass, groupShort, SOURCE_LABEL } from './format.js';
import { dataAge, isStale, notes } from './readout.js';

const ITEM = '.row-target, .gtog, .more', STORE_KEY = 'cle.collapsed', SHORT_Q = { extrap: 'extr' };
const listable = T => T.source !== 'request' && !T.removed;
const shown = T => T.visible && T.has;                                // drawn at this epoch, as Situation counts it
// Writes only on change: refresh() runs twice a second over every row.
const text = (el, s) => { if (el.textContent !== s) el.textContent = s; }, attr = (el, k, v) => { v = String(v); if (el.getAttribute(k) !== v) el.setAttribute(k, v); };
const keyOf = el => (el.dataset.id ? `id:${el.dataset.id}` : `${el.className}:${el.closest('.grp').firstElementChild.dataset.group}`);   // survives render()

export function searchText(T) {
  const c = T.cat;
  return [c.id, c.name, c.label, ...(Array.isArray(c.aliases) ? c.aliases : String(c.aliases || '').split(';')), c.norad, c.cospar, c.horizons_id]
    .filter(x => x != null && x !== '').join(' ').toLowerCase();
}

export class TargetList {
  constructor(el, world, app) {
    this.el = el; this.w = world; this.app = app; this.query = ''; this.rows = new Map(); this.heads = new Map(); this.cursor = null; this.followed = null;
    try { this.collapsed = new Set(JSON.parse(app.store?.getItem(STORE_KEY) || '[]')); } catch { this.collapsed = new Set(); }
    el.setAttribute('role', 'listbox'); if (!el.hasAttribute('aria-label')) el.setAttribute('aria-label', 'Targets');
    el.addEventListener('click', e => this.click(e));
    el.addEventListener('keydown', e => this.key(e));
    el.addEventListener('focusin', e => { const item = e.target.closest(ITEM); if (item) this.rove(item); });
    el.addEventListener('mousedown', e => {
      if (!e.target.closest('.row-target, .group-head, .more')) return;
      e.preventDefault();
      const a = document.activeElement, item = e.target.closest(ITEM);
      if (el.contains(a)) item?.focus({ preventScroll: true });          // keyboard focus already in the list follows the click
      else { if (a && a !== document.body) a.blur(); if (item) this.rove(item); }
    });
    if (typeof ResizeObserver === 'function') new ResizeObserver(() => this.alignEnd()).observe(el, { box: 'border-box' });
  }

  // The list rests on whole rows (CSS scroll snap, list.css), but the largest scroll offset is a rest position too: the
  // bottom padding (≥ 16 px, the fade) is sized so that offset is itself a row snap position (the last rows just under
  // their sticky head), else the end of the list could leave a row half under a head.
  alignEnd() {
    const el = this.el, base = 16, last = el.lastElementChild, heads = [...el.querySelectorAll('.group-head')];
    if (!heads.length || !last || !el.clientHeight) { el.style.paddingBottom = ''; return; }
    const top = el.getBoundingClientRect().top - el.scrollTop, end = last.getBoundingClientRect().bottom - top, want = end + base - el.clientHeight;
    if (want <= 0) { el.style.paddingBottom = ''; return; }
    // snap offsets: rows (and "+ n Tier 3") at their top less the head height; heads (sticky: their group's top) less 4 px
    const m = x => parseFloat(getComputedStyle(x).scrollMarginTop) || 0, rows = [...el.querySelectorAll('.row-target, .more')], rm = rows.length ? m(rows[0]) : 0, hm = m(heads[0]);
    const snaps = [...rows.map(r => r.getBoundingClientRect().top - top - rm), ...heads.map(h => h.parentElement.getBoundingClientRect().top - top - hm)];
    const snap = Math.min(...snaps.filter(s => s >= want - .5)), pad = Number.isFinite(snap) ? base + snap - want : base;
    if (Math.abs((parseFloat(el.style.paddingBottom) || base) - pad) > .5) el.style.paddingBottom = `${pad.toFixed(1)}px`;
  }

  listed(T) {
    if (!listable(T)) return false;
    if (this.query) return searchText(T).includes(this.query);
    return T.tier <= 2 || this.app.chipOn(T) || T.visible;
  }

  groupVisible(g) { return this.w.targets.some(T => T.group === g && listable(T) && T.visible); }

  counts() {
    const pool = this.w.targets.filter(listable);
    return { shown: pool.filter(shown).length, drawable: pool.filter(T => T.draw).length, listed: pool.length };
  }

  render() {
    const { w, el } = this, focused = el.contains(document.activeElement) && document.activeElement.closest(ITEM), groups = new Map();
    if (focused) this.cursor = keyOf(focused);
    for (const T of w.targets) if (listable(T)) (groups.get(T.group) || groups.set(T.group, []).get(T.group)).push(T);
    const rank = ([g, list]) => w.groupOrder.get(g) ?? 100 + list.reduce((m, T) => Math.min(m, T.regime.order), 99);
    const html = [];
    for (const [g, all] of [...groups].sort((a, b) => rank(a) - rank(b) || a[0].localeCompare(b[0]))) {
      const list = all.filter(T => this.listed(T)), open = !!this.query || !this.collapsed.has(g), hidden = this.query ? 0 : all.length - list.length;
      if (this.query && !list.length) continue;
      list.sort((a, b) => a.tier - b.tier || a.regime.order - b.regime.order || (a.label > b.label ? 1 : -1));
      html.push(`<div class="grp" role="group" aria-label="${escapeHtml(g)}">${this.head(g, all, open)}`,
        ...(open ? list.map(T => this.row(T)) : []),
        open && hidden ? `<button type="button" class="more" tabindex="-1">+ ${hidden} Tier 3 · show</button>` : '', '</div>');
    }
    el.innerHTML = html.join('') || `<p class="empty">No targets match “${escapeHtml(this.query)}”.</p>`;
    this.rows.clear(); this.heads.clear();
    for (const row of el.querySelectorAll('.row-target')) this.rows.set(row.dataset.id, row);
    for (const head of el.querySelectorAll('.group-head')) this.heads.set(head.dataset.group, head);
    const items = [...el.querySelectorAll(ITEM)], cur = items.find(x => keyOf(x) === this.cursor) || this.rows.get(this.app.selected) || items[0];
    if (cur) { cur.tabIndex = 0; if (focused) cur.focus({ preventScroll: true }); }
    this.alignEnd();
    this.refresh(w.t);
  }

  // The group's short name ("EM libration · DRO"; the full one is the title); Space on its toggle shows / hides the group.
  head(g, all, open) {
    const name = escapeHtml(g), on = this.groupVisible(g), color = all.reduce((a, T) => (T.regime.order < a.regime.order ? T : a)).css;
    return `<div class="group-head" data-group="${name}" title="${name}"><button type="button" class="eye" tabindex="-1" aria-hidden="true" aria-pressed="${on}">${on ? '◉' : '○'}</button>`
      + `<i class="sw" style="--c:${color}"></i><button type="button" class="gtog" tabindex="-1" aria-expanded="${open}" aria-label="${escapeHtml(this.headLabel(g, all))}">${open ? '▾' : '▸'} ${escapeHtml(groupShort(g))}</button>`
      + `<span class="count" aria-hidden="true"><b>${all.filter(shown).length}</b>/${all.length}</span></div>`;
  }

  headLabel(g, all) { return `${g}, ${all.filter(shown).length} of ${all.length} shown${this.groupVisible(g) ? '' : ', hidden'}`; }

  // The tier tag sits after the name (not inside its ellipsis): .name is a flex box whose .nt shrinks first.
  row(T) {
    const name = escapeHtml(T.label), notrack = T.draw && !T.track;
    return `<div class="row-target${T.draw ? '' : ' nodraw'}${notrack ? ' notrack' : ''}" data-id="${escapeHtml(T.id)}" role="option" tabindex="-1" title="${escapeHtml(this.tip(T))}">`
      + `<button type="button" class="eye" tabindex="-1" aria-hidden="true"${T.draw ? '' : ' disabled'}></button><i class="${glyphClass(T)}" style="--c:${T.css}"></i>`
      + `<span class="name"><span class="nt">${name}</span>${T.tier > 1 ? `<span class="tier" aria-hidden="true">T${T.tier}</span>` : ''}</span>`
      + (notrack ? `<span class="nd" title="${escapeHtml(T.why || 'drawable, but this build has no track for it')}">${T.why ? 'not drawn' : 'no track'}</span>`
        : T.draw ? `<span class="badges"><b class="src"></b><span class="q"></span><span class="warn"></span></span><span class="range"></span><button type="button" class="focus" tabindex="-1" aria-hidden="true" title="Fly to (F)">⌖</button>`
        : '<span class="nd">listed, not drawn</span>')
      + '</div>';
  }

  // "FX-RB, tier 2, extrapolated, extrap, stale, 0.97 LD, hidden"
  rowLabel(T, t, seg, stale) {
    const head = [T.label, T.tier > 1 && `tier ${T.tier}`];
    if (!T.draw) return [...head, 'listed, not drawn'].filter(Boolean).join(', ');
    if (!T.track) return [...head, T.why ? 'not drawn' : 'no track'].filter(Boolean).join(', ');
    const b = seg ? badgeOf(seg.src) : '', q = seg?.quality || '';
    return [...head, SOURCE_LABEL[b], q, stale && 'stale', T.has ? rangeText(Math.hypot(T.state[0], T.state[1], T.state[2])) : 'no data at this epoch',
      T.visible ? '' : 'hidden'].filter(Boolean).join(', ');
  }

  // Row tooltip: the full name, status, the GP-epoch line, caveats and notes (the inspector shows the same as tags and cards).
  tip(T) {
    const c = T.cat, n = notes(T, this.w.t), status = c.status && c.status !== 'unknown' ? [c.status.replaceAll('_', ' '), c.status_date].filter(Boolean).join(' ') : '';
    return [c.name || T.label, status, c.left_neocp && `left the NEOCP ${String(c.left_neocp).slice(0, 10)}`, n.epoch && `${n.stale ? '⚠ ' : ''}${n.epoch}`,
      n.caveat && `⚠ ${n.caveat}`, n.note, T.qualityNote, T.draw && !T.track && T.why].filter(Boolean).join(' — ');
  }

  // In place (no rebuild, so keyboard focus and scroll survive): visibility, selection, glyph, range, badges, group counts.
  refresh(t) {
    const sel = this.app.selected;
    for (const [id, row] of this.rows) {
      const T = this.w.byId.get(id), eye = row.firstElementChild, seg = T.seg, stale = isStale(T, t), q = seg?.quality || '';
      row.classList.toggle('off', !T.visible); row.classList.toggle('selected', id === sel); attr(row, 'aria-selected', id === sel);
      text(eye, T.visible ? '◉' : '○'); attr(eye, 'aria-pressed', T.visible);
      attr(row.children[1], 'class', glyphClass(T, stale || q === 'extrap'));
      attr(row, 'aria-label', this.rowLabel(T, t, seg, stale));
      const range = row.querySelector('.range');
      if (!range) continue;
      text(range, T.has ? rangeText(Math.hypot(T.state[0], T.state[1], T.state[2])) : 'no data');
      const badges = range.previousElementSibling, [src, ql, warn] = badges.children, age = stale && dataAge(T, t);
      attr(badges, 'data-q', q); text(src, seg ? badgeOf(seg.src) : ''); text(ql, SHORT_Q[q] || q); attr(ql, 'class', `q q-${q || 'none'}`);
      text(warn, stale ? '⚠' : ''); attr(warn, 'title', stale ? `stale${age ? ` · ${durationText(age.ms)} since ${age.label}` : ''}` : '');
    }
    for (const [g, head] of this.heads) {
      const on = this.groupVisible(g), eye = head.firstElementChild, all = this.w.targets.filter(T => T.group === g && listable(T));
      text(eye, on ? '◉' : '○'); attr(eye, 'aria-pressed', on);
      text(head.querySelector('.count b'), String(all.filter(shown).length));
      attr(head.querySelector('.gtog'), 'aria-label', this.headLabel(g, all));
    }
    if (sel !== this.followed) { this.followed = sel; this.rows.get(sel)?.scrollIntoView({ block: 'nearest' }); }
  }

  // Situation regime row → expand and scroll to the first group (in list order) holding that regime.
  scrollToRegime(key) {
    const groups = new Set(this.w.targets.filter(T => listable(T) && T.regime.key === key).map(T => T.group));
    const g = [...this.heads.keys()].find(x => groups.has(x));
    if (g == null) return false;
    if (this.collapsed.delete(g)) { this.save(); this.render(); }
    this.heads.get(g).parentElement.scrollIntoView({ block: 'start' });
    return true;
  }

  click(e) {
    const { app, w } = this, btn = e.target.closest('button'), row = e.target.closest('.row-target'), head = e.target.closest('.group-head');
    if (btn?.classList.contains('more')) app.setChip('t3', true);
    else if (row) {
      const id = row.dataset.id;
      if (btn?.classList.contains('eye')) app.setVisible(id, !w.byId.get(id).visible);
      else if (btn?.classList.contains('focus')) app.focus(id);
      else if (e.detail > 1) app.focus(id);                          // double click (counted by the platform, so a render between clicks is fine)
      else app.select(id);
    } else if (head && btn?.classList.contains('eye')) app.setGroupVisible(head.dataset.group, !this.groupVisible(head.dataset.group));
    else if (head) this.fold(head.dataset.group);
  }

  fold(g) { this.collapsed.has(g) ? this.collapsed.delete(g) : this.collapsed.add(g); this.save(); this.render(); }

  save() { try { this.app.store?.setItem(STORE_KEY, JSON.stringify([...this.collapsed])); } catch { /* storage full or denied */ } }

  key(e) {
    const item = e.target.closest(ITEM);
    if (!item || e.metaKey || e.ctrlKey || e.altKey) return;
    const items = [...this.el.querySelectorAll(ITEM)], i = items.indexOf(item), row = item.classList.contains('row-target') && item, g = item.closest('[data-group]')?.dataset.group;
    const move = { ArrowDown: i + 1, ArrowUp: i - 1, Home: 0, End: items.length - 1 }[e.key];
    if (move != null) { e.preventDefault(); items[move]?.focus(); return; }
    if (e.key !== 'Enter' && e.key !== ' ') return;
    e.stopPropagation();                                              // the page's Space (play) and Enter (fly) keys stay out
    if (e.target !== item) return;                                    // a row's eye or ⌖ button: its own click
    if (row && e.key === 'Enter') { e.preventDefault(); this.app.selected === row.dataset.id ? this.app.focus(row.dataset.id) : this.app.select(row.dataset.id); }
    else if (row) { e.preventDefault(); if (this.w.byId.get(row.dataset.id).draw) this.app.setVisible(row.dataset.id, !this.w.byId.get(row.dataset.id).visible); }
    else if (e.key === ' ' && item.classList.contains('gtog')) { e.preventDefault(); this.app.setGroupVisible(g, !this.groupVisible(g)); }
  }

  rove(item) {
    this.cursor = keyOf(item);
    for (const x of this.el.querySelectorAll('[tabindex="0"]')) if (x !== item) x.tabIndex = -1;
    item.tabIndex = 0;
  }
}
