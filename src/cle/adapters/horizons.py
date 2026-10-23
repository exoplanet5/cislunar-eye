"""Horizons vectors (ARCHITECTURE.md §5.3), adapted from MMX src/ephemeris.py.

Requests are MMX's (geometric ICRF states, UT epochs, km and km/s), centred on the Moon (500@301)
for LUNAR_ORBIT targets. Each target is sampled on the window grid at its regime step (§6.2) together
with the step/2 midpoints in one lattice (even samples = grid, odd samples = the Hermite check), clipped
to the Horizons coverage, refined where h*omega > 0.3; failing intervals are re-requested at h/4 and
either kept (error falls ~h^4) or broken as a splice. Raw responses are cached under
work/cache/horizons_eph/<stamp>/ with a manifest (MMX fetch_snapshot) and reused, so a rebuild with the
same window runs offline.

Cache (REVIEW.md EFF-4): a request is served from any cached responses of the same command/centre whose
lattice contains its epochs (Snapshots.serve). The grid is fetched as fixed TILE_D-day tiles on the 00:00 UTC
lattice at gcd(step/2, 3600), which contains every hour-floored window lattice, so a moved window fetches only
the tiles it lacks. A 2-row OBJ_DATA probe per target (a new one each UTC day, or after PROBE_TTL_S) names the
solution (header hash); cached responses of another solution are not used, so a re-delivered trajectory
refetches that target. Responses without a solution tag (older caches) serve only while the solution is unknown.

Environment: CLE_OFFLINE=1 never touches the network; CLE_REFRESH=1 ignores the cache.

Outages (REVIEW.md DW-04 b/c): after BREAKER consecutive connection failures the run stops requesting
(SNAPSHOTS.down, HorizonsDown). Offline or down, a lattice the cache serves only in part is clipped to its longest
cached run (responses of an older solution stand in when the probed one is not cached); one it cannot serve at all
is Hermite re-gridded from the newest overlapping cached response (pre-tile caches after a window hour change). The
track then carries the caveat "Horizons snapshot of <date>", the provenance names the cached files, and midpoints
the cache cannot serve are counted as unchecked.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np
from scipy.interpolate import CubicHermiteSpline

from ..statetable import Segment, StateTable
from ..timeutil import iso, parse_iso

ROOT = Path(__file__).resolve().parents[3]
API = "https://ssd.jpl.nasa.gov/api/horizons.api"
CACHE = ROOT / "work/cache/horizons_eph"
SPANS = ROOT / "catalog/sources/horizons_all_spacecraft.tsv"
BASE_STEP = {"LUNAR_ORBIT": 300, "EM_L1": 1800, "EM_L2": 1800, "SE_L1": 21600, "SE_L2": 21600,
             "EARTH_NEAR_HELIO": 21600, "OTHER": 21600}          # everything else 3600 s
TOL_KM = {"earth": 1.0, "moon": 0.1}
OMEGA_H = 0.3                 # refine where h * omega > 0.3 rad (§6.2)
MAX_ROWS = 50000              # per request; Horizons caps output near 90k lines
SPAN_MARGIN_S = 120           # TSV spans are TDB-ish; stay clear of the ends
PAUSE_S = 1.0
BREAKER = 2                   # consecutive failed attempts before the run stops requesting (DW-04 b)
TILE_D = 10                   # grid tiles: TILE_D whole days, boundaries on 1970-01-01 + k * TILE_D d (EFF-4)
PROBE_TTL_S = 20 * 3600       # a cached header probe older than this is refreshed when online
MONTHS = {m: i for i, m in enumerate(("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT",
                                      "NOV", "DEC"), 1)}


class CoverageError(ValueError):
    def __init__(self, side: str, t_s: float, text: str):
        super().__init__(text)
        self.side, self.t_s = side, t_s


class HorizonsDown(ConnectionError):
    """The breaker tripped: no further Horizons requests this run."""


def hz_time(t_s: float) -> str:
    return datetime.fromtimestamp(t_s, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def hz_times(params: dict) -> tuple[float, float]:
    return tuple(parse_iso(params[k].strip("'").replace(" ", "T") + "Z") for k in ("START_TIME", "STOP_TIME"))


def step_of(params: dict):
    """Step in seconds of a cached request ('N m' or an interval count), None for other forms."""
    s = params.get("STEP_SIZE", "").strip("'")
    if s.endswith(" m"):
        return float(s[:-2]) * 60
    if s.isdigit():
        a, b = hz_times(params)
        return (b - a) / int(s)
    return None


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def request_params(command, start_s, stop_s, step_s, centre="earth", obj_data=False):
    # Whole minutes as 'N m'; other steps as a unitless interval count (equal spacing).
    step = f"{int(step_s) // 60} m" if step_s % 60 == 0 else str(int(round((stop_s - start_s) / step_s)))
    values = dict(COMMAND=command, EPHEM_TYPE="VECTORS", CENTER="500@301" if centre == "moon" else "500@399",
                  START_TIME=hz_time(start_s), STOP_TIME=hz_time(stop_s), STEP_SIZE=step,
                  REF_SYSTEM="ICRF", REF_PLANE="FRAME", TIME_TYPE="UT", VEC_CORR="NONE",
                  OUT_UNITS="KM-S", VEC_TABLE="2", CSV_FORMAT="YES", CAL_FORMAT="CAL",
                  OBJ_DATA="YES" if obj_data else "NO")
    return {"format": "json", **{k: f"'{v}'" for k, v in values.items()}}


def target_token(command: str) -> str:
    return f"({command.removeprefix('DES=').strip()})"


def coverage_error(text: str):
    m = re.search(r"No ephemeris for target.*?(after|prior to) A\.D\. (\d{4})-([A-Z]{3})-(\d{2}) "
                  r"(\d{2}):(\d{2}):(\d{2}(?:\.\d+)?) UT", text)
    if not m:
        return None
    t = datetime(int(m[2]), MONTHS[m[3]], int(m[4]), int(m[5]), int(m[6]), tzinfo=timezone.utc).timestamp()
    return CoverageError("end" if m[1] == "after" else "start", t + float(m[7]), text.strip())


def parse_response(text, command, start_s, stop_s, step_s, centre="earth"):
    """(t_s, states (n,6), header) with MMX parse_response checks; CoverageError on an SPK edge."""
    response = json.loads(text)
    if response.get("error"):
        raise coverage_error(response["error"]) or ValueError(response["error"].strip())
    result = response.get("result", "")
    if result.count("$$SOE") != 1 or result.count("$$EOE") != 1:
        raise coverage_error(result) or ValueError("Missing or ambiguous state table")
    header = result.split("$$SOE")[0]
    target_line = next((ln for ln in header.splitlines() if ln.startswith("Target body name:")), "")
    centre_name = "Center body name: Moon (301)" if centre == "moon" else "Center body name: Earth (399)"
    required = [centre_name, "Reference frame : ICRF", "Output units    : KM-S", "GEOMETRIC cartesian states",
                "Calendar Date (UT"]
    if target_token(command) not in target_line or not all(token in header for token in required):
        raise ValueError(f"Unexpected target/frame/time/unit metadata: {target_line.strip()!r}")
    rows = csv.reader(io.StringIO(result.split("$$SOE")[1].split("$$EOE")[0].strip()))
    epochs, states = [], []
    for row in rows:
        t = datetime.strptime(row[1].strip(), "A.D. %Y-%b-%d %H:%M:%S.%f").replace(tzinfo=timezone.utc).timestamp()
        if abs((float(row[0]) - 2440587.5) * 86400 - t) > 0.002:
            raise ValueError("Calendar UTC and Julian UT disagree")
        epochs.append(t)
        states.append([float(x) for x in row[2:8]])
    expected = int(round((stop_s - start_s) / step_s)) + 1
    epochs = np.asarray(epochs)
    if len(states) != expected or not np.allclose(epochs - start_s, np.arange(expected) * step_s, rtol=0, atol=1e-3):
        raise ValueError(f"Incorrect coverage/count: {len(states)} rows, expected {expected}")
    states = np.asarray(states)
    if states.shape != (expected, 6) or not np.isfinite(states).all() or (np.abs(states[:, :3]).sum(1) == 0).any():
        raise ValueError("Invalid Cartesian states")
    return epochs, states, header


def solution_id(header: str) -> tuple[str, str]:
    """(hash, note) of the solution an OBJ_DATA header describes: the object block without the request time stamp
    plus the {source: ...} tag; the note is the 'Revised:' date or small-body 'Soln.date:' for people."""
    lines = [" ".join(ln.split()) for ln in header.split("Ephemeris / API_USER")[0].splitlines()]
    text = "\n".join(ln for ln in lines if ln and not ln.startswith("*****") and "JPL/HORIZONS" not in ln)
    tag = re.search(r"\{source:\s*([^}]*)\}", header)
    text += f"\nsource: {tag[1].strip() if tag else ''}"
    m = re.search(r"Revised: ([A-Z][a-z]{2} \d{1,2}, \d{4})|Soln\.date: (\S+)", text)
    return hashlib.sha1(text.encode()).hexdigest()[:12], (m[1] or m[2]) if m else ""


def digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Snapshots:
    """Raw-response cache: work/cache/horizons_eph/<stamp>/{<file>.json, manifest.json} (MMX layout)."""

    def __init__(self, root: Path = CACHE):
        self.root, self.folder, self.manifest, self.index, self.last = Path(root), None, None, None, 0.0
        self.spans, self.parsed = None, {}         # (COMMAND, CENTER) -> [(folder, entry, c0, c1, step)]; path -> parse
        self.fetched = self.reused = self.failures = 0
        self.down = False

    def load_index(self):
        self.index, self.spans = {}, {}
        for path in sorted(self.root.glob("*/manifest.json")):         # newest last, so newest wins
            for key, entry in json.loads(path.read_text())["requests"].items():
                self.index[key] = (path.parent, entry)
        for folder, entry in self.index.values():
            self.span(folder, entry)

    def span(self, folder, entry):
        p, step = entry["parameters"], step_of(entry["parameters"])
        if step and p.get("EPHEM_TYPE") == "'VECTORS'":
            self.spans.setdefault((p["COMMAND"], p["CENTER"]), []).append((folder, entry, *hz_times(p), step))

    def degraded(self) -> bool:
        return os.environ.get("CLE_OFFLINE") == "1" or self.down

    def get(self, name: str, params: dict, max_age_s=None, solution=None) -> tuple[Path, dict]:
        """The cached response of exactly these parameters (of this solution when one is named, younger than
        max_age_s unless offline/down), else fetched into this run's stamp."""
        key = hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()[:16]
        if self.index is None:
            self.load_index()
        hit = self.index.get(key) if os.environ.get("CLE_REFRESH") != "1" else None
        if hit and solution is not None and hit[1].get("solution") != solution:
            hit = None
        if hit and max_age_s is not None and not self.degraded() and time.time() - parse_iso(hit[1]["retrieved_utc"]) > max_age_s:
            hit = None
        if hit:
            folder, entry = hit
            path = folder / entry["file"]
            if digest(path) != entry["sha256"]:
                raise ValueError(f"Cached Horizons response changed: {path}")
            self.reused += 1
            return path, entry
        if self.degraded():
            raise LookupError(f"{'Horizons down' if self.down else 'offline'} and not cached: {name}")
        body, seconds = self.fetch(params)
        if self.folder is None:
            self.root.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_")
            self.folder = Path(tempfile.mkdtemp(prefix=stamp, dir=self.root))
            self.manifest = {"retrieved_utc": iso(time.time()), "api": API, "requests": {}}
        path = self.folder / f"{re.sub(r'[^A-Za-z0-9+-]+', '_', name)}__{key}.json"
        path.write_bytes(body)
        self.parsed.pop(path, None)                  # a refetch of the same key in this run replaces the file
        entry = {"name": name, "parameters": params, "url": API + "?" + urlencode(params), "file": path.name,
                 "sha256": digest(path), "retrieved_utc": iso(time.time()), "seconds": round(seconds, 2)}
        if solution is not None:
            entry["solution"] = solution
        self.manifest["requests"][key] = entry
        tmp = self.folder / ".manifest.json"
        tmp.write_text(json.dumps(self.manifest, indent=2))
        tmp.replace(self.folder / "manifest.json")
        self.index[key] = (self.folder, entry)
        self.span(self.folder, entry)
        self.fetched += 1
        return path, entry

    def read(self, folder: Path, entry: dict, command: str, centre: str):
        """Parsed cached response, memoised for the run; sha256 checked on first read. Raises ValueError (or
        CoverageError) for an error reply."""
        path = folder / entry["file"]
        if path not in self.parsed:
            if digest(path) != entry["sha256"]:
                raise ValueError(f"Cached Horizons response changed: {path}")
            p = entry["parameters"]
            self.parsed[path] = parse_response(path.read_text(), command, *hz_times(p), step_of(p), centre)
        return self.parsed[path]

    def serve(self, command: str, centre: str, t, solution):
        """Rows at the epochs t (NaN where nothing serves) from cached responses of this command/centre whose
        lattice contains them: responses of the named solution only (any while it is unknown), the one holding most
        of the epochs first, newest among equals. Also [(entry, path, header)] of the responses used."""
        if self.index is None:
            self.load_index()
        rv, used = np.full((len(t), 6), np.nan), []
        if os.environ.get("CLE_REFRESH") == "1":
            return rv, used
        q, found = request_params(command, 0, 60, 60, centre), []
        for folder, entry, c0, c1, s in self.spans.get((q["COMMAND"], q["CENTER"]), []):
            if solution is not None and entry.get("solution") != solution:
                continue
            k = (t - c0) / s
            inside = (t > c0 - 1e-3) & (t < c1 + 1e-3) & (np.abs(k - np.rint(k)) < 1e-6)
            if inside.any():
                found.append((int(inside.sum()), entry["retrieved_utc"], folder, entry, c0, s, inside))
        for _, _, folder, entry, c0, s, inside in sorted(found, key=lambda x: x[:2], reverse=True):
            need = inside & np.isnan(rv[:, 0])
            if not need.any():
                continue
            try:
                _, rows, header = self.read(folder, entry, command, centre)
            except ValueError:                           # a cached error reply (coverage) or a foreign request
                continue
            rv[need] = rows[np.rint((t[need] - c0) / s).astype(int)]
            used.append((entry, folder / entry["file"], header))
            self.reused += 1
            if not np.isnan(rv[:, 0]).any():
                break
        return rv, used

    def nearest(self, params: dict, max_step=None) -> list[tuple[Path, dict]]:
        """[(path, entry)] of the cached responses with the same command/centre, a step up to max_step (default:
        the request's) and a span overlapping the request by at least one step: replies covering the whole request
        (to within one step) first, newest day among them; otherwise the longest overlap (a reply that covers the
        window start beats a newer one that starts a day later); then the same OBJ_DATA, then the finer step (DW-04 c)."""
        if self.index is None:
            self.load_index()
        (a, b), step, found = hz_times(params), step_of(params), []
        for folder, entry, c0, c1, s in self.spans.get((params["COMMAND"], params["CENTER"]), []):
            if s > (max_step or step) + 1e-6 or min(b, c1) - max(a, c0) < step - 1e-6:
                continue
            ov, day = round(min(b, c1) - max(a, c0)), int(folder.name[:8])
            full = ov >= (b - a) - min(step, 3600.0) - 1e-6       # within an hour (a daily step is not "nearly full")
            rank = (full, day if full else ov, ov if full else day, entry["parameters"].get("OBJ_DATA") == params["OBJ_DATA"], -s)
            found.append((rank, folder / entry["file"], entry))
        return [(path, entry) for _, path, entry in sorted(found, key=lambda x: x[0], reverse=True)]

    def fetch(self, params: dict) -> tuple[bytes, float]:
        if self.down:
            raise HorizonsDown(f"Horizons unreachable: {self.failures} consecutive failures; no more requests this run")
        url = API + "?" + urlencode(params)
        for attempt in range(8):
            time.sleep(max(0.0, PAUSE_S - (time.time() - self.last)))
            t = time.time()
            try:
                with urlopen(Request(url, headers={"User-Agent": "cislunar-eye/1"}), timeout=180) as response:
                    body = response.read()
                self.last, self.failures = time.time(), 0
                return body, self.last - t
            except HTTPError as e:                     # Horizons reports some errors as HTTP 400 + JSON
                self.last = time.time()
                body = e.read()
                if e.code == 400 and body.lstrip().startswith(b"{"):
                    self.failures = 0
                    return body, self.last - t
                print(f"  HTTP {e.code}; retry {attempt + 1}", flush=True)
            except (URLError, TimeoutError, ConnectionError) as e:
                self.last = time.time()
                print(f"  {type(e).__name__}: {e}; retry {attempt + 1}", flush=True)
            self.failures += 1
            if self.failures >= BREAKER:               # breaker: bound the run instead of 8 attempts per request
                self.down = True
                raise HorizonsDown(f"Horizons unreachable: {self.failures} consecutive failures; no more requests this run")
            time.sleep(min(60, 10 * (attempt + 1)))
        raise ConnectionError("Horizons request failed after 8 attempts")


