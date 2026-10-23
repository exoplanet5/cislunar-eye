// DOM labels with priority, a cap and a screen grid hash (ARCHITECTURE §9.5; replaces MMX's O(N²) occupied.some).
export class Occupancy {
  constructor(cell = 48) { this.cell = cell; this.map = new Map(); this.hard = []; }
  clear() { this.map.clear(); this.hard = []; }
  *cells(b, pad) {
    const c = this.cell, x0 = Math.floor((b.x - pad) / c), x1 = Math.floor((b.x + b.width + pad) / c);
    const y0 = Math.floor((b.y - pad) / c), y1 = Math.floor((b.y + b.height + pad) / c);
    for (let i = x0; i <= x1; i++) for (let j = y0; j <= y1; j++) yield i * 4096 + j;
  }
  // hard boxes (title, legend, credit, banner, Sun glyph) are never covered, not even by always-on labels
  add(b, hard = false) { if (hard) this.hard.push(b); for (const k of this.cells(b, 0)) { let l = this.map.get(k); if (!l) this.map.set(k, l = []); l.push(b); } }
  collidesHard(b) { return this.hard.some(r => b.x < r.x + r.width && b.x + b.width > r.x && b.y < r.y + r.height && b.y + b.height > r.y); }
  // number of placed boxes b touches (no padding): the cost of forcing an always-on label there
  hits(b) {
    const seen = new Set();
    for (const k of this.cells(b, 0)) for (const r of this.map.get(k) || [])
      if (b.x < r.x + r.width && b.x + b.width > r.x && b.y < r.y + r.height && b.y + b.height > r.y) seen.add(r);
    return seen.size;
  }
  collides(b, pad = 4) {
    for (const k of this.cells(b, pad)) for (const r of this.map.get(k) || [])
      if (b.x < r.x + r.width + pad && b.x + b.width + pad > r.x && b.y < r.y + r.height + pad * .75 && b.y + b.height + pad * .75 > r.y) return true;
    return false;
  }
}

export class Labels {
  constructor(layer, cap = 40) { this.layer = layer; this.cap = cap; this.items = new Map(); }

  item(key, text, cls) {
    let it = this.items.get(key);
    if (!it) {
      const el = document.createElement('div'); el.className = `body-label ${cls || ''}`; el.hidden = true;
      this.layer.appendChild(el); it = { el, text: null, size: null }; this.items.set(key, it);
    }
    if (it.text !== text) { it.el.textContent = it.text = text; it.size = null; }
    return it;
  }

  // candidates: {key, text, cls, x, y, priority, always?, free?}; highest priority first, at most `cap` placed
  // (always- and free-items, e.g. Lagrange markers, are extra).
  place(candidates, w, h, occupied) {
    candidates.sort((a, b) => b.priority - a.priority);
    const shown = new Set();
    let count = 0;
    for (const c of candidates) {
      if (count >= this.cap && !c.always && !c.free) continue;
      const it = this.item(c.key, c.text, c.cls);
      if (it.el.hidden) it.el.hidden = false;
      it.size ||= [it.el.offsetWidth, it.el.offsetHeight];
      const [width, height] = it.size;
      // always-on labels (Earth, Moon, the frame's L points, selection) get a second ring before they are forced over markers
      const near = [[9, -14], [9, 4], [-width - 9, -14], [-width - 9, 4]];
      const offsets = c.always ? [...near, [9, -28], [9, 18], [-width - 9, -28], [-width - 9, 18], [-width / 2, -30], [-width / 2, 20]] : near;
      let box = null;
      for (const [dx, dy] of offsets) {
        const b = { x: Math.max(4, Math.min(w - width - 4, c.x + dx)), y: c.y + dy, width, height };
        if (b.y < 4 || b.y + height > h - 4) continue;
        if (!occupied.collides(b)) { box = b; break; }
      }
      if (!box && c.always) {                          // forced: the offset that covers the fewest markers and labels
        let best = Infinity;
        for (const [dx, dy] of offsets) {
          const b = { x: Math.max(4, Math.min(w - width - 4, c.x + dx)), y: c.y + dy, width, height }, n = occupied.hits(b);
          if (b.y >= 4 && b.y + height <= h - 4 && n < best && !occupied.collidesHard(b)) { best = n; box = b; }
        }
      }
      if (!box) { it.el.hidden = true; continue; }
      occupied.add(box); shown.add(c.key); if (!c.free) count++;
      it.el.style.transform = `translate(${Math.round(box.x)}px,${Math.round(box.y)}px)`;
    }
    for (const [key, it] of this.items) if (!shown.has(key) && !it.el.hidden) it.el.hidden = true;
    return shown;
  }
}
