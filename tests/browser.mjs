// Playwright (system Chrome, headless, as MMX tests/browser.mjs) against the fixture page on file://, plus the real
// dist/index.html with --dist. Every http(s) request is aborted and must never be attempted. Screenshots → build/screens/.
// Every screenshot is taken paused at a pinned ?t= with the wall clock pinned (page.clock) and CSS transitions finished,
// so reruns match to a few anti-aliased DOM pixels; each is compared with build/screens_baseline/<name>.png: pixels with
// max channel |Δ| > 8, fail above 0.1 %, diff image → build/screens_diff/. Baselines are keyed on the data manifests
// (minus built_utc): when the fixture or dist data changed the set's diffs are skipped with a notice; no baseline yet →
// notice. --update-baselines writes them only after a passing run. Checks of features still being integrated report
// "pending" while their hook or markup is missing; --strict turns pending and missing baselines into failures.
//   node tests/browser.mjs [--dist] [--no-fixture] [--update-baselines] [--strict] [--baselines DIR]      (CHROME_PATH overrides the browser)
import { chromium } from 'playwright';
import assert from 'node:assert/strict';
import { cp, mkdir, readFile, readdir, rm, writeFile, copyFile } from 'node:fs/promises';
import { existsSync, readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { inflateSync, deflateSync, crc32 } from 'node:zlib';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const args = process.argv.slice(2), UPDATE = args.includes('--update-baselines'), STRICT = args.includes('--strict'), opt = k => args[args.indexOf(k) + 1];
const screens = path.join(root, 'build/screens'), diffDir = path.join(root, 'build/screens_diff'), work = path.join(root, 'build/browser', String(process.pid));   // per run: parallel runs keep their page
const baselines = path.resolve(root, args.includes('--baselines') ? opt('--baselines') : 'build/screens_baseline');
const NOW = '2026-10-21T13:00:00Z';            // epoch of every screenshot (the fixture's now)
const WALL = '2026-10-22T13:00:00Z';           // pinned wall clock: inside the window (LIVE available), fixture NEOCP snapshot 24 h old (stale path)
const DIFF_TOL = 8, DIFF_MAX = 1e-3;
await mkdir(screens, { recursive: true }); await mkdir(baselines, { recursive: true });
await rm(diffDir, { recursive: true, force: true });
const browser = await chromium.launch({ headless: true,
  executablePath: process.env.CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' });
const errors = [], requests = [], pending = [];
const ok = (name, detail = '') => console.log(`ok  ${name}${detail ? ` · ${detail}` : ''}`);
// A check of a feature under integration: runs when its hook/markup is present, otherwise reports pending.
const feature = (name, present, detail = '') => { if (present) return true; pending.push(name); console.log(`..  pending ${name}${detail ? ` · ${detail}` : ''}`); return false; };

// ---------- PNG codec (8-bit RGB/RGBA, non-interlaced: Chrome screenshots) and the pixel diff ----------
const SIG = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
function decodePNG(buf) {
  let pos = 8, width = 0, height = 0, depth = 0, type = 0, interlace = 0;
  const idat = [];
  while (pos < buf.length) {
    const len = buf.readUInt32BE(pos), kind = buf.toString('latin1', pos + 4, pos + 8), data = buf.subarray(pos + 8, pos + 8 + len);
    if (kind === 'IHDR') { width = data.readUInt32BE(0); height = data.readUInt32BE(4); depth = data[8]; type = data[9]; interlace = data[12]; }
    else if (kind === 'IDAT') idat.push(data); else if (kind === 'IEND') break;
    pos += 12 + len;
  }
  const ch = { 2: 3, 6: 4 }[type];
  if (!buf.subarray(0, 8).equals(SIG) || depth !== 8 || !ch || interlace) throw new Error(`unsupported PNG (depth ${depth}, type ${type}, interlace ${interlace})`);
  const raw = inflateSync(Buffer.concat(idat)), stride = width * ch, rgba = new Uint8Array(width * height * 4);
  let prev = new Uint8Array(stride), cur = new Uint8Array(stride);
  for (let y = 0; y < height; y++) {
    const f = raw[y * (stride + 1)], o = y * (stride + 1) + 1;
    for (let x = 0; x < stride; x++) {
      const a = x >= ch ? cur[x - ch] : 0, b = prev[x], c = x >= ch ? prev[x - ch] : 0, v = raw[o + x];
      if (f === 0) cur[x] = v; else if (f === 1) cur[x] = v + a; else if (f === 2) cur[x] = v + b; else if (f === 3) cur[x] = v + ((a + b) >> 1);
      else { const q = a + b - c, pa = Math.abs(q - a), pb = Math.abs(q - b), pc = Math.abs(q - c); cur[x] = v + (pa <= pb && pa <= pc ? a : pb <= pc ? b : c); }
    }
    for (let x = 0, i = y * width * 4; x < width; x++, i += 4) { rgba[i] = cur[x * ch]; rgba[i + 1] = cur[x * ch + 1]; rgba[i + 2] = cur[x * ch + 2]; rgba[i + 3] = ch === 4 ? cur[x * ch + 3] : 255; }
    [prev, cur] = [cur, prev];
  }
  return { width, height, rgba };
}
function encodePNG({ width, height, rgba }) {
  const chunk = (kind, data) => { const k = Buffer.from(kind, 'latin1'), len = Buffer.alloc(4), crc = Buffer.alloc(4); len.writeUInt32BE(data.length); crc.writeUInt32BE(crc32(data, crc32(k)) >>> 0); return Buffer.concat([len, k, data, crc]); };
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(width, 0); ihdr.writeUInt32BE(height, 4); ihdr[8] = 8; ihdr[9] = 6;
  const raw = Buffer.alloc(height * (width * 4 + 1));
  for (let y = 0; y < height; y++) raw.set(rgba.subarray(y * width * 4, (y + 1) * width * 4), y * (width * 4 + 1) + 1);
  return Buffer.concat([SIG, chunk('IHDR', ihdr), chunk('IDAT', deflateSync(raw)), chunk('IEND', Buffer.alloc(0))]);
}
// Changed pixels (largest channel difference > tol) and a diff image: the new shot dimmed, changed pixels red.
function diffPNG(a, b, tol) {
  if (a.width !== b.width || a.height !== b.height) return { changed: b.width * b.height, total: b.width * b.height, sizeMismatch: `${a.width}×${a.height} vs ${b.width}×${b.height}` };
  const n = a.width * a.height, out = new Uint8Array(n * 4);
  let changed = 0;
  for (let i = 0, o = 0; i < n; i++, o += 4) {
    const d = Math.max(Math.abs(a.rgba[o] - b.rgba[o]), Math.abs(a.rgba[o + 1] - b.rgba[o + 1]), Math.abs(a.rgba[o + 2] - b.rgba[o + 2]));
    if (d > tol) { changed++; out[o] = 255; out[o + 1] = 40; out[o + 2] = 40; } else { out[o] = b.rgba[o] >> 2; out[o + 1] = b.rgba[o + 1] >> 2; out[o + 2] = b.rgba[o + 2] >> 2; }
    out[o + 3] = 255;
  }
  return { changed, total: n, image: { width: a.width, height: a.height, rgba: out } };
}

// ---------- baselines: build/screens_baseline/<name>.png + manifest.json {sets: {fixture|dist: {manifest, chrome, updated}}} ----------
const fingerprint = set => {                         // the data files' hashes and the window; built_utc changes on every export
  const f = path.join(root, set === 'dist' ? 'dist/data/manifest.js' : 'build/fixture/data/manifest.js');
  return existsSync(f) ? createHash('sha256').update(readFileSync(f, 'utf8').replace(/"built_utc":"[^"]*",?/, '')).digest('hex').slice(0, 16) : null;
};
const metaFile = path.join(baselines, 'manifest.json');
const meta = existsSync(metaFile) ? JSON.parse(readFileSync(metaFile, 'utf8')) : { sets: {} };
const shots = { compared: 0, updated: 0, missing: [], skipped: 0, failed: [] }, staleSets = new Set(), fresh = [];
async function compare(name, file) {
  const set = name.split('-')[0], fp = fingerprint(set);
  assert.ok(!fresh.some(x => x.name === name) && !shots.missing.includes(name), `screenshot name ${name} used twice`);
  if (UPDATE) { fresh.push({ name, set, file, fp }); return 'baseline queued'; }
  const base = path.join(baselines, `${name}.png`);
  if (!existsSync(base)) { shots.missing.push(name); return 'no baseline yet (--update-baselines)'; }
  if (meta.sets[set]?.manifest !== fp) {
    if (!staleSets.has(set)) { staleSets.add(set); console.log(`..  ${set} data changed since its baselines (manifest ${fp} vs ${meta.sets[set]?.manifest}): ${set}-* diffs skipped, rerun with --update-baselines`); }
    shots.skipped++; return 'diff skipped (data changed)';
  }
  const d = diffPNG(decodePNG(readFileSync(base)), decodePNG(readFileSync(file)), DIFF_TOL), frac = d.changed / d.total;
  shots.compared++;
  if (d.sizeMismatch || frac > DIFF_MAX) {
    if (d.image) { await mkdir(diffDir, { recursive: true }); await writeFile(path.join(diffDir, `${name}.png`), encodePNG(d.image)); }
    const chrome = meta.sets[set]?.chrome !== browser.version() ? ` (Chrome ${meta.sets[set]?.chrome} → ${browser.version()} since the baselines)` : '';
    shots.failed.push(`${name}: ${d.sizeMismatch ? `size ${d.sizeMismatch}` : `${d.changed} px (${(100 * frac).toFixed(3)} %) differ from the baseline`}${chrome}`);
    return `DIFF ${d.sizeMismatch || `${(100 * frac).toFixed(3)} % > ${100 * DIFF_MAX} %`}`;
  }
  return `baseline Δ ${d.changed} px (${(100 * frac).toFixed(3)} %)`;
}

// --update-baselines: only after the whole run passed; a re-baselined set loses its old PNGs.
async function writeBaselines() {
  for (const set of new Set(fresh.map(x => x.set))) {
    for (const f of await readdir(baselines)) if (f.startsWith(`${set}-`) && f.endsWith('.png')) await rm(path.join(baselines, f));
    const mine = fresh.filter(x => x.set === set);
    meta.sets[set] = { manifest: mine[0].fp, chrome: browser.version(), viewport: '1440×900 / 390×844, DPR 1', updated: new Date().toISOString(), shots: mine.map(x => x.name) };
  }
  for (const x of fresh) await copyFile(x.file, path.join(baselines, `${x.name}.png`));
  await writeFile(metaFile, JSON.stringify(meta, null, 1) + '\n');
  shots.updated = fresh.length;
}

async function open(file, query = '', viewport = { width: 1440, height: 900 }, reducedMotion = 'reduce') {
  const context = await browser.newContext({ viewport, reducedMotion });
  await context.route(/^https?:/, route => { requests.push(route.request().url()); return route.abort(); });
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(`${path.basename(file)}: ${error.message}`));
  page.on('console', message => { if (message.type() === 'error') errors.push(`${path.basename(file)}: ${message.text()}`); });
  await page.clock.setSystemTime(WALL);                    // Date.now keeps advancing from WALL; performance.now is untouched
  await page.goto(pathToFileURL(file).href + query);
  await page.waitForFunction(() => window.__cle?.ready, null, { timeout: 20000 });
  await settle(page);
  return page;
}
const settle = page => page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(() => requestAnimationFrame(r)))));
const shot = async (page, name, locator = null) => {
  await settle(page);
  const clock = await ev(page, () => ({ live: window.__cle.clock.live, url: location.search }));
  assert.ok(!clock.live && /[?&]t=/.test(clock.url), `${name}: screenshots need a pinned ?t= (LIVE ${clock.live}, ${clock.url})`);
  const file = path.join(screens, `${name}.png`);
  await (locator ? page.locator(locator) : page).screenshot({ path: file, animations: 'disabled' });     // CSS transitions finished, not mid-way
  const r = await compare(name, file);
  console.log(`${r.startsWith('DIFF') ? '!! ' : 'ok '} screenshot ${name}.png · ${r}`);
};
const ev = (page, f, arg) => page.evaluate(f, arg);
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const canvasBox = page => page.locator('#scene canvas').boundingBox();
const parkMouse = page => page.mouse.move(0, 0);                                  // top bar corner: nothing hoverable, leaves the canvas
const shown = (page, sel) => ev(page, s => { const e = document.querySelector(s); return !!e && !e.hidden && getComputedStyle(e).display !== 'none'; }, sel);
// Target labels whose box covers another target's marker centre (labels.js: soft dot boxes; always-on labels forced to
// the least-covering offset), as 'label/marker' pairs.
const labelsOverMarkers = page => ev(page, () => {
  const c = window.__cle, sr = document.getElementById('scene').getBoundingClientRect(), out = [];
  for (const [key, it] of c.labels.items) {
    if (it.el.hidden) continue;
    const b = it.el.getBoundingClientRect(), x0 = b.left - sr.left, y0 = b.top - sr.top;
    for (const T of c.world.targets) if (T.screen && T.id !== key && T.screen.x > x0 + 1 && T.screen.x < x0 + b.width - 1 && T.screen.y > y0 + 1 && T.screen.y < y0 + b.height - 1) out.push(`${it.el.textContent}/${T.label}`);
  }
  return out;
});
// Edge chevrons that overlap the Sun indicator, the title, the legend or each other (V1/V12 placement), as 'a×b' pairs.
const chevronClash = page => ev(page, () => {
  const box = e => e.getBoundingClientRect(), hit = (a, b) => a.left < b.right - .5 && a.right > b.left + .5 && a.top < b.bottom - .5 && a.bottom > b.top + .5;
  const chev = [...document.querySelectorAll('.edge-marker')].filter(e => !e.hidden && getComputedStyle(e).display !== 'none').map(e => [e.dataset.id, box(e)]);
  const fixed = ['sun-indicator', 'legend', 'view-title', 'scene-clock'].map(id => document.getElementById(id)).filter(e => e && !e.hidden && getComputedStyle(e).display !== 'none').map(e => [e.id, box(e)]);
  const out = [];
  chev.forEach(([a, ba], i) => { for (const [b, bb] of [...chev.slice(i + 1), ...fixed]) if (hit(ba, bb)) out.push(`${a}×${b}`); });
  return { clash: out, chevrons: chev.map(([id]) => id) };
});

