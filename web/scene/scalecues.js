// Scale cues (V12, V1): a scale bar in the scene foot, a toggleable GEO / 1 LD rings layer about Earth, and edge
// chevrons with range for whatever is off screen (Earth, the Moon, SE-L1/L2, the selected target, natural objects).
// The bar and the chevrons are DOM (haloed Menlo like the labels); the rings are Line2 rings like the terminator.
import * as THREE from 'three';
import { ring } from './bodies.js';
import { RE_KM } from '../states.js';
import { apply } from '../frames.js';
import { scaleStep, scaleLabel, edgePoint, placeEdgeBox } from '../camera.js';
import { rangeText } from '../ui/format.js';
import { token } from '../ui/theme.js';

export { edgePoint };

const GEO_KM = 42164, AXIS = new THREE.Vector3(0, 0, 1), DEG = Math.PI / 180;
const CSS = '.scale-bar{position:absolute;right:18px;bottom:44px;z-index:2;display:flex;align-items:center;gap:6px;font-size:10px;color:var(--dim);pointer-events:none;text-shadow:0 0 5px var(--surface)}'
  + '.scale-bar i{display:block;height:5px;border:1px solid var(--dim);border-top:0}'
  + '@media(max-width:1400px){.scale-bar{bottom:24px}}@media(max-width:700px){.scale-bar{right:14px;bottom:22px}}';

function style() {
  if (document.getElementById('scalecues-style')) return;
  const el = document.createElement('style'); el.id = 'scalecues-style'; el.textContent = CSS; document.head.appendChild(el);
}

// "100,000 km" / "1 LD" / "1 Mkm" bracket sized at the focus plane, appended to `parent`: the scene foot's right column
// (#foot-right, where the template lays it out statically above the hint) or the scene itself (absolute, bottom right).
export class ScaleBar {
  constructor(parent) {
    style();
    this.el = document.createElement('div'); this.el.className = 'scale-bar'; this.el.innerHTML = '<i></i><span></span>';
    parent.appendChild(this.el); this.km = 0; this.px = 0;
  }

  update(camera, sceneHeight) {
    const pxPerKm = sceneHeight / 2 / Math.tan(camera.fov * DEG / 2) / camera.position.length() / RE_KM, km = scaleStep(pxPerKm), px = km * pxPerKm;
    if (km !== this.km) { this.el.lastChild.textContent = scaleLabel(km); this.km = km; }
    if (Math.abs(px - this.px) > .5) { this.el.firstChild.style.width = `${px.toFixed(1)}px`; this.px = px; }
    return { km, px };
  }

  // Box in scene coordinates for the label occupancy.
  box(sceneEl) {
    const r = sceneEl.getBoundingClientRect(), b = this.el.getBoundingClientRect();
    return { x: b.left - r.left, y: b.top - r.top, width: b.width, height: b.height };
  }
}

// GEO (42,164 km, equatorial plane from the Earth quaternion) and 1 LD (the instantaneous Moon distance, lunar orbit
// plane) rings about Earth at 0.25 alpha, only with the layer on and Earth ≥ 2 px: never in the wide views.
export class ScaleRings {
  constructor(scene, materials) {
    const c = token('--dim');
    this.geo = ring(c, 1, .25, materials); this.ld = ring(c, 1, .25, materials);
    this.geo.visible = this.ld.visible = false; scene.add(this.geo, this.ld);
    this.n = new THREE.Vector3(); this.h = new Float64Array(3);
  }

