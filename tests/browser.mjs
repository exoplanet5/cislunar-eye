// Playwright (system Chrome, headless, as MMX tests/browser.mjs) against the fixture page on file://, plus the real
// dist/index.html with --dist. Network: the page's only requests are MPC's neocp.json (read ≥ 500 ms after the first
// frame, fulfilled from tests/fixtures/request/mpc_neocp.json) and the local NEOCP helper (http://127.0.0.1:8765:
// /api/health, /api/neocp/summary, /api/neocp/C46FX01?t0&t1&now from build/fixture/request/C46FX01.json; a context can
// play the helper offline, its requests refused). Any other http(s) request is aborted and fails the run, and so does any
// console error except the refused-helper loads of an offline context. Screenshots → build/screens/.
// Every screenshot is taken paused at a pinned ?t= with the wall clock pinned (page.clock) and CSS transitions finished,
// so reruns match to a few anti-aliased DOM pixels; each is compared with build/screens_baseline/<name>.png: pixels with
// max channel |Δ| > 8, fail above 0.1 %, diff image → build/screens_diff/. Baselines are keyed on the data manifests
// (minus built_utc): when the fixture or dist data changed the set's diffs are skipped with a notice; no baseline yet →
// notice. --update-baselines writes them only after a passing run. Checks of features still being integrated report
// "pending" while their hook or markup is missing; --strict turns pending and missing baselines into failures.
//   node tests/browser.mjs [--dist] [--no-fixture] [--update-baselines] [--strict] [--baselines DIR]      (CHROME_PATH overrides the browser)
import { chromium } from 'playwright';
import assert from 'node:assert/strict';
import { cp, mkdir, readdir, rm, writeFile, copyFile } from 'node:fs/promises';
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { inflateSync, deflateSync, crc32 } from 'node:zlib';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const args = process.argv.slice(2), UPDATE = args.includes('--update-baselines'), STRICT = args.includes('--strict'), opt = k => args[args.indexOf(k) + 1];
const screens = path.join(root, 'build/screens'), diffDir = path.join(root, 'build/screens_diff'), work = path.join(root, 'build/browser', String(process.pid));   // per run: parallel runs keep their page
const baselines = path.resolve(root, args.includes('--baselines') ? opt('--baselines') : 'build/screens_baseline');
const NOW = '2026-10-21T13:00:00Z';            // epoch of every screenshot (the fixture's now)
const WALL = '2026-10-22T13:00:00Z';           // pinned wall clock: inside the window (LIVE available); re-pinned before each screenshot
// The real build's own epoch (dist/data/manifest.js now_utc; make daily moves it), so the dist checks sit mid-window like the
// fixture's; its wall clock is pinned 24 h later, as WALL is to NOW.
function buildNow() { const f = path.join(root, 'dist/data/manifest.js'), m = existsSync(f) && /"now_utc":"([^"]+)"/.exec(readFileSync(f, 'utf8')); return m ? m[1] : NOW; }
const MPC_LIST = 'https://www.minorplanetcenter.net/Extended_Files/neocp.json', HELPER = 'http://127.0.0.1:8765';
const MPC_JSON = readFileSync(path.join(root, 'tests/fixtures/request/mpc_neocp.json'), 'utf8');
const CANNED = path.join(root, 'build/fixture/request/C46FX01.json');                 // npm run fixture: the helper's answer for C46FX01
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
    meta.sets[set] = { manifest: mine[0].fp, chrome: browser.version(), viewport: '1440×900 / 1024×768 / 390×844, DPR 1', updated: new Date().toISOString(), shots: mine.map(x => x.name) };
  }
  for (const x of fresh) await copyFile(x.file, path.join(baselines, `${x.name}.png`));
  await writeFile(metaFile, JSON.stringify(meta, null, 1) + '\n');
  shots.updated = fresh.length;
}

// The fixture network (see the header). net.helper 'online' answers from the fixtures; 'offline' refuses the helper's
// requests like a closed port (the NEOCP lookup must show "helper offline" and keep the row waiting). net.log lists what
// the page asked for, in order: 'mpc', 'health', 'summary', 'C46FX01'. Anything else → `requests` (a failure).
const geoFlags = Object.fromEntries(JSON.parse(MPC_JSON).map(r => [r.Temp_Desig, { geo: r.Temp_Desig === 'C46FX01', moid_au: null, score: r.Score, n_obs: r.NObs, arc_d: r.Arc }]));
function serve(route, net) {
  const url = route.request().url(), u = new URL(url);
  const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', headers: { 'access-control-allow-origin': '*', 'cache-control': 'no-store' },
    body: typeof body === 'string' ? body : JSON.stringify(body) });
  if (url === MPC_LIST) { net.log.push('mpc'); return json(MPC_JSON); }
  if (u.origin === HELPER) {
    const kind = u.pathname === '/api/health' ? 'health' : u.pathname === '/api/neocp/summary' ? 'summary' : u.pathname === '/api/neocp/C46FX01' ? 'C46FX01' : null;
    if (kind) {
      net.log.push(kind);
      if (net.helper === 'offline') return route.abort('connectionrefused');
      if (kind === 'health') return json({ ok: true, service: 'cislunar-eye', api: 1, fo: true, now_utc: WALL });
      if (kind === 'summary') return json({ ok: true, regenerated_utc: '2026-10-22T12:35:01Z', fetched_utc: WALL, objects: geoFlags });
      net.query.push(Object.fromEntries(u.searchParams));
      return json(readFileSync(CANNED, 'utf8'));
    }
  }
  requests.push(url); return route.abort();
}