// Visible chevrons whose box covers an on-screen target marker (it would hide the marker and catch its taps), 'chevron/target'.
const chevronsOverMarkers = page => ev(page, () => {
  const c = window.__cle, sr = document.getElementById('scene').getBoundingClientRect(), out = [];
  for (const e of document.querySelectorAll('.edge-marker')) {
    if (e.hidden) continue;
    const b = e.getBoundingClientRect(), x0 = b.left - sr.left, y0 = b.top - sr.top;
    for (const T of c.world.targets) if (T.screen && T.screen.x > x0 && T.screen.x < x0 + b.width && T.screen.y > y0 && T.screen.y < y0 + b.height) out.push(`${e.dataset.id}/${T.label}`);
  }
  return out;
});
// Tracklet beads drawn on screen (inside each group's draw range), per target: on screen, and how many of those belong to a
// target whose marker is off screen.
const beadsOnScreen = page => ev(page, () => {
  const c = window.__cle, el = document.getElementById('scene'), w = el.clientWidth, h = el.clientHeight, out = {};
  c.camera.updateMatrixWorld();
  for (const L of c.tracklets.layers) {
    if (!L.g.visible) continue;
    const T = c.world.byId.get(L.id), tk = c.tracklets.by.get(L.id).tk, pos = L.dots.points.geometry.attributes.position, v = new (c.camera.position.constructor)();
    let on = 0;
    for (let i = tk[L.shown[0]].d0; i < tk[L.shown[1] - 1].d1; i++) {
      v.fromBufferAttribute(pos, i); L.g.localToWorld(v); v.project(c.camera);
      if (v.z < 1 && Math.abs(v.x) <= 1 && Math.abs(v.y) <= 1) on++;
    }
    if (on) out[L.id] = { on, markerOn: !!T.screen };
  }
  return { out, offTarget: Object.entries(out).filter(([, x]) => !x.markerOn).map(([id, x]) => `${id}:${x.on}`), shown: c.tracklets.shownCount, total: c.tracklets.dotCount };
});
// Angular span (deg) about Earth of the Moon trail as drawn now (instances inside its texel window), in the scene's
// horizontal plane (y up = the SE/ECI frame normal; the lunar orbit is within 5° of the ecliptic).
const moonTrailSpan = page => ev(page, () => {
  const t = window.__cle.trails, b = t.batches.earth, { start, time, info } = b.buf, n = b.geometry.instanceCount, now = t.material.uniforms.uNow.value;
  const past = t.data[(t.width + t.moonIndex) * 4], future = t.data[(t.width + t.moonIndex) * 4 + 1], a = [];
  for (let i = 0; i < n; i++) if (info[i * 2] === t.moonIndex && time[i * 2 + 1] >= now - past && time[i * 2] <= now + future) a.push(Math.atan2(start[i * 3 + 2], start[i * 3]) * 180 / Math.PI);
  a.sort((x, y) => x - y);
  let gap = a.length ? 360 - (a.at(-1) - a[0]) : 360;
  for (let i = 1; i < a.length; i++) gap = Math.max(gap, a[i] - a[i - 1]);
  return { span: 360 - gap, past: past / 86400, future: future / 86400 };
});

// Frame geometry, all in km in the active display frame.
const geometry = page => ev(page, () => {
  const c = window.__cle, w = c.world, R = w.R, ap = p => [0, 1, 2].map(k => R[k * 3] * p[0] + R[k * 3 + 1] * p[1] + R[k * 3 + 2] * p[2]);
  return { R: [...R], sunF: [...w.sunF], moonF: [...w.moonF], L: w.lagrange.out.map(p => ap(p)), moonD: Math.hypot(...w.moon.slice(0, 3)),
    sunD: Math.hypot(...w.sun.slice(0, 3)), qf: c.bodies.qf.toArray(), skyQ: c.sky.root.quaternion.toArray(), frame: c.state.frame };
});
// Max distance (km) of a target's batched trail vertices from a frame point.
const trailSpread = (page, id, ref) => ev(page, ([id, ref]) => {
  const c = window.__cle, T = c.world.byId.get(id), RE = 6378.137, anchor = T.track.center === 'moon' ? 'moon' : 'earth';
  const { start, info } = c.trails.batches[anchor].buf, n = c.trails.batches[anchor].geometry.instanceCount;
  const s = [ref[0] / RE, ref[2] / RE, -ref[1] / RE];
  let max = 0, count = 0;
  for (let i = 0; i < n; i++) if (info[i * 2] === T.index) { count++; max = Math.max(max, Math.hypot(start[i * 3] - s[0], start[i * 3 + 1] - s[1], start[i * 3 + 2] - s[2]) * RE); }
  return { max, count };
}, [id, ref]);
// Camera framing, the Lagrange glyph alphas (SE-L1/L2; EM-L1…L5) and the edge chevrons of the current view.
const view = page => ev(page, () => {
  const c = window.__cle, cam = c.camera, t = Math.tan(cam.fov * Math.PI / 360), fit = half => 1.06 * Math.max(half / (t * cam.aspect), .35 * half / t);
  const el = document.getElementById('scene'), moon = c.bodies.moon.position.clone().project(cam), earth = c.bodies.earth.position.clone().project(cam);
  return { d: cam.position.length(), fitEm: fit(72), fitWide: fit(275), earthPx: c.state.earthPx, moonPx: Math.hypot((moon.x - earth.x) * el.clientWidth / 2, (moon.y - earth.y) * el.clientHeight / 2),
    preset: document.querySelector('#views .active')?.dataset.view || null, chevrons: [...document.querySelectorAll('.edge-marker')].filter(e => !e.hidden).map(e => e.dataset.id),
    se: [0, 1].map(i => c.lagrange.layer.style[i * 4 + 3]), em: [2, 3, 4, 5, 6].map(i => c.lagrange.layer.style[i * 4 + 3]), labels: c.labelsShown() };
});
// Trail texel (past_s, future_s, highlight, alpha gain) of a target.
const texel = (page, id) => ev(page, id => { const t = window.__cle.trails, T = window.__cle.world.byId.get(id); return [...t.data.slice((t.width + T.index) * 4, (t.width + T.index) * 4 + 4)]; }, id);
// C1: the future-dash cycle on screen (CSS px) at every visible on-screen target, over its first ~40 px of future trail
// in the real instance buffers: the dash unit per instance by the shader's rule (trails.dashUnit(k), else DASH_PX and
// the foreshortening floor read from the built GLSL), times the instance's on-screen length per scene unit (projected
// end points). trails.dashCyclePx(id) (the instance under the marker) must agree with the same instance measured
// here (atNow) within 2 %. hook null: no screen-space dash.
const dashCycles = page => ev(page, () => {
  const c = window.__cle, W = c.world, t = c.trails, cam = c.camera, el = document.getElementById('scene'), w = el.clientWidth, h = el.clientHeight;
  const src = t.material.vertexShader, m = src.match(/vDash = ([\d.]+) \/ exp2\( round\( 2\.0 \* log2\( k \) \) \* 0\.5 \)/), fl = src.match(/max\( length\( sf \), ([\d.]+) \)/);
  const unit = t.dashUnit?.bind(t) ?? (m && (k => +m[1] / 2 ** (Math.round(2 * Math.log2(k)) / 2))), floor = t.minForeshorten ?? (fl ? +fl[1] : 0);
  if (!unit) return { hook: null, out: [] };
  const U = t.material.uniforms, now = U.uNow.value, res = U.resolution.value, P = cam.projectionMatrix.elements[5], V = cam.position.constructor;
  const px = v => { const q = v.project(cam); return [(q.x + 1) * w / 2, (1 - q.y) * h / 2]; };
  const out = [];
  for (const T of W.targets) {
    const future = t.data[(t.width + T.index) * 4 + 1];
    if (!c.isVisible(T) || !T.has || !T.screen || !T.track || !(future > 0)) continue;
    const focus = t.focusT === T, moon = T.track.center === 'moon', b = focus ? t.focusBatch : t.batches[moon ? 'moon' : 'earth'], g = focus ? t.focusGroup : moon ? t.moonGroup : t.earthGroup;
    const idx = focus ? t.focusIndex : T.index, { start, end, time, info } = b.buf, n = b.geometry.instanceCount, MV = cam.matrixWorldInverse.clone().multiply(g.matrixWorld), cyc = [];
    const cycle = i => {                                               // [on-screen cycle px, on-screen length px] of instance i
      const a = new V().fromArray(start, i * 3), e = new V().fromArray(end, i * 3), L = a.distanceTo(e);
      if (!L) return null;
      const va = a.clone().applyMatrix4(MV), ve = e.clone().applyMatrix4(MV), d = ve.clone().sub(va), mid = va.clone().add(ve).multiplyScalar(.5), sw = Math.max(1e-9, -mid.z);
      const k = .5 * res.y * P / sw * Math.max(Math.hypot(d.x + mid.x / sw * d.z, d.y + mid.y / sw * d.z) / L, floor);
      const pa = px(a.applyMatrix4(g.matrixWorld)), pe = px(e.applyMatrix4(g.matrixWorld)), s = Math.hypot(pe[0] - pa[0], pe[1] - pa[1]);
      return [unit(k) * s / L, s];
    };
    let atNow = null;
    for (let i = 0, run = 0; i < n && run < 40; i++) {                  // a target's instances are stored in time order
      if (info[i * 2] !== idx || time[i * 2 + 1] <= now || time[i * 2] > now + future) continue;   // [t0, t1) like trails.locate
      const r = cycle(i);
      if (!r) continue;
      if (time[i * 2] <= now) { atNow ??= r[0]; continue; }              // the culled instance under the head segments
      cyc.push(r[0]); run += r[1];
    }
    if (!cyc.length) continue;
    cyc.sort((x, y) => x - y);
    out.push({ id: T.id, px: cyc[cyc.length >> 1], min: cyc[0], n: cyc.length, atNow, hook: t.dashCyclePx?.(T.id) ?? null });
  }
  return { hook: t.dashUnit ? 'trails.dashUnit' : 'shader', dashPx: m ? +m[1] : null, out };
});
const dashReport = d => {
  const q = (k, f) => d.out.map(x => x[k]).sort((a, b) => a - b)[f], worst = [...d.out].sort((a, b) => a.px - b.px)[0];
  return { n: d.out.length, min: q('px', 0) ?? NaN, median: q('px', d.out.length >> 1) ?? NaN, max: q('px', d.out.length - 1) ?? NaN, instMin: q('min', 0) ?? NaN, worst };
};
const dashText = r => `${r.n} targets, cycle median ${r.median.toFixed(1)} px (min ${r.min.toFixed(1)} at ${r.worst?.id}, max ${r.max.toFixed(1)}); shortest single instance ${r.instMin.toFixed(1)} px`;
// C1 at an opening view: ≥ 8 px at every on-screen target (the review measured a 2.44 px median, 38/56 < 4 px, in the old default).
async function dashCheck(page, label, nMin) {
  const dc = await dashCycles(page);
  if (!feature(`C1 screen-space dashes${label}`, !!dc.hook, 'no screen-space dash in the trail shader')) return;
  const r = dashReport(dc), off = dc.out.filter(x => x.hook != null && !(Math.abs(x.hook / x.atNow - 1) < .02));
  assert.ok(r.n >= nMin && r.min >= 8, `future-dash cycle: ${dashText(r)}`);
  assert.deepEqual(off, [], 'trails.dashCyclePx(id) agrees with the instance under the marker measured here, within 2 %');
  ok(`dash cycle at the opening view${label}`, `${dc.hook}${dc.dashPx ? `, DASH_PX ${dc.dashPx}` : ''}: ${dashText(r)}${dc.out.some(x => x.hook != null) ? '; trails.dashCyclePx agrees' : ''}`);
}

