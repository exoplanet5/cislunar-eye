#!/usr/bin/env python3
"""Local helper for the viewer (`make serve`): dist/ and the on-request NEOCP API on http://127.0.0.1:8765/.

    ~/.venvs/astro313/bin/python -I scripts/serve.py [--port 8765] [--dist dist] [--no-fo] [--allow-origin URL ...]

Binds 127.0.0.1 only (stdlib ThreadingHTTPServer). Static: / -> dist/index.html, /data/*.js and the other files under
dist/ with their content types; no directory listing, no dot files, nothing outside dist/. API (JSON):

  GET /api/health            {"ok": true, "service": "cislunar-eye", "api": 1, "fo": <find_orb binary present>, "now_utc"}
  GET /api/neocp/summary     Bill Gray's neocp2 summary flags (cle.request.summary), cached 10 min; 502 upstream
  GET /api/neocp/<DESIG>?t0=<ms>&t1=<ms>&now=<ms>
                             200 {"ok": true, "schema": "cle/neocp-request@1", "desig", "built_utc",
                                  "window": {"start_utc", "stop_utc"}, "object": {cid, catalog, track, fit, caveat,
                                  tracklets, rms_arcsec, request}}
                             or {"ok": false, "error": <code>, "message": <one line for the UI>} with the status of
                             cle.request.CODES (bad_designation / bad_request 400, not_found 404, no_orbit and
                             outside_window 422, upstream 502, busy 503, internal 500). t0 < t1 required, at most 45 d
                             apart; now defaults to the wall clock and is clamped into the window.

Every response carries Access-Control-Allow-Origin: * and Cache-Control: no-store; OPTIONS preflights answer 204 with
the allowed methods / headers and Access-Control-Allow-Private-Network: true (a file:// or public page may call the
loopback helper). A Host header other than 127.0.0.1:<port> / localhost:<port> gets 421 (DNS-rebinding guard). /api/
requests (and their preflights) from a browser page must come from an allowed origin, else 403 bad_origin: the
helper's own pages (same origin, no Origin header), file:// ("null"), any http(s) loopback server, the GitHub Pages
site (PAGES_ORIGIN) and --allow-origin ones; a cross-site request without an Origin header (an <img> or <script> tag,
a no-cors fetch, a link) is refused by its Sec-Fetch-Site. Without these checks any web site open in the browser
could make the helper fetch from projectpluto.com and MPC. (A sandboxed iframe also sends "null"; accepted for
file://.) Answers are cached 10 min under (designation, t0, t1, the page's Created stamp, sha256 of the observations);
a hit is sent with object.request cached: true, its own seconds and served_utc (fetched_utc stays the upstream read);
the upstream fetch and that lookup run under a per-designation lock, so a cached answer never waits for another
build. Builds run one at a time (a request waits up to 30 s for the build slot, then 503 busy). The DE440s Context is
loaded once at start. A client that disconnects mid-build (the page's AbortController) costs nothing: the result is
still cached. One log line per request on stdout, control characters escaped (the stdlib's own 400 / 414 / 501
answers included). The port is refused when another program already accepts on 127.0.0.1:<port> (macOS would let
SO_REUSEADDR bind it beside a *:<port> server).
"""
from __future__ import annotations

import argparse
import errno
import json
import math
import re
import signal
import socket
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cle import pluto, request  # noqa: E402
from cle.adapters import fo  # noqa: E402
from cle.timeutil import iso  # noqa: E402

PORT = 8765
RESULT_TTL_S = 600
BUSY_WAIT_S = 30.0
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".json": "application/json; charset=utf-8", ".map": "application/json; charset=utf-8",
         ".txt": "text/plain; charset=utf-8", ".md": "text/plain; charset=utf-8", ".svg": "image/svg+xml",
         ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif",
         ".ico": "image/x-icon", ".woff2": "font/woff2", ".woff": "font/woff", ".wasm": "application/wasm"}
