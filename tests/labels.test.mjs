// Label placement (web/labels.js, ARCHITECTURE §9.5) without a browser: a stub DOM gives every label a 40×14 box.
import test from 'node:test';
import assert from 'node:assert/strict';

globalThis.document = { createElement: () => ({ className: '', hidden: true, style: {}, textContent: '', offsetWidth: 40, offsetHeight: 14 }) };
const { Labels, Occupancy } = await import('../web/labels.js');
const layer = { appendChild() {} };
const at = el => el.style.transform.match(/translate\((-?\d+)px,(-?\d+)px\)/).slice(1).map(Number);
const markerBoxes = pts => pts.map(([x, y]) => ({ x: x - 4, y: y - 4, width: 8, height: 8 }));
const inside = ([x, y], [lx, ly]) => x > lx && x < lx + 40 && y > ly && y < ly + 14;

test('a free label takes the first clear offset (9, −14)', () => {
  const L = new Labels(layer), occ = new Occupancy();
  L.place([{ key: 'a', text: 'A', x: 100, y: 100, priority: 1 }], 800, 600, occ);
  assert.deepEqual(at(L.items.get('a').el), [109, 86]);
});

test('an ordinary label in a crowd is dropped; an always-on one moves to the offset covering the fewest markers', () => {
  // markers on the four near offsets and the right-hand second ring; the left-hand second ring is clear but for one
  const pts = [[115, 92], [140, 92], [115, 110], [140, 110], [70, 92], [60, 110], [70, 110], [115, 78], [140, 78], [115, 124], [140, 124], [70, 124], [55, 80]];
  const run = always => {
    const L = new Labels(layer), occ = new Occupancy();
    for (const b of markerBoxes(pts)) occ.add(b);
    L.place([{ key: 'earth', text: 'Earth', x: 100, y: 100, priority: 950, always }], 800, 600, occ);
    return L.items.get('earth').el;
  };
  assert.equal(run(false).hidden, true);
  const el = run(true), p = at(el), covered = pts.filter(q => inside(q, p));
  assert.equal(el.hidden, false);
  const occ = new Occupancy(); for (const b of markerBoxes(pts)) occ.add(b);
  const forcedAtFirst = occ.hits({ x: 109, y: 86, width: 40, height: 14 });
  assert.ok(covered.length <= 1 && forcedAtFirst > covered.length, `placed at ${p}, covers ${JSON.stringify(covered)} (first offset would touch ${forcedAtFirst})`);
});

test('hard boxes are never covered, not even by a forced label', () => {
  const L = new Labels(layer), occ = new Occupancy();
  occ.add({ x: 0, y: 0, width: 800, height: 120 }, true);
  L.place([{ key: 'earth', text: 'Earth', x: 100, y: 100, priority: 950, always: true }], 800, 600, occ);
  const p = at(L.items.get('earth').el);
  assert.ok(p[1] >= 120, `label at ${p} inside the hard box`);
});