async function open(file, query = '', viewport = { width: 1440, height: 900 }, reducedMotion = 'reduce', { helper = 'online', wall = WALL } = {}) {
  const context = await browser.newContext({ viewport, reducedMotion });
  const net = { helper, log: [], query: [], refused: [] };
  await context.route(/^https?:/, route => serve(route, net));
  const page = await context.newPage();
  page.net = net; page.wall = wall;
  page.on('pageerror', error => errors.push(`${path.basename(file)}: ${error.message}`));
  page.on('console', message => {
    if (message.type() !== 'error') return;
    // an offline helper: the browser logs each refused load; that is the expected path, not a page error
    const at = message.location()?.url || '';
    if (net.helper === 'offline' && at.startsWith(`${HELPER}/api/`) && /net::ERR_CONNECTION_REFUSED/.test(message.text())) { net.refused.push(at); return; }
    errors.push(`${path.basename(file)}: ${message.text()}${at ? ` (${at})` : ''}`);
  });
  await page.clock.setSystemTime(wall);                    // Date.now keeps advancing from the pinned wall; performance.now is untouched
  await page.goto(pathToFileURL(file).href + query);
  await page.waitForFunction(() => window.__cle?.ready, null, { timeout: 20000 });
  await settle(page);
  return page;
}
// Wait (≤ ms) for a Node-side condition, e.g. a request the page makes on a timer.
async function until(cond, ms = 5000, what = 'condition') {
  for (const t0 = Date.now(); !(await cond()); ) { if (Date.now() - t0 > ms) throw new Error(`timed out after ${ms} ms waiting for ${what}`); await new Promise(r => setTimeout(r, 50)); }
}
// MPC's list is read ≥ 500 ms after the first frame: screenshots wait for it so the dock's meta line is settled.
const listRead = page => until(async () => page.net.log.includes('mpc') && await ev(page, () => /on NEOCP/.test(document.getElementById('neocp-meta')?.textContent || '')), 5000, 'the MPC neocp.json read');
const settle = page => page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(() => requestAnimationFrame(r)))));
const shot = async (page, name, locator = null) => {
  await settle(page);
  const clock = await ev(page, () => ({ live: window.__cle.clock.live, url: location.search }));
  assert.ok(!clock.live && /[?&]t=/.test(clock.url), `${name}: screenshots need a pinned ?t= (LIVE ${clock.live}, ${clock.url})`);
  await page.clock.setSystemTime(page.wall);                                          // Δ now and the MPC read time: the same at every shot
  await ev(page, () => window.__cle.setTime(window.__cle.clock.t));                   // repaint the clock UI at the pinned wall clock
  await settle(page);
  const file = path.join(screens, `${name}.png`);
  await (locator ? page.locator(locator) : page).screenshot({ path: file, animations: 'disabled' });     // CSS transitions finished, not mid-way
  const r = await compare(name, file);
  console.log(`${r.startsWith('DIFF') ? '!! ' : 'ok '} screenshot ${name}.png · ${r}`);
};
const ev = (page, f, arg) => page.evaluate(f, arg);
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const canvasBox = page => page.locator('#scene canvas').boundingBox();
const parkMouse = page => page.mouse.move(0, 0);                                  // top bar corner: nothing hoverable, leaves the canvas
// Visible elements whose own text is set below 10 px (design §7: nothing under 10 px, credit and tags included).
const smallText = page => ev(page, () => [...document.querySelectorAll('body *')].filter(e => e.offsetParent !== null && [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())
  && parseFloat(getComputedStyle(e).fontSize) < 10).map(e => `${e.id || e.className || e.tagName}: ${getComputedStyle(e).fontSize} "${e.textContent.trim().slice(0, 20)}"`));
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
// Edge chevrons that overlap the Sun indicator, the scale bar (the only chrome left on the render) or each other (V1/V12
// placement), as 'a×b' pairs.
const chevronClash = page => ev(page, () => {
  const box = e => e.getBoundingClientRect(), hit = (a, b) => a.left < b.right - .5 && a.right > b.left + .5 && a.top < b.bottom - .5 && a.bottom > b.top + .5;
  const chev = [...document.querySelectorAll('.edge-marker')].filter(e => !e.hidden && getComputedStyle(e).display !== 'none').map(e => [e.dataset.id, box(e)]);
  const fixed = ['sun-indicator', 'scale-bar'].map(id => document.getElementById(id)).filter(e => e && !e.hidden && getComputedStyle(e).display !== 'none').map(e => [e.id, box(e)]);
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
// target that is off screen: its marker is, and so is its own trail position at the epoch of every drawn tracklet. A fast
// outbound target (ESCAPADE Gold two days after its 2026-11-09 Earth flyby) has left the view while the drawn trail its
// beads sit on still crosses it.
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
    if (on) out[L.id] = { on, markerOn: !!T.screen, trailOn: tk.slice(L.shown[0], L.shown[1]).some(k => !!c.screenAt(L.id, k.t0)) };
  }
  return { out, offTarget: Object.entries(out).filter(([, x]) => !x.markerOn && !x.trailOn).map(([id, x]) => `${id}:${x.on}`), shown: c.tracklets.shownCount, total: c.tracklets.dotCount };
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

// Screen width (CSS px) of the drawn Moon trail and its share of the scene: the opening view frames the lunar orbit (V1,
// about 60 % of the scene width) at any phase, while the Moon itself is foreshortened to ~sin 30° of its distance near quarter.
const moonOrbitWidth = page => ev(page, () => {
  const c = window.__cle, t = c.trails, b = t.batches.earth, { start, time, info } = b.buf, n = b.geometry.instanceCount, now = t.material.uniforms.uNow.value;
  const past = t.data[(t.width + t.moonIndex) * 4], future = t.data[(t.width + t.moonIndex) * 4 + 1], el = document.getElementById('scene');
  const v = c.bodies.earth.position.clone(); let lo = Infinity, hi = -Infinity;
  b.mesh.updateMatrixWorld(true);
  for (let i = 0; i < n; i++) if (info[i * 2] === t.moonIndex && time[i * 2 + 1] >= now - past && time[i * 2] <= now + future) {
    v.set(start[i * 3], start[i * 3 + 1], start[i * 3 + 2]).applyMatrix4(b.mesh.matrixWorld).project(c.camera);
    lo = Math.min(lo, v.x); hi = Math.max(hi, v.x);
  }
  return { px: (hi - lo) * el.clientWidth / 2, frac: (hi - lo) / 2 };
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

  // Data, defaults and sky. No minimoons, no NEOCP snapshot (neocp.js) and no NEOCP live / archive rows: requested NEOCP
  // objects come only from the lookup dock.
  const info = await ev(page, () => {
    const c = window.__cle, w = c.world;
    return { n: w.targets.length, tracks: w.targets.filter(T => T.track).length, missing: c.missing, frame: c.state.frame, t: c.clock.t, live: c.clock.live,
      visible: w.targets.filter(c.isVisible).map(T => T.id).sort(), stars: c.sky.stars.geometry.attributes.position.count,
      grid: c.sky.grid.visible, cons: c.sky.constellations.visible, starsOn: c.sky.stars.visible, labels: c.labelsShown(),
      sources: [...new Set(w.targets.map(T => T.source))], groups: [...new Set(w.targets.map(T => T.group))], natural: w.targets.filter(T => T.cat.obj_type === 'natural' || 'natural' in T.cat).length,
      legacy: ['neocpBuilt', 'neocpSnapshot', 'refreshNeocp'].filter(k => k in c), loaded: [...document.scripts].map(x => x.src).filter(Boolean).map(x => x.split('/').pop()),
      listed: document.querySelector('[data-id="fx-listed"]')?.textContent, listedTitle: document.querySelector('[data-id="fx-listed"]')?.title,
      tier2Row: !!document.querySelector('[data-id="fx-stale-rb"]'), banner: document.getElementById('banner').textContent };
  });
  assert.equal(info.n, 10); assert.equal(info.tracks, 9); assert.deepEqual(info.missing, []);
  assert.equal(info.frame, 'se'); assert.equal(info.t, Date.parse(NOW)); assert.equal(info.live, false);
  assert.deepEqual(info.visible, ['fx-derelict', 'fx-dro', 'fx-em-l2', 'fx-flyby', 'fx-heo', 'fx-lunar-2h', 'fx-se-l1', 'fx-se-l2']);
  assert.equal(info.stars, 9827); assert.equal(info.starsOn, true); assert.equal(info.grid, false); assert.equal(info.cons, false);
  assert.deepEqual(info.sources, ['catalog']); assert.equal(info.natural, 0, 'no natural objects');
  assert.ok(!info.groups.some(g => /NEOCP|Natural/i.test(g)), `no NEOCP live / archive or natural groups: ${info.groups}`);
  assert.deepEqual(info.legacy, [], 'the neocp.js snapshot hooks are gone');
  assert.ok(!info.loaded.some(x => /neocp/.test(x)) && !existsSync(path.join(work, 'data/neocp.js')), `no neocp.js: ${info.loaded}`);
  assert.match(info.listed, /listed, not drawn/); assert.match(info.listedTitle, /no public ephemeris/);
  assert.equal(info.tier2Row, true, 'tier-2 rows are listed even when hidden');
  assert.ok(info.labels.length <= 40 + 4 && info.labels.includes('Earth') && info.labels.includes('Moon'), info.labels.join(','));
  ok('fixture loaded', `${info.n} targets, ${info.tracks} tracks, visible ${info.visible.length}, ${info.labels.length} labels, 9827 stars; no natural, NEOCP live/archive or neocp.js`);

  // Network policy (spec A3): on a file:// page with nothing to restore, MPC's list is read once after the first frame and
  // the helper is not touched until the dock is used.
  await listRead(page); await page.waitForTimeout(300);
  const net0 = { log: [...page.net.log], meta: await ev(page, () => [document.getElementById('neocp-meta').textContent, document.getElementById('helper-status').textContent,
    [...document.querySelectorAll('#situation dt')].find(d => d.textContent === 'NEOCP')?.nextElementSibling.textContent]) };
  assert.deepEqual(net0.log, ['mpc'], `requests at load: ${net0.log}`);
  assert.match(net0.meta[0], /^6 on NEOCP · MPC 13:00/); assert.equal(net0.meta[1], 'helper', 'helper not probed at load');
  assert.equal(net0.meta[2], 'on request · list 13:00', 'Situation shows the MPC read on a paused, idle page');
  ok('network at load', `MPC neocp.json only ("${net0.meta[0].replace(/[↻▾▸].*$/, '').replace('geo only', '').trim()}"); helper not probed`);

  // Chrome removed by the redesign: the Natural chip, the twilight (Sun −12°) layer, its legend key and its Earth ring,
  // the old clocks and their containers.
  const gone = await ev(page, () => ({ sel: ['[data-chip=nat]', '[data-layer=twilight]', '[data-legend=nat]', '[data-legend=twilight]', '#top-clock', '#utc-mini', '#scene-clock', '#frame-note', '.scene-title', '.scene-foot', '.time-panel']
    .filter(q => document.querySelector(q)), twilight: 'twilight' in window.__cle.bodies, layers: Object.keys(window.__cle.layers).filter(k => /twilight|nat/i.test(k)),
    chips: Object.keys(window.__cle.chips), text: document.body.innerText }));
  assert.deepEqual(gone.sel, [], `removed chrome still present: ${gone.sel}`);
  assert.ok(!gone.twilight && !gone.layers.length && gone.chips.join() === 't1,t2,t3', JSON.stringify({ twilight: gone.twilight, layers: gone.layers, chips: gone.chips }));
  assert.ok(!/Blue Marble|minimoon|−12°|NEOCP archive/i.test(gone.text), 'no Blue Marble credit, minimoon, −12° or NEOCP archive text on the page');
  ok('removed chrome', 'no Natural chip, twilight layer / legend / Earth ring, old clocks or NEOCP archive');

  // One epoch clock: at an off-grid epoch exactly one element shows the time (the title bar's #utc-time).
  const epochAt = async iso => { await ev(page, t => window.__cle.setTime(Date.parse(t)), iso); await settle(page);
    return ev(page, hms => [...document.querySelectorAll('body *')].filter(e => e.offsetParent !== null && [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.includes(hms)))
      .map(e => e.id || e.className), iso.slice(11, 19)); };
  for (const iso of ['2026-10-21T09:41:27Z', '2026-10-24T22:05:53Z']) assert.deepEqual(await epochAt(iso), ['utc-time'], `elements showing ${iso.slice(11, 19)}`);
  const clk = await ev(page, () => ({ date: document.getElementById('utc-date').textContent, time: document.getElementById('utc-time').textContent, dt: document.getElementById('utc-clock').dateTime,
    pill: document.getElementById('live-badge').textContent, delta: document.getElementById('elapsed').textContent }));
  assert.deepEqual([clk.date, clk.time, clk.dt.slice(0, 19)], ['2026-10-24', '22:05:53', '2026-10-24T22:05:53'], JSON.stringify(clk));
  assert.ok(/PAUSED/.test(clk.pill) && /^Δ now \+2 d 09 h$/.test(clk.delta), JSON.stringify(clk));
  await ev(page, t => window.__cle.setTime(Date.parse(t)), NOW); await settle(page);
  ok('one epoch clock', `#utc-time only (${clk.date} ${clk.time}, "${clk.pill}", "${clk.delta}")`);

  // Credit: exactly "Illustration: Mickey", in the dock below the scene canvas, at its bottom right.
  const cr = await ev(page, () => { const e = document.getElementById('credit'), b = e.getBoundingClientRect(), c = document.querySelector('#scene canvas').getBoundingClientRect(), d = document.getElementById('time-dock').getBoundingClientRect();
    return { text: e.textContent, cls: e.className, n: document.querySelectorAll('.scene-credit').length, inScene: !!e.closest('#scene'), top: b.top, bottom: b.bottom, right: b.right, left: b.left,
      cBottom: c.bottom, cRight: c.right, cLeft: c.left, dBottom: d.bottom, dRight: d.right, size: parseFloat(getComputedStyle(e).fontSize) }; });
  assert.equal(cr.text, 'Illustration: Mickey'); assert.equal(cr.n, 1); assert.ok(/scene-credit/.test(cr.cls) && !cr.inScene, JSON.stringify(cr));
  assert.ok(cr.top >= cr.cBottom && cr.left > (cr.cLeft + cr.cRight) / 2 && cr.cRight - cr.right <= 24 && cr.dBottom - cr.bottom <= 16 && cr.size >= 10,
    `credit below the canvas at its bottom right: ${JSON.stringify(cr)}`);
  ok('credit', `"${cr.text}" ${cr.size} px, ${(cr.top - cr.cBottom).toFixed(0)} px below the canvas, ${(cr.cRight - cr.right).toFixed(0)} px from its right edge`);
  assert.deepEqual(await smallText(page), [], 'text below 10 px at 1440×900');

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
    return { normalDot: (n.x * sv[0] + n.y * sv[1] + n.z * sv[2]) / d, color: b.terminator.material.color.getHexString(), solid: !b.terminator.material.dashed,
      twilight: 'twilight' in b, tidal: ex.dot(toEarth) };
  });
  assert.ok(term.normalDot > 1 - 1e-9 && term.color === 'ffd54f' && term.solid && !term.twilight && term.tidal > Math.cos(6 * Math.PI / 180), JSON.stringify(term));
  const halo = await trailSpread(page, 'fx-se-l2', g.L[1]);
  assert.ok(halo.count > 100 && halo.max < 6e5, `SE-L2 halo trail spread ${JSON.stringify(halo)}`);
  ok('SE rotating frame', `Sun x=${(g.sunF[0] / 1.496e8).toFixed(4)} AU, SE-L2 x=${g.L[1][0].toFixed(0)} km, terminator normal·sun=${term.normalDot.toFixed(12)}, Moon tidal ${term.tidal.toFixed(12)}, halo trail ≤ ${(halo.max / 1e3).toFixed(0)}e3 km of SE-L2`);
  await shot(page, 'fixture-se-default');

  // Marker glyphs (V4): hollow = position not from a current ephemeris (extrap or stale), derelict = dim fill (FX-DEREL: fresh GP)
  const mk = await ev(page, () => {
    const c = window.__cle, out = {};
    c.world.targets.forEach((T, i) => { if (c.isVisible(T) && T.has) out[T.id] = { q: T.seg.quality, status: T.cat.status, stale: !!(T.cat.staleness?.flag || /stale/i.test(T.caveat || '')),
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

  // The three frames from the title bar's segmented control and from keys 1 / 2 / 3; the stage head names the frame.
  const frameUI = () => ev(page, () => ({ frame: window.__cle.state.frame, pressed: [...document.querySelectorAll('#frames [data-frame]')].filter(b => b.getAttribute('aria-pressed') === 'true').map(b => b.dataset.frame),
    title: document.getElementById('view-title').title, trails: window.__cle.trails.frame }));
  const frames = [];
  for (const f of ['se', 'eci', 'em']) { await page.locator(`#frames [data-frame="${f}"]`).click(); await settle(page); frames.push(await frameUI()); }
  await parkMouse(page);
  for (const [k, f] of [['2', 'eci'], ['1', 'se'], ['3', 'em'], ['1', 'se']]) { await page.keyboard.press(k); await settle(page); const u = await frameUI(); frames.push(u); assert.equal(u.frame, f, `key ${k}`); }
  for (const u of frames) assert.ok(u.pressed.length === 1 && u.pressed[0] === u.frame && u.trails === u.frame, JSON.stringify(u));
  assert.deepEqual(frames.slice(0, 3).map(u => u.title), ['Sun–Earth rotating · focus Earth', 'ECI · ICRF · focus Earth', 'Earth–Moon rotating · focus Earth']);
  // View presets from the stage head: the pressed button follows; a hand-moved camera has none.
  const views = [];
  for (const v of ['earth', 'moon', 'wide', 'l1', 'l2', 'em']) {
    await page.locator(`#views [data-view="${v}"]`).click(); await settle(page);
    views.push(await ev(page, () => ({ view: window.__cle.state.view, active: [...document.querySelectorAll('#views .active')].map(b => b.dataset.view), focus: window.__cle.state.focus })));
  }
  assert.deepEqual(views.map(x => [x.view, x.active.join()]), ['earth', 'moon', 'wide', 'l1', 'l2', 'em'].map(v => [v, v]), JSON.stringify(views));
  assert.deepEqual(views.map(x => x.focus), ['earth', 'moon', 'earth', 'SE-L1', 'SE-L2', 'earth']);
  ok('frames and views', `buttons and keys 1/2/3: ${frames.map(u => u.frame).join(' → ')}; presets ${views.map(x => x.view).join(', ')}`);

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

  // Inspector tabs (TARGET / SITUATION / ABOUT): selecting shows TARGET, deselecting SITUATION; a tab picked by hand holds
  // until the selection changes; TARGET is disabled ("none") while nothing is selected.
  const tabs = () => ev(page, () => ({ tab: window.__cle.tab, sel: [...document.querySelectorAll('[role=tab]')].filter(b => b.getAttribute('aria-selected') === 'true').map(b => b.id),
    shown: ['readout', 'situation', 'about-panel'].filter(id => !document.getElementById(id).hidden), targetOff: document.getElementById('itab-target').disabled, nav: !document.getElementById('tab-nav').hidden }));
  await ev(page, () => window.__cle.select(null)); await settle(page);
  const tb = [await tabs()];
  await page.locator('#itab-about').click(); tb.push(await tabs());
  await ev(page, () => window.__cle.select('fx-dro')); await settle(page); tb.push(await tabs());
  await page.locator('#itab-situation').click(); tb.push(await tabs());
  await ev(page, () => window.__cle.setTime(window.__cle.clock.t + 3600e3)); await settle(page); tb.push(await tabs());   // no selection change: holds
  await ev(page, () => window.__cle.select(null)); await settle(page); tb.push(await tabs());
  await ev(page, () => window.__cle.select('fx-lunar-2h')); await settle(page); tb.push(await tabs());
  await page.locator('#tab-nav [data-act="next"]').click(); await settle(page);
  const stepped = await ev(page, () => window.__cle.state.selected);
  await ev(page, t => { window.__cle.select(null); window.__cle.setTime(Date.parse(t)); }, NOW); await settle(page); tb.push(await tabs());
  assert.deepEqual(tb.map(x => x.tab), ['situation', 'about', 'target', 'situation', 'situation', 'situation', 'target', 'situation'], JSON.stringify(tb));
  for (const x of tb) assert.ok(x.sel.length === 1 && x.sel[0] === `itab-${x.tab}` && x.shown.length === 1 && x.shown[0] === { target: 'readout', situation: 'situation', about: 'about-panel' }[x.tab], JSON.stringify(x));
  assert.ok(tb[0].targetOff && !tb[2].targetOff && tb[2].nav && !tb[3].nav && tb[5].targetOff, JSON.stringify(tb));
  assert.ok(stepped && stepped !== 'fx-lunar-2h', `› steps to the next visible target: ${stepped}`);
  ok('inspector tabs', 'select → TARGET, deselect → SITUATION, a hand-picked tab holds until the selection changes; ‹ › step');

  // Earth close-up: the textured globe and the solid #ffd54f terminator; no Sun −12° ring (removed with its layer)
  await ev(page, () => { const c = window.__cle; c.select(null); c.preset('earth'); }); await settle(page);
  const earth = await ev(page, () => { const b = window.__cle.bodies; return { px: window.__cle.state.earthPx, map: !!b.earth.material.map?.image?.naturalWidth, term: b.terminator.visible }; });
  assert.ok(earth.px > 80 && earth.map && earth.term, JSON.stringify(earth));
  ok('Earth close-up', `${earth.px.toFixed(0)} px radius, terminator on, no −12° ring`);
  await shot(page, 'fixture-earth');

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
  const keys = await ev(page, () => ({ n: document.querySelectorAll('#help kbd').length, text: [...document.querySelectorAll('#help .keys dt')].map(dt => `${dt.textContent.replace(/\s+/g, ' ').trim()} = ${dt.nextElementSibling.textContent}`).join(' | '), glyphs: document.querySelector('#help .glyphs').textContent }));
  await page.keyboard.press('Escape'); await settle(page);
  assert.ok(keys.n >= 11 && await ev(page, () => window.__cle.state.selected === 'fx-heo' && document.getElementById('help').hidden), `${keys.n} kbd entries; Esc on the help keeps the selection`);
  assert.ok(keys.text.includes('1 2 3 = SE · ECI · EM frame') && keys.text.includes('N = NEOCP lookup'), keys.text);
  assert.ok(!/natural|▲|−12/.test(keys.glyphs) && /Sun 0° terminator/.test(keys.glyphs), keys.glyphs);
  await ev(page, () => window.__cle.select(null));

  // Tier 2 chip (through the button, with its count); the legend follows its layers (tracklets: V10); no natural row
  await ev(page, () => { const c = window.__cle; c.select(null); c.focusOn('earth'); c.preset('wide'); }); await settle(page);
  const chip0 = await ev(page, () => [...document.querySelectorAll('#chips [data-chip]')].map(b => [b.dataset.chip, b.textContent, b.getAttribute('aria-pressed'), b.hidden]));
  assert.deepEqual(chip0, [['t1', 'Tier 19', 'true', false], ['t2', 'Tier 21', 'false', false], ['t3', 'Tier 30', 'false', true]], JSON.stringify(chip0));
  await page.locator('#chips [data-chip="t2"]').click(); await settle(page);
  const vis = await ev(page, () => window.__cle.world.targets.filter(window.__cle.isVisible).map(T => T.id));
  assert.ok(vis.includes('fx-stale-rb') && !vis.includes('fx-listed') && await ev(page, () => window.__cle.chips.t2), vis.join(','));
  ok('tier 2 chip', `${vis.length} visible; chips ${chip0.map(x => x[1]).join(' · ')}`);
  await shot(page, 'fixture-tier2');
  const lg = await ev(page, () => { const row = s => { const e = document.querySelector(`[data-legend="${s}"]`); return e ? !e.hidden : null; }; return { nat: row('nat'), twilight: row('twilight'), tracklets: row('tracklets'), text: document.getElementById('legend').textContent.replace(/\s+/g, ' ').trim() }; });
  assert.ok(lg.nat === null && lg.twilight === null && !/natural|−12/.test(lg.text), JSON.stringify(lg));
  if (feature('V10 tracklet legend row', lg.tracklets !== null, '[data-legend="tracklets"] missing')) {
    assert.ok(lg.tracklets && /tracklet/i.test(lg.text), lg.text);
    await ev(page, () => document.querySelector('[data-layer="tracklets"]').click()); await settle(page);
    assert.equal(await ev(page, () => document.querySelector('[data-legend="tracklets"]').hidden), true, 'tracklet legend row follows the layer');
    await ev(page, () => document.querySelector('[data-layer="tracklets"]').click()); await settle(page);
    ok('tracklet legend row', 'shown with the layer, hidden without');
  }
  await shot(page, 'fixture-legend', '#legend');
  ok('legend', lg.text);

  // Layers popover: "Layers 6/11" (Scene · Observations · Sky); the count follows the checkboxes; a click outside or Esc
  // closes it. Then Lagrange and trails toggles; trail length presets update one texel per target, no geometry rebuild.
  const lc = () => ev(page, () => ({ open: document.getElementById('layers-panel').open, count: document.getElementById('layer-count').textContent, on: document.getElementById('layer-on').textContent,
    boxes: document.querySelectorAll('#layers-panel [data-layer]').length, groups: [...document.querySelectorAll('#layers-panel .lgh')].map(e => e.textContent) }));
  const l0 = await lc();
  assert.deepEqual(l0, { open: false, count: '6/11', on: '6 of 11 on', boxes: 11, groups: ['Scene', 'Observations', 'Sky'] }, 'Layers starts closed, 6 of 11 on');
  await page.locator('#layers-panel summary').click(); await settle(page);
  await page.locator('[data-layer="grid"]').check(); await settle(page);
  const l1 = await lc();
  assert.ok(l1.open && l1.count === '7/11' && l1.on === '7 of 11 on' && await ev(page, () => window.__cle.sky.grid.visible), JSON.stringify(l1));
  const sb = await page.locator('#scene canvas').boundingBox();
  await page.mouse.click(sb.x + 40, sb.y + sb.height - 40); await settle(page);                   // outside: closes
  assert.equal((await lc()).open, false, 'a click outside closes Layers');
  await page.locator('#layers-panel summary').click(); await page.locator('[data-layer="grid"]').uncheck(); await page.keyboard.press('Escape'); await settle(page);
  const l2 = await lc();
  assert.ok(!l2.open && l2.count === '6/11', JSON.stringify(l2));
  ok('layers popover', `${l0.count} → ${l1.count} → ${l2.count}; closes on an outside click and on Esc`);
  await page.locator('#layers-panel summary').click();
  const before = await ev(page, () => window.__cle.trails.count.earth);
  await page.selectOption('#trail-length', '1d'); await settle(page);
  const tex = await ev(page, () => { const c = window.__cle, T = c.world.byId.get('fx-heo'); return [c.trails.data[(c.trails.width + T.index) * 4], c.trails.count.earth]; });
  assert.ok(tex[0] === 86400 && tex[1] === before, JSON.stringify(tex));
  await page.locator('[data-layer="lagrange"]').uncheck(); await settle(page);
  assert.ok(!(await ev(page, () => window.__cle.labelsShown())).some(s => s.startsWith('SE-L')));
  await page.locator('[data-layer="lagrange"]').check(); await page.selectOption('#trail-length', 'auto'); await settle(page);
  ok('trail presets and Lagrange toggle', `past 1 d texel, ${before} segments unchanged`);

  await page.keyboard.press('Escape'); await settle(page);
  assert.equal(await ev(page, () => document.getElementById('layers-panel').open), false, 'Esc in the popover closes Layers');

  // Catalog tracklets (identified targets' astrometry, now in the chunk payloads): FX-HEO carries W68 and M22, 4 obs each;
  // beads and sight lines; station chips with the code as text (V10)
  await ev(page, () => { document.querySelector('[data-layer="sightlines"]').click(); window.__cle.select('fx-heo'); window.__cle.focusOn('fx-heo'); }); await settle(page);
  await page.waitForTimeout(150);
  const stationChips = () => ev(page, () => [...document.querySelectorAll('#readout .stations .stn[data-station]')].map(e => e.dataset.station));
  const tk = await ev(page, () => { const t = window.__cle.tracklets; return { dots: t.dotCount, shown: t.shownCount, sight: t.layers.some(L => L.sight.visible), stations: t.stations, items: t.items.map(x => `${x.id}:${x.stn}:${x.n}`) }; });
  assert.ok(tk.dots === 8 && tk.shown === 8 && tk.sight && tk.stations.join() === 'W68,M22', JSON.stringify(tk));
  assert.deepEqual(tk.items, ['fx-heo:W68:4', 'fx-heo:M22:4']);
  assert.deepEqual(await stationChips(), ['W68', 'M22']);
  ok('catalog tracklets', `FX-HEO: ${tk.items.length} tracklets, ${tk.dots} beads, stations ${tk.stations.join(', ')}, sight lines on; station chips`);
  await shot(page, 'fixture-tracklets');

  // NEOCP on request (spec A1–A4): the first use of the dock probes the helper (health, then neocp2 flags: FLAG column,
  // geo first); C46FX01 typed and fetched comes back from the helper (the fixture) and is drawn as target req-C46FX01:
  // selected (TARGET tab), its 2 tracklets (G96, T05), its scene label with its range, URL neocp=, Situation's
  // "NEOCP requested", the NEOCP REQUEST card with its MPC listing, the timeline's observation bars.
  await ev(page, () => { const c = window.__cle; c.select(null); c.focusOn('earth', false); c.preset('em', 0); }); await settle(page);
  const n0 = await ev(page, () => window.__cle.world.targets.length);
  await page.locator('#neocp-input').click();
  await until(() => page.net.log.includes('summary'), 5000, 'the helper probe and the neocp2 flags');
  await until(() => ev(page, () => document.getElementById('helper-status').textContent === 'helper ready' && !!document.querySelector('#neocp-short .geo')), 3000, 'helper ready + FLAG column');
  const sl = await ev(page, () => ({ head: [...document.querySelectorAll('#neocp-short thead th')].map(e => e.textContent), rows: [...document.querySelectorAll('#neocp-short tbody tr')].map(r => r.dataset.desig) }));
  assert.deepEqual(page.net.log, ['mpc', 'health', 'summary'], `requests: ${page.net.log}`);
  assert.ok(sl.head.includes('flag') && sl.rows[0] === 'C46FX01' && sl.rows.length === 5, `shortlist: geo first, 5 rows: ${JSON.stringify(sl)}`);
  await page.keyboard.type('C46FX01'); await page.locator('#neocp-fetch').click();
  await page.waitForFunction(() => window.__cle.world.byId.get('req-C46FX01')?.track && document.querySelector('#neocp-requested .rq.drawn'), null, { timeout: 15000 });
  await page.waitForFunction(() => /[?&]neocp=C46FX01(&|$)/.test(location.search), null, { timeout: 3000 });   // URL writes are throttled (500 ms)
  await page.waitForTimeout(150); await settle(page);
  const nq = await ev(page, () => {
    const c = window.__cle, W = c.world, T = W.byId.get('req-C46FX01'), sit = document.querySelector('#situation [data-neocp]');
    return { n: W.targets.length, src: T.source, label: T.label, group: T.group, regime: T.regime.key, vis: c.isVisible(T), has: T.has, sel: c.state.selected, tab: c.tab, t: c.clock.t,
      t0: W.t0, t1: W.t1, labels: c.labelsShown(), url: location.search, store: localStorage.getItem('cle.neocp'), tracklets: c.tracklets.items.filter(x => x.id === T.id).map(x => `${x.stn}:${x.n}`),
      row: document.querySelector('#neocp-requested .rq.drawn')?.dataset.id, st: document.querySelector('#neocp-requested .rq .st')?.textContent.replace(/ /g, ' '),
      listing: document.getElementById('ro-listing')?.textContent, card: document.querySelector('#readout .card.request h4')?.textContent,
      sit: sit && [...sit.querySelectorAll('td')].map(td => td.textContent), mpc: [...document.querySelectorAll('#situation dt')].find(d => d.textContent === 'NEOCP')?.nextElementSibling.textContent,
      obs: document.querySelectorAll('#timeline-marks .obs').length, obsSel: document.querySelectorAll('#timeline-marks .obs.sel').length, list: [...document.querySelectorAll('#target-list [data-id]')].some(r => r.dataset.id === T.id) };
  });
  const q = page.net.query[0];
  assert.ok(nq.n === n0 + 1 && nq.src === 'request' && nq.label === 'C46FX01' && nq.group === 'NEOCP · requested' && nq.regime === 'NEOCP_GEO' && nq.vis && nq.has, JSON.stringify(nq));
  assert.ok(nq.sel === 'req-C46FX01' && nq.tab === 'target' && nq.t === Date.parse(NOW), `selected, TARGET tab, clock kept inside the track: ${JSON.stringify(nq)}`);
  assert.ok(+q.t0 === nq.t0 && +q.t1 === nq.t1 && +q.now >= nq.t0 && +q.now <= nq.t1, `helper query ${JSON.stringify(q)}`);
  assert.deepEqual(nq.tracklets, ['G96:5', 'T05:5']); assert.deepEqual(await stationChips(), ['G96', 'T05']);
  assert.ok(nq.labels.some(l => /^C46FX01 \d+\.\d+ LD$/.test(l)), `scene label "C46FX01 n LD": ${nq.labels}`);
  assert.ok(/[?&]neocp=C46FX01(&|$)/.test(nq.url) && /[?&]sel=req-C46FX01(&|$)/.test(nq.url) && nq.store === 'C46FX01', `${nq.url} · localStorage ${nq.store}`);
  assert.ok(nq.row === 'req-C46FX01' && nq.st === 'drawn · 10 obs · 1.13 d · rms 0.19″' && !nq.list, JSON.stringify({ row: nq.row, st: nq.st, list: nq.list }));
  assert.ok(/^NEOCP request/i.test(nq.card) && nq.listing === 'Score 97 · V 19.8 · Updated Oct. 21.52 UT · not seen 2.08 d', `NEOCP REQUEST card listing: ${nq.listing}`);
  assert.deepEqual(nq.sit, ['NEOCP requested', '1', '1', '1']); assert.equal(nq.mpc, 'on request · list 13:00');
  assert.ok(nq.obs >= 2 && nq.obs === nq.obsSel, `timeline obs bars ${nq.obs} (${nq.obsSel} selected)`);
  ok('NEOCP request', `C46FX01 → req-C46FX01 drawn and selected; "${nq.st}"; label "${nq.labels.find(l => l.startsWith('C46FX01'))}"; tracklets ${nq.tracklets}; listing "${nq.listing}"; ${nq.obs} obs bars`);
  await shot(page, 'fixture-neocp');

  // The same designation again adds no row and asks the helper nothing; Situation's "NEOCP requested" row opens the dock.
  await page.locator('#neocp-input').fill('C46FX01'); await page.locator('#neocp-fetch').click(); await settle(page);
  assert.ok(page.net.log.filter(x => x === 'C46FX01').length === 1 && await ev(page, () => document.querySelectorAll('#neocp-requested .rq').length === 1), 'a duplicate request adds nothing');
  await page.locator('#itab-situation').click(); await page.locator('#situation [data-neocp] button').click(); await settle(page);
  assert.equal(await ev(page, () => document.activeElement?.closest('.rq')?.dataset.desig), 'C46FX01', 'Situation → NEOCP requested focuses its row in the dock');
  ok('NEOCP duplicate + Situation row', 'no second row or request; the Situation row focuses the requested row');

  // Remove (row ×): soft removal (the target keeps its index: trail texels, marker slots), deselected, URL and storage
  // cleaned; fetching it again redraws the same target in place.
  await page.locator('#neocp-requested .rq[data-desig="C46FX01"] .x').click();
  await page.waitForFunction(() => !/[?&]neocp=/.test(location.search), null, { timeout: 3000 }); await settle(page);
  const rmv = await ev(page, () => { const c = window.__cle, T = c.world.byId.get('req-C46FX01'), sit = document.querySelector('#situation [data-neocp]');
    return { kept: !!T, removed: T?.removed, track: T?.track, vis: T?.visible, sel: c.state.selected, tab: c.tab, url: location.search, store: localStorage.getItem('cle.neocp'), rows: document.querySelectorAll('#neocp-requested .rq').length,
      head: document.querySelector('#neocp-dock .req-h').hidden, labels: c.labelsShown().filter(l => l.startsWith('C46FX01')), tracklets: c.tracklets.items.filter(x => x.id === 'req-C46FX01').length,
      sit: sit && [...sit.querySelectorAll('td')].map(td => td.textContent), n: c.world.targets.length }; });
  assert.ok(rmv.kept && rmv.removed && rmv.track === null && !rmv.vis && rmv.sel === null && rmv.tab === 'situation' && rmv.n === n0 + 1, JSON.stringify(rmv));
  assert.ok(!/neocp=/.test(rmv.url) && rmv.store === null && rmv.rows === 0 && rmv.head && !rmv.labels.length && !rmv.tracklets, JSON.stringify(rmv));
  assert.deepEqual(rmv.sit, ['NEOCP requested', '0', '0', '0']);
  await page.locator('#neocp-input').fill('C46FX01'); await page.keyboard.press('Enter');
  await page.waitForFunction(() => window.__cle.world.byId.get('req-C46FX01')?.track && !window.__cle.world.byId.get('req-C46FX01').removed, null, { timeout: 15000 });
  const again = await ev(page, () => ({ n: window.__cle.world.targets.length, sel: window.__cle.state.selected, vis: window.__cle.isVisible(window.__cle.world.byId.get('req-C46FX01')) }));
  assert.deepEqual(again, { n: n0 + 1, sel: 'req-C46FX01', vis: true }, 're-requested in place (Enter fetches)');
  ok('NEOCP remove + refetch', `soft removed (index kept, track dropped), URL "${rmv.url.slice(0, 60)}…" without neocp=; refetched into the same target`);
  await page.locator('#neocp-requested .rq .x').click(); await settle(page);

  // Reload with ?neocp=C46FX01: the helper is probed at load (something to restore) and the object is drawn again,
  // without moving the clock or taking the selection.
  const reload = await open(file, `?t=${NOW}&neocp=C46FX01`);
  await reload.waitForFunction(() => window.__cle.world.byId.get('req-C46FX01')?.track, null, { timeout: 15000 }); await settle(reload);
  const rs = await ev(reload, () => ({ t: window.__cle.clock.t, sel: window.__cle.state.selected, rows: [...document.querySelectorAll('#neocp-requested .rq.drawn')].map(r => r.dataset.desig),
    url: location.search, vis: window.__cle.isVisible(window.__cle.world.byId.get('req-C46FX01')) }));
  assert.ok(reload.net.log[0] === 'health' && reload.net.log.includes('C46FX01'), `restore probes the helper at load: ${reload.net.log}`);
  assert.ok(rs.t === Date.parse(NOW) && rs.sel === null && rs.rows.join() === 'C46FX01' && rs.vis && /neocp=C46FX01/.test(rs.url), JSON.stringify(rs));
  await reload.locator('#neocp-requested .rq').click(); await settle(reload);
  assert.equal(await ev(reload, () => window.__cle.state.selected), 'req-C46FX01', 'a click on the requested row selects it');
  ok('NEOCP restore from the URL', `requests ${reload.net.log.join(', ')}; drawn, clock and selection kept`);
  await reload.context().close();

  // Helper offline: the health probe is refused → "helper offline", the row waits with the helper's instructions, the
  // designation is kept (URL), nothing throws; when the helper answers, ↻ draws it.
  const off = await open(file, `?t=${NOW}`, undefined, 'reduce', { helper: 'offline' });
  await listRead(off);
  assert.deepEqual(off.net.log, ['mpc'], 'nothing but the MPC list at load');
  await off.locator('#neocp-input').fill('C46FX01'); await off.locator('#neocp-fetch').click();
  await off.waitForFunction(() => document.querySelector('#neocp-requested .rq.err'), null, { timeout: 10000 });
  await off.waitForFunction(() => /[?&]neocp=C46FX01(&|$)/.test(location.search), null, { timeout: 3000 }); await settle(off);   // the waiting designation is kept
  const of = await ev(off, () => ({ status: document.getElementById('helper-status').textContent, st: document.querySelector('#neocp-requested .rq .st').textContent.replace(/ /g, ' '),
    note: document.getElementById('neocp-note').textContent, retry: !!document.querySelector('#neocp-requested .rq .re'), target: window.__cle.world.byId.has('req-C46FX01'), url: location.search,
    flag: [...document.querySelectorAll('#neocp-short thead th')].map(e => e.textContent).includes('flag') }));
  assert.ok(of.status === 'helper offline' && of.st === 'helper not running: run make serve' && /run make serve, then Fetch again/.test(of.note) && of.retry && !of.target && !of.flag, JSON.stringify(of));
  assert.ok(/neocp=C46FX01/.test(of.url) && off.net.refused.length >= 1 && off.net.refused.every(u => u === `${HELPER}/api/health`), JSON.stringify({ url: of.url, refused: off.net.refused }));
  await shot(off, 'fixture-neocp-offline');
  off.net.helper = 'online'; await off.clock.setSystemTime(Date.parse(WALL) + 60e3);   // past the 3 s re-probe guard (shot() re-pinned WALL)
  await off.locator('#neocp-requested .rq .re').click();
  await off.waitForFunction(() => window.__cle.world.byId.get('req-C46FX01')?.track && document.querySelector('#neocp-requested .rq.drawn'), null, { timeout: 15000 });
  assert.equal(await ev(off, () => document.getElementById('helper-status').textContent), 'helper ready');
  ok('NEOCP helper offline', `"${of.status}", row "${of.st}", ${off.net.refused.length} refused probe(s) and no page error; ↻ draws it once the helper answers`);
  await off.context().close();

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
  ok('phone V13 details', `view em, label cap 16, frame note hidden, legend collapsed, banner ${ph13.bannerH.toFixed(0)} px (≤ one line)`);
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

  // Phone sheets (design §1): the tab bar opens Targets (full sheet), NEOCP (the same sheet, field focused) and the
  // Inspector (half sheet, ⤢ full); a pick from a sheet closes it and the peek row shows the selection (ⓘ opens the
  // Inspector on TARGET, × deselects); Esc, × or the active tab closes a sheet.
  const sheet = () => ev(page, () => { const d = id => getComputedStyle(document.getElementById(id)).display, r = id => document.getElementById(id).getBoundingClientRect();
    return { left: d('controls-panel'), right: d('state-panel'), full: document.getElementById('state-panel').classList.contains('full'), peek: !document.getElementById('peek').hidden,
      peekName: document.querySelector('#peek .nm').textContent, hasSel: document.getElementById('ptab-inspector').classList.contains('has-sel'), sel: window.__cle.state.selected, tab: window.__cle.tab,
      exp: ['targets', 'neocp', 'inspector'].map(k => document.getElementById(`ptab-${k}`).getAttribute('aria-expanded')), focus: document.activeElement?.id || '',
      top: r('state-panel').top, ws: document.querySelector('.workspace').getBoundingClientRect().top, wh: document.querySelector('.workspace').getBoundingClientRect().height, tabs: r('ptabs').height, count: document.getElementById('ptab-count').textContent, sw: document.documentElement.scrollWidth }; });
  const s0 = await sheet();
  assert.ok(s0.left === 'none' && s0.right === 'none' && !s0.peek && s0.tabs === 48 && s0.count === '8' && s0.exp.join() === 'false,false,false', JSON.stringify(s0));
  await page.locator('#ptab-targets').click(); await settle(page);
  assert.deepEqual(await smallText(page), [], 'text below 10 px on the phone (Targets sheet)');
  const s1 = await sheet();
  assert.ok(s1.left === 'flex' && s1.exp[0] === 'true' && s1.sw <= 390, JSON.stringify(s1));
  await listRead(page);
  await shot(page, 'fixture-phone-targets');
  await page.locator('.row-target[data-id="fx-dro"] .name').click(); await settle(page);
  const s2 = await sheet();
  assert.ok(s2.left === 'none' && s2.sel === 'fx-dro' && s2.peek && /DRO/.test(s2.peekName) && s2.hasSel, `a pick closes the sheet, the peek shows it: ${JSON.stringify(s2)}`);
  await page.locator('#peek [data-peek="info"]').click(); await settle(page);
  const s3 = await sheet();
  assert.ok(s3.right === 'flex' && s3.tab === 'target' && s3.exp[2] === 'true' && Math.abs(s3.top - (s3.ws + .45 * s3.wh)) < 2, `ⓘ: Inspector half sheet on TARGET ${JSON.stringify(s3)}`);
  await shot(page, 'fixture-phone-inspector');
  await page.locator('#expand-info').click(); await settle(page);
  const s4 = await sheet();
  assert.ok(s4.full && Math.abs(s4.top - s4.ws) < 1, `⤢ full height: ${JSON.stringify(s4)}`);
  await page.locator('#ptab-inspector').click(); await settle(page);                 // the active tab closes its sheet
  assert.equal((await sheet()).right, 'none');
  await page.locator('#ptab-neocp').click(); await settle(page);
  const s5 = await sheet();
  assert.ok(s5.left === 'flex' && s5.exp[1] === 'true' && s5.focus === 'neocp-input', `NEOCP tab: the sheet with the field focused ${JSON.stringify(s5)}`);
  await page.keyboard.press('Escape'); await page.keyboard.press('Escape'); await settle(page);   // the field, then the sheet
  const s6 = await sheet();
  assert.ok(s6.left === 'none' && s6.sel === 'fx-dro', JSON.stringify(s6));
  await page.locator('#peek [data-peek="close"]').click(); await settle(page);
  const s7 = await sheet();
  assert.ok(!s7.peek && s7.sel === null && !s7.hasSel, JSON.stringify(s7));
  ok('phone sheets', 'Targets sheet → pick closes it, peek shows the selection; ⓘ → Inspector half sheet (⤢ full), active tab closes; NEOCP tab focuses the field; Esc closes; × deselects');
  await page.context().close();

  // Mid width (701–1100 px, 1024×768): Targets is a drawer over the scene (closed by default; ◧ opens it; Esc or a click
  // on the scene closes it; the canvas never resizes), the Inspector stays docked at 272 px, views from the select.
  page = await open(file, `?t=${NOW}`, { width: 1024, height: 768 });
  const midUI = () => ev(page, () => { const cp = document.getElementById('controls-panel'), cs = getComputedStyle(cp), sp = document.getElementById('state-panel');
    return { left: cs.display, pos: cs.position, z: cs.zIndex, w: cp.getBoundingClientRect().width, right: sp.getBoundingClientRect().width, rightOn: getComputedStyle(sp).display !== 'none',
      canvas: document.querySelector('#scene canvas').getBoundingClientRect().width, sw: document.documentElement.scrollWidth, pressed: document.getElementById('toggle-controls').getAttribute('aria-pressed'),
      views: getComputedStyle(document.getElementById('views')).display, select: getComputedStyle(document.getElementById('view-select')).display, desc: getComputedStyle(document.getElementById('view-description')).display }; });
  const m0 = await midUI();
  assert.ok(m0.left === 'none' && m0.rightOn && m0.right === 272 && m0.sw <= 1024 && m0.pressed === 'false' && m0.views === 'none' && m0.select !== 'none' && m0.desc === 'none', JSON.stringify(m0));
  await page.locator('#toggle-controls').click(); await settle(page);
  const m1 = await midUI();
  assert.ok(m1.left === 'flex' && m1.pos === 'absolute' && m1.w === 304 && +m1.z >= 20 && m1.canvas === m0.canvas && m1.pressed === 'true', `drawer over the scene: ${JSON.stringify({ m0, m1 })}`);
  await listRead(page);                                                              // the drawer shows the dock's MPC rows
  await shot(page, 'fixture-mid-drawer');
  assert.deepEqual(await smallText(page), [], 'text below 10 px at 1024×768');
  await page.keyboard.press('Escape'); await settle(page);
  assert.equal((await midUI()).left, 'none', 'Esc closes the drawer');
  await page.locator('#toggle-controls').click(); await settle(page);
  const cb = await page.locator('#scene canvas').boundingBox();
  await page.mouse.click(cb.x + cb.width - 60, cb.y + 60); await settle(page);
  assert.equal((await midUI()).left, 'none', 'a click on the scene closes the drawer');
  await page.selectOption('#view-select', 'moon'); await settle(page);
  assert.deepEqual(await ev(page, () => [window.__cle.state.view, window.__cle.state.focus]), ['moon', 'moon']);
  ok('mid 1024×768', `drawer ${m1.w} px over a ${m0.canvas.toFixed(0)} px canvas (unchanged), Inspector ${m0.right} px; Esc / scene click close it; view select`);
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
  // The title pill is the LIVE state and toggle (L); ● Now is pressed and the rate reads "real time" while LIVE.
  const pill = () => ev(page, () => ({ badge: document.getElementById('live-badge').textContent, pressed: document.getElementById('live').getAttribute('aria-pressed'), cls: document.getElementById('live').className,
    disabled: document.getElementById('live').getAttribute('aria-disabled'), now: document.getElementById('now').getAttribute('aria-pressed'), speed: [document.getElementById('speed').disabled, document.getElementById('speed').value],
    delta: document.getElementById('elapsed').textContent, live: window.__cle.clock.live }));
  const p0 = await pill();
  assert.deepEqual(p0, { badge: 'LIVE', pressed: 'true', cls: 'pill live', disabled: 'false', now: 'true', speed: [true, 'live'], delta: 'Δ now 0 s', live: true });
  await parkMouse(page); await page.keyboard.press('l'); await settle(page);
  const p1 = await pill();
  assert.ok(p1.badge === '❚❚ PAUSED' && p1.pressed === 'false' && p1.now === 'false' && !p1.speed[0] && p1.speed[1] === '3600' && !p1.live, JSON.stringify(p1));
  await page.locator('#play').click(); await settle(page);
  const p2 = await pill();
  assert.ok(p2.badge === '▶ 1 h/s' && /play/.test(p2.cls), JSON.stringify(p2));
  await page.locator('#now').click(); await settle(page);
  assert.equal((await pill()).badge, 'LIVE', '● Now goes LIVE inside the window');
  ok('LIVE default', `LIVE at ${new Date(live.t).toISOString()} (wall clock pinned to ${WALL}); pill LIVE → "${p1.badge}" (L) → "${p2.badge}" (Play) → LIVE (● Now)`);
  const ms = await ev(page, () => window.__cle.probe(60));
  ok('frame-time probe', `median ${ms.median.toFixed(2)} ms, p90 ${ms.p90.toFixed(2)} ms over 60 frames`);
  await page.context().close();
}

// Review fixes on the real data: chips keep default_visible, hidden targets hide their tracklet lines, master caveats,
// source badges, Lagrange glyph, and §9.2 focus precision (float32 GPU path emulated).
async function fixes(page) {
  const r = await ev(page, () => {
    const c = window.__cle, w = c.world, vis = () => w.targets.filter(c.isVisible).map(T => T.id).sort().join();
    const before = vis(); c.setChip('t1', false); const off = vis(); c.setChip('t1', true);
    const tl = c.tracklets.layers.map(L => [L.id, L.g.visible, c.isVisible(w.byId.get(L.id))]);
    const row = id => document.querySelector(`.row-target[data-id="${id}"]`);
    return { same: before === vis(), off, tl, ibexTitle: row('ibex')?.title || '', droBadge: row('dro-a')?.querySelector('.src')?.textContent,
      t3: document.querySelector('[data-chip=t3]').hidden, lshape: c.lagrange.layer.style[1],
      stale: w.targets.filter(T => T.draw && T.cat.staleness?.flag && row(T.id)).map(T => [T.id, row(T.id).querySelector('.warn')?.textContent]) };
  });
  assert.ok(r.same && r.off === '', `t1 chip off/on restores the default set (off: ${r.off})`);
  assert.ok(r.tl.every(([, g, v]) => !g || v) && r.tl.some(([, g]) => !g), `a hidden target hides its tracklet group ${JSON.stringify(r.tl)}`);
  assert.match(r.ibexTitle, /manoeuvre/); assert.equal(r.droBadge, 'S'); assert.equal(r.t3, true); assert.equal(r.lshape, 5);
  assert.ok(r.stale.length && r.stale.every(([, wn]) => wn === '⚠'), `catalog-stale rows carry ⚠: ${JSON.stringify(r.stale)}`);
  ok('review fixes', `chip t1 off/on keeps defaults; ${r.tl.filter(x => !x[1]).length}/${r.tl.length} tracklet groups hidden (target hidden, or no tracklet inside its drawn trail window); `
    + `IBEX caveat kept; DRO-A badge S; ${r.stale.length} catalog-stale rows marked ⚠`);

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

// Catalog tracklets in the real build's chunk payloads (dist/data/targets-*.js): {id: [tracklets, stations]}. Which targets
// have astrometry inside the window depends on the window make daily chose, so the dist checks compare with the data.
function chunkTracklets() {
  const dir = path.join(root, 'dist/data'), out = {};
  for (const f of existsSync(dir) ? readdirSync(dir).filter(n => /^targets-.*\.js$/.test(n)) : []) {
    const js = readFileSync(path.join(dir, f), 'utf8'), p = JSON.parse(js.slice(js.indexOf(',') + 1, js.lastIndexOf(')')));
    for (const [id, t] of Object.entries(p.targets)) if (t.tracklets?.length) out[id] = [t.tracklets.length, new Set(t.tracklets.map(k => k.stn)).size, t.tracklets.reduce((m, k) => m + (k.n ?? k.t_ms.length), 0)];
  }
  return out;
}

// Real data (dist/index.html): the build's own epoch; one screenshot per view of the integration checklist.
async function dist() {
  const file = path.join(root, 'dist/index.html');
  if (!existsSync(file)) { console.log('skip dist: dist/index.html missing'); return; }
  const NOW = buildNow(), wall = new Date(Date.parse(NOW) + 24 * 3600e3).toISOString();   // shadows the fixture's epoch below
  const t0 = Date.now(), page = await open(file, `?t=${NOW}`, undefined, undefined, { wall }), loadMs = Date.now() - t0;
  const timing = await ev(page, () => { const n = performance.getEntriesByType?.('navigation')?.[0]; return { dcl: n?.domContentLoadedEventEnd ?? NaN, load: n?.loadEventEnd ?? NaN }; });   // page.clock hides the entries
  const info = await ev(page, () => {
    const c = window.__cle, w = c.world, by = {};
    for (const T of w.targets) by[T.cat.tier] = (by[T.cat.tier] || 0) + 1;
    return { n: w.targets.length, tracks: w.targets.filter(T => T.track).length, visible: w.targets.filter(c.isVisible).length, has: w.targets.filter(T => T.has && c.isVisible(T)).length,
      missing: c.missing, frame: c.state.frame, labels: c.labelsShown().length, tiers: by, nodraw: document.querySelectorAll('.row-target.nodraw').length,
      notrack: document.querySelectorAll('.row-target.notrack').length, hidden2: w.targets.filter(T => T.tier === 2 && c.isVisible(T)).length,
      tracklets: c.tracklets.items.length, dots: c.tracklets.dotCount, window: [c.world.t0, c.world.t1].map(t => new Date(t).toISOString()),
      excluded: w.targets.filter(T => /^NEOCP (live|archive)|^Natural/.test(T.group) || T.cat.obj_type === 'natural' || T.source !== 'catalog').map(T => T.id),
      groups: [...document.querySelectorAll('#target-list .group-head')].map(h => h.dataset.group) };
  });
  assert.equal(info.frame, 'se'); assert.deepEqual(info.missing, []); assert.ok(info.tracks > 0 && info.has > 0, JSON.stringify(info));
  assert.deepEqual(info.excluded, [], 'no minimoons, NEOCP live or NEOCP archive rows in the viewer');
  assert.ok(info.groups.length >= 5 && !info.groups.some(g => /NEOCP|Natural/.test(g)), `list groups ${info.groups}`);
  assert.equal(info.hidden2, 0, 'tier 2 hidden by default');
  ok('dist loaded', `${JSON.stringify(info)} · page ready ${loadMs} ms (DOMContentLoaded ${timing.dcl.toFixed(0)} ms, load ${timing.load.toFixed(0)} ms)`);
  await listRead(page); await page.waitForTimeout(300);
  assert.deepEqual(page.net.log, ['mpc'], `dist requests at load: ${page.net.log}`);

  // Sticky group heads: the list rests on whole rows, so no row is left half under a stuck head (the previous group's last
  // row peeking out beneath it), wherever a wheel, a scrollTop or a selection leaves the list, its end included.
  const lb = await page.locator('#target-list').boundingBox();
  const slivers = () => ev(page, () => {
    const L = document.getElementById('target-list'), lr = L.getBoundingClientRect(), out = [];
    for (const h of L.querySelectorAll('.group-head')) {
      const hr = h.getBoundingClientRect();
      if (hr.bottom <= lr.top || hr.top >= lr.bottom) continue;
      for (const r of L.querySelectorAll('.row-target, .more')) {
        const rr = r.getBoundingClientRect();
        if (rr.top < hr.bottom - .5 && rr.bottom > hr.bottom + .5 && rr.bottom > lr.top) out.push(`${r.dataset.id || r.textContent} ${rr.top.toFixed(1)}–${rr.bottom.toFixed(1)} under ${h.dataset.group} ${hr.top.toFixed(1)}–${hr.bottom.toFixed(1)}`);
      }
    }
    return { st: L.scrollTop, max: L.scrollHeight - L.clientHeight, out };
  });
  await page.mouse.move(lb.x + 120, lb.y + 200);
  const rests = [];
  for (const dy of [37, 53, 100, 211, 95, 400, 17, 5000, -333, -71]) { await page.mouse.wheel(0, dy); await page.waitForTimeout(450); rests.push(await slivers()); }
  for (const st of [262, 500, 731]) { await ev(page, x => { document.getElementById('target-list').scrollTop = x; }, st); await page.waitForTimeout(300); rests.push(await slivers()); }
  await ev(page, () => window.__cle.select('tiandu-1')); await page.waitForTimeout(400); rests.push(await slivers());
  await ev(page, () => window.__cle.select(null)); await settle(page);
  // Situation's regime rows jump to their group (scrollToRegime), which lands on the head's snap position
  const jumps = [];
  for (const key of ['SE_L2', 'EARTH_NEAR_HELIO', 'LUNAR_ORBIT']) {
    await page.locator(`#situation [data-regime="${key}"] button`).click(); await page.waitForTimeout(450);
    jumps.push(await ev(page, k => { const L = document.getElementById('target-list'), top = L.getBoundingClientRect().top, max = L.scrollHeight - L.clientHeight;
      const h = [...L.querySelectorAll('.group-head')].find(h => [...h.parentElement.querySelectorAll('.row-target')].some(r => window.__cle.world.byId.get(r.dataset.id)?.regime.key === k));
      return { key: k, group: h?.dataset.group, dy: h ? h.parentElement.getBoundingClientRect().top - top : NaN, st: L.scrollTop, max }; }, key));
    rests.push(await slivers());
  }
  for (const j of jumps) assert.ok(j.group && (Math.abs(j.dy) <= 4.5 || j.st === j.max && j.dy > 0), `Situation ${j.key} → its group at the top of the list: ${JSON.stringify(j)}`);
  await ev(page, () => { document.getElementById('target-list').scrollTop = 0; }); await parkMouse(page); await settle(page);
  assert.ok(rests.some(x => x.st === x.max) && rests.some(x => x.st > 0 && x.st < x.max), `rest offsets ${rests.map(x => x.st)}`);
  assert.deepEqual(rests.flatMap(x => x.out), [], 'rows half under a sticky group head');
  ok('sticky group heads', `${rests.length} rest positions (${rests.map(x => x.st).join(', ')} of ${rests[0].max} px): no row under a stuck head; Situation rows jump to ${jumps.map(j => j.group).join(', ')}`);
  // Opening view (V1 + V5 + C1 on the real catalog), then the L1–L2 preset
  let v = await view(page);
  if (feature('V1 opening view (dist)', v.earthPx > 4, `Earth ${v.earthPx.toFixed(2)} px`)) {
    const mo = await moonOrbitWidth(page);                                  // the real build's epoch: any lunar phase
    assert.ok(v.chevrons.includes('SE-L1') && v.chevrons.includes('SE-L2') && mo.frac > .45 && v.em.every(a => a === 0), JSON.stringify({ chevrons: v.chevrons, moonPx: v.moonPx, orbit: mo, em: v.em }));
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
    ok('dist opening view', `Earth ${v.earthPx.toFixed(1)} px, Moon ${v.moonPx.toFixed(0)} px out, lunar orbit ${mo.px.toFixed(0)} px wide (${(mo.frac * 100).toFixed(0)} % of the scene; trail ${ms.span.toFixed(0)}°), ${v.labels.length} labels, EM glyphs hidden, chevrons "${ct['SE-L1']}", "${ct['SE-L2']}"; beads ${bd.shown}/${bd.total} in their trail windows, ${Object.keys(bd.out).length} targets with beads on screen; hint "${hint.text}"`);
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
  // Vela 5A, or another visible cislunar HEO target, with no other marker within 16 px (the pick radius is 12 px), so the
  // pointer can only mean it at whatever epoch the real build has
  const heo = await ev(page, () => {
    const c = window.__cle, W = c.world, shown = W.targets.filter(T => c.isVisible(T) && T.screen);
    const clear = T => shown.every(U => U === T || Math.hypot(U.screen.x - T.screen.x, U.screen.y - T.screen.y) > 16);
    const pool = [W.byId.get('vela-5a'), ...shown.filter(T => T.cat.regime === 'CISLUNAR_HEO')];
    return pool.find(T => T && c.isVisible(T) && T.screen && clear(T))?.id || null;
  });
  assert.ok(heo, 'a cislunar HEO target on screen, clear of other markers');
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

  // Catalog tracklets, now in the chunk payloads (no neocp.js): the identified targets with astrometry in this window, as the
  // data has them; the one with the most, selected, shows every bead, with a station chip per station.
  const neo = await ev(page, () => { const c = window.__cle, ids = c.tracklets.items.reduce((m, x) => m.set(x.id, (m.get(x.id) || 0) + x.n), new Map());
    const id = [...ids.entries()].sort((a, b) => b[1] - a[1]).find(([i]) => c.world.byId.get(i)?.track)?.[0];
    c.setFrame('eci'); c.select(id); c.focusOn(id);
    return { id, dots: ids.get(id), items: c.tracklets.items.filter(x => x.id === id).length, with: [...ids.keys()].sort(), sources: [...new Set(c.tracklets.items.map(x => c.world.byId.get(x.id)?.source))] }; });
  await settle(page); await page.waitForTimeout(200);
  const data = chunkTracklets(), busiest = Object.entries(data).sort((a, b) => b[1][2] - a[1][2])[0];     // most beads, as the page picks
  assert.ok(busiest, 'the real build has catalog tracklets in its chunk payloads');
  assert.deepEqual(neo.with, Object.keys(data).sort(), 'catalog targets with tracklets');
  assert.ok(neo.id === busiest[0] && neo.items === busiest[1][0] && neo.dots > 0 && neo.sources.join() === 'catalog', JSON.stringify({ neo, busiest }));
  const neoShown = await ev(page, id => { const t = window.__cle.tracklets, L = t.layers.find(x => x.id === id), tk = t.by.get(id).tk; return L.g.visible ? tk.slice(...L.shown).reduce((m, x) => m + x.d1 - x.d0, 0) : 0; }, neo.id);
  assert.equal(neoShown, neo.dots, 'a selected target shows every bead (its trail is the whole window)');
  const fhChips = await ev(page, () => document.querySelectorAll('#readout .stations .stn[data-station]').length);
  assert.equal(fhChips, busiest[1][1], `${fhChips} station chips for ${busiest[1][1]} stations`);
  ok('catalog tracklets', `${neo.with.join(', ')}; ${neo.id}: ${neo.items} tracklets, ${neo.dots} beads all shown when selected, ${fhChips} station chips`);
  await shot(page, 'dist-tracklets');

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

  await ev(page, () => { const c = window.__cle; c.select(null); c.setFrame('se'); c.focusOn('earth'); c.setChip('t2', true); c.preset('wide'); });
  const t2 = await ev(page, () => window.__cle.world.targets.filter(T => T.tier === 2 && window.__cle.isVisible(T)).length);
  assert.ok(t2 > 0);
  ok('tier 2 on', `${t2} tier-2 targets visible`);
  await shot(page, 'dist-tier2');
  await shot(page, 'dist-legend', '#legend');
  const ms = await ev(page, () => window.__cle.probe(60));
  ok('dist frame-time probe', `median ${ms.median.toFixed(2)} ms, p90 ${ms.p90.toFixed(2)} ms`);
  await page.context().close();

  // Phone
  const phone = await open(file, `?t=${NOW}`, { width: 390, height: 844 }, undefined, { wall });
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