SNAPSHOTS = Snapshots()


@dataclass
class Piece:
    """A uniform run of Horizons samples; consecutive pieces share their boundary sample."""
    t0_s: float
    step_s: float
    rv: np.ndarray
    join_before: str = "continuous"
    jump_km: float = 0.0
    origin: list = field(default_factory=list)      # identity tag: slices share their parent's list
    mid: np.ndarray | None = None                   # the step/2 samples between rv rows (len(rv) - 1), when fetched

    @property
    def t_s(self):
        return self.t0_s + self.step_s * np.arange(len(self.rv))

    @property
    def t1_s(self):
        return self.t0_s + self.step_s * (len(self.rv) - 1)

    def slice(self, i, j):
        return Piece(self.t0_s + i * self.step_s, self.step_s, self.rv[i:j + 1], origin=self.origin,
                     mid=None if self.mid is None else self.mid[i:j])


def chunks(a, b, h):
    """[(start, stop)] below MAX_ROWS rows each, sharing their end samples."""
    n = int(round((b - a) / h))
    cuts = list(range(0, n, MAX_ROWS)) + [n]
    return [(a + i * h, a + j * h) for i, j in zip(cuts[:-1], cuts[1:])]


class Sampler:
    def __init__(self, command: str, centre: str, cid: str, cov=(None, None)):
        self.command, self.centre, self.cid, self.cov = command, centre, cid, cov      # cov: Horizons coverage (s)
        self.provenance, self.header, self.unchecked, self.solution = [], "", [], None

    def record(self, entry, path, kind, **extra):
        self.provenance.append({"src": f"horizons:{self.command}", "name": entry["name"], "kind": kind,
                                "retrieved_utc": entry["retrieved_utc"], "raw": rel(path), "sha256": entry["sha256"],
                                "parameters": entry["parameters"], **extra})

    def probe(self, t_s):
        """The target's OBJ_DATA header (2 rows at t_s): the solution it names and the tracking-data note. Offline
        or down the newest cached probe stands whatever its age; without one the solution stays unknown."""
        params = request_params(self.command, t_s, t_s + 60, 60, self.centre, obj_data=True)
        try:
            path, entry = SNAPSHOTS.get(f"{self.cid}_probe_{iso(t_s)}_60", params, max_age_s=PROBE_TTL_S)
        except (LookupError, HorizonsDown) as e:
            if not SNAPSHOTS.degraded():
                raise
            return
        _, _, self.header = SNAPSHOTS.read(path.parent, entry, self.command, self.centre)  # CoverageError: build() clips
        self.solution = solution_id(self.header)[0]
        self.record(entry, path, "probe")

    def states(self, start_s, stop_s, step_s, kind="grid", regrid=True, tile=False) -> Piece:
        """One uniform piece at step_s carrying its step/2 midpoints (kinds grid/refine/repair; 'mid' and
        'moon_check' sample step_s only), served from the cache or fetched as TILE_D-day tiles (tile=True) or the
        exact span. Offline or down without the whole lattice cached, the fallback piece may cover less."""
        half = kind not in ("mid", "moon_check")
        h = step_s / 2 if half else step_s
        n = int(round((stop_s - start_s) / h))
        if n == 0:                                   # one epoch: Horizons needs stop > start
            return self.states(start_s, start_s + step_s, step_s, kind, regrid, tile).slice(0, 0)
        t, name = start_s + h * np.arange(n + 1), f"{self.cid}_{kind}_{iso(start_s)}_{int(h)}"
        rv, used = SNAPSHOTS.serve(self.command, self.centre, t, self.solution)
        missing = np.isnan(rv[:, 0])
        if missing.any() and not SNAPSHOTS.degraded():
            try:
                self.request(t[missing], h, kind, tile)
            except HorizonsDown:                     # breaker tripped mid-piece: what was cached stands
                pass
            rv, used = SNAPSHOTS.serve(self.command, self.centre, t, self.solution)
            missing = np.isnan(rv[:, 0])
            if missing.any() and not SNAPSHOTS.degraded():
                raise ValueError(f"Horizons did not cover {name}: {int(missing.sum())} of {len(t)} epochs")
        why = f"{'Horizons down' if SNAPSHOTS.down else 'offline'} and not cached: {name}"
        have = ~missing[::2] if half else ~missing           # grid samples; a missing midpoint is only unchecked
        other = not have.any() and self.solution is not None and SNAPSHOTS.degraded()
        if other:                                    # DW-04 (c): this solution is not cached, an older one may be
            rv, used = SNAPSHOTS.serve(self.command, self.centre, t, None)
            missing = np.isnan(rv[:, 0])
            have = ~missing[::2] if half else ~missing
            why += f"; solution {self.solution} not cached"
        if not have.any():
            return self.fallback(start_s, stop_s, step_s, h, kind, name, why, regrid)
        for entry, path, header in used:
            self.record(entry, path, kind)
            if len(header) > len(self.header):       # an OBJ_DATA header beats a plain one
                self.header = header
        if other or not have.all():                  # DW-04 (c): the longest cached run of the grid
            i0, i1 = max(runs(have), key=lambda r: r[1] - r[0])
            if i1 == i0:
                raise LookupError(f"{why}; cached responses overlap by under 2 samples")
            i0, i1 = (2 * i0, 2 * i1) if half else (i0, i1)
            fb = {"why": why, "requested": [iso(start_s), iso(stop_s)], "cached": ", ".join(e["name"] for e, *_ in used),
                  "used": [iso(t[i0]), iso(t[i1])], "regrid": False}
            for x in self.provenance[-len(used):]:
                x["fallback"] = fb
            t, rv = t[i0:i1 + 1], rv[i0:i1 + 1]
        return Piece(float(t[0]), step_s, rv[::2], mid=rv[1::2]) if half else Piece(float(t[0]), step_s, rv)

    def request(self, t, h, kind, tile):
        """Fetch what the cache lacks at the epochs t: TILE_D-day tiles on the 00:00 UTC lattice at gcd(h, 3600),
        clipped to the coverage (tile=True and t on that lattice), else the exact span below MAX_ROWS rows."""
        ht = math.gcd(int(h), 3600)
        if tile and h == int(h) and not (t % ht).any():
            lo = -np.inf if self.cov[0] is None else math.ceil(self.cov[0] / ht) * ht
            hi = np.inf if self.cov[1] is None else math.floor(self.cov[1] / ht) * ht
            later, T = t, TILE_D * 86400
            while len(later):
                a = math.floor(later[0] / T) * T
                a0, b0 = max(a, lo), min(a + T, hi)
                self.fetch_span(b0 - ht if b0 <= a0 else a0, b0, ht, kind)
                later = later[later > a + T + 1e-6]      # the tile's last sample is a + T
            return
        if t[-1] - t[0] < h:                         # a lone epoch: Horizons needs stop > start
            t = (t[0], t[0] + h) if self.cov[1] is None or t[0] + h <= self.cov[1] else (t[0] - h, t[0])
        for a, b in chunks(t[0], t[-1], h):
            self.fetch_span(a, b, h, kind)

    def fetch_span(self, a, b, h, kind):
        params = request_params(self.command, a, b, h, self.centre)
        path, entry = SNAPSHOTS.get(f"{self.cid}_{kind}_{iso(a)}_{int(h)}", params, solution=self.solution)
        SNAPSHOTS.read(path.parent, entry, self.command, self.centre)      # CoverageError: build() clips and retries

    def fallback(self, start_s, stop_s, step_s, h, kind, name, why, regrid=True) -> Piece:
        """DW-04 (c), nothing cached on the lattice: the newest overlapping cached response of this command/centre
        at a step up to step_s, sliced to the requested lattice; Hermite re-gridded when the lattices differ
        (regrid=True), else kept on the cached lattice."""
        half = kind not in ("mid", "moon_check")
        params = request_params(self.command, start_s, stop_s, h, self.centre)
        for path, entry in SNAPSHOTS.nearest(params, step_s):
            s = step_of(entry["parameters"])
            if not regrid and abs(s - h) > 1e-6:
                continue
            try:
                _, rv, header = SNAPSHOTS.read(path.parent, entry, self.command, self.centre)
                break
            except ValueError:                       # a cached error reply (coverage) or a foreign request
                continue
        else:
            raise LookupError(f"{why}; no overlapping cached response")
        c0, c1 = hz_times(entry["parameters"])
        cached = Piece(c0, s, rv)
        n = int(round((stop_s - start_s) / h))
        k0, k1 = max(int(np.ceil((c0 - start_s) / h - 1e-6)), 0), min(int(np.floor((c1 - start_s) / h + 1e-6)), n)
        if half:
            k0, k1 = k0 + k0 % 2, k1 - k1 % 2
        t = start_s + h * np.arange(k0, k1 + 1)
        phase = (t[0] - c0) % s if len(t) else 0.0
        regridded = abs(s - h) > 1e-6 or min(phase, s - phase) > 1e-3
        if regridded and regrid:
            rv = np.hstack(hermite(cached, t))
        elif regridded:                              # cached lattice inside [start, stop]
            keep = (cached.t_s >= start_s - 1e-3) & (cached.t_s <= stop_s + 1e-3)
            t, rv = cached.t_s[keep], cached.rv[keep]
        else:
            i = int(round((t[0] - c0) / s)) if len(t) else 0
            rv = cached.rv[i:i + len(t)]
        if len(t) < (3 if half else 2):
            raise LookupError(f"{why}; cached {entry['name']} overlaps by under 2 samples")
        if len(header) > len(self.header):
            self.header = header
        self.record(entry, path, kind, fallback={"why": why, "requested": [iso(start_s), iso(stop_s)], "cached": entry["name"],
                                                 "used": [iso(t[0]), iso(t[-1])], "regrid": bool(regridded and regrid)})
        return Piece(float(t[0]), step_s, rv[::2], mid=rv[1::2]) if half else Piece(float(t[0]), step_s, rv)


