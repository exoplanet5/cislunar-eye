// Classic-script sidecars (ARCHITECTURE §3.2, §11): CLE.register(name, payload) works on file://, where fetch and
// ES modules do not. A boot snippet in the page queues registrations made before the bundle runs.
const store = {}, waiters = {}, handlers = {};

export function installRegister(root = globalThis) {
  const queued = root.CLE?.q || [];
  root.CLE = { register(name, payload) { store[name] = payload; handlers[name]?.(payload); for (const f of waiters[name] || []) f(payload); delete waiters[name]; } };
  for (const [name, payload] of queued) root.CLE.register(name, payload);
  return root.CLE;
}

export const registered = name => store[name];
export const onRegister = (name, f) => { handlers[name] = f; };

export function bytes(text) {
  if (typeof Uint8Array.fromBase64 === 'function') return Uint8Array.fromBase64(text);
  const s = atob(text), b = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) b[i] = s.charCodeAt(i);
  return b;
}

// Little-endian base64 → zero-copy typed view (every supported platform is little-endian): f64, f32, u16, i16.
const TYPED = { f64: Float64Array, f32: Float32Array, u16: Uint16Array, i16: Int16Array };
export function decode(text, dtype) {
  if (text == null) return null;
  const b = bytes(text), T = TYPED[dtype] || Float32Array;
  return new T(b.buffer, b.byteOffset, b.byteLength / T.BYTES_PER_ELEMENT);
}

// §11 Track: segment src indexes track.srcs (a string in the viewer fixture); sigma_km / jump_km only when present.
export function decodeTrack(track) {
  if (!track) return null;
  const srcs = track.srcs || [];
  const segments = track.segments.map(s => {
    const rv = decode(s.rv, track.dtype), src = typeof s.src === 'number' ? srcs[s.src] : s.src;
    if (rv.length !== s.n * 6) throw new Error(`segment ${src}: ${rv.length} values for n=${s.n}`);
    return { t0_ms: s.t0_ms, step_s: s.step_s, n: s.n, t1_ms: s.t0_ms + (s.n - 1) * s.step_s * 1000, rv,
      sigma: decode(s.sigma_km, 'f32'), src, quality: s.quality, join: s.join_before, jump_km: s.jump_km || 0 };
  });
  return { center: track.center, dtype: track.dtype, segments, t0_ms: segments[0]?.t0_ms, t1_ms: segments.at(-1)?.t1_ms };
}

// catalog.js sources: "kind:ref[start..end]" with a leading '*' on the primary one (cle.catalog.compact) →
// {kind, ref, start, end, primary}; an object (fixture, neocp.js) passes through.
const SOURCE = /^(\*?)(\w+):(.*?)(?:\[(.*?)\.\.(.*?)\])?$/;
export function parseSource(s) {
  if (typeof s !== 'string') return s;
  const m = SOURCE.exec(s);
  return m ? { kind: m[2], ref: m[3], start: m[4] || '', end: m[5] || '', primary: m[1] === '*' } : { kind: '', ref: s, start: '', end: '', primary: false };
}

// Inject <script src> and resolve after it ran (true) or failed (false). Re-injection with a cache-buster re-runs it.
export function inject(src, doc = document) {
  return new Promise(resolve => {
    const el = doc.createElement('script');
    el.src = src; el.async = false;
    el.onload = () => { el.remove(); resolve(true); };
    el.onerror = () => { el.remove(); resolve(false); };
    doc.head.appendChild(el);
  });
}