async function fixture() {
  await rm(work, { recursive: true, force: true });
  await mkdir(work, { recursive: true });
  await cp(path.join(root, 'build/fixture/index.html'), path.join(work, 'index.html'));
  await cp(path.join(root, 'build/fixture/data'), path.join(work, 'data'), { recursive: true });
  const file = path.join(work, 'index.html');
  let page = await open(file, `?t=${NOW}`);

  // Data, defaults and sky
  const info = await ev(page, () => {
    const c = window.__cle, w = c.world;
    return { n: w.targets.length, tracks: w.targets.filter(T => T.track).length, missing: c.missing, frame: c.state.frame, t: c.clock.t, live: c.clock.live,
      visible: w.targets.filter(c.isVisible).map(T => T.id).sort(), stars: c.sky.stars.geometry.attributes.position.count,
      grid: c.sky.grid.visible, cons: c.sky.constellations.visible, starsOn: c.sky.stars.visible, neocp: c.neocpBuilt, labels: c.labelsShown(),
      listed: document.querySelector('[data-id="fx-listed"]')?.textContent, listedTitle: document.querySelector('[data-id="fx-listed"]')?.title,
      tier2Row: !!document.querySelector('[data-id="fx-stale-rb"]'), credit: document.querySelector('.scene-credit').textContent,
      stale: document.getElementById('banner').textContent + ' ' + document.getElementById('readout').textContent };
  });
  assert.equal(info.n, 12); assert.equal(info.tracks, 11); assert.deepEqual(info.missing, []);
  assert.equal(info.frame, 'se'); assert.equal(info.t, Date.parse(NOW)); assert.equal(info.live, false);
  assert.deepEqual(info.visible, ['c46fx01', 'fx-derelict', 'fx-dro', 'fx-em-l2', 'fx-flyby', 'fx-heo', 'fx-lunar-2h', 'fx-se-l1', 'fx-se-l2']);
  assert.equal(info.stars, 9827); assert.equal(info.starsOn, true); assert.equal(info.grid, false); assert.equal(info.cons, false);
  assert.ok(info.neocp, 'neocp.js registered');
  assert.match(info.listed, /listed, not drawn/); assert.match(info.listedTitle, /no public ephemeris/);
  assert.equal(info.tier2Row, true, 'tier-2 rows are listed even when hidden');
  assert.ok(info.labels.length <= 40 + 4 && info.labels.includes('Earth') && info.labels.includes('Moon'), info.labels.join(','));
  assert.match(info.credit, /Illustration: Mickey · base map NASA Blue Marble/);
  assert.match(info.stale, /older than 3 h/);                                           // pinned wall clock 24 h after the snapshot
  ok('fixture loaded', `${info.n} targets, ${info.tracks} tracks, visible ${info.visible.length}, ${info.labels.length} labels, 9827 stars`);

  // Opening view (V1): the Earth–Moon framing with SE-L1/L2 as edge chevrons; the EM glyphs stay hidden outside the EM
  // frame (V5, decision 4); Earth under 8 px gets an ink ring 3 px outside its limb; future dashes ≥ 8 px on screen at
  // every visible marker (C1).
  let v = await view(page);
  if (feature('V1 opening view', v.earthPx > 4, `Earth ${v.earthPx.toFixed(2)} px: still the L1–L2 framing`)) {
    assert.ok(v.chevrons.includes('SE-L1') && v.chevrons.includes('SE-L2'), `SE-L1/L2 edge chevrons: ${v.chevrons.join(',') || 'none'}`);
    assert.ok(v.moonPx > 150 && v.em.every(a => a === 0) && v.se.every(a => a > .5), `Moon ${v.moonPx.toFixed(0)} px out, EM glyph alpha ${v.em} (hidden), SE ${v.se}`);
    assert.ok(v.labels.includes('Earth') && v.labels.includes('Moon'), v.labels.join(','));
    const ring = await ev(page, () => { const c = window.__cle, i = c.world.targets.length, st = c.markers.style; return { size: st[i * 4], hollow: st[i * 4 + 2], alpha: st[i * 4 + 3], px: c.state.earthPx, color: c.markers.color.slice(i * 3, i * 3 + 3).join() }; });
    assert.ok(Math.abs(ring.size - (2 * ring.px + 6)) < 1e-3 && ring.hollow === 1 && Math.abs(ring.alpha - .8) < 1e-6, `Earth ring ${JSON.stringify(ring)}`);
    ok('opening view', `Earth ${v.earthPx.toFixed(1)} px with a ${ring.size.toFixed(1)} px ring, Moon ${v.moonPx.toFixed(0)} px out, EM glyphs hidden, camera ${v.d.toFixed(1)} R⊕ (em preset ${v.fitEm.toFixed(1)}), chevrons ${v.chevrons.join(', ')}`);
  }
  await dashCheck(page, '', 5);
  await shot(page, 'fixture-open');

  // The L1–L2 preset (one click): Earth a dot between SE-L1 and SE-L2; EM-L1…L5 glyphs, labels and picking off (V5)
  await ev(page, () => window.__cle.preset('wide')); await settle(page);
  v = await view(page);
  assert.ok(Math.abs(v.d - v.fitWide) < 1e-6 * v.fitWide && v.earthPx < 3 && v.preset === 'wide', `wide preset: camera ${v.d.toFixed(2)} vs ${v.fitWide.toFixed(2)} R⊕, Earth ${v.earthPx.toFixed(2)} px, button ${v.preset}`);
  assert.ok(v.se.every(a => a > .5) && v.labels.some(s => s.startsWith('SE-L2')) && v.labels.includes('Earth'), `SE glyphs ${v.se}, labels ${v.labels.join(',')}`);
  if (feature('V5 EM Lagrange glyphs hidden in the wide view', v.em.every(a => a === 0), `EM glyph alpha ${v.em} with the Moon ${v.moonPx.toFixed(0)} px out`)) {
    assert.ok(!v.labels.some(s => s.startsWith('EM-L')), `EM labels placed: ${v.labels.filter(s => s.startsWith('EM-L')).join(',')}`);
    const at = await ev(page, () => { const c = window.__cle, p = c.lagrange.scene[5].clone().project(c.camera), el = document.getElementById('scene'); return { x: (p.x + 1) * el.clientWidth / 2, y: (1 - p.y) * el.clientHeight / 2 }; });
    const box = await canvasBox(page);
    await page.mouse.move(box.x + at.x, box.y + at.y); await settle(page);
    assert.notEqual(await ev(page, () => window.__cle.state.hovered), 'EM-L4', 'a hidden glyph is not hoverable');
    await parkMouse(page); await settle(page);
    ok('EM Lagrange glyphs hidden in the wide view', `Moon ${v.moonPx.toFixed(0)} px out; no EM label, EM-L4 not pickable`);
  }
  // lagrangeVisibility(frame, moonPx, focus) → a mask over the 7 LAGRANGE entries (SE-L1, SE-L2, EM-L1…L5); the layer draws it
  const lv = await ev(page, () => {
    const c = window.__cle, f = c.overlays?.lagrangeVisibility, W = c.world;
    if (!f) return null;
    const cases = [['se', 66, 'earth'], ['eci', 104, 'SE-L2'], ['se', 66, W.byId.get('fx-heo')], ['em', 66, 'earth'], ['se', 150, 'earth'], ['se', 66, 'moon'], ['se', 66, 'EM-L2'],
      ['se', 66, W.byId.get('fx-dro')], ['se', 66, W.byId.get('fx-lunar-2h')]];
    return { masks: cases.map(a => f(...a)), live: f(c.state.frame, c.state.moonDistPx, W.byId.get(c.state.focus) || c.state.focus), shown: [...c.lagrange.shown] };
  });
  if (feature('overlays.lagrangeVisibility(frame, moonPx, focus)', !!lv, 'hook __cle.overlays.lagrangeVisibility missing')) {
    assert.ok(lv.masks.every(m => m.length === 7 && m[0] && m[1]), `SE-L1/L2 always drawn: ${JSON.stringify(lv.masks)}`);
    assert.deepEqual(lv.masks.map(m => m.slice(2).every(Boolean) ? 'em' : m.slice(2).some(Boolean) ? 'mixed' : '-'), ['-', '-', '-', 'em', '-', 'em', 'em', 'em', 'em'],
      'EM-L1…L5 only in the EM frame or with a Moon / EM-point / Moon-bound focus (not a HEO, not for a 150 px Moon distance)');
    assert.deepEqual(lv.shown, lv.live, `the layer draws the mask of the current view: shown ${lv.shown} vs ${lv.live}`);
    ok('lagrangeVisibility', 'EM hidden for se/66 px, se/150 px, eci/SE-L2, a HEO focus; shown for em, Moon, EM-L2, DRO and LLO focus; layer follows the mask');
  }

  // Interpolation reproduces native samples; Moon-centred states recombine with the Moon
  const interp = await ev(page, () => {
    const c = window.__cle, w = c.world, out = {};
    for (const id of ['fx-lunar-2h', 'fx-se-l2', 'fx-heo']) {
      const T = w.byId.get(id), seg = T.track.segments[0], i = Math.min(seg.n - 2, 37), t = seg.t0_ms + i * seg.step_s * 1000;
      c.setTime(t); w.evaluate(t);
      out[id] = Math.max(...[0, 1, 2].map(k => Math.abs(T.rel[k] - seg.rv[i * 6 + k])));
      if (T.track.center === 'moon') out.moonSum = Math.max(...[0, 1, 2, 3, 4, 5].map(k => Math.abs(T.state[k] - T.rel[k] - w.moon[k])));
    }
    return out;
  });
  assert.ok(interp['fx-lunar-2h'] < 1e-6 && interp['fx-se-l2'] < 1e-6 && interp['fx-heo'] < 1e-6 && interp.moonSum < 1e-9, JSON.stringify(interp));
  ok('Hermite at native samples', JSON.stringify(interp));
  await ev(page, t => window.__cle.setTime(Date.parse(t)), NOW); await settle(page);

  // SE rotating (default): Sun on −x, SE-L2 on +x, star sphere and globe rotated by R(t_now)
  let g = await geometry(page);
  assert.ok(g.sunF[0] < -1.4e8 && Math.hypot(g.sunF[1], g.sunF[2]) < 1e-3, `sun ${g.sunF}`);
  assert.ok(g.L[1][0] > 1.49e6 && g.L[1][0] < 1.52e6 && Math.hypot(g.L[1][1], g.L[1][2]) < 1e-3, `SE-L2 ${g.L[1]}`);
  assert.ok(g.L[0][0] < -1.48e6 && Math.hypot(g.L[0][1], g.L[0][2]) < 1e-3, `SE-L1 ${g.L[0]}`);
  assert.deepEqual(g.skyQ.map(x => +x.toFixed(12)), g.qf.map(x => +x.toFixed(12)));
  const term = await ev(page, () => {
    const c = window.__cle, b = c.bodies, n = new (b.terminator.position.constructor)(0, 0, 1).applyQuaternion(b.terminator.quaternion);
    const s = c.world.sunF, sv = [s[0], s[2], -s[1]], d = Math.hypot(...sv), ex = new (b.terminator.position.constructor)(1, 0, 0).applyQuaternion(b.moon.quaternion);
    const toEarth = b.earth.position.clone().sub(b.moon.position).normalize();
    return { normalDot: (n.x * sv[0] + n.y * sv[1] + n.z * sv[2]) / d, color: b.terminator.material.color.getHexString(),
      twilight: b.twilight.visible, twColor: b.twilight.material.color.getHexString(), tidal: ex.dot(toEarth) };
  });
  assert.ok(term.normalDot > 1 - 1e-9 && term.color === 'ffd54f' && !term.twilight && term.twColor === '4fc3f7' && term.tidal > Math.cos(6 * Math.PI / 180), JSON.stringify(term));
  const halo = await trailSpread(page, 'fx-se-l2', g.L[1]);
  assert.ok(halo.count > 100 && halo.max < 6e5, `SE-L2 halo trail spread ${JSON.stringify(halo)}`);
  ok('SE rotating frame', `Sun x=${(g.sunF[0] / 1.496e8).toFixed(4)} AU, SE-L2 x=${g.L[1][0].toFixed(0)} km, terminator normal·sun=${term.normalDot.toFixed(12)}, Moon tidal ${term.tidal.toFixed(12)}, halo trail ≤ ${(halo.max / 1e3).toFixed(0)}e3 km of SE-L2`);
  await shot(page, 'fixture-se-default');

  // Marker glyphs (V4): hollow = position not from a current ephemeris (extrap or stale), derelict = dim fill (FX-DEREL: fresh GP)
  const mk = await ev(page, () => {
    const c = window.__cle, out = {};
    c.world.targets.forEach((T, i) => { if (c.isVisible(T) && T.has) out[T.id] = { q: T.seg.quality, status: T.cat.status, stale: !!(T.snapshotStale || T.cat.staleness?.flag || /stale/i.test(T.caveat || '')),
      hollow: c.markers.style[i * 4 + 2], alpha: +c.markers.style[i * 4 + 3].toFixed(2), size: c.markers.style[i * 4] }; });
    return out;
  });
  const der = mk['fx-derelict'];
  if (feature('V4 marker semantics', der && !der.hollow, `derelict FX-DEREL (${der?.q}) drawn ${der?.hollow ? 'hollow' : 'filled'} at alpha ${der?.alpha}`)) {
    for (const [id, m] of Object.entries(mk)) assert.equal(!!m.hollow, m.q === 'extrap' || m.stale, `${id} ${JSON.stringify(m)}`);
    assert.ok(der.alpha >= .5 && der.alpha < .8 && mk['fx-lunar-2h'].alpha === 1 && !mk['fx-lunar-2h'].hollow, JSON.stringify({ derelict: der, llo: mk['fx-lunar-2h'] }));
    ok('marker glyphs', `hollow: ${Object.entries(mk).filter(([, m]) => m.hollow).map(([id]) => id).join(', ')}; derelict alpha ${der.alpha}, size ${der.size} px`);
  }

  // HEO trails (V2): idle dim when nothing is selected, hover emphasis without a click (hooks), then through the pointer
  const tr = await ev(page, () => {
    const c = window.__cle, t = c.trails;
    if (!t.setHover || !t.setIdleDim) return null;
    const row = id => [...t.data.slice((t.width + c.world.byId.get(id).index) * 4, (t.width + c.world.byId.get(id).index) * 4 + 4)];
    const read = () => ({ heo: row('fx-heo'), derelict: row('fx-derelict'), llo: row('fx-lunar-2h') }), step = f => { f(); c.probe(1); return read(); };
    const off = step(() => t.setIdleDim(false)), on = step(() => t.setIdleDim(true)), hov = step(() => t.setHover('fx-heo')), back = step(() => t.setHover(null));
    return { off, on, hov, back };
  });
  if (feature('trails.setHover / setIdleDim', !!tr, 'hooks __cle.trails.setHover(id|null) / setIdleDim(bool) missing')) {
    assert.ok(!same(tr.off.heo, tr.on.heo) && same(tr.off.llo, tr.on.llo), `idle dim changes the HEO texel only: ${JSON.stringify(tr)}`);
    assert.ok(!same(tr.on.heo, tr.hov.heo) && same(tr.on.derelict, tr.hov.derelict) && same(tr.on.heo, tr.back.heo), `hover emphasis on FX-HEO only, restored on setHover(null): ${JSON.stringify(tr)}`);
    ok('HEO idle dim and hover emphasis', `texel ${tr.off.heo.map(x => +x.toFixed(2))} → dim ${tr.on.heo.map(x => +x.toFixed(2))} → hover ${tr.hov.heo.map(x => +x.toFixed(2))}`);
  }
  await ev(page, () => { const c = window.__cle; c.select(null); c.preset('em'); }); await settle(page);
  const idle = await texel(page, 'fx-heo'), box = await canvasBox(page), at = await ev(page, () => window.__cle.screenOf('fx-heo'));
  assert.ok(at, 'FX-HEO on screen in the Earth–Moon preset');
  await page.mouse.move(box.x + at.x + 3, box.y + at.y - 2); await settle(page);
  const hv = await ev(page, () => ({ hovered: window.__cle.state.hovered, tip: document.getElementById('tooltip').textContent, hidden: document.getElementById('tooltip').hidden }));
  assert.ok(hv.hovered === 'fx-heo' && !hv.hidden && /Fixture cislunar HEO/.test(hv.tip), JSON.stringify(hv));
  if (tr) assert.ok(!same(idle, await texel(page, 'fx-heo')), 'pointer hover raises the FX-HEO trail');
  ok('hover a HEO target', hv.tip.slice(0, 60));
  await shot(page, 'fixture-hover-heo');
  await parkMouse(page); await settle(page);
  assert.equal(await ev(page, () => window.__cle.state.hovered), null);

  // ECI: identity rotation, the DRO trail sweeps the Moon's orbit
  await ev(page, () => { const c = window.__cle; c.preset('wide'); c.setFrame('eci'); }); await settle(page);
  g = await geometry(page);
  assert.deepEqual(g.R, [1, 0, 0, 0, 1, 0, 0, 0, 1]); assert.equal(g.frame, 'eci');
  const droEci = await trailSpread(page, 'fx-dro', g.moonF);
  assert.ok(droEci.max > 3e5, `DRO in ECI ${JSON.stringify(droEci)}`);
  assert.equal(await ev(page, () => window.__cle.trails.frame), 'eci');
  ok('ECI frame', `R = I, DRO trail spans ${(droEci.max / 1e3).toFixed(0)}e3 km from the Moon`);
  await shot(page, 'fixture-eci');

  // EM rotating: Moon on +x, EM-L1/L2 on the x axis, L4/L5 at ±60°, the DRO trail closes about the Moon; EM glyphs shown
  await ev(page, () => window.__cle.setFrame('em')); await settle(page);
  g = await geometry(page);
  const d = g.moonD, near = (a, b, tol) => a.every((x, k) => Math.abs(x - b[k]) < tol);
  assert.ok(near(g.moonF, [d, 0, 0], 1e-6), `moon ${g.moonF}`);
  assert.ok(near(g.L[2], [(0.8369151 + 0.0121506) * d, 0, 0], 50) && near(g.L[3], [(1.1556822 + 0.0121506) * d, 0, 0], 50), `EM-L1/L2 ${g.L[2]} ${g.L[3]}`);
  assert.ok(near(g.L[5], [d / 2, d * Math.sqrt(3) / 2, 0], 1e-3) && near(g.L[6], [d / 2, -d * Math.sqrt(3) / 2, 0], 1e-3), 'EM-L4/L5');
  const droEm = await trailSpread(page, 'fx-dro', g.moonF);
  assert.ok(droEm.max < 1.3e5, `DRO in EM ${JSON.stringify(droEm)}`);
  v = await view(page);
  assert.ok(v.em.every(a => a > .5) && v.labels.some(s => s.startsWith('EM-L')), `EM glyphs in the EM frame: alpha ${v.em}, labels ${v.labels.filter(s => s.startsWith('EM-L')).join(',')}`);
  ok('EM rotating frame', `Moon x=${d.toFixed(0)} km, EM-L1 ${(g.L[2][0] / d).toFixed(5)} d, EM-L2 ${(g.L[3][0] / d).toFixed(5)} d (geocentric), DRO trail ≤ ${(droEm.max / 1e3).toFixed(0)}e3 km of the Moon; EM glyphs shown`);
  await ev(page, () => window.__cle.preset('em')); await shot(page, 'fixture-em');

  // Focus the Moon (floating origin: the Moon sits at the scene origin)
  await ev(page, () => { window.__cle.setFrame('se'); window.__cle.focusOn('moon'); }); await settle(page);
  const fm = await ev(page, () => { const c = window.__cle; return { moon: c.bodies.moon.position.length(), focus: c.state.focus, cam: c.camera.position.length(),
    target: c.controls.target.length(), labels: c.labelsShown() }; });
  assert.ok(fm.moon < 1e-9 && fm.focus === 'moon' && fm.target === 0, JSON.stringify(fm));
  ok('focus Moon', `Moon at origin (${fm.moon.toExponential(1)}), camera ${fm.cam.toFixed(2)} R⊕`);
  await shot(page, 'fixture-focus-moon');

  // Focus and select a target (lunar orbiter): its marker sits at the origin, readouts fill
  await ev(page, () => { window.__cle.select('fx-lunar-2h'); window.__cle.focusOn('fx-lunar-2h'); }); await settle(page);
  await page.waitForTimeout(150);
  const ft = await ev(page, () => { const c = window.__cle, T = c.world.byId.get('fx-lunar-2h'); return { scene: T.scene.length(), sel: c.state.selected,
    range: document.getElementById('ro-range')?.textContent, q: document.getElementById('ro-q')?.textContent, a: document.getElementById('ro-a')?.textContent,
    src: document.getElementById('ro-src')?.textContent, seleno: document.getElementById('ro-seleno')?.textContent, url: location.href }; });
  assert.ok(ft.scene < 1e-9 && ft.sel === 'fx-lunar-2h' && /\d/.test(ft.range) && /^pred|^ref/.test(ft.q) && /horizons/.test(ft.src), JSON.stringify(ft));
  const a = Number(ft.a.replace(/,/g, '')), a2h = Math.cbrt(4902.800118 * (7200 / 2 / Math.PI) ** 2);
  assert.ok(Math.abs(a - a2h) < 1, `lunar a ${ft.a} vs ${a2h}`);
  ok('focus + select target', `FX-LLO at origin, a = ${ft.a} km (Moon conic), range ${ft.range} km, ${ft.q}`);
  await shot(page, 'fixture-focus-target');

  // Earth close-up: Blue Marble globe, #ffd54f terminator, optional Sun −12° ring
  await ev(page, () => { const c = window.__cle; c.select(null); c.preset('earth'); document.querySelector('[data-layer="twilight"]').click(); }); await settle(page);
  const earth = await ev(page, () => { const b = window.__cle.bodies; return { tw: b.twilight.visible, px: window.__cle.state.earthPx, map: !!b.earth.material.map?.image?.naturalWidth }; });
  assert.ok(earth.tw && earth.px > 80 && earth.map, JSON.stringify(earth));
  ok('Earth close-up', `${earth.px.toFixed(0)} px radius, Sun −12° ring on`);
  await shot(page, 'fixture-earth');
  await ev(page, () => document.querySelector('[data-layer="twilight"]').click());

  // Scale cues (V12): a scale bar in the scene foot that follows the view, a GEO / 1-LD rings layer where Earth ≥ 2 px
  const barAt = () => ev(page, () => document.getElementById('scale-bar')?.textContent ?? null);
  const barEarth = await barAt();
  await ev(page, () => { const c = window.__cle; c.setFrame('se'); c.preset('em'); }); await settle(page);
  const sc = await ev(page, () => ({ bar: document.getElementById('scale-bar')?.textContent ?? null, rings: !!document.querySelector('[data-layer="rings"]'), earthPx: window.__cle.state.earthPx }));
  if (feature('V12 scale bar + rings layer', sc.bar !== null && sc.rings, `#scale-bar ${sc.bar === null ? 'missing' : 'ok'}, [data-layer="rings"] ${sc.rings ? 'ok' : 'missing'}`)) {
    assert.ok(/\d/.test(sc.bar) && /km|LD/.test(sc.bar) && sc.bar !== barEarth, `scale bar "${sc.bar}" (Earth preset "${barEarth}")`);
    await ev(page, () => document.querySelector('[data-layer="rings"]').click()); await settle(page);
    const rg = await ev(page, () => ({ on: window.__cle.layers.rings, legend: !!document.querySelector('[data-legend="rings"]') && !document.querySelector('[data-legend="rings"]').hidden }));
    assert.ok(rg.on && sc.earthPx >= 2, JSON.stringify({ ...rg, earthPx: sc.earthPx }));
    ok('scale bar + rings', `"${sc.bar}" in the Earth–Moon view ("${barEarth}" at Earth); rings layer on at Earth ${sc.earthPx.toFixed(1)} px${rg.legend ? ', legend row' : ''}`);
    await shot(page, 'fixture-scale');
    await ev(page, () => document.querySelector('[data-layer="rings"]').click());
  }

  // Help overlay (C9): ? opens the key reference, Esc closes it
  if (feature('C9 help overlay', await ev(page, () => !!document.getElementById('help')), '#help missing')) {
    await page.keyboard.press('?'); await settle(page);
    assert.ok(await shown(page, '#help'), '#help shown after ?');
    await shot(page, 'fixture-help');
    await page.keyboard.press('Escape'); await settle(page);
    assert.ok(!(await shown(page, '#help')), '#help hidden after Esc');
    ok('help overlay', '? opens, Esc closes');
  }

  // Pointer: hover tooltip, click selects, double-click flies to (MMX nearest-anchor picking)
  await ev(page, () => { const c = window.__cle; c.setFrame('se'); c.preset('wide'); }); await settle(page);
  const box2 = await canvasBox(page), at2 = await ev(page, () => window.__cle.screenOf('fx-se-l2'));
  await page.mouse.move(box2.x + at2.x + 3, box2.y + at2.y - 2); await settle(page);
  const tip = await ev(page, () => ({ hidden: document.getElementById('tooltip').hidden, text: document.getElementById('tooltip').textContent }));
  assert.ok(!tip.hidden && /Fixture SE-L2 halo/.test(tip.text), JSON.stringify(tip));
  await page.mouse.click(box2.x + at2.x + 3, box2.y + at2.y - 2); await settle(page);
  assert.equal(await ev(page, () => window.__cle.state.selected), 'fx-se-l2');
  await page.mouse.dblclick(box2.x + at2.x + 3, box2.y + at2.y - 2); await settle(page);
  assert.equal(await ev(page, () => window.__cle.state.focus), 'fx-se-l2');
  ok('hover, click, double-click', tip.text.slice(0, 80));
  await page.fill('#search', '99003'); await settle(page);
  const found = await ev(page, () => [...document.querySelectorAll('#target-list [data-id]')].map(r => r.dataset.id));
  assert.deepEqual(found, ['fx-dro']);
  await page.fill('#search', ''); await page.keyboard.press('Escape');                       // first Esc leaves the search box
  await page.keyboard.press('Escape'); await page.keyboard.press('e'); await settle(page);
  assert.deepEqual(await ev(page, () => [window.__cle.state.focus, window.__cle.state.selected]), ['earth', null]);
  ok('search by NORAD, keys Esc / Esc / E');
  // C9 detail: the help lists the 11 shortcuts, and Esc closes it without deselecting
  await ev(page, () => window.__cle.select('fx-heo')); await page.keyboard.press('?'); await settle(page);
  const keys = await ev(page, () => document.querySelectorAll('#help kbd').length);
  await page.keyboard.press('Escape'); await settle(page);
  assert.ok(keys >= 11 && await ev(page, () => window.__cle.state.selected === 'fx-heo' && document.getElementById('help').hidden), `${keys} kbd entries; Esc on the help keeps the selection`);
  await ev(page, () => window.__cle.select(null));

  // Tier 2 and Natural chips; the legend follows its layers and chips (natural row, Sun −12°, tracklets: V10)
  await ev(page, () => { const c = window.__cle; c.select(null); c.focusOn('earth'); c.preset('wide'); c.setChip('t2', true); }); await settle(page);
  let vis = await ev(page, () => window.__cle.world.targets.filter(window.__cle.isVisible).map(T => T.id));
  assert.ok(vis.includes('fx-stale-rb') && !vis.includes('fx-minimoon'), vis.join(','));
  await ev(page, () => window.__cle.setChip('nat', true)); await settle(page);
  vis = await ev(page, () => window.__cle.world.targets.filter(window.__cle.isVisible).map(T => T.id));
  assert.ok(vis.includes('fx-minimoon') && !vis.includes('fx-listed'), vis.join(','));
  ok('tier 2 + natural toggles', `${vis.length} visible`);
  await shot(page, 'fixture-tier2');
  await ev(page, () => document.querySelector('[data-layer="twilight"]').click()); await settle(page);
  const lg = await ev(page, () => { const row = s => { const e = document.querySelector(`[data-legend="${s}"]`); return e ? !e.hidden : null; }; return { nat: row('nat'), twilight: row('twilight'), tracklets: row('tracklets'), text: document.getElementById('legend').textContent.replace(/\s+/g, ' ').trim() }; });
  assert.ok(lg.nat === true && lg.twilight === true, JSON.stringify(lg));
  if (feature('V10 tracklet legend row', lg.tracklets !== null, '[data-legend="tracklets"] missing')) {
    assert.ok(lg.tracklets && /tracklet/i.test(lg.text), lg.text);
    await ev(page, () => document.querySelector('[data-layer="tracklets"]').click()); await settle(page);
    assert.equal(await ev(page, () => document.querySelector('[data-legend="tracklets"]').hidden), true, 'tracklet legend row follows the layer');
    await ev(page, () => document.querySelector('[data-layer="tracklets"]').click()); await settle(page);
    ok('tracklet legend row', 'shown with the layer, hidden without');
  }
  await shot(page, 'fixture-legend', '#legend');
  ok('legend', lg.text);
  await ev(page, () => document.querySelector('[data-layer="twilight"]').click()); await settle(page);

  // Lagrange and trails toggles; trail length presets update one texel per target, no geometry rebuild
  assert.equal(await ev(page, () => document.getElementById('layers-panel').open), false, 'Layers starts closed');
  await page.locator('#layers-panel summary').click();
  const before = await ev(page, () => window.__cle.trails.count.earth);
  await page.selectOption('#trail-length', '1d'); await settle(page);
  const tex = await ev(page, () => { const c = window.__cle, T = c.world.byId.get('fx-heo'); return [c.trails.data[(c.trails.width + T.index) * 4], c.trails.count.earth]; });
  assert.ok(tex[0] === 86400 && tex[1] === before, JSON.stringify(tex));
  await page.locator('[data-layer="lagrange"]').uncheck(); await settle(page);
  assert.ok(!(await ev(page, () => window.__cle.labelsShown())).some(s => s.startsWith('SE-L')));
  await page.locator('[data-layer="lagrange"]').check(); await page.selectOption('#trail-length', 'auto'); await settle(page);
  ok('trail presets and Lagrange toggle', `past 1 d texel, ${before} segments unchanged`);

  // NEOCP tracklets: dots and sight lines; station chips with the code as text (V10)
  await ev(page, () => { document.querySelector('[data-layer="sightlines"]').click(); window.__cle.select('c46fx01'); window.__cle.focusOn('c46fx01'); }); await settle(page);
  await page.waitForTimeout(150);
  const tk = await ev(page, () => { const t = window.__cle.tracklets; return { dots: t.dotCount, shown: t.shownCount, sight: t.layers.some(L => L.sight.visible), stations: t.stations, items: t.items.length,
    chips: [...document.querySelectorAll('#readout [data-station], #readout .stations .stn')].map(e => e.dataset.station || [...e.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join('').trim()) }; });
  assert.ok(tk.dots === 10 && tk.shown === 10 && tk.sight && tk.items === 2 && tk.stations.join() === 'G96,T05', JSON.stringify(tk));
  ok('NEOCP tracklets', `${tk.dots} dots, stations ${tk.stations.join(', ')}, sight lines on`);
  if (feature('V10 station chips', tk.chips.length > 0, '#readout .stations .stn missing')) {
    assert.deepEqual([...tk.chips].sort(), ['G96', 'T05']);
    ok('station chips', tk.chips.join(', '));
  }
  await shot(page, 'fixture-neocp');

  // Re-injecting neocp.js swaps the data only when built_utc changes (§11, file://)
  assert.equal(await ev(page, () => window.__cle.refreshNeocp()), false);
  const neocpFile = path.join(work, 'data/neocp.js'), text = await readFile(neocpFile, 'utf8');
  await writeFile(neocpFile, text.replace(/"built_utc":"[^"]+"/, '"built_utc":"2026-10-21T13:10:00Z"').replaceAll('C46FX01 (NEOCP fixture)', 'C46FX01 (refreshed)'));
  assert.equal(await ev(page, () => window.__cle.refreshNeocp()), true);
  const re = await ev(page, () => ({ built: window.__cle.neocpBuilt, name: window.__cle.world.byId.get('c46fx01').cat.name }));
  assert.deepEqual(re, { built: '2026-10-21T13:10:00Z', name: 'C46FX01 (refreshed)' });
  ok('neocp.js re-injection', `built_utc → ${re.built}`);

  // URL round trip (replaceState, throttled 500 ms); a preset travels as view= (no cam=), a hand-moved camera as cam=
  await ev(page, () => { const c = window.__cle; c.setFrame('em'); c.select('fx-dro'); c.setTime(Date.parse('2026-10-25T06:00:00Z')); c.preset('wide'); });
  await page.waitForTimeout(800);
  const href = await ev(page, () => location.href);
  await page.context().close();
  page = await open(file, new URL(href).search);
  const st = await ev(page, () => ({ frame: window.__cle.state.frame, sel: window.__cle.state.selected, t: window.__cle.clock.t, chips: { ...window.__cle.chips }, view: window.__cle.state.view, d: window.__cle.camera.position.length() }));
  assert.deepEqual({ frame: st.frame, sel: st.sel, t: st.t, view: st.view }, { frame: 'em', sel: 'fx-dro', t: Date.parse('2026-10-25T06:00:00Z'), view: 'wide' });
  assert.ok(st.chips.t2 && /view=wide/.test(href) && !/cam=/.test(href) && st.d > 500, `${new URL(href).search} → camera ${st.d.toFixed(0)} R⊕`);
  ok('URL round trip', new URL(href).search.slice(0, 120));
  await page.context().close();

  // Phone layout (V13: opens on the Earth–Moon framing, legend chip, label cap 16)
  page = await open(file, `?t=${NOW}`, { width: 390, height: 844 });
  const phone = await ev(page, () => ({ sw: document.documentElement.scrollWidth, left: getComputedStyle(document.getElementById('controls-panel')).display,
    canvas: document.querySelector('#scene canvas').getBoundingClientRect().width, earthPx: window.__cle.state.earthPx, labels: window.__cle.labelsShown().length,
    chip: !!document.getElementById('legend-toggle') }));
  assert.ok(phone.sw <= 390 && phone.left === 'none' && phone.canvas >= 380, JSON.stringify(phone));
  ok('phone layout 390×844', JSON.stringify(phone));
  const ph13 = await ev(page, () => ({ view: window.__cle.state.view, cap: window.__cle.labels.cap, note: getComputedStyle(document.getElementById('view-description')).display,
    legend: getComputedStyle(document.getElementById('legend')).display, bannerH: document.getElementById('banner').getBoundingClientRect().height }));
  assert.ok(ph13.view === 'em' && ph13.cap === 16 && ph13.note === 'none' && ph13.legend === 'none' && ph13.bannerH < 30, JSON.stringify(ph13));
  ok('phone V13 details', `view em, label cap 16, frame note hidden, legend collapsed, banner ${ph13.bannerH.toFixed(0)} px (≤ one line; the NEOCP alarm is in the Situation panel)`);
  if (feature('V13 phone opening view', phone.earthPx >= 2, `Earth ${phone.earthPx.toFixed(2)} px`)) assert.ok(phone.labels <= 16 + 4, `${phone.labels} labels (cap 16 + bodies/L points)`);
  if (feature('V13 legend chip', phone.chip, '#legend-toggle missing')) {
    const h0 = await ev(page, () => document.getElementById('legend').getBoundingClientRect().height);
    await page.locator('#legend-toggle').click(); await settle(page);
    const h1 = await ev(page, () => document.getElementById('legend').getBoundingClientRect().height);
    assert.ok(h1 > h0, `legend expands on tap: ${h0} → ${h1} px`);
    await page.locator('#legend-toggle').click(); await settle(page);
    ok('phone legend chip', `collapsed ${h0.toFixed(0)} px, expanded ${h1.toFixed(0)} px`);
  }
  await shot(page, 'fixture-phone');
  await page.context().close();

  // Animated fly-to (1 s ease-in-out of the focus point; the camera offset is kept), then the camera module (V14):
  // tweenTo(dir, dist, ms) slerps the direction and lands exactly; focusDistance(target, frame) frames the target
  page = await open(file, `?t=${NOW}`, undefined, 'no-preference');
  const mid = await ev(page, () => { window.__cle.focusOn('moon'); return new Promise(r => setTimeout(() => r(window.__cle.bodies.moon.position.length()), 450)); });
  await page.waitForTimeout(800); await settle(page);
  const end = await ev(page, () => window.__cle.bodies.moon.position.length());
  assert.ok(mid > 1 && mid < 60 && end < 1e-9, `fly-to ${mid} → ${end}`);
  ok('fly-to tween', `Moon ${mid.toFixed(1)} R⊕ from the origin at 0.45 s, ${end.toExponential(1)} at 1.25 s`);
  // V14 through the UI: a preset slerps the camera direction (app.js preset() → camera.js tween) instead of snapping it
  const snap = await ev(page, () => new Promise(r => { const c = window.__cle, d = () => c.camera.position.clone().normalize(); c.preset('em', 0); const a = d(); c.preset('l2');
    setTimeout(() => { const m = d(); setTimeout(() => r({ total: a.angleTo(d()) * 180 / Math.PI, mid: a.angleTo(m) * 180 / Math.PI }), 700); }, 450); }));
  assert.ok(snap.total > 10 && snap.mid > 1 && snap.mid < snap.total - 1, JSON.stringify(snap));
  ok('preset direction tween', `Earth–Moon → SE-L2: ${snap.total.toFixed(1)}° in all, ${snap.mid.toFixed(1)}° at 0.45 s`);
  // A frame switch in the middle of a preset tween keeps the view continuous: the camera, the tween's start and end turn
  // by S_new · S_oldᵀ, so the next frame steps a few degrees (not back by ~37°) and the move ends at the rotated target.
  const fs = await ev(page, () => new Promise(r => {
    const c = window.__cle, d = () => c.camera.position.clone().normalize(), deg = (a, b) => a.angleTo(b) * 180 / Math.PI;
    c.setFrame('se'); c.preset('em', 0); c.preset('l2');
    setTimeout(() => {
      const S0 = Float64Array.from(c.world.S), to = c.camTween.to, v = [to.x, to.y, to.z], o = [0, 1, 2].map(k => S0[k] * v[0] + S0[3 + k] * v[1] + S0[6 + k] * v[2]);
      c.setFrame('em');
      const S1 = c.world.S, want = new (c.camera.position.constructor)(...[0, 1, 2].map(k => S1[k * 3] * o[0] + S1[k * 3 + 1] * o[1] + S1[k * 3 + 2] * o[2])), after = d();
      requestAnimationFrame(() => requestAnimationFrame(() => { const step = deg(after, d()); setTimeout(() => { const end = deg(d(), want); c.setFrame('se'); r({ step, end }); }, 900); }));
    }, 400);
  }));
  assert.ok(fs.step < 10 && fs.end < 1e-3, `setFrame mid-tween: next-frame step ${fs.step.toFixed(2)}°, end ${fs.end.toExponential(2)}° from S_new·S_oldᵀ·to`);
  ok('frame switch during a tween', `next frame ${fs.step.toFixed(2)}°, end ${fs.end.toExponential(1)}° from the rotated target`);
  // An interrupted fly-to eases on from the eased focus point, not from the target it never reached
  const fi = await ev(page, () => new Promise(r => {
    const c = window.__cle, f = () => Float64Array.from(c.focusKm), dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
    c.focusOn('earth', false, null, null, 0);
    requestAnimationFrame(() => { c.focusOn('moon'); setTimeout(() => { const a = f(); c.focusOn('SE-L2'); requestAnimationFrame(() => requestAnimationFrame(() => r({ jump: dist(a, f()), toMoon: dist(f(), c.world.moonF) }))); }, 350); });
  }));
  assert.ok(fi.jump < 3e4, `interrupted fly-to: focus jumped ${fi.jump.toFixed(0)} km in one frame`);
  ok('interrupted fly-to', `focus moves ${fi.jump.toFixed(0)} km over the first frame (Moon still ${fi.toMoon.toFixed(0)} km away)`);
  await ev(page, () => window.__cle.preset('em', 0));
  // The camera module is reached as __cle.cam (preferred: __cle.camera stays the THREE camera), or as methods on either.
  const fd = await ev(page, () => { const c = window.__cle, a = c.cam?.tweenTo ? c.cam : c.camera?.tweenTo ? c.camera : c.tweenTo ? c : null;
    return a ? ['earth', 'moon', 'fx-heo', 'fx-lunar-2h'].map(id => a.focusDistance(id, 'se')) : null; });
  if (feature('camera.focusDistance / tweenTo', !!fd, 'hook __cle.cam {focusDistance(target, frame), tweenTo(dir, dist, ms)} missing')) {
    assert.ok(fd.every(x => Number.isFinite(x) && x > 0) && fd[0] < fd[2] && fd[3] < fd[2], `focusDistance earth/moon/fx-heo/fx-lunar-2h → ${fd.map(x => x.toFixed(2))} R⊕`);
    const tw = await ev(page, () => new Promise(r => {
      const c = window.__cle, a = c.cam?.tweenTo ? c.cam : c.camera?.tweenTo ? c.camera : c, cam = c.camera;
      const from = cam.position.clone().normalize(), to = new cam.position.constructor(0.2, 1, 0.3).normalize(), n = from.clone().cross(to).normalize();
      a.tweenTo(to.clone(), 50, 600);
      setTimeout(() => {
        const m = cam.position.clone().normalize(), mid = { fromDeg: m.angleTo(from) * 180 / Math.PI, toDeg: m.angleTo(to) * 180 / Math.PI, offPlane: Math.abs(m.dot(n)) };
        setTimeout(() => r({ totalDeg: from.angleTo(to) * 180 / Math.PI, mid, endDeg: cam.position.clone().normalize().angleTo(to) * 180 / Math.PI, endLen: cam.position.length() }), 500);
      }, 300);
    }));
    assert.ok(tw.mid.fromDeg > .15 * tw.totalDeg && tw.mid.toDeg > .15 * tw.totalDeg && tw.mid.offPlane < 1e-3, `slerp mid-point ${JSON.stringify(tw)}`);
    assert.ok(tw.endDeg < 1e-3 && Math.abs(tw.endLen - 50) < .05, `tween end state ${JSON.stringify(tw)}`);
    ok('camera tween', `${tw.totalDeg.toFixed(1)}° great circle: ${tw.mid.fromDeg.toFixed(1)}° at 0.3 s, ${tw.endDeg.toExponential(1)}° / ${tw.endLen.toFixed(3)} R⊕ at 0.8 s; focusDistance ${fd.map(x => x.toFixed(1)).join(' / ')} R⊕`);
  }
  await page.context().close();

  // LIVE by default: the pinned wall clock is inside the window; frame-time probe (all tier-1 fixture targets)
  page = await open(file);
  const live = await ev(page, () => ({ live: window.__cle.clock.live, inside: window.__cle.clock.liveAvailable, t: window.__cle.clock.t }));
  assert.ok(live.live && live.inside && Math.abs(live.t - Date.parse(WALL)) < 120e3, JSON.stringify(live));
  ok('LIVE default', `LIVE at ${new Date(live.t).toISOString()} (wall clock pinned to ${WALL})`);
  const ms = await ev(page, () => window.__cle.probe(60));
  ok('frame-time probe', `median ${ms.median.toFixed(2)} ms, p90 ${ms.p90.toFixed(2)} ms over 60 frames`);
  await page.context().close();
}

// Review fixes on the real data: chips keep default_visible, hidden targets hide their tracklet lines, master caveats,
// NEOCP snapshot staleness, source badges, Lagrange glyph, and §9.2 focus precision (float32 GPU path emulated).
async function fixes(page) {
  const r = await ev(page, () => {
    const c = window.__cle, w = c.world, vis = () => w.targets.filter(c.isVisible).map(T => T.id).sort().join();
    const before = vis(); c.setChip('t1', false); const off = vis(); c.setChip('t1', true);
    const tl = c.tracklets.layers.map(L => [L.id, L.g.visible, c.isVisible(w.byId.get(L.id))]);
    const row = id => document.querySelector(`.row-target[data-id="${id}"]`);
    return { same: before === vis(), off, tl, ibexTitle: row('ibex')?.title || '', droBadge: row('dro-a')?.querySelector('.src')?.textContent,
      t3: document.querySelector('[data-chip=t3]').hidden, lshape: c.lagrange.layer.style[1],
      neocp: w.targets.filter(T => T.source === 'neocp').map(T => [T.id, !!T.snapshotStale, row(T.id)?.querySelector('.warn')?.textContent]),
      snapshot: c.neocpSnapshot, banner: document.getElementById('banner').textContent };
  });
  assert.ok(r.same && r.off === '', `t1 chip off/on restores the default set (off: ${r.off})`);
  assert.ok(r.tl.every(([, g, v]) => !g || v) && r.tl.some(([, g]) => !g), `a hidden target hides its tracklet group ${JSON.stringify(r.tl)}`);
  assert.match(r.ibexTitle, /manoeuvre/); assert.equal(r.droBadge, 'G'); assert.equal(r.t3, true); assert.equal(r.lshape, 5);
  const old = Date.parse(WALL) - Date.parse(r.snapshot) > 3 * 3600e3;
  assert.ok(r.neocp.length && r.neocp.every(([, s, wn]) => s === old && (!old || wn === '⚠')), JSON.stringify(r));
  ok('review fixes', `chip t1 off/on keeps defaults; ${r.tl.filter(x => !x[1]).length}/${r.tl.length} tracklet groups hidden (target hidden, or no tracklet inside its drawn trail window); `
    + `IBEX caveat kept; DRO-A badge G; NEOCP snapshot ${old ? 'old: all ' + r.neocp.length + ' marked stale' : 'fresh'}`);

  // §9.2: focus JWST, camera 1.6 km away; f32 emulation of modelViewMatrix·vertex for the marker and the head end.
  await ev(page, () => { const c = window.__cle; c.select('jwst'); c.focusOn('jwst'); });
  await settle(page); await page.waitForTimeout(100); await settle(page);
  const prec = await ev(page, () => {
    const c = window.__cle, T = c.world.byId.get('jwst'), cam = c.camera, f32 = Math.fround, d = 2.5e-4;
    cam.position.setLength(d); c.controls.update(); c.probe(1);
    const tr = c.trails, mk = c.markers.position, k = T.index, h = (geo, i) => [...geo.buf.end.slice(i * 3, i * 3 + 3)];
    const shared = c.trails.heads.earth, si = [...Array(shared.geometry.instanceCount).keys()].find(i => shared.buf.info[i * 2] === T.index);
    const mv = (model, v) => {
      const M = cam.matrixWorldInverse.clone().multiply(model).elements.map(f32), x = [f32(v[0]), f32(v[1]), f32(v[2]), 1], acc = [0, 0, 0, 0];
      for (let j = 0; j < 4; j++) for (let i = 0; i < 4; i++) acc[i] = f32(acc[i] + f32(M[j * 4 + i] * x[j]));
      return acc;
    };
    const half = d * Math.tan(cam.fov * Math.PI / 360), H = document.getElementById('scene').clientHeight, gaps = [], old = [];
    let seed = 7; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647 - .5;
    for (let n = 0; n < 20; n++) {
      cam.position.set(rnd(), rnd(), rnd()).setLength(d); cam.lookAt(0, 0, 0); cam.updateMatrixWorld(true);
      [tr.focusGroup, tr.earthGroup, c.markers.points].forEach(o => o.updateMatrixWorld(true));
      const m = mv(c.markers.points.matrixWorld, [mk[k * 3], mk[k * 3 + 1], mk[k * 3 + 2]]), px = v => Math.hypot(v[0] - m[0], v[1] - m[1]) / half * H / 2;
      gaps.push(px(mv(tr.focusGroup.matrixWorld, h(tr.focusHeads, 0))));
      if (si != null) old.push(px(mv(tr.earthGroup.matrixWorld, h(shared, si))));
    }
    const bar = document.getElementById('scale-bar');
    return { max: Math.max(...gaps), old: Math.max(...old), heads: tr.focusHeads.geometry.instanceCount, focusT: tr.focusT?.id,
      ownHidden: tr.data[T.index * 4 + 3] === 0, copyShown: tr.data[tr.focusIndex * 4 + 3] === 1, r: Math.hypot(...T.pos), bar: bar.textContent, barPx: bar.firstChild.getBoundingClientRect().width };
  });
  assert.ok(prec.focusT === 'jwst' && prec.heads === 2 && prec.ownHidden && prec.copyShown && prec.max < 2, JSON.stringify(prec));
  assert.ok(/^\d+ m$/.test(prec.bar) && prec.barPx <= 181, `scale bar at 1.6 km: "${prec.bar}" ${prec.barPx.toFixed(1)} px (metres, ≤ 180 px)`);
  ok('focus anchor §9.2', `JWST at ${(prec.r / 1e6).toFixed(3)}e6 km, camera 1.6 km: trail head ≤ ${prec.max.toFixed(2)} px from the marker over 20 directions (shared Earth-anchored copy: ${prec.old.toFixed(0)} px); scale bar "${prec.bar}" ${prec.barPx.toFixed(0)} px`);
  await ev(page, () => { const c = window.__cle; c.select(null); c.focusOn('earth'); c.preset('wide'); }); await settle(page);
}

// Real data (dist/index.html): the build's own epoch; one screenshot per view of the integration checklist.
async function dist() {
  const file = path.join(root, 'dist/index.html');
  if (!existsSync(file)) { console.log('skip dist: dist/index.html missing'); return; }
  const t0 = Date.now(), page = await open(file, `?t=${NOW}`), loadMs = Date.now() - t0;
  const timing = await ev(page, () => { const n = performance.getEntriesByType?.('navigation')?.[0]; return { dcl: n?.domContentLoadedEventEnd ?? NaN, load: n?.loadEventEnd ?? NaN }; });   // page.clock hides the entries
  const info = await ev(page, () => {
    const c = window.__cle, w = c.world, by = {};
    for (const T of w.targets) by[T.cat.tier] = (by[T.cat.tier] || 0) + 1;
    return { n: w.targets.length, tracks: w.targets.filter(T => T.track).length, visible: w.targets.filter(c.isVisible).length, has: w.targets.filter(T => T.has && c.isVisible(T)).length,
      missing: c.missing, frame: c.state.frame, labels: c.labelsShown().length, tiers: by, nodraw: document.querySelectorAll('.row-target.nodraw').length,
      notrack: document.querySelectorAll('.row-target.notrack').length, flyby: w.targets.filter(T => T.cat.subgroup === 'flyby').map(T => [T.id, c.isVisible(T), T.has]),
      hidden2: w.targets.filter(T => T.tier === 2 && c.isVisible(T)).length, tracklets: c.tracklets.items.length, dots: c.tracklets.dotCount,
      window: [c.world.t0, c.world.t1].map(t => new Date(t).toISOString()) };
  });
  assert.equal(info.frame, 'se'); assert.deepEqual(info.missing, []); assert.ok(info.tracks > 0 && info.has > 0, JSON.stringify(info));
  assert.ok(info.flyby.every(([, v, h]) => v === h), `NEOCP flybys shown exactly when they have data now: ${JSON.stringify(info.flyby)}`);
  assert.equal(info.hidden2, 0, 'tier 2 hidden by default');
  ok('dist loaded', `${JSON.stringify(info)} · page ready ${loadMs} ms (DOMContentLoaded ${timing.dcl.toFixed(0)} ms, load ${timing.load.toFixed(0)} ms)`);
  // Opening view (V1 + V5 + C1 on the real catalog), then the L1–L2 preset
  let v = await view(page);
  if (feature('V1 opening view (dist)', v.earthPx > 4, `Earth ${v.earthPx.toFixed(2)} px`)) {
    assert.ok(v.chevrons.includes('SE-L1') && v.chevrons.includes('SE-L2') && v.moonPx > 150 && v.em.every(a => a === 0), JSON.stringify({ chevrons: v.chevrons, moonPx: v.moonPx, em: v.em }));
    // L-point chevrons: the count of shown targets of that regime off screen and the range in Mkm (LD only below 1 Mkm)
    const ct = await ev(page, () => Object.fromEntries([...document.querySelectorAll('.edge-marker')].filter(e => !e.hidden).map(e => [e.dataset.id, e.textContent])));
    assert.ok(/^SE-L1 · [1-9]\d* · 1\.\d\d Mkm$/.test(ct['SE-L1']) && /^SE-L2 · [1-9]\d* · 1\.\d\d Mkm$/.test(ct['SE-L2']), JSON.stringify(ct));
    // the Moon trail closes over one synodic month around the orbit the view is framed on (7 d ≈ 90° before)
    const ms = await moonTrailSpan(page);
    assert.ok(ms.span >= 300, `Moon trail spans ${ms.span.toFixed(0)}° (${ms.past} d back, ${ms.future} d ahead)`);
    // no tracklet bead of an off-screen target on screen (beads follow the drawn trail window)
    const bd = await beadsOnScreen(page);
    assert.deepEqual(bd.offTarget, [], `beads of off-screen targets on screen: ${JSON.stringify(bd.out)}`);
    // the short mouse hint between 981 and 1600 px
    const hint = await ev(page, () => ({ long: getComputedStyle(document.querySelector('.hint-long')).display, short: getComputedStyle(document.querySelector('.hint-short')).display, text: document.querySelector('.hint-short').textContent }));
    assert.ok(hint.long === 'none' && hint.short !== 'none', JSON.stringify(hint));
    ok('dist opening view', `Earth ${v.earthPx.toFixed(1)} px, Moon ${v.moonPx.toFixed(0)} px out (trail ${ms.span.toFixed(0)}°), ${v.labels.length} labels, EM glyphs hidden, chevrons "${ct['SE-L1']}", "${ct['SE-L2']}"; beads ${bd.shown}/${bd.total} in their trail windows, ${Object.keys(bd.out).length} targets with beads on screen; hint "${hint.text}"`);
  }
  await dashCheck(page, ' (dist)', 20);
  let cc = await chevronClash(page);
  assert.deepEqual(cc.clash, [], `edge chevrons overlap at the opening view: ${cc.clash}`);
  assert.deepEqual(await chevronsOverMarkers(page), [], 'edge chevrons over markers at the opening view');
  assert.deepEqual(await labelsOverMarkers(page), [], 'labels over other markers at the opening view');
  await shot(page, 'dist-open');
  await ev(page, () => window.__cle.preset('wide')); await settle(page);
  v = await view(page);
  assert.ok(v.earthPx < 3 && v.se.every(a => a > .5), `wide preset: Earth ${v.earthPx.toFixed(2)} px, SE glyphs ${v.se}`);
  if (feature('V5 EM Lagrange glyphs hidden (dist)', v.em.every(a => a === 0), `EM glyph alpha ${v.em}`)) assert.ok(!v.labels.some(s => s.startsWith('EM-L')), v.labels.join(','));
  assert.deepEqual(await labelsOverMarkers(page), [], 'labels over other markers in the L1–L2 view (Earth label in the 106-px blob)');
  await shot(page, 'dist-se-default');
  await fixes(page);
  let g = await geometry(page);
  assert.ok(g.L[1][0] > 1.49e6 && Math.hypot(g.L[1][1], g.L[1][2]) < 1e-3);
  // Markers sit on their own trails: at an off-grid epoch, distance from each marker to its batched trail polyline
  const onTrail = await ev(page, t => {
    const c = window.__cle, w = c.world, RE = 6378.137, out = {};
    c.setTime(t); w.evaluate(t);
    const now = (t - w.t0) / 1000;
    for (const T of w.targets) if (c.isVisible(T) && T.has) {
      const anchor = T.track.center === 'moon' ? 'moon' : 'earth', b = c.trails.batches[anchor], { start, end, time, info } = b.buf, n = b.geometry.instanceCount;
      const rel = T.track.center === 'moon' ? [0, 1, 2].map(k => T.pos[k] - w.moonF[k]) : T.pos, p = [rel[0] / RE, rel[2] / RE, -rel[1] / RE];
      let best = Infinity;
      for (let i = 0; i < n; i++) if (info[i * 2] === T.index && time[i * 2] <= now && time[i * 2 + 1] >= now) {
        const a = [0, 1, 2].map(k => start[i * 3 + k]), d = [0, 1, 2].map(k => end[i * 3 + k] - a[k]), dd = d[0] ** 2 + d[1] ** 2 + d[2] ** 2;
        const u = dd ? Math.max(0, Math.min(1, ((p[0] - a[0]) * d[0] + (p[1] - a[1]) * d[1] + (p[2] - a[2]) * d[2]) / dd)) : 0;
        best = Math.min(best, Math.hypot(...[0, 1, 2].map(k => a[k] + u * d[k] - p[k])) * RE);
      }
      out[T.id] = { km: best, r: Math.hypot(...T.pos) };
    }
    return out;
  }, Date.parse(NOW) + 37 * 60e3 + 13e3);
  const worst = Object.entries(onTrail).sort((a, b) => b[1].km / b[1].r - a[1].km / a[1].r)[0];
  assert.ok(Object.values(onTrail).every(v => v.km < 2e-4 * v.r + 0.5), JSON.stringify(worst));        // chord sagitta of the 0.035 rad subdivision: 1.5e-4 r
  ok('markers on their trails', `${Object.keys(onTrail).length} targets at an off-grid epoch, worst ${worst[0]} ${worst[1].km.toFixed(2)} km off its trail chord at ${(worst[1].r / 1e3).toFixed(0)}e3 km`);
  await ev(page, t => window.__cle.setTime(Date.parse(t)), NOW);

  await ev(page, () => { const c = window.__cle; c.setFrame('eci'); c.preset('wide'); }); await shot(page, 'dist-eci');
  g = await geometry(page); assert.deepEqual(g.R, [1, 0, 0, 0, 1, 0, 0, 0, 1]);
  await ev(page, () => { const c = window.__cle; c.setFrame('em'); c.preset('em'); }); await settle(page);
  // EM frame, idle: SE-L1/L2 and heliocentric trails are capped (V8), so their tracklets are not drawn; no bead of an
  // off-screen target is on screen (before: 101 of Falcon Heavy-013 S2's beads in a string across the top)
  const bem = await beadsOnScreen(page);
  assert.deepEqual(bem.offTarget, [], `EM frame idle: beads of off-screen targets on screen ${JSON.stringify(bem.out)}`);
  ok('EM frame idle beads', `${bem.shown}/${bem.total} beads drawn, none of an off-screen target on screen`);
  await shot(page, 'dist-em');
  g = await geometry(page);
  assert.ok(Math.abs(g.moonF[0] - g.moonD) < 1e-6 && Math.abs(g.L[3][0] / g.moonD - 1.1678328) < 1e-4, JSON.stringify(g.L[3]));

  // Hover a HEO target in the Earth–Moon preset (V2): tooltip, hover ring, trail emphasis through the pointer
  await ev(page, () => { const c = window.__cle; c.select(null); c.setFrame('se'); c.focusOn('earth'); c.preset('em'); }); await settle(page);
  const heo = await ev(page, () => { const c = window.__cle, W = c.world; return (W.byId.get('vela-5a')?.screen ? 'vela-5a' : W.targets.find(T => T.cat.regime === 'CISLUNAR_HEO' && c.isVisible(T) && T.screen)?.id) || null; });
  assert.ok(heo, 'a cislunar HEO target on screen');
  const idle = await texel(page, heo), box = await canvasBox(page), at = await ev(page, id => window.__cle.screenOf(id), heo);
  await page.mouse.move(box.x + at.x + 3, box.y + at.y - 2); await settle(page);
  const hv = await ev(page, () => ({ hovered: window.__cle.state.hovered, tip: document.getElementById('tooltip').textContent, hidden: document.getElementById('tooltip').hidden }));
  assert.ok(hv.hovered === heo && !hv.hidden, JSON.stringify(hv));
  const hooks = await ev(page, () => !!(window.__cle.trails.setHover && window.__cle.trails.setIdleDim));
  if (feature('V2 hover emphasis (dist)', hooks, 'trails.setHover missing')) assert.ok(!same(idle, await texel(page, heo)), `hovering ${heo} raises its trail`);
  ok('hover a HEO target', `${heo}: ${hv.tip.slice(0, 60)}`);
  await shot(page, 'dist-hover-heo');
  await parkMouse(page); await settle(page);
  // The pointer over a trail (the same target 12 h ago, away from its marker) raises it too (V2 "marker/trail")
  const trailAt = await ev(page, ([id, t]) => { const c = window.__cle, s = c.screenAt(id, Date.parse(t) - 12 * 3600e3), m = c.screenOf(id); return s && m && Math.hypot(s.x - m.x, s.y - m.y) > 20 ? s : null; }, [heo, NOW]);
  if (trailAt) {
    await page.mouse.move(box.x + trailAt.x, box.y + trailAt.y); await settle(page); await page.waitForTimeout(60); await settle(page);
    const th = await ev(page, () => ({ hovered: window.__cle.state.hovered, trail: window.__cle.trails.hovered, tip: document.getElementById('tooltip').hidden }));
    assert.deepEqual(th, { hovered: heo, trail: heo, tip: false });
    ok('trail hover', `${heo} raised from its trail at (${trailAt.x.toFixed(0)}, ${trailAt.y.toFixed(0)})`);
    await parkMouse(page); await settle(page);
  }

  // Focus the Moon: lunar orbiters around it
  await ev(page, () => { const c = window.__cle; c.setFrame('se'); c.preset('moon'); }); await settle(page);
  await ev(page, () => { const c = window.__cle; c.camera.position.setLength(5.5); }); await settle(page);
  const lunar = await ev(page, () => { const c = window.__cle, w = c.world;
    return w.targets.filter(T => T.cat.regime === 'LUNAR_ORBIT' && T.has && c.isVisible(T)).map(T => [T.id, Math.hypot(...T.rel.slice(0, 3))]); });
  assert.ok(lunar.length >= 5 && lunar.every(([, r]) => r < 2e4), JSON.stringify(lunar));
  ok('focus Moon', lunar.map(([id, r]) => `${id} ${r.toFixed(0)} km`).join(', '));
  await shot(page, 'dist-focus-moon');

  // Focus SE-L2: the L2 population around the marker
  await ev(page, () => window.__cle.preset('l2')); await settle(page);
  const l2 = await ev(page, () => { const c = window.__cle, w = c.world, L = w.lagrange.out[1], f = c.focusKm;
    return { focus: c.state.focus, origin: Math.hypot(...f) , near: w.targets.filter(T => T.has && c.isVisible(T) && T.cat.regime === 'SE_L2')
      .map(T => [T.label, Math.hypot(T.state[0] - L[0], T.state[1] - L[1], T.state[2] - L[2])]) }; });
  assert.equal(l2.focus, 'SE-L2');
  cc = await chevronClash(page);
  assert.ok(cc.chevrons.includes('earth') && cc.chevrons.includes('moon') && !cc.clash.length, `SE-L2 view chevrons ${cc.chevrons}, overlaps ${cc.clash}`);
  assert.deepEqual(await chevronsOverMarkers(page), [], 'edge chevrons over markers in the SE-L2 view');
  ok('focus SE-L2', l2.near.map(([id, d]) => `${id} ${(d / 1e3).toFixed(0)}e3 km`).join(', '));
  await shot(page, 'dist-focus-se-l2');

  // DRO-A in the Earth–Moon rotating frame
  await ev(page, () => { const c = window.__cle; c.setFrame('em'); c.select('dro-a'); c.focusOn('moon'); }); await settle(page);
  await ev(page, () => { const c = window.__cle; c.camera.position.set(0.0, 1, 0.35).setLength(75); c.controls.update(); }); await settle(page);
  const dro = await trailSpread(page, 'dro-a', (await geometry(page)).moonF);
  ok('DRO-A in EM rotating', `${dro.count} trail vertices, ≤ ${(dro.max / 1e3).toFixed(0)}e3 km of the Moon`);
  await shot(page, 'dist-focus-dro-a-em');

  // Vela 5A selected and followed (V7): the HEO focus distance keeps Earth on screen for 2 d of playback
  await ev(page, () => { const c = window.__cle; c.setFrame('se'); c.select('vela-5a'); c.focusOn('vela-5a'); }); await settle(page);
  const v7 = await ev(page, t0 => {
    const c = window.__cle, out = [];
    for (let h = 0; h <= 48; h += 3) {
      c.setTime(t0 + h * 3600e3); c.probe(1);
      const p = c.bodies.earth.position.clone().project(c.camera);
      out.push([h, +p.x.toFixed(3), +p.y.toFixed(3)]);
    }
    c.setTime(t0); c.probe(1);
    return { d: c.camera.position.length(), out };
  }, Date.parse(NOW));
  const off = v7.out.filter(([, x, y]) => Math.abs(x) > .95 || Math.abs(y) > .95);
  assert.deepEqual(off, [], `Earth leaves the view while following Vela 5A (camera ${v7.d.toFixed(1)} R⊕): ${JSON.stringify(off)}`);
  cc = await chevronClash(page);
  assert.deepEqual(cc.clash, [], `edge chevrons overlap at the Vela 5A focus: ${cc.clash}`);
  ok('follow Vela 5A (V7)', `camera ${v7.d.toFixed(1)} R⊕; Earth on screen at all ${v7.out.length} epochs over 48 h (|ndc| ≤ ${Math.max(...v7.out.flatMap(([, x, y]) => [Math.abs(x), Math.abs(y)])).toFixed(2)})`);
  await settle(page); await shot(page, 'dist-vela5a');
  await page.keyboard.press('?'); await settle(page);
  assert.ok(await shown(page, '#help'), '#help shown after ?');
  const hc = await ev(page, () => ({ n: document.querySelectorAll('#help-colours .swatch').length, text: document.getElementById('help-colours').textContent, mouse: getComputedStyle(document.querySelector('.mouse-hint')).display, touch: getComputedStyle(document.querySelector('.touch-hint')).display }));
  assert.ok(hc.n === 8 && /lunar/.test(hc.text) && /SE-L2/.test(hc.text) && hc.mouse !== 'none' && hc.touch === 'none', JSON.stringify(hc));
  ok('help colour key', hc.text);
  await shot(page, 'dist-help');
  await page.keyboard.press('Escape'); await settle(page);
  await ev(page, () => { const c = window.__cle; c.select(null); c.focusOn('earth'); }); await settle(page);

  // A NEOCP object with its tracklets (the identified target with the most tracklets)
  const neo = await ev(page, () => { const c = window.__cle, ids = c.tracklets.items.reduce((m, x) => m.set(x.id, (m.get(x.id) || 0) + x.n), new Map());
    const id = [...ids.entries()].sort((a, b) => b[1] - a[1]).find(([i]) => c.world.byId.get(i)?.track)?.[0];
    c.setFrame('eci'); c.select(id); c.focusOn(id); return { id, dots: ids.get(id), items: c.tracklets.items.filter(x => x.id === id).length }; });
  await settle(page); await page.waitForTimeout(200);
  assert.ok(neo.id && neo.dots > 0, JSON.stringify(neo));
  const neoShown = await ev(page, id => { const t = window.__cle.tracklets, L = t.layers.find(x => x.id === id), tk = t.by.get(id).tk; return L.g.visible ? tk.slice(...L.shown).reduce((m, x) => m + x.d1 - x.d0, 0) : 0; }, neo.id);
  assert.equal(neoShown, neo.dots, 'a selected target shows every bead (its trail is the whole window)');
  ok('NEOCP tracklets', `${neo.id}: ${neo.items} tracklets, ${neo.dots} dots`);
  await shot(page, 'dist-neocp');

  // Scale cues (V12) on the Earth preset: GEO ring + scale bar
  await ev(page, () => { const c = window.__cle; c.select(null); c.setFrame('se'); c.preset('earth'); }); await settle(page);
  if (feature('V12 scale bar + rings layer (dist)', await ev(page, () => !!document.getElementById('scale-bar') && !!document.querySelector('[data-layer="rings"]')), '#scale-bar / [data-layer="rings"] missing')) {
    await ev(page, () => document.querySelector('[data-layer="rings"]').click()); await settle(page);
    const sc = await ev(page, () => ({ bar: document.getElementById('scale-bar').textContent, on: window.__cle.layers.rings, earthPx: window.__cle.state.earthPx }));
    assert.ok(sc.on && /\d/.test(sc.bar), JSON.stringify(sc));
    ok('dist scale bar + rings', `"${sc.bar}" at Earth ${sc.earthPx.toFixed(0)} px`);
    await shot(page, 'dist-scale');
    await ev(page, () => document.querySelector('[data-layer="rings"]').click());
  }

  await ev(page, () => { const c = window.__cle; c.select(null); c.setFrame('se'); c.focusOn('earth'); c.setChip('t2', true); c.setChip('nat', true); c.preset('wide'); });
  const t2 = await ev(page, () => window.__cle.world.targets.filter(T => T.tier === 2 && window.__cle.isVisible(T)).length);
  assert.ok(t2 > 0);
  ok('tier 2 + natural on', `${t2} tier-2 targets visible`);
  await shot(page, 'dist-tier2');
  await shot(page, 'dist-legend', '#legend');
  const ms = await ev(page, () => window.__cle.probe(60));
  ok('dist frame-time probe', `median ${ms.median.toFixed(2)} ms, p90 ${ms.p90.toFixed(2)} ms`);
  await page.context().close();

  // Phone
  const phone = await open(file, `?t=${NOW}`, { width: 390, height: 844 });
  const ph = await ev(phone, () => ({ sw: document.documentElement.scrollWidth, canvas: document.querySelector('#scene canvas').getBoundingClientRect().width, earthPx: window.__cle.state.earthPx, labels: window.__cle.labelsShown().length }));
  assert.ok(ph.sw <= 390 && ph.canvas >= 380, JSON.stringify(ph));
  ok('dist phone layout', JSON.stringify(ph));
  const pr = await ev(phone, () => [...document.querySelectorAll('.edge-marker')].filter(e => !e.hidden).map(e => [e.dataset.id, e.classList.contains('right'), getComputedStyle(e).display]));
  assert.ok(pr.some(([, right]) => right) && pr.every(([, , d]) => d !== 'none'), `phone edge chevrons ${JSON.stringify(pr)}`);
  ok('dist phone chevrons', pr.map(([id, r]) => `${id}${r ? ' (right)' : ''}`).join(', '));
  assert.deepEqual(await labelsOverMarkers(phone), [], 'labels over other markers on the phone');
  assert.deepEqual((await chevronClash(phone)).clash, [], 'edge chevrons overlap on the phone');
  assert.deepEqual(await chevronsOverMarkers(phone), [], 'edge chevrons over markers on the phone');
  await shot(phone, 'dist-phone');
  // EM frame on the phone: the SE-L2 chevron sat on Euclid and caught its taps; L1–L2 preset: the Sun glyph sat on SE-L1
  await ev(phone, () => { const c = window.__cle; c.setFrame('em'); c.preset('em'); }); await settle(phone);
  const pem = await chevronsOverMarkers(phone), tap = await ev(phone, () => { const c = window.__cle, r = document.getElementById('scene').getBoundingClientRect();
    return c.world.targets.filter(T => T.screen).map(T => [T.label, document.elementFromPoint(r.left + T.screen.x, r.top + T.screen.y)?.closest('.edge-marker')?.dataset.id]).filter(([, e]) => e); });
  assert.ok(!pem.length && !tap.length, `phone EM frame: chevrons over markers ${pem}, taps caught ${JSON.stringify(tap)}`);
  await ev(phone, () => { const c = window.__cle; c.setFrame('se'); c.preset('wide'); }); await settle(phone);
  const sun = await ev(phone, () => { const c = window.__cle, sr = document.getElementById('scene').getBoundingClientRect(), b = document.getElementById('sun-indicator').getBoundingClientRect(), p = c.lagrange.scene[0].clone().project(c.camera);
    const x = (p.x + 1) * sr.width / 2, y = (1 - p.y) * sr.height / 2, cx = b.left - sr.left + b.width / 2, cy = b.top - sr.top + b.height / 2;
    return { d: Math.hypot(x - cx, y - cy), covers: x > b.left - sr.left - 4 && x < b.right - sr.left + 4 && y > b.top - sr.top - 4 && y < b.bottom - sr.top + 4, edge: document.getElementById('sun-indicator').classList.contains('edge') }; });
  assert.ok(!sun.covers && sun.d >= 24, `phone L1–L2: Sun indicator over the SE-L1 glyph ${JSON.stringify(sun)}`);
  await phone.keyboard.press('?'); await settle(phone);
  const ph2 = await ev(phone, () => ({ mouse: getComputedStyle(document.querySelector('.mouse-hint')).display, touch: getComputedStyle(document.querySelector('.touch-hint')).display }));
  assert.ok(ph2.mouse === 'none' && ph2.touch !== 'none', JSON.stringify(ph2));
  await phone.keyboard.press('Escape');
  ok('phone EM frame / L1–L2 / help', `no chevron over a marker or catching a tap in the EM frame; Sun ${sun.d.toFixed(0)} px from the SE-L1 glyph${sun.edge ? ' (edge)' : ''}; help shows the touch line`);
  await phone.context().close();
}

try {
  if (!args.includes('--no-fixture')) await fixture();
  if (args.includes('--dist')) await dist();
  assert.deepEqual(errors, [], `console errors:\n${errors.join('\n')}`);
  assert.deepEqual(requests, [], `network requests attempted:\n${requests.join('\n')}`);
  ok('no console errors, no http(s) requests');
  if (STRICT) assert.ok(!pending.length && (UPDATE || !shots.missing.length), `--strict: pending feature checks: ${pending.join('; ') || 'none'}; ${shots.missing.length} screenshots without baseline`);
  if (UPDATE) await writeBaselines();
  console.log(`screenshots: ${shots.compared} compared with ${path.relative(root, baselines)}, ${shots.updated} baselines written, ${shots.missing.length} without baseline, ${shots.skipped} skipped (data changed)`
    + `${pending.length ? `; pending: ${pending.join('; ')}` : ''}`);
  assert.deepEqual(shots.failed, [], `screenshots differ from ${path.relative(root, baselines)} (diffs: build/screens_diff/):\n${shots.failed.join('\n')}`);
} finally {
  await browser.close();
  await rm(work, { recursive: true, force: true });
}