def omega(rv):
    r = np.linalg.norm(rv[:, :3], axis=1)
    return np.linalg.norm(np.cross(rv[:, :3], rv[:, 3:]), axis=1) / r ** 2


def finer(step_s: float, limit_s: float, floor: int = 60) -> int:
    """Largest even divisor of step_s in [floor, limit_s] (even, so step/2 stays whole seconds)."""
    s = int(step_s)
    cands = [d for d in range(floor, s, 2) if s % d == 0 and d <= limit_s]
    return max(cands) if cands else min(floor, s)


def runs(mask, pad=0):
    """Inclusive [k0, k1] runs of True in a per-interval mask, dilated by `pad` intervals."""
    m = mask.copy()
    for _ in range(pad):
        m[1:] |= mask[:-1]
        m[:-1] |= mask[1:]
        mask = m.copy()
    edges = np.flatnonzero(np.diff(np.r_[0, m.astype(int), 0]))
    return list(zip(edges[::2], edges[1::2] - 1))


def hermite(p: Piece, t):
    s = CubicHermiteSpline(p.t_s, p.rv[:, :3], p.rv[:, 3:])
    return s(t), s(t, 1)


def skip_one(p: Piece):
    """Hermite error at every interior sample from its two neighbours (step 2h): (r0+r1)/2 + 2h(v0-v1)/8."""
    r, v = p.rv[:, :3], p.rv[:, 3:]
    guess = (r[:-2] + r[2:]) / 2 + 2 * p.step_s * (v[:-2] - v[2:]) / 8
    return np.linalg.norm(guess - r[1:-1], axis=1)