  update(bodies, world, on, earthPx) {
    const v = !!on && earthPx >= 2;
    this.geo.visible = this.ld.visible = v;
    if (!v) return;
    this.n.set(0, 1, 0).applyQuaternion(bodies.earth.quaternion);
    this.geo.quaternion.setFromUnitVectors(AXIS, this.n); this.geo.position.copy(bodies.earth.position); this.geo.scale.setScalar(GEO_KM / RE_KM);
    const m = world.moon, h = this.h;
    h[0] = m[1] * m[5] - m[2] * m[4]; h[1] = m[2] * m[3] - m[0] * m[5]; h[2] = m[0] * m[4] - m[1] * m[3];
    apply(world.R, h, h);
    this.n.set(h[0], h[2], -h[1]).normalize();
    this.ld.quaternion.setFromUnitVectors(AXIS, this.n); this.ld.position.copy(bodies.earth.position); this.ld.scale.setScalar(Math.hypot(m[0], m[1], m[2]) / RE_KM);
  }
}

// Edge chevrons: items [{id, label, color, p (scene THREE.Vector3, focus-relative), title?}] in priority order; those projecting
// inside the view are skipped, at most `cap` are placed, a click calls onClick(id). The range is from the view's focus
// (|p|), so every chevron reads "that way, this far from what you look at" (from Earth in the Earth-centred views). Text is
// re-formatted only when the range moved by 0.2 %, widths are cached and styles written only on change, so the per-frame
// cost is the projection and the placement loop (≈ 0.03–0.05 ms for 4–6 chevrons). Returns the boxes.
export class Chevrons {
  constructor(sceneEl, onClick, cap = 8) {
    this.el = sceneEl; this.onClick = onClick; this.cap = cap; this.pool = [];
    this.q = new THREE.Quaternion(); this.v = new THREE.Vector3(); this.count = 0;
  }

  update(items, camera, w, h, keepOut = []) {
    const q = this.q.copy(camera.quaternion).invert(), boxes = [], v = this.v;
    let n = 0;
    for (const it of items) {
      if (n >= this.cap) break;
      const ndc = v.copy(it.p).project(camera);
      if (ndc.z > -1 && ndc.z < 1 && Math.abs(ndc.x) <= 1.05 && Math.abs(ndc.y) <= 1.05) continue;
      const c = v.copy(it.p).sub(camera.position).applyQuaternion(q), a = Math.atan2(-c.y, c.x), p = edgePoint(a, w, h);
      let el = this.pool[n];
      if (!el) {
        el = this.pool[n] = document.createElement('button'); el.className = 'edge-marker'; el.innerHTML = '<i></i><span></span>';
        el.addEventListener('click', () => this.onClick(el.dataset.id)); this.el.appendChild(el);
      }
      const km = it.p.length() * RE_KM;
      if (el.label !== it.label || !(Math.abs(km - el.km) <= .002 * el.km)) {
        el.label = it.label; el.km = km; el.lastChild.textContent = `${it.label} · ${rangeText(km)}`; el.boxW = null;
      }
      if (el.hidden) el.hidden = false;
      if (el.dataset.id !== it.id) el.dataset.id = it.id;
      if (el.color !== it.color) el.style.color = el.color = it.color;
      if (el.title !== (it.title || '')) el.title = it.title || '';
      if (Math.abs(a - (el.angle ?? 99)) > 2e-3) { el.firstChild.style.transform = `rotate(${a.toFixed(3)}rad)`; el.angle = a; }
      if (el.right !== p.right) { el.classList.toggle('right', p.right); el.right = p.right; }
      el.boxW ||= el.offsetWidth;
      const b = placeEdgeBox({ x: Math.round(Math.max(4, Math.min(w - el.boxW - 4, p.right ? p.x - el.boxW + 12 : p.x - 2))), y: Math.round(p.y - 2),
        width: el.boxW, height: 16 }, [...boxes, ...keepOut], h);                       // clear of the title, legend, Sun and each other
      const tf = `translate(${b.x}px,${b.y}px)`;
      if (el.tf !== tf) el.style.transform = el.tf = tf;                                // style writes only on change
      boxes.push(b); n++;
    }
    for (let i = n; i < this.pool.length; i++) if (!this.pool[i].hidden) this.pool[i].hidden = true;
    this.count = n;
    return boxes;
  }
}