JSON = "application/json; charset=utf-8"
GONE = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)
PAGES_ORIGIN = "https://exoplanet5.github.io"                     # make publish (README)
LOOPBACK = re.compile(r"^https?://(127\.0\.0\.1|localhost|\[::1\])(:\d{1,5})?$")
CTRL = {c: f"\\x{c:02x}" for c in (*range(0x20), *range(0x7F, 0xA0))}   # as BaseHTTPRequestHandler.log_message


def printable(text: str) -> str:
    """A log line with control characters escaped (a request line may carry terminal escape sequences)."""
    return text.translate(CTRL)


def port_taken(port: int) -> bool:
    """True when something already accepts connections on 127.0.0.1:<port>."""
    if not port:
        return False
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def clean(x):
    """JSON-safe copy: numpy scalars to Python, non-finite floats to null."""
    if isinstance(x, dict):
        return {k: clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    if hasattr(x, "item") and not isinstance(x, (str, bytes)):
        x = x.item()
    if isinstance(x, float) and not math.isfinite(x):
        return None
    return x


def dumps(doc) -> bytes:
    return json.dumps(clean(doc), ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port, ctx, *, dist=ROOT / "dist", cache_root=request.CACHE, runs=request.RUNS,
                 fetcher=request.fetch, summary=request.summary, use_fo=True, lock_timeout=request.LOCK_TIMEOUT_S,
                 busy_wait_s=BUSY_WAIT_S, log=None, origins=()):
        if port_taken(port):              # SO_REUSEADDR (kept for quick restarts) would not catch a *:<port> server
            raise OSError(errno.EADDRINUSE, "address already in use by another program")
        super().__init__(("127.0.0.1", port), Handler)
        self.origins = {"null", PAGES_ORIGIN, *(o.strip().rstrip("/").lower() for o in origins)}
        self.ctx, self.dist, self.cache_root, self.runs = ctx, Path(dist).resolve(), Path(cache_root), Path(runs)
        self.fetcher, self.summary, self.use_fo, self.lock_timeout = fetcher, summary, use_fo, lock_timeout
        self.busy_wait_s = busy_wait_s
        self.log = log or (lambda line: print(line, flush=True))
        self.build_lock, self.summary_lock, self.results_lock = threading.Lock(), threading.Lock(), threading.Lock()
        self.results = {}                         # cache key -> (time, response document)
        self.desig_locks = {}                     # desig.lower() -> its fetch / result-cache lock

    def desig_lock(self, desig) -> threading.Lock:
        """One lock per designation, case-folded (cache_dir_for's case-safe names): one fetch at a time per object."""
        with self.results_lock:
            return self.desig_locks.setdefault(desig.lower(), threading.Lock())

    @property
    def port(self):
        return self.server_address[1]

    def cached(self, key):
        with self.results_lock:
            hit = self.results.get(key)
            return hit[1] if hit and time.time() - hit[0] < RESULT_TTL_S else None

    def remember(self, key, doc):
        now = time.time()
        with self.results_lock:
            self.results = {k: v for k, v in self.results.items() if now - v[0] < RESULT_TTL_S}
            self.results[key] = (now, doc)


def served(doc, started, cached) -> bytes:
    """The answer as sent: object.request says when this answer was served (served_utc), how long it took (seconds) and
    whether it came from the 10-min result cache (cached; fetched_utc stays when MPC and neocp2 were read)."""
    now = time.time()
    req = doc["object"]["request"] | {"served_utc": iso(now)}
    if cached:
        req |= {"cached": True, "seconds": round(now - started, 2)}
    return dumps(doc | {"object": doc["object"] | {"request": req}})


class Handler(BaseHTTPRequestHandler):
    server_version = "cislunar-eye-helper/1"

    def version_string(self):
        return self.server_version

    def log_message(self, format, *args):        # one line per request instead (done())
        pass

    def log_error(self, format, *args):          # the stdlib's own answers (send_error: 400, 414, 501): a line too
        line = getattr(self, "requestline", "") or "-"
        line = line if len(line) <= 160 else line[:157] + "..."
        self.server.log(printable(f"{iso(time.time())} {line} {format % args}"))

    # ---------------------------------------------------------------- plumbing

    def host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").strip().lower()
        if host in (f"127.0.0.1:{self.server.port}", f"localhost:{self.server.port}"):
            return True
        self.note += f" bad_host: {host[:80]!r}"
        return False

    def origin_ok(self) -> bool:
        """An /api/ request from an allowed page (module docstring); tools without these headers (curl) pass."""
        origin = self.headers.get("Origin")
        if origin is None:
            site = (self.headers.get("Sec-Fetch-Site") or "same-origin").strip().lower()
            if site in ("same-origin", "none"):
                return True
            self.note += f" bad_origin: Sec-Fetch-Site {site[:40]!r}"
            return False
        origin = origin.strip().rstrip("/").lower()
        if origin in self.server.origins or LOOPBACK.match(origin):
            return True
        self.note += f" bad_origin: {origin[:80]!r}"
        return False

    def refuse_origin(self):
        self.send(403, dumps({"ok": False, "error": "bad_origin", "message": "page origin not allowed by the helper"}))

    def send(self, status, body=b"", ctype=JSON, extra=()):
        self.status = status
        try:
            self.send_response(status)
            if status != 204:
                self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (("Access-Control-Allow-Origin", "*"), ("Cache-Control", "no-store"),
                         ("X-Content-Type-Options", "nosniff"), *extra):
                self.send_header(k, v)
            self.end_headers()
            if body and self.command != "HEAD":
                self.wfile.write(body)
        except GONE:
            self.note += " (client gone)"

    def fail(self, err: request.RequestError):
        self.send(err.status, dumps(err.doc()))

    def done(self, t0):
        path = self.path if len(self.path) <= 160 else self.path[:157] + "..."
        self.server.log(printable(f"{iso(time.time())} {self.command} {path} {getattr(self, 'status', '-')} "
                                  f"{time.time() - t0:.2f}s{self.note}"))

    # ---------------------------------------------------------------- methods

    def do_OPTIONS(self):
        t0, self.note = time.time(), ""
        if not self.host_ok():
            self.send(421, dumps({"ok": False, "error": "bad_host", "message": "use http://127.0.0.1 or localhost"}))
        elif urlsplit(self.path).path.startswith("/api/") and not self.origin_ok():
            self.refuse_origin()
        else:
            self.send(204, extra=(("Access-Control-Allow-Methods", "GET, OPTIONS"),
                                  ("Access-Control-Allow-Headers", "content-type"),
                                  ("Access-Control-Allow-Private-Network", "true"), ("Access-Control-Max-Age", "600")))
        self.done(t0)

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        t0, self.note = time.time(), ""
        self.send(405, dumps({"ok": False, "error": "bad_request", "message": "GET only"}),
                  extra=(("Allow", "GET, OPTIONS"),))
        self.done(t0)

    do_PUT = do_DELETE = do_PATCH = do_POST

    def do_GET(self):
        t0, self.note = time.time(), ""
        try:
            if not self.host_ok():
                self.send(421, dumps({"ok": False, "error": "bad_host", "message": "use http://127.0.0.1 or localhost"}))
                return
            url = urlsplit(self.path)
            q = parse_qs(url.query)
            arg = lambda k: (q.get(k) or [None])[0]
            if url.path.startswith("/api/") and not self.origin_ok():
                self.refuse_origin()
            elif url.path == "/api/health":
                self.send(200, dumps({"ok": True, "service": "cislunar-eye", "api": 1,
                                      "fo": fo.FO.exists() and self.server.use_fo, "now_utc": iso(time.time())}))
            elif url.path == "/api/neocp/summary":
                self.send(200, self.neocp_summary())
            elif url.path.startswith("/api/neocp/"):
                self.send(200, self.neocp(unquote(url.path[len("/api/neocp/"):]), arg("t0"), arg("t1"), arg("now"), t0))
            elif url.path.startswith("/api/"):
                self.send(404, dumps({"ok": False, "error": "not_found", "message": "no such helper endpoint"}))
            else:
                self.static(url.path)
        except request.RequestError as err:
            self.note += f" {err.code}: {err.message}"
            self.fail(err)
        except Exception as err:                                    # noqa: BLE001 - answer, log, keep serving
            traceback.print_exc(file=sys.stderr)
            self.note += f" internal: {type(err).__name__}: {err}"
            self.fail(request.RequestError("internal", f"helper error: {type(err).__name__}"[:60]))
        finally:
            self.done(t0)

    # ---------------------------------------------------------------- routes

    def neocp_summary(self) -> bytes:
        with self.server.summary_lock:
            return dumps(self.server.summary(self.server.cache_root))

    def neocp(self, desig, t0_ms, t1_ms, now_ms, started) -> bytes:
        srv = self.server
        if not pluto.valid_desig(desig):
            raise request.RequestError("bad_designation")
        win = request.window_from(t0_ms, t1_ms, now_ms)
        lock = srv.desig_lock(desig)
        if not lock.acquire(timeout=srv.busy_wait_s):
            raise request.RequestError("busy")
        try:                                            # fetch + result cache outside the build slot: a hit never waits
            fetched = srv.fetcher(desig, request.cache_dir_for(desig, srv.cache_root))
            key = request.cache_key(fetched, win)
            hit = srv.cached(key)
            if hit is not None:
                self.note += f" {desig} result cached"
                return served(hit, started, cached=True)
            if not srv.build_lock.acquire(timeout=srv.busy_wait_s):
                raise request.RequestError("busy")
            try:
                doc = request.respond(fetched, win, srv.ctx, use_fo=srv.use_fo, lock_timeout=srv.lock_timeout,
                                      runs=srv.runs, started=started)
            finally:
                srv.build_lock.release()
            srv.remember(key, doc)                      # before writing: a client that has gone still gets it next time
            o = doc["object"]
            self.note += (f" {desig} {o['request']['mode']} {o['request']['n_obs_used']} obs "
                          f"{len(o['tracklets'])} tracklets{' (fetch cached)' if fetched.cached else ''}")
            return served(doc, started, cached=False)
        finally:
            lock.release()

    def static(self, path):
        rel = unquote(path)
        parts = [p for p in rel.split("/") if p]
        if "\x00" in rel or "\\" in rel or any(p == ".." or p.startswith(".") for p in parts):
            self.send(403, b"forbidden\n", "text/plain; charset=utf-8")
            return
        target = self.server.dist.joinpath(*parts) if parts else self.server.dist / "index.html"
        try:                                            # OSError (a name > 255 bytes, no permission): 404
            if target.is_dir():
                target = target / "index.html"
            target = target.resolve()
            body = target.read_bytes() if target.is_relative_to(self.server.dist) and target.is_file() else None
        except OSError:
            body = None
        if body is None:
            hint = " (make build)" if not parts else ""
            self.send(404, f"not found{hint}\n".encode(), "text/plain; charset=utf-8")
            return
        self.send(200, body, TYPES.get(target.suffix.lower(), "application/octet-stream"))


def stop(*_):
    raise KeyboardInterrupt


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=PORT, help="port on 127.0.0.1 (0: any free one)")
    ap.add_argument("--dist", type=Path, default=ROOT / "dist", help="static root (default dist/)")
    ap.add_argument("--no-fo", action="store_true", help="never run find_orb: page state vector only")
    ap.add_argument("--allow-origin", action="append", default=[], metavar="URL",
                    help=f"another page origin allowed to call /api/ (besides loopback, file:// and {PAGES_ORIGIN})")
    args = ap.parse_args(argv)
    from cle.context import Context
    ctx = Context()
    try:
        server = Server(args.port, ctx, dist=args.dist, use_fo=not args.no_fo, origins=args.allow_origin)
    except OSError as err:
        print(f"serve.py: cannot listen on 127.0.0.1:{args.port}: {err}", file=sys.stderr)
        return 1
    have_fo = fo.FO.exists() and not args.no_fo
    print(f"cislunar-eye helper: open http://127.0.0.1:{server.port}/  (find_orb {'on' if have_fo else 'off'}; "
          f"{'dist/index.html' if (server.dist / 'index.html').exists() else 'dist not built: make build'}; Ctrl-C stops)",
          flush=True)
    signal.signal(signal.SIGTERM, stop)                      # kill / launchd: stop like Ctrl-C
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