def jumps(p: Piece):
    """MMX transfer.py splice metric per interval: |dr - h/2 (v0 + v1)| (km)."""
    return np.linalg.norm(np.diff(p.rv[:, :3], axis=0) - p.step_s / 2 * (p.rv[:-1, 3:] + p.rv[1:, 3:]), axis=1)


def refine(sampler: Sampler, p: Piece, depth=0) -> list[Piece]:
    """Split p where h*omega > 0.3 into finer uniform sub-pieces (§6.2), re-checking each sub-piece."""
    w = omega(p.rv)
    bad = p.step_s * np.maximum(w[:-1], w[1:]) > OMEGA_H
    if p.step_s <= 60 or not bad.any() or depth > 3:
        return [p]
    out, cursor = [], 0
    for k0, k1 in runs(bad, pad=1):
        if k0 > cursor:
            out.append(p.slice(cursor, k0))
        h = finer(p.step_s, max(60.0, OMEGA_H / w[k0:k1 + 2].max()))
        try:
            sub = sampler.states(p.t0_s + k0 * p.step_s, p.t0_s + (k1 + 1) * p.step_s, h, "refine")
        except LookupError:                          # offline/down, nothing cached at h: keep the coarse samples
            out.append(p.slice(k0, k1 + 1))
            cursor = k1 + 1
            continue
        out.extend(refine(sampler, sub, depth + 1))
        cursor = k1 + 1
    if cursor < len(p.rv) - 1:
        out.append(p.slice(cursor, len(p.rv) - 1))
    return out


