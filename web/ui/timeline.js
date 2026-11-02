// Timeline marks (design §5): #timeline-marks lies over the rail and under the native #timeline slider in .track (44 px),
// inset by half the thumb at each end (template CSS), so a fraction of its width is the thumb centre at that epoch.
// Rows, y in px: "now" label −4–6 and the selected target's tracklet epochs 6–10; the requested objects' observation bars
// 11–21 (on the rail); coverage band 22–25; day ticks 26–32 (6 px every 5 d, at 00:00 UTC); date labels 32–44.
// Marks are CSS fractions (--f); the ResizeObserver only re-thins the labels and merges bars per pixel column.
// px of the inset layer (= track − 12 px thumb). End-skip: 22 px, 12 px on a narrow (phone) track, whose window ends have
// no #window-start / #window-stop text beside them; a 30 px label there still stays inside the track (layer + 6 px).
const DAY = 86400e3, LABEL_EDGE = 22, LABEL_EDGE_NARROW = 12, NARROW = 468, LABEL_GAP = 44;
const pad = n => String(n).padStart(2, '0');
const f5 = f => f.toFixed(5);

export const frac = (t, t0, t1) => (t1 > t0 ? Math.min(1, Math.max(0, (t - t0) / (t1 - t0))) : 0);

// Ticks at each 00:00 UTC in [t0, t1]: {t, f, major (day of month divisible by 5), label "MM-DD" or ''}. Labels every 5 d,
// every 10 d on a track under 480 px (or when 5 d is too narrow for a label), none within 22 px of a layer end (12 px on a
// narrow track). width is the marks layer's (0 = not laid out: every major labelled, minor ticks kept).
export function dayTicks(t0, t1, width = 0) {
  if (!(t1 > t0)) return [];
  const pxDay = width ? width / ((t1 - t0) / DAY) : Infinity;
  const step = !width ? 5 : width < NARROW || 5 * pxDay < LABEL_GAP ? (10 * pxDay < LABEL_GAP ? 30 : 10) : 5;
  const out = [];
  for (let t = Math.ceil(t0 / DAY) * DAY; t <= t1; t += DAY) {
    const d = new Date(t), dom = d.getUTCDate(), major = dom % 5 === 0, f = frac(t, t0, t1), x = width ? f * width : NaN;
    if (!major && pxDay < 3) continue;
    const e = width < NARROW ? LABEL_EDGE_NARROW : LABEL_EDGE, edge = x < e || x > width - e;
    out.push({ t, f, major, label: major && dom % step === 0 && !edge ? `${pad(d.getUTCMonth() + 1)}-${pad(dom)}` : '' });
  }
  return out;
}

// Coverage spans clipped to the window: [{f0, f1, kind, color}] (kind 'data' | 'extrap').
export function coverageSpans(coverage, t0, t1) {
  return (coverage || []).filter(c => Number.isFinite(c?.t0) && Number.isFinite(c?.t1) && c.t1 > t0 && c.t0 < t1 && c.t1 > c.t0)
    .map(c => ({ f0: frac(c.t0, t0, t1), f1: frac(c.t1, t0, t1), kind: c.kind === 'extrap' ? 'extrap' : 'data', color: c.color || '' }));
}

// Observation bars inside the window, one per pixel column once laid out (a selected bar wins its column), selected last.
export function obsMarks(obs, t0, t1, width = 0) {
  const cols = new Map();
  for (const o of obs || []) {
    if (!Number.isFinite(o?.t) || o.t < t0 || o.t > t1) continue;
    const f = frac(o.t, t0, t1), key = width ? Math.round(f * width) : f5(f), prev = cols.get(key);
    if (!prev || (o.selected && !prev.selected)) cols.set(key, { f, selected: !!o.selected });
  }
  return [...cols.values()].sort((a, b) => a.selected - b.selected || a.f - b.f);
}

export class TimelineMarks {
  // now: the initial position of the "now" line (wall clock, ms or null), replaced by update({wall}).
  constructor(el, { t0, t1, now = null } = {}) {
    this.el = el; this.t0 = t0; this.t1 = t1; this.state = { wall: now }; this.html = null; this.width = el.clientWidth || 0;
    el.setAttribute('aria-hidden', 'true');
    const RO = globalThis.ResizeObserver;
    this.ro = RO ? new RO(() => { const w = el.clientWidth || 0; if (w !== this.width) { this.width = w; this.render(); } }) : null;
    this.ro?.observe(el);
    this.render();
  }

  setWindow(t0, t1, now) {
    this.t0 = t0; this.t1 = t1;
    if (now !== undefined) this.state = { ...this.state, wall: now };
    this.render();
  }

  // state: {wall: ms | null (outside the window), coverage: [{t0,t1,kind,color}], obs: [{t, selected}], trackletTimes: [ms]};
  // a key left out keeps its value. Re-renders only when the marks change, so it may run on every UI tick.
  update(state = {}) {
    this.state = { ...this.state, ...state };
    this.render();
  }

  render() {
    const { t0, t1, width } = this, s = this.state, at = f => `--f:${f5(f)}`;
    if (!(t1 > t0)) return;
    const out = [];
    for (const k of dayTicks(t0, t1, width)) {
      out.push(`<i class="tick${k.major ? ' maj' : ''}" style="${at(k.f)}"></i>`);
      if (k.label) out.push(`<span class="tlab" style="${at(k.f)}">${k.label}</span>`);
    }
    for (const c of coverageSpans(s.coverage, t0, t1)) out.push(`<i class="cov ${c.kind}" style="--f:${f5(c.f0)};--w:${f5(c.f1 - c.f0)}${c.kind === 'data' && /^[#\w(),.\s%-]+$/.test(c.color) ? `;--c:${c.color}` : ''}"></i>`);
    for (const o of obsMarks(s.obs, t0, t1, width)) out.push(`<i class="obs${o.selected ? ' sel' : ''}" style="${at(o.f)}"></i>`);
    for (const t of new Set(s.trackletTimes || [])) if (t >= t0 && t <= t1) out.push(`<i class="tkt" style="${at(frac(t, t0, t1))}"></i>`);
    if (Number.isFinite(s.wall) && s.wall >= t0 && s.wall <= t1) {
      const f = frac(s.wall, t0, t1);
      out.push(`<i class="nowm" style="${at(f)}"></i><span class="nowl" style="${at(f)}">now</span>`);
    }
    const html = out.join('');
    if (html !== this.html) this.el.innerHTML = this.html = html;
  }

  destroy() { this.ro?.disconnect(); this.el.innerHTML = ''; this.html = null; }
}
