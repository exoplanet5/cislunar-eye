// Timeline marks (web/ui/timeline.js, design §5) without a browser: day ticks and label thinning, coverage clipping,
// observation bars merged per pixel column, and the marks layer rendered into a tiny fake element.
import test from 'node:test';
import assert from 'node:assert/strict';
import { frac, dayTicks, coverageSpans, obsMarks, TimelineMarks } from '../web/ui/timeline.js';

const t0 = Date.parse('2026-10-07T12:00:00Z'), t1 = Date.parse('2026-11-06T12:00:00Z'), D = 86400e3;   // the dist window, ±15 d
const labels = ticks => ticks.filter(k => k.label).map(k => k.label);

test('day ticks at 00:00 UTC; 6 px majors every 5 d of the month; labels every 5 d, 10 d on a narrow track', () => {
  const k = dayTicks(t0, t1);
  assert.equal(k.length, 30);
  assert.equal(k[0].t, Date.parse('2026-10-08T00:00:00Z'));
  assert.ok(k.every(x => x.t % D === 0));
  assert.deepEqual(k.filter(x => x.major).map(x => new Date(x.t).toISOString().slice(5, 10)), ['10-10', '10-15', '10-20', '10-25', '10-30', '11-05']);
  assert.deepEqual(labels(k), ['10-10', '10-15', '10-20', '10-25', '10-30', '11-05'], 'not laid out yet: every major labelled');
  assert.deepEqual(labels(dayTicks(t0, t1, 628)), ['10-10', '10-15', '10-20', '10-25', '10-30', '11-05'], '1440 px desktop track');
  assert.deepEqual(labels(dayTicks(t0, t1, 280)), ['10-10', '10-20', '10-30'], 'phone track: every 10 d');
  assert.equal(frac(Date.parse('2026-10-22T12:00:00Z'), t0, t1), 0.5);
});

test('labels within 22 px of a track end (12 px on a narrow phone track) are skipped; minor ticks drop out below 3 px a day', () => {
  const a = Date.parse('2026-10-09T23:00:00Z');
  assert.deepEqual(labels(dayTicks(a, a + 30 * D, 628)).slice(0, 1), ['10-15'], '10-10 sits 1 px from the start');
  assert.equal(labels(dayTicks(t0, t0 + 23.1 * D, 628)).at(-1), '10-25', '10-30 would sit 22 px from the track end');
  assert.deepEqual(labels(dayTicks(t0, t1, 260)), ['10-10', '10-20', '10-30'], '10-10, 22 px from the start of a phone track, is kept (12 px end-skip)');
  assert.deepEqual(labels(dayTicks(a, a + 30 * D, 260)).slice(0, 1), ['10-20'], 'but not 10-10 at 0.4 px from the start');
  const long = dayTicks(t0, t0 + 90 * D, 200);
  assert.ok(long.length > 0 && long.every(x => x.major), 'only majors when a day is under 3 px');
  assert.ok(labels(long).every(l => /-30$/.test(l)), 'labels thin to the 30th when 10 d is narrower than a label');
  assert.deepEqual(dayTicks(t1, t0), []);
});

test('coverage spans are clipped to the window; extrapolation keeps no series colour', () => {
  const spans = coverageSpans([
    { t0: t0 - 5 * D, t1: t0 + 10 * D, kind: 'data', color: '#4db99a' },
    { t0: t0 + 10 * D, t1: t1 + 9 * D, kind: 'extrap', color: '#4db99a' },
    { t0: t1 + D, t1: t1 + 2 * D, kind: 'data' }, { t0: NaN, t1: t1 }, null,
  ], t0, t1);
  assert.deepEqual(spans.map(s => [s.f0, +s.f1.toFixed(4), s.kind]), [[0, 0.3333, 'data'], [0.3333333333333333, 1, 'extrap']]);
});

test('observation bars: one per pixel column, a selected bar wins its column and draws last; outside the window dropped', () => {
  const at = h => t0 + h * 3600e3;
  const marks = obsMarks([{ t: at(10) }, { t: at(10.1), selected: true }, { t: at(100) }, { t: t0 - 1 }, { t: NaN }], t0, t1, 600);
  assert.equal(marks.length, 2);
  assert.deepEqual(marks.map(m => m.selected), [false, true]);
  assert.equal(obsMarks([{ t: at(10) }, { t: at(10.1) }], t0, t1).length, 2, 'not laid out: no merging');
});

test('TimelineMarks renders band, bars, ticks, labels and the now line; identical updates do not touch the DOM', () => {
  let writes = 0, html = '';
  const el = { clientWidth: 628, style: {}, setAttribute(k, v) { this[k] = v; }, get innerHTML() { return html; }, set innerHTML(v) { writes++; html = v; } };
  const m = new TimelineMarks(el, { t0, t1, now: null });
  assert.equal(el['aria-hidden'], 'true');
  const count = cls => (html.match(new RegExp(`class="${cls}"`, 'g')) || []).length;
  assert.equal(count('tick') + count('tick maj'), 30); assert.equal(count('tlab'), 6); assert.equal(count('nowm'), 0);
  const state = { wall: Date.parse('2026-10-22T12:00:00Z'), coverage: [{ t0, t1: t0 + 10 * D, kind: 'data', color: '#4db99a' }, { t0: t0 + 10 * D, t1, kind: 'extrap' }],
    obs: [{ t: t0 + 3 * D }, { t: t0 + 4 * D, selected: true }], trackletTimes: [t0 + 5 * D, t0 + 5 * D, t1 + D] };
  m.update(state);
  assert.match(html, /<i class="nowm" style="--f:0\.50000"><\/i><span class="nowl" style="--f:0\.50000">now<\/span>/);
  assert.match(html, /class="cov data" style="--f:0\.00000;--w:0\.33333;--c:#4db99a"/);
  assert.match(html, /class="cov extrap" style="--f:0\.33333;--w:0\.66667"/);
  assert.equal(count('obs'), 1); assert.equal(count('obs sel'), 1);
  assert.equal(count('tkt'), 1, 'tracklet epochs deduplicated, outside the window dropped');
  const n = writes;
  m.update({ ...state }); m.update({});
  assert.equal(writes, n, 'unchanged marks are not rewritten');
  m.update({ wall: null, coverage: [{ t0, t1, kind: 'data', color: 'red;background:url(x)' }] });
  assert.equal(count('nowm'), 0, 'no now line outside the window');
  assert.doesNotMatch(html, /url\(x\)/, 'colours are sanitised before they reach a style attribute');
  el.clientWidth = 280; m.width = 280; m.render();
  assert.equal(count('tlab'), 3);
  m.destroy();
  assert.equal(html, '');
});