def midpoints(sampler: Sampler, p: Piece):
    """Hermite position/velocity error at the step/2 midpoints: the piece's own odd samples (NaN where the cache
    had none), or a Horizons re-request for a piece without them. Offline or down, midpoints the cache cannot
    serve count as unchecked (error 0) and are listed in sampler.unchecked."""
    tm = p.t_s[:-1] + p.step_s / 2
    if p.mid is not None and len(p.mid) == len(tm):
        mid = Piece(float(tm[0]), p.step_s, p.mid)
    else:
        try:
            mid = sampler.states(tm[0], tm[-1], p.step_s, "mid")
        except LookupError as e:
            if not SNAPSHOTS.degraded():
                raise
            sampler.unchecked.append({"t0": iso(tm[0]), "t1": iso(tm[-1]), "step_s": p.step_s, "n": int(len(tm)), "why": str(e)})
            return np.zeros(len(tm)), np.zeros(len(tm)), tm
    r, v = hermite(p, mid.t_s)
    e, ve = np.linalg.norm(r - mid.rv[:, :3], axis=1), np.linalg.norm(v - mid.rv[:, 3:], axis=1)
    gap = np.isnan(e)
    if gap.any():                                    # odd samples the cache could not serve (degraded)
        e[gap], ve[gap] = 0.0, 0.0
        sampler.unchecked.append({"t0": iso(mid.t_s[gap][0]), "t1": iso(mid.t_s[gap][-1]), "step_s": p.step_s, "n": int(gap.sum())})
    if len(mid.rv) == len(tm):
        return e, ve, mid.t_s
    k = np.rint((mid.t_s - tm[0]) / p.step_s).astype(int)      # partial fallback: the rest is unchecked
    full_e, full_ve = np.zeros(len(tm)), np.zeros(len(tm))
    full_e[k], full_ve[k] = e, ve
    sampler.unchecked.append({"t0": iso(tm[0]), "t1": iso(tm[-1]), "step_s": p.step_s, "n": int(len(tm) - len(k))})
    return full_e, full_ve, tm


def repair(sampler: Sampler, p: Piece, err, tol, notes) -> list[Piece]:
    """Refine-or-break (§5.3): re-request failing intervals at h/4; keep them if the error falls
    roughly as h^4, otherwise break the piece at the largest jump (join_before='splice')."""
    out, cursor, pending = [], 0, [None]

    def emit(q):
        if len(q.rv) < 2:
            return
        if pending[0] is not None:
            q.join_before, q.jump_km, pending[0] = "splice", pending[0], None
        out.append(q)

    for k0, k1 in runs(err > tol):
        emit(p.slice(cursor, k0))
        a, b = p.t0_s + k0 * p.step_s, p.t0_s + (k1 + 1) * p.step_s
        h, coarse = finer(p.step_s, p.step_s / 4, floor=10), float(err[k0:k1 + 1].max())
        smooth, sub = False, None
        if h < p.step_s:
            try:
                sub = sampler.states(a, b, h, "repair")
            except LookupError:                      # offline/down, nothing cached at h
                sub = None
            if sub is not None and len(sub.rv) < 3:
                sub = None
            if sub is not None:
                e2 = skip_one(sub).max()
                smooth = coarse / max(e2, 1e-9) > (p.step_s / (2 * h)) ** 4 / 4
        else:
            sub = p.slice(k0, k1 + 1)
        if sub is None:                              # keep the coarse samples, flagged
            emit(p.slice(k0, k1 + 1))
            notes.append({"t0": iso(a), "t1": iso(b), "step_s": p.step_s, "action": "unrepaired", "coarse_err_km": coarse})
        elif smooth:
            emit(sub)
            notes.append({"t0": iso(a), "t1": iso(b), "step_s": h, "action": "refine",
                          "coarse_err_km": coarse, "est_err_km": float(e2) / 16})
        else:
            jj = jumps(sub)
            j = int(np.argmax(jj))
            emit(sub.slice(0, j))
            pending[0] = float(jj[j])
            emit(sub.slice(j + 1, len(sub.rv) - 1))
            notes.append({"t0": iso(sub.t_s[j]), "t1": iso(sub.t_s[j + 1]), "step_s": h, "action": "splice",
                          "coarse_err_km": coarse, "jump_km": float(jj[j])})
        cursor = k1 + 1
    emit(p.slice(cursor, len(p.rv) - 1))
    return out


def checked(sampler: Sampler, p: Piece, tol, notes, depth=0):
    """[(piece, pos_err, vel_err, t_mid)] after refine-or-break on p, repeated on re-requested pieces."""
    e, ve, tm = midpoints(sampler, p)
    if not (e > tol).any():
        return [(p, e, ve, tm)]
    out = []
    for q in repair(sampler, p, e, tol, notes):
        if q.origin is p.origin and q.step_s == p.step_s:
            i, m = int(round((q.t0_s - p.t0_s) / p.step_s)), len(q.rv) - 1
            out.append((q, e[i:i + m], ve[i:i + m], tm[i:i + m]))
        elif depth < 2:
            out.extend(checked(sampler, q, tol, notes, depth + 1))
        else:
            out.append((q, *midpoints(sampler, q)))
    return out


def spans(path: Path = SPANS) -> dict:
    with open(path, newline="") as f:
        return {r["horizons_id"]: (r["span_start"], r["span_end"]) for r in csv.DictReader(f, delimiter="\t")}


def plan(a_s, b_s, base, win) -> list[tuple[float, float, float]]:
    """Uniform (start, stop, step) pieces on the window grid covering [a, b]: the base step inside,
    plus edge pieces at min(base, 600 s), so a coverage end off the base grid loses < 10 min."""
    edge = min(base, 600)
    g, e = win.grid(base, a_s, b_s), win.grid(edge, a_s, b_s)
    if len(e) < 3:
        return []
    if len(g) < 2:
        return [(e[0], e[-1], edge)]
    out = [(e[0], g[0], edge)] if g[0] > e[0] else []
    out.append((g[0], g[-1], base))
    return out + ([(g[-1], e[-1], edge)] if e[-1] > g[-1] else [])


def pred_boundary(header: str, retrieved_s: float):
    """End of tracking data stated in the object header ('Tracking data through October 19', 'Last tracking
    data was October 18'): samples after the end of that day are predictions. None if not stated."""
    obj = " ".join(header.split("Ephemeris / API_USER")[0].split())
    m = re.search(r"tracking data (?:through|thru|was|up to|until|to)\s+([A-Z][a-z]+)\.?\s+(\d{1,2})(?:,\s*(\d{4}))?",
                  obj, re.I)
    if not m:
        return None, ""
    year = int(m[3]) if m[3] else datetime.fromtimestamp(retrieved_s, tz=timezone.utc).year
    for fmt in ("%B %d %Y", "%b %d %Y"):
        try:
            t = datetime.strptime(f"{m[1]} {m[2]} {year}", fmt).replace(tzinfo=timezone.utc).timestamp() + 86400
        except ValueError:
            continue
        if t > retrieved_s + 86400:                 # no year given and the date reads as the future: last year
            t = datetime.strptime(f"{m[1]} {m[2]} {year - 1}", fmt).replace(tzinfo=timezone.utc).timestamp() + 86400
        return t, m[0]
    return None, ""


def split_quality(pieces: list[Piece], boundary) -> list[tuple[Piece, str]]:
    out = []
    for p in pieces:
        t = p.t_s
        k = len(t) if boundary is None else int(np.searchsorted(t, boundary))
        if k >= len(t) - 1:
            out.append((p, "ref"))
        elif k <= 0:
            out.append((p, "pred"))
        else:
            head = p.slice(0, k)
            head.join_before, head.jump_km = p.join_before, p.jump_km
            out += [(head, "ref"), (p.slice(k, len(t) - 1), "pred")]
    return out


def build(target: dict, win, ctx) -> StateTable:
    from . import primary
    _, ref, _ = primary(target)
    if not re.fullmatch(r"-?\d+|DES=[\w ]+", ref):
        raise ValueError(f"unverified Horizons command {ref!r}")
    centre = "moon" if target["regime"] == "LUNAR_ORBIT" else "earth"
    base, tol = BASE_STEP.get(target["regime"], 3600), TOL_KM[centre]
    s0, s1 = spans().get(ref, ("", ""))
    cov = [parse_iso(s0) + SPAN_MARGIN_S if s0 else None, parse_iso(s1) - SPAN_MARGIN_S if s1 else None]
    sampler, fetched = Sampler(ref, centre, target["cid"]), SNAPSHOTS.fetched
    for _ in range(4):
        a = win.t0_s if cov[0] is None else max(win.t0_s, cov[0])
        b = win.t1_s if cov[1] is None else min(win.t1_s, cov[1])
        try:
            sampler.cov = tuple(cov)
            sampler.probe(min(max(win.now_s - win.now_s % 86400, a), b - 60))
            pieces = [sampler.states(*p, tile=p[2] == base) for p in plan(a, b, base, win)]
            break
        except CoverageError as e:
            cov[0 if e.side == "start" else 1] = e.t_s + 1 if e.side == "start" else e.t_s - 1
            sampler.provenance.clear()
    else:
        raise ValueError("Horizons coverage did not converge")
    if not pieces:
        raise ValueError(f"Horizons coverage {iso(a)}..{iso(b)} is under 2 steps")
    notes = []
    rows = [x for p in pieces for q in refine(sampler, p) for x in checked(sampler, q, tol, notes)]
    for (prev, *_), (cur, *_) in zip(rows[:-1], rows[1:]):
        if abs(cur.t0_s - prev.t1_s) > 1e-6 and cur.join_before == "continuous":
            cur.join_before = "gap"
    final = [p for p, *_ in rows]
    e_all, ve_all, t_all = (np.concatenate([x[i] for x in rows]) for i in (1, 2, 3))
    retrieved = max(parse_iso(x["retrieved_utc"]) for x in sampler.provenance)
    boundary, qnote = pred_boundary(sampler.header, retrieved)
    segs = [Segment(t0_s=p.t0_s, step_s=p.step_s, r_km=p.rv[:, :3], v_kms=p.rv[:, 3:], source=f"horizons:{ref}",
                    quality=q, join_before=p.join_before, jump_km=p.jump_km)
            for p, q in split_quality(final, boundary)]
    segs[0].join_before = "start"
    tag = re.search(r"Target body name:.*\{source:\s*([^}]*)\}", sampler.header)
    k = int(np.argmax(e_all))
    unchecked = sum(u["n"] for u in sampler.unchecked)
    probe = next((x for x in sampler.provenance if x["kind"] == "probe"), None)
    validation = {
        "adapter": "horizons", "midpoint_method": "Horizons step/2 samples", "midpoint_count": int(len(e_all)) - unchecked,
        "midpoint_max_km": float(e_all[k]), "midpoint_max_utc": iso(t_all[k]),
        "midpoint_vel_max_kms": float(ve_all.max()), "tol_km": tol, "midpoint_pass": bool(e_all.max() < tol),
        "coverage": [iso(segs[0].t0_s), iso(segs[-1].t_s[-1])], "source_tag": tag[1].strip() if tag else "",
        "pred_after_utc": iso(boundary) if boundary else None, "pred_note": qnote,
        "solution": {"id": sampler.solution, "note": solution_id(sampler.header)[1],
                     "checked_utc": probe["retrieved_utc"] if probe else None},
        "refined": [{"t0": iso(p.t0_s), "t1": iso(p.t1_s), "step_s": p.step_s} for p in final if p.step_s < base],
        "repairs": notes, "requests": len(sampler.provenance), "fetched": SNAPSHOTS.fetched - fetched,
        "retrieved_utc": iso(retrieved), "base_step_s": base, "midpoint_unchecked": unchecked,
    }
    caveat = target.get("caveat", "")
    used = [x for x in sampler.provenance if "fallback" in x]
    if used:                                         # DW-04 (c): say which snapshot stood in, and where it ends
        snap, regrid = min(x["retrieved_utc"] for x in used)[:10], any(x["fallback"]["regrid"] for x in used)
        validation["fallback"] = {"snapshot_utc": snap, "files": sorted({x["raw"] for x in used}), "regrid": regrid,
                                  "requests": len(used), "unchecked": sampler.unchecked}
        note = (f"Horizons snapshot of {snap} ({'re-gridded ' if regrid else ''}offline fallback), ends "
                f"{iso(segs[-1].t_s[-1])[:16]}Z" + (f", {unchecked} midpoints unchecked" if unchecked else ""))
        caveat = "; ".join(filter(None, [note, caveat]))
    return StateTable(cle_id=target["cid"], centre=centre, segments=segs, provenance=sampler.provenance,
                      validation=validation, caveat=caveat)


def moon_check(win, ctx) -> dict:
    """Horizons 301 (geocentric, 1 d) against DE440s; MMX archived < 0.003 km (§5.3). Offline or down without
    the lattice cached, the newest overlapping cached response is checked at its own epochs."""
    days = int((win.t1_s - win.t0_s) // 86400)
    sampler = Sampler("301", "earth", "moon")
    p = sampler.states(win.t0_s, win.t0_s + 86400 * days, 86400, "moon_check", regrid=False, tile=True)
    d = np.linalg.norm(ctx.moon_rv(p.t_s)[:, :3] - p.rv[:, :3], axis=1)
    out = {"samples": int(len(d)), "max_km": float(d.max()), "kernel": str(ctx.kernel_path), "pass": bool(d.max() < 1),
           "files": sorted({x["raw"] for x in sampler.provenance}), "span": [iso(p.t0_s), iso(p.t1_s)]}
    if "fallback" in sampler.provenance[0]:
        x = sampler.provenance[0]
        out |= {"fallback": x["fallback"], "raw": x["raw"], "retrieved_utc": x["retrieved_utc"]}
    return out
