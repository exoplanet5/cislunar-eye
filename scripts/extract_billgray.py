#!/usr/bin/env python3
"""Extract cislunar / high-Earth-orbit candidates from Bill Gray's TLE archive.

Source: ~/tles (Bill Gray hex/classical TLE archive, index tle_list.txt) plus
../highartsat (catalog/build.py parser; catalog/data/orbits.json for a
cross-check).  Output: catalog/sources/billgray.tsv in the schema of
catalog/sources/SCHEMA.md (one row per physical object).

What it does
------------
1. Parses ~/tles/tle_list.txt with sat_id.cpp semantics (sat_code/sat_id.cpp
   lines ~1290-1445): only lines starting with '#' in column 0 are
   directives; '# Range:' applies to the *next* '# Include' only (sat_id
   resets tle_start/tle_range after every Include); '# ID:' persists until
   the next '# ID:' or '# ID off'; indented lines are comments; '# Warn
   <date> | text' lines are upcoming-event reminders.  Range dates are parsed
   like lunar/get_time.cpp (YYYY-MM-DD[.f], YYYYMMDD[.f], YYYYmoDD[.f] with a
   partial month name such as 'no' / 'de').  highartsat/catalog/build.py's
   own parse_tle_list() is *not* used because it (a) keeps a Range alive
   across Includes, (b) treats the indented, commented-out
   '   # Include s512009_21.tle' as live and (c) cannot read '2022no16.5'.
2. Parses every included .tle file with build.py's parse_tle_file() /
   parse_tle_epoch() / compute_orbit_metrics() (classical vs Bill Gray
   extended 'hex' format) - imported read-only with bytecode writing off -
   and adds its own reader for the find_orb element blocks in the file
   header (build.py's regex misses e.g. '# a342392.95547').
3. Evaluates TLEs with Bill Gray's get_vect (SGP4/SDP4 + his hex SDP4-high
   integrator, J2000 equatorial output) one TLE at a time at that TLE's own
   epoch (TDT), i.e. inside its validity interval.  Hex files are sampled
   along their whole span (<= MAX_SAMPLES per file) to get geocentric /
   selenocentric distance history; Moon/Sun/Earth come from JPL DE440s
   (~/JPLDE).  Results are cached in work/cache/billgray/derived/.
   Sample states inside the Earth (failed eph2tle fits such as slim.tle
   2023-09-26) are dropped from the statistics and named in notes.
   Reported elements are osculating at the reference TLE (latest TLE inside
   a Range, preferring one covering 2026-10-21): selenocentric only when the
   state is inside the lunar SOI with apolune inside it too, geocentric when
   bound or within 1.5e6 km, else heliocentric (then period_d is empty).
4. Groups Include entries into physical objects ((NORAD, intl) key plus a
   few documented merges), applies the selection rule (period > 2.4 d at
   any TLE, or hex/extended format, or apogee >= 100 000 km from Earth
   centre), assigns regime/status (automatic rules + a knowledge table that
   cites Bill Gray's commit messages / tle_list comments / memory), and
   writes the TSV.  Rejected objects are only counted (list in
   work/cache/billgray/derived/rejected.tsv).  Objects named only in
   tle_list '# Warn' lines (Planck, Herschel, J002E3, ...) get a row of
   their own with no ephem_sources.

Idempotent: re-running rewrites the same outputs; network is not used
(upstream GitHub commit list / files are read from work/cache/billgray/
upstream/ if present - fetch them separately).

Usage:  ~/.venvs/astro313/bin/python -I scripts/extract_billgray.py
"""
from __future__ import annotations

import importlib.util
import json
import math
import re
import subprocess
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _now import now  # noqa: E402

UTC = timezone.utc
HOME = Path.home()
PROJECT = Path(__file__).resolve().parents[1]
TLE_ROOT = HOME / "tles"
TLE_LIST = TLE_ROOT / "tle_list.txt"
HIGHARTSAT = HOME / "ciscluar" / "highartsat"
BUILD_PY = HIGHARTSAT / "catalog" / "build.py"
ORBITS_JSON = HIGHARTSAT / "catalog" / "data" / "orbits.json"
GET_VECT = HOME / "Desktop" / "billgray" / "sat_code" / "get_vect"
DE_KERNEL = HOME / "JPLDE" / "de440s.bsp"
CACHE = PROJECT / "work" / "cache" / "billgray"
UPSTREAM = CACHE / "upstream"
DERIVED = CACHE / "derived"
GV_TMP = DERIVED / "get_vect_tmp"
OUT_TSV = PROJECT / "catalog" / "sources" / "billgray.tsv"

NOW = now("day")                      # CLE_NOW floored to the day, else today (scripts/_now.py)
STALE_DAYS = 120
MAX_SAMPLES = 220

MU_E = 398600.4418
MU_M = 4902.800066
MU_S = 132712440041.93938
AU_KM = 149597870.7
R_SOI_MOON = 66100.0
OBLIQ = math.radians(23.4392911)

PERIOD_MIN_D = 2.4
APOGEE_MIN_KM = 100000.0

COLUMNS = ["src", "src_ref", "name", "aliases", "norad", "cospar", "horizons_id",
           "obj_type", "regime", "status", "status_date", "period_d", "a_km", "e",
           "incl_deg", "perigee_km", "apogee_km", "elem_epoch", "frame_center",
           "ephem_sources", "covers_now", "notes"]


# --------------------------------------------------------------------------
# build.py import (read-only)
# --------------------------------------------------------------------------

def import_build():
    sys.dont_write_bytecode = True        # never write __pycache__ into highartsat
    spec = importlib.util.spec_from_file_location("highartsat_build", BUILD_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["highartsat_build"] = mod        # dataclasses needs the module registered
    spec.loader.exec_module(mod)
    return mod


B = import_build()


# --------------------------------------------------------------------------
# time helpers
# --------------------------------------------------------------------------

_LEAPS = [  # (UTC date from which TAI-UTC applies, TAI-UTC seconds)
    ((1972, 1, 1), 10), ((1972, 7, 1), 11), ((1973, 1, 1), 12), ((1974, 1, 1), 13),
    ((1975, 1, 1), 14), ((1976, 1, 1), 15), ((1977, 1, 1), 16), ((1978, 1, 1), 17),
    ((1979, 1, 1), 18), ((1980, 1, 1), 19), ((1981, 7, 1), 20), ((1982, 7, 1), 21),
    ((1983, 7, 1), 22), ((1985, 7, 1), 23), ((1988, 1, 1), 24), ((1990, 1, 1), 25),
    ((1991, 1, 1), 26), ((1992, 7, 1), 27), ((1993, 7, 1), 28), ((1994, 7, 1), 29),
    ((1996, 1, 1), 30), ((1997, 7, 1), 31), ((1999, 1, 1), 32), ((2006, 1, 1), 33),
    ((2009, 1, 1), 34), ((2012, 7, 1), 35), ((2015, 7, 1), 36), ((2017, 1, 1), 37)]


def tt_minus_utc(dt: datetime) -> float:
    v = 10
    for (y, m, d), s in _LEAPS:
        if dt >= datetime(y, m, d, tzinfo=UTC):
            v = s
    return v + 32.184


def jd_utc(dt: datetime) -> float:
    return 2440587.5 + dt.timestamp() / 86400.0


def jd_tt(dt: datetime) -> float:
    return jd_utc(dt) + tt_minus_utc(dt) / 86400.0


def mjd_to_dt(mjd: float) -> datetime:          # MJD (treated as UTC-ish)
    return datetime(1858, 11, 17, tzinfo=UTC) + timedelta(days=mjd)


def iso(dt: datetime | None, with_time=True) -> str:
    if dt is None:
        return ""
    if not with_time:
        return dt.strftime("%Y-%m-%d")
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def short_date(dt: datetime | None) -> str:
    """YYYY-MM-DD, or YYYY-MM-DDTHH:MM when not midnight."""
    if dt is None:
        return ""
    if dt.hour == 0 and dt.minute == 0 and dt.second < 30:
        return dt.strftime("%Y-%m-%d")
    return dt.strftime("%Y-%m-%dT%H:%M")


def dday(dt: datetime | None) -> str:
    """YYYY-MM-DD (date part, i.e. floor) for ephem_sources spans."""
    return dt.strftime("%Y-%m-%d") if dt is not None else ""


_MONTHS = ["january", "february", "march", "april", "may", "june", "july",
           "august", "september", "october", "november", "december"]


def parse_bg_date(tok: str) -> datetime | None:
    """Dates as written in tle_list.txt (get_time.cpp-compatible subset)."""
    tok = tok.strip()
    d = B.parse_flexible_date(tok)          # YYYY-MM-DD[.f] and YYYYMMDD[.f]
    if d:
        return d
    m = re.fullmatch(r"(\d{4})([A-Za-z]+)(\d{1,2}(?:\.\d+)?)", tok)   # 2022no16.5
    if m:
        mon = next((i + 1 for i, n in enumerate(_MONTHS) if n.startswith(m.group(2).lower())), None)
        if mon:
            day = float(m.group(3))
            return datetime(int(m.group(1)), mon, 1, tzinfo=UTC) + timedelta(days=day - 1)
    m = re.fullmatch(r"(\d{4})-([A-Za-z]{3})-(\d{1,2})", tok)          # 2026-Nov-10
    if m:
        mon = next((i + 1 for i, n in enumerate(_MONTHS) if n.startswith(m.group(2).lower())), None)
        if mon:
            return datetime(int(m.group(1)), mon, int(m.group(3)), tzinfo=UTC)
    return None


# --------------------------------------------------------------------------
# tle_list.txt
# --------------------------------------------------------------------------

@dataclass
class Entry:
    line_no: int
    include: str
    range_start: datetime | None
    range_end: datetime | None
    range_raw: str
    norad: str | None
    intl: str | None
    max_err: float | None
    max_err_comment: str
    old: bool = False
    missing: bool = False


@dataclass
class TleList:
    entries: list = field(default_factory=list)
    warns: list = field(default_factory=list)          # dicts
    commented_includes: list = field(default_factory=list)
    indented_comments: list = field(default_factory=list)   # (line_no, text)
    hash_comments: list = field(default_factory=list)       # (line_no, text)


def parse_tle_list(path: Path) -> TleList:
    out = TleList()
    rs = re_ = None
    rraw = ""
    norad = intl = None
    max_err = None
    max_err_comment = ""
    for n, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if not raw.strip():
            continue
        if not raw.startswith("#"):
            out.indented_comments.append((n, raw.strip()))
            continue
        line = raw.rstrip()
        if line.startswith("# Max error"):
            m = re.match(r"# Max error\s+([0-9.]+)\s*(.*)", line)
            if m:
                max_err = float(m.group(1))
                max_err_comment = m.group(2).strip()
        elif line.startswith("# Range: "):
            parts = line[9:].split()
            rs = parse_bg_date(parts[0]) if parts else None
            re_ = parse_bg_date(parts[1]) if len(parts) > 1 else None
            rraw = " ".join(parts[:2])
        elif line.startswith("# ID: "):
            parts = line[6:].split()
            norad, intl = parts[0], (parts[1] if len(parts) > 1 else None)
        elif line.startswith("# ID off"):
            norad = intl = None
        elif line.startswith("# Include "):
            inc = line[10:].strip()
            e = Entry(n, inc, rs, re_, rraw, norad, intl, max_err, max_err_comment)
            e.old = inc.startswith("old_tles/")
            e.missing = not (TLE_ROOT / inc).exists()
            out.entries.append(e)
            rs = re_ = None          # sat_id: Range applies to the next Include only
            rraw = ""
        elif line.startswith("# Warn ") or re.match(r"#\s+\d{4}-[A-Za-z]{3}-\d{1,2}\s*\|", line):
            active = line.startswith("# Warn ")
            body = line[7:] if active else line[1:]
            date_s, _, text = body.partition("|")
            out.warns.append({"line_no": n, "active": active, "date": parse_bg_date(date_s.strip()),
                              "date_raw": date_s.strip(), "text": text.strip()})
        elif re.match(r"#\s*#\s*Include", line):
            out.commented_includes.append((n, line))
        elif line.startswith("# TLEs expected") or line.startswith("# No updates"):
            pass
        else:
            out.hash_comments.append((n, line[1:].strip()))
    return out


# --------------------------------------------------------------------------
# .tle files
# --------------------------------------------------------------------------

_MACHINE = re.compile(r"^#\s*(Created |Ephemeris (start|end)|Times are all|Positions/velocities|"
                      r"Ephem range|1 NoradU|2 NoradU|Worst residual|MJD \d)")


def parse_header(path: Path) -> dict:
    """Ephem range, find_orb element blocks and free-text comments that sit
    in the comment lines of a Bill Gray TLE file (after the eph2tle
    boilerplate)."""
    info = {"ephem_range": None, "blocks": [], "free_text": [], "no_updates": False,
            "first_name_line": None}
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    in_boiler = True
    blk = None
    prev = None
    for ln in lines:
        if ln.startswith("# No updates"):
            info["no_updates"] = True
        if ln.startswith("1 ") and info["first_name_line"] is None and prev is not None \
                and prev.strip() and not prev.startswith("#"):
            info["first_name_line"] = prev.strip()
        prev = ln
        if not ln.startswith("#"):
            continue
        if ln.startswith("# Ephem range:"):
            if info["ephem_range"] is None:
                try:
                    a, b, c = map(float, ln[14:].split()[:3])
                    info["ephem_range"] = (a, b, c)
                except ValueError:
                    pass
            in_boiler = False
            continue
        if in_boiler:
            continue
        m = re.match(r"#\s*Orbital elements:\s*(.*)", ln)
        if m:
            blk = {"name": m.group(1).strip(), "lines": []}
            info["blocks"].append(blk)
            continue
        if blk is not None and re.match(r"#\s*(Peri|Epoch|M |n |a|e |P|q |Q|H |A[123]|dx|dz|"
                                        r"\d+ (of \d+ )?observations|From \d+|Perihelion)", ln):
            blk["lines"].append(ln)
            continue
        if _MACHINE.match(ln) or ln.strip() in ("#",):
            if ln.startswith("# 1 NoradU"):
                blk = None
            continue
        if blk is not None and re.match(r"#\s+(Node|Incl|Peri)", ln):
            blk["lines"].append(ln)
            continue
        t = ln[1:].strip()
        if t:
            info["free_text"].append(t)
    for b in info["blocks"]:
        b.update(parse_block(b["lines"]))
        del b["lines"]
    return info


def _num(rx, text):
    m = re.search(rx, text)
    return float(m.group(1)) if m else None


def parse_block(lines: list[str]) -> dict:
    txt = "\n".join(lines)
    d = {}
    d["epoch"] = (re.search(r"Epoch\s+(\d{4} \w{3}\s+[\d.]+)", txt) or [None, None])[1]
    d["a"] = _num(r"(?m)^#\s*a\s*([\d.]+)", txt)
    d["e"] = _num(r"(?m)^#\s*e\s+([\d.]+)", txt)
    d["q"] = _num(r"(?m)^#\s*q\s+([\d.]+)", txt)
    d["Q"] = _num(r"\bQ\s+([\d.]+)", txt)
    d["incl"] = _num(r"Incl\.\s+([\d.]+)", txt)
    d["node"] = _num(r"Node\s+([\d.]+)", txt)
    m = re.search(r"(?m)^#\s*P\s*([\d.]+)([dmy]?)(?:/([\d.]+)([dmy]))?", txt)
    if m:
        vals = [(m.group(1), m.group(2))] + ([(m.group(3), m.group(4))] if m.group(3) else [])
        pd = None
        for v, u in vals:
            if u == "d":
                pd = float(v)
        d["P_days"] = pd
    else:
        d["P_days"] = None
    d["ecliptic"] = "ecliptic" in txt
    d["obs"] = (re.search(r"(?m)^#\s*((?:\d+ of )?\d+ observations.*|From \d+ observations.*)$", txt) or [None, None])[1]
    d["moid"] = _num(r"Earth MOID:\s*([\d.]+)", txt)
    d["amr"] = _num(r"AMR\s+([\d.]+)", txt)
    d["perigee_txt"] = (re.search(r"(?m)^#\s*(Perigee .*|Perihelion .*)$", txt) or [None, None])[1]
    # geocentric (a in km) vs heliocentric (a in AU)
    a = d["a"]
    if a is None and d["q"] is not None and d["e"] is not None and d["e"] < 1:
        a = d["q"] / (1 - d["e"])
    d["helio"] = a is not None and a < 1000.0 or (d["q"] is not None and d["q"] < 100.0)
    return d


@dataclass
class FileData:
    name: str
    records: list
    header: dict
    is_hex: bool
    t_first: datetime | None
    t_last: datetime | None
    cover_end: datetime | None


def load_tle_file(path: Path) -> FileData:
    recs = B.parse_tle_file(path)
    hdr = parse_header(path)
    is_hex = any(r.is_extended_format for r in recs)
    eps = [r.epoch_utc for r in recs if r.epoch_utc]
    t0 = min(eps) if eps else None
    t1 = max(eps) if eps else None
    cover_end = None
    er = hdr["ephem_range"]
    if er:
        cover_end = mjd_to_dt(er[1])
    elif t1:
        cover_end = t1 + timedelta(days=1)
    return FileData(path.name, recs, hdr, is_hex, t0, t1, cover_end)


# --------------------------------------------------------------------------
# get_vect evaluation (cached)
# --------------------------------------------------------------------------

_GV_CACHE_PATH = DERIVED / "get_vect_cache.json"
_gv_cache: dict = {}


def _gv_key(l1, l2):
    return l1.strip() + "|" + l2.strip()


def _run_get_vect(args):
    key, name, l1, l2, jd = args
    fn = GV_TMP / (re.sub(r"[^A-Za-z0-9]", "_", key)[:40] + f"_{abs(hash(key)) % 10**8}.tle")
    fn.write_text(f"{name or 'obj'}\n{l1}\n{l2}\n")
    try:
        p = subprocess.run([str(GET_VECT), str(fn), "-t", f"{jd:.6f}"], capture_output=True,
                           text=True, timeout=20, cwd=str(GV_TMP))
        out = p.stdout.splitlines()
    finally:
        fn.unlink(missing_ok=True)
    for i, ln in enumerate(out):
        if "# Ctr 3 km sec" in ln and i + 1 < len(out):
            r = [float(x) for x in ln.split()[:3]]
            v = [float(x) for x in out[i + 1].split()[:3]]
            return key, (jd, r, v)
    return key, None


def eval_states(reqs: list[tuple]) -> dict:
    """reqs: (name, line1, line2, epoch_dt).  Returns key -> (jd_tt, r, v)."""
    todo = []
    for name, l1, l2, ep in reqs:
        k = _gv_key(l1, l2)
        if k not in _gv_cache:
            todo.append((k, name, l1, l2, jd_tt(ep)))
    if todo:
        GV_TMP.mkdir(parents=True, exist_ok=True)
        with ThreadPoolExecutor(max_workers=8) as ex:
            for k, res in ex.map(_run_get_vect, todo):
                _gv_cache[k] = res
    return {_gv_key(l1, l2): _gv_cache.get(_gv_key(l1, l2)) for _, l1, l2, _ in reqs}


# --------------------------------------------------------------------------
# ephemeris + orbital elements
# --------------------------------------------------------------------------

class Ephem:
    def __init__(self):
        from jplephem.spk import SPK
        self.k = SPK.open(str(DE_KERNEL))

    def _pv(self, a, b, jd):
        p, v = self.k[a, b].compute_and_differentiate(jd)
        return [x for x in p], [x / 86400.0 for x in v]

    def moon_geo(self, jd):
        p1, v1 = self._pv(3, 301, jd)
        p2, v2 = self._pv(3, 399, jd)
        return [a - b for a, b in zip(p1, p2)], [a - b for a, b in zip(v1, v2)]

    def earth_helio(self, jd):
        pe, ve = self._pv(0, 3, jd)
        p3, v3 = self._pv(3, 399, jd)
        ps, vs = self._pv(0, 10, jd)
        return ([a + b - c for a, b, c in zip(pe, p3, ps)],
                [a + b - c for a, b, c in zip(ve, v3, vs)])


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def _norm(a):
    return math.sqrt(_dot(a, a))


def to_ecliptic(v):
    c, s = math.cos(OBLIQ), math.sin(OBLIQ)
    return [v[0], c * v[1] + s * v[2], -s * v[1] + c * v[2]]


def kepler(r, v, mu):
    rr = _norm(r)
    v2 = _dot(v, v)
    h = _cross(r, v)
    hm = _norm(h)
    energy = 0.5 * v2 - mu / rr
    ev = [(v2 - mu / rr) * ri / mu - _dot(r, v) * vi / mu for ri, vi in zip(r, v)]
    e = _norm(ev)
    inc = math.degrees(math.acos(max(-1, min(1, h[2] / hm)))) if hm > 0 else float("nan")
    out = {"e": e, "incl": inc, "energy": energy, "r": rr}
    if abs(energy) > 1e-12:
        a = -mu / (2 * energy)
        out["a"] = a
        out["rp"] = a * (1 - e) if e < 1 else (hm * hm / mu) / (1 + e)
        out["ra"] = a * (1 + e) if e < 1 else None
        out["P_d"] = 2 * math.pi * math.sqrt(a ** 3 / mu) / 86400.0 if energy < 0 else None
    else:
        out.update(a=None, rp=None, ra=None, P_d=None)
    return out


# --------------------------------------------------------------------------
# helpers on ids / names
# --------------------------------------------------------------------------

_COSPAR_RX = re.compile(r"^(\d{4})-(\d{3})([A-Z]{1,3})$")


def intl_to_cospar(intl: str | None) -> str:
    """'24048A' -> '2024-048A' (only for real piece designators)."""
    if not intl:
        return ""
    m = re.fullmatch(r"(\d{2})(\d{3})([A-Z]{1,3})", intl)
    if not m:
        return ""
    yy, num, piece = m.groups()
    if num in ("999", "000"):
        return ""
    year = 1900 + int(yy) if int(yy) >= 57 else 2000 + int(yy)
    return f"{year}-{num}{piece}"


def is_surrogate_norad(n: str | None) -> bool:
    if not n or not n.isdigit():
        return False
    v = int(n)
    return 79000 <= v <= 79999 or 88000 <= v <= 89999 or v == 99999


_TEMP_RX = re.compile(r"^[A-Za-z0-9]{6,8}$")


def is_temp_desig(tok: str) -> bool:
    return bool(_TEMP_RX.match(tok)) and sum(c.isdigit() for c in tok) >= 2 \
        and any(c.isalpha() for c in tok)


def split_name_line(s: str | None) -> list[str]:
    if not s:
        return []
    return [t.strip().strip("'") for t in s.split(" = ") if t.strip()]


def common_name(tokens: list[str]) -> str | None:
    for t in tokens:
        if re.fullmatch(r"\d{4}-\d{3}[A-Z]{0,3}", t) or re.fullmatch(r"NORAD \d+", t) \
                or is_temp_desig(t) or re.fullmatch(r"\d{2}\d{3}[A-Z]+", t):
            continue
        return t
    return None


# --------------------------------------------------------------------------
# knowledge table (regime / status / type / name), with provenance
# --------------------------------------------------------------------------
# Keys: canonical object key "NORAD/INTL" exactly as in tle_list '# ID:'
# (after MERGE), or "file:<name>" for files on disk that no Include uses.
# Values (all optional): name, aliases (list, added), type, regime, status,
# status_date, cospar, norad, note.  Provenance tags inside notes:
# 'BG <sha> <date>' = Bill Gray upstream commit message (cached in
# work/cache/billgray/upstream/commit_messages.txt); 'L<n>' = tle_list.txt
# line; 'hdr' = comment header of the .tle file; 'mem' = from memory
# (Claude), not verified against a file here.

KNOW: dict = {}


def _k(key, name=None, type=None, regime=None, status=None, status_date=None, note=None, **kw):
    d = {k: v for k, v in dict(name=name, type=type, regime=regime, status=status,
                                status_date=status_date, note=note).items() if v is not None}
    d.update(kw)
    KNOW[key] = d


P, R, D, U = "payload", "rocket_body", "debris", "unknown"
# 'lm' = ../cislunar-eye/catalog/sources/lunarmission.tsv, 'hz' = .../horizons.tsv
# (sibling extracts of this project, read for consistency); 'rd' =
# ../rocketdebris/status_verified.tsv (Space-Track DECAY column).
# --- crewed / lunar missions -------------------------------------------------
_k("68538/26069A", "Artemis II Orion Integrity", P, "TRANSLUNAR", "landed", "2026-04-11",
   "crewed free-return lunar flyby; hdr event table: entry interface 04-11 00:04, splashdown "
   "04-11 00:17 UTC off Baja California; BG 7331df2 2026-04-06", aliases=["Artemis II", "Orion"])
_k("54257/22156A", "Artemis I Orion", P, "TRANSLUNAR", "landed", "2022-12-11",
   "hdr 'IMPACT at 11 Dec 2022 17:21:47.67' = splashdown; BG e253fa1", aliases=["Artemis-1 Orion"])
_k("54258/22156B", "Artemis I ICPS", R, "OTHER", "escaped", None,
   "heliocentric after 2022-11/12 lunar flyby (BG samples geo-unbound 160/165); not within 0.05 AU "
   "2024-2030 in BG data (lm est. 0.106 AU); TLEs from A10Re7q obs (BG acb5a13)")
_k("79970/22156Z", "EQUULEUS", P, "CISLUNAR_HEO", "unknown", None,
   "JAXA 6U cubesat from Artemis I aiming at EM-L2 (hdr); BG tracked as A10TGEB=A10T4rQ, then "
   "Horizons -101 ephems (BG 47d6dca, 252a2d8, 1397bd0); Horizons arc ends 2023-12-30 in a "
   "233k-308k km Earth-bound orbit, not an EML2 halo, hence CISLUNAR_HEO; operating status after "
   "2023 unknown (lm says lost 2023-08-24); cospar 2022-156E from Horizons body list + GCAT D01108 "
   "(BG uses intl 22156Z)", cospar="2022-156E")
_k("79976/22156ZZZ", "NEA Scout", P, "OTHER", "lost", None,
   "Artemis I solar-sail cubesat; TLEs from Horizons -182 (BG acb5a13, 5580cca); never heard from "
   "after deployment (mem); heliocentric after 2022-12 lunar flyby; 'no updates' BG 9d67013; cospar "
   "2022-156H from Horizons body list + GCAT D01111 (BG uses intl 22156ZZZ)", cospar="2022-156H")
_k("54696/22168A", "Hakuto-R M1 lander", P, "LUNAR_ORBIT", "lunar_impact", "2023-04-25",
   "TLEs end at lunar-orbit arrival 2023-03-21; crashed in landing attempt 2023-04-25 (mem; lm "
   "agrees); BG 825ba36 (=C3L0GC1), 3d904d4", aliases=["Hakuto-R"])
_k("54697/22168B", "Lunar Flashlight", P, "TRANSLUNAR", "escaped", None,
   "ballistic lunar transfer toward NRHO (hdr); thrusters failed, never reached lunar orbit and "
   "went heliocentric (lm; hz -164 final traj to 2023-06-27); BG TLEs only to 2023-03-21")
_k("54698/22168C", "Hakuto-R M1 / Lunar Flashlight Falcon 9 stage 2", R, "OTHER", "escaped", None,
   "file hdr 'going to heliocentric orbit'; BG 39f7bae, 958fe4f; lm rough GCAT estimate ~0.044 AU "
   "2024-2030 (not in BG data)", aliases=["Falcon 9 R/B"])
_k("62716/25010A", "Blue Ghost M1", P, "LUNAR_ORBIT", "landed", "2025-03-02",
   "landed Mare Crisium 2025-03-02 (mem; rd DECAY 2025-03-02); TLEs to lunar-orbit insertion "
   "(BG 052d4d1); was A11hSI4 (BG 85e2dfe)", aliases=["BGM-1", "A11hSI4"])
_k("62717/25010B", "Hakuto-R M2 Resilience", P, "LUNAR_ORBIT", "lunar_impact", "2025-06-05",
   "SGP4-only TLEs to lunar arrival; maneuvers 2025-02 (BG 808d847, 572807b); crashed in landing "
   "attempt 2025-06-05 (mem; lm agrees)", aliases=["Hakuto-R", "Resilience"])
_k("62718/25010C", "Blue Ghost/Hakuto-R MPC upper section", D, "CISLUNAR_HEO", "decayed", "2025-03-15",
   "multi-payload carrier upper section (BG b5323a1); hdr 're-enters 2025 Mar 15 at 12:08 UTC over "
   "Argentina/Chile'", aliases=["MPC Upper Section"])
_k("62719/25010D", "Blue Ghost/Hakuto-R Falcon 9 stage 2", R, "CISLUNAR_HEO", "lunar_impact", "2026-08-05",
   "was A11hSI1 (BG 318fb92); BG e3d1b79 2025-12-20 'Should hit west side of the moon on 2026 Aug 05'; "
   "Range ends 2026-08-05; hz -162719 also ends in lunar impact 2026-08-05. LOCAL: tle_list Includes "
   "25010d26v6.tle (untracked local refit, eph2tle run 2026-08-02, no orbit block) instead of "
   "upstream 25010d26.tle (BG fit 2025-12-21, 44 of 74 obs); v2-v5 also local",
   aliases=["Falcon 9 R/B", "A11hSI1"])
_k("58963/24030A", "IM-1 Odysseus", P, "LUNAR_ORBIT", "landed", "2024-02-22",
   "landed 2024-02-22 (mem; rd DECAY 2024-02-22); BG 3d3aee3", aliases=["Nova-C", "Odysseus"])
_k("58964/24030B", "IM-1 Falcon 9 stage 2", R, "EARTH_NEAR_HELIO", "returning", None,
   "heliocentric after 2024-02 lunar flyby; split into pre-/post-lunar-encounter files (BG c048a94); "
   "=2024 JV8=CALF532 (BG e6158b8)", aliases=["2024 JV8", "CALF532", "Falcon 9 R/B"])
_k("58751/24006A", "Peregrine M1", P, "CISLUNAR_HEO", "decayed", "2024-01-18",
   "propellant leak, never left Earth orbit; hdr 'IMPACT at 18 Jan 2024 20:22:34' (re-entry, S Pacific); "
   "non-grav fits (BG 40427e4); tle_list ID 58751 24006A but file label '2024-000A = NORAD 99999'",
   aliases=["Peregrine Lander"])
_k("58752/24006B", "Peregrine Vulcan Centaur", R, "EARTH_NEAR_HELIO", "escaped", None,
   "hdr 'headed for heliocentric orbit'; BG e9f3856, 5e9f4e3")
_k("57320/23098A", "Chandrayaan-3 (Vikram lander; integrated stack in file)", P, "TRANSLUNAR", "landed",
   "2023-08-23", "chandra3.tle = Horizons -158 stack 2023-07-15..08-04 (Earth phase); BG a472243: 2023-098A "
   "now = lander, PM re-designated 2023-098C; landed 2023-08-23 (mem; lm)", aliases=["Chandrayaan-3"])
_k("57770/23098C", "Chandrayaan-3 propulsion module", P, "CISLUNAR_HEO", "unknown", None,
   "back in HEO since 2023-11; lunar flybys 2025-11-06/11 assumed unpowered (BG 475704b); 'leaks fuel', "
   "unseen mid-Aug..Oct 2025 (BG 1565baa)", aliases=["Chandrayaan-3 PM"])
_k("57803/23137D", "SLIM", P, "LUNAR_ORBIT", "landed", "2024-01-19",
   "hdr warns ephems may be bogus (Horizons -240 cut at 2023-12-25 lunar-orbit insertion); "
   "landed 2024-01-19 (mem; lm 2024-01-31 = end of hz arc)")
_k("57804/23137E", "SLIM/XRISM H-IIA F47 stage 2", R, "CISLUNAR_HEO", "decayed", "2025-01-03",
   "apogee >= 100000 km at some TLEs, then drag-lowered; = A10Zv9c = A111B8V (BG 4d5275a); BG 9aea63a "
   "'re-entry at the end of 2024'; rd DECAY 2025-01-03", aliases=["H-2A F47 Stage 2", "A10Zv9c", "A111B8V"])
_k("44441/19042A", "Chandrayaan-2 orbiter", P, "LUNAR_ORBIT", "active", None,
   "19042a.tle = Horizons -152 Earth-orbit/transfer phase 2019-07-22..08-17 (BG 9de7a15); hdr 'No updates "
   "(because it's in lunar orbit now)'; orbiter active (hz -152 ephemeris to 2026-11)", aliases=["Chandrayaan-2"])
_k("43470/18045A", "Queqiao", P, "EM_L2", "active", None,
   "TLEs cover launch week only (2018-05-21..25); EM-L2 halo relay since 2018-06 (lm)", aliases=["Queqiao-1"])
_k("43473/18045D", "Queqiao CZ-4C stage 3", R, "CISLUNAR_HEO", "decayed", "2018-09-25",
   "hdr 'will re-enter the earth's atmosphere on 25 Sep'; rd DECAY 2018-09-25")
_k("59276/24051A", "Queqiao-2", P, "LUNAR_ORBIT", "active", "2024-03-24",
   "TLEs cover transfer 2024-03-20..24 only; 24-h elliptical frozen lunar orbit since 2024-03-24 (lm, mem)")
_k("59277/24051B", "Queqiao-2 CZ-8 Y3 stage 2", R, "TRANSLUNAR", "decayed", "2024-04-24",
   "hdr 'will re-enter over South Atlantic on 2024 Apr 24'; BG 143fe06", aliases=["CZ-8 Y3 Stage 2"])
_k("79928/24999ZZR", "QQ-2C (Queqiao-2 launch object C)", U, "TRANSLUNAR", "unknown", None,
   "hdr orbit from 19 obs 2024-03-21 (11.4 h), a=207924 km; probably the Tiandu-1/2 stack (2024-051C) "
   "before lunar-orbit insertion (inference, not stated by BG); get_vect of qq2c.tle vs 24051a.tle "
   "(Queqiao-2) agree to 0.4-1.3k km over 2024-03-21..23 (this extract)")
_k("59278/24051C", "Tiandu-1", P, "CISLUNAR_HEO", "unknown", None,
   "= A11npTg = A11nvvx = A11mpfO (BG 41da739); left lunar orbit 2025 (lm); BG 4aed76b 2025-11-30 "
   "doubts solution, possible maneuver", aliases=["A11npTg", "A11nvvx", "A11mpfO"])
_k("59627/24083A", "Chang'e-6 orbiter", P, "CISLUNAR_HEO", "active", None,
   "post-sample-return extended mission; maneuvers 2025-04-02 (~1 m/s) and 2025-11-08 (BG 69ff477, "
   "925c1a4); BG b0d2f94: 'if it doesn't [maneuver], it hits the earth'", aliases=["Chang'e 6"])
_k("59228/24048A", "DRO-A", P, "EM_DRO", "active", None,
   "Earth-Moon DRO; designations swapped with DRO-B on 2025-04-25 (BG 0d86994); was SSA02 = 048ZZY "
   "(BG f208341)", aliases=["SSA02"])
_k("60058/24048D", "DRO-B", P, "EM_DRO", "active", None,
   "Earth-Moon DRO; maneuvers 2025-07-21 and 2025-09-16 (~17 m/s) (BG 9006b7a, f1c6121); 'returning "
   "to the moon' (BG 9519e87, via 2025 US6 = C4557E1)", aliases=["2025 US6", "C4557E1"])
_k("60326/24048E", "DRO-A/B Yuanzheng-1S Y11 upper stage", R, "CISLUNAR_HEO", "derelict", None,
   "= A116CgX = A116I21 (BG 240f5fb: Space-Track designated it 2024-048E); BG 5848115 gave it a made-up "
   "intl desig 24999ZZO/NORAD 79925 for 2024; hit upper atmosphere Feb/Mar 2025 (BG 5b965a7); P~2.1 d "
   "but apogee > 100000 km", aliases=["Yuanzheng-1S Y11", "A116CgX", "A116I21", "2024-048Z"])
_k("47097/20087A", "Chang'e 5 orbiter", P, "EARTH_NEAR_HELIO", "unknown", None,
   "extended mission SE-L1 -> DRO -> heliocentric; BG 6c45c95 2024-12-22 'now in heliocentric orbits'; "
   "file was the 'unknown cislunar' object (BG 47709b6)", aliases=["Chang'e 5", "Change5"])
_k("47098/20087B", "Chang'e 5 CZ-5 Y5 stage 2", R, "CISLUNAR_HEO", "decayed", "2020-11-30",
   "hdr 'No updates (re-entering)'; rd/lm decay 2020-11-30")
_k("43846/18103B", "Chang'e 4 CZ-3B Y30 stage 3", R, "EARTH_NEAR_HELIO", "returning", None,
   "~2018-2024 cislunar HEO, heliocentric since 2024 (BG 6c45c95; hdr 18103b24 'object is now "
   "heliocentric')", aliases=["Chang'e 4 booster"])
_k("39459/13070B", "Chang'e 3 CZ-3B Y23 stage 3", R, "CISLUNAR_HEO", "derelict", None,
   "SGP4-only TLEs in older files; = UX29E26", aliases=["Chang'e 3 booster", "CZ-3B Y23 Stage 3"])
_k("37175/10050B", "Chang'e 2 CZ-3C stage 3", R, "OTHER", "returning", None,
   "cislunar HEO 2010-2021 (P~150 d), heliocentric since 2021 (hdr 10050b21 'now in heliocentric "
   "orbit', BG 308f91f); = RV223A9; 2019-06-12 lunar pass (BG d5cb590); OTHER = heliocentric now",
   aliases=["Chang'e 2 booster"])
_k("40391/15007B", "Chang'e 5-T1 CZ-3C stage 3 (WE0913A)", R, "CISLUNAR_HEO", "lunar_impact", "2022-03-04",
   "BG 1cb074d 2022-02-22: files labelled '2015-007B = DSCOVR object = NORAD 40391' are actually "
   "2014-065B; hdr 15007b22 'it'll hit the moon on 2022 Mar 4'; real NORAD 40284 (Space-Track via "
   "../lunarmission/spacetrack_status.tsv, DECAY 2022-03-04 LUNAR IMPACT); tle_list still uses ID 40391 15007B",
   cospar="2014-065B", norad="40284",
   aliases=["2015-007B (BG label)", "NORAD 40391 (BG label)", "CZ-3C Y12 Stage 3"])
_k("file:che4.tle", "Chang'e 4 lander/rover", P, "LUNAR_ORBIT", "landed", "2019-01-03",
   "Earth-departure arc 2018-12-07..11 (hdr perilune 2018-12-11.6); BG surrogate NORAD 89988; real "
   "2018-103A = NORAD 43845 (../lunarmission/spacetrack_status.tsv); landed far side 2019-01-03 (mem)",
   cospar="2018-103A", norad="43845", aliases=["Change4", "NORAD 89988 (BG surrogate)"])
_k("57601/23118B", "Luna-25 Fregat No. 122-10", R, "CISLUNAR_HEO", "decayed", "2024-02-26",
   "BG 311a637: re-entry 2024-02-26 18:43:33 UTC at W167.80 S12.14 (near Fiji); rd DECAY 2024-02-26",
   aliases=["Fregat No. 122-10"])
_k("52916/22070C", "CAPSTONE Lunar Photon", R, "CISLUNAR_HEO", "escaped", None,
   "Rocket Lab Photon kick stage; Earth orbit to 1.6e6 km apogee 2022-2023; hdr 'headed to "
   "heliocentric orbit'; BG 07d3eae, ad6bf63", aliases=["Photon"])
_k("63101/25038C", "Lunar Trailblazer", P, "EARTH_NEAR_HELIO", "escaped", None,
   "contact lost after launch; BG 0d407bc 2025-08-09 'we've lost it'; hdr 'sadly, this one's lost'; "
   "heliocentric (hz -242 to 2051)")
_k("63102/25038D", "Odin", P, "EARTH_NEAR_HELIO", "escaped", None,
   "AstroForge; hdr: no contact after launch; TLEs from Horizons -10001001 (25038d.tle: Horizons "
   "revision 2025-02-28; 25038b.tle: revision 2025-03-06, same trajectory, get_vect agrees to 0.2 km on "
   "2025-03-10). 25038b.tle is labelled 'Odin = 2025-038B = NORAD 63100' with tle_list ID 63100 25038B "
   "(BG b1c4799 '2025-038B = Odin'), but Space-Track/GCAT 63100 = 2025-038B = Chimera GEO-1 "
   "(../lunarmission/gcat_lunar_objects.tsv), so both files are merged here as Odin = 63102; BG e90b81c",
   aliases=["Brokkr-2 Odin", "2025-038B (BG label)", "NORAD 63100 (BG label)"],
   drop_tokens=["2025-038B"])
_k("63103/25038E", "IM-2 Falcon 9 stage 2", R, "EARTH_NEAR_HELIO", "returning", None,
   "BG names it Falcon 9-442, GCAT Falcon 9-444; with ESPA (lm); BG d652fbc, 4f695d8",
   aliases=["Falcon 9-442 Stage 2", "Falcon 9 R/B"])
# --- libration / heliocentric observatories --------------------------------------
_k("100532/26199A", "Nancy Grace Roman Space Telescope", P, "SE_L2", "active", "2026-08-30",
   "launch 2026-08-30 11:26 UTC (hdr); hex TLEs fitted to Horizons -211 file RST_EPH_PRED_NOMNVR "
   "(no-maneuver prediction). LOCAL: 26199a.tle and tle_list L9-11 are uncommitted local additions in "
   "~/tles (not in Bill-Gray/tles upstream @4b14176)", aliases=["Roman", "RST", "A0532 (alpha-5)"])
_k("50463/21130A", "JWST", P, "SE_L2", "active", None,
   "TLEs from Horizons -170 (BG 5598362 etc.); no 2026 file in tle_list (last Range ends 2026-01-01)",
   aliases=["James Webb Space Telescope"])
_k("50464/21130B", "JWST Ariane 5 ESC-D stage", R, "OTHER", "returning", None,
   "hdr 'next visible in 2046'", aliases=["JWST Booster"])
_k("57209/23092A", "Euclid", P, "SE_L2", "active", None,
   "TLEs from Horizons -680; no 2026 file in tle_list (last Range ends 2026-01-01)")
_k("57210/23092B", "Euclid Falcon 9 stage 2", R, "OTHER", "escaped", None,
   "heliocentric; 'of historical interest only' (BG 16fdc0b)", aliases=["Falcon 9-237 Stage 2"])
_k("44432/19040A", "Spektr-RG", P, "SE_L2", "active", None,
   "SE-L2 halo; station-keeping 2025-05-17 (BG 4739529)", aliases=["SRG"])
_k("44433/19040B", "Spektr-RG Blok DM-03 stage", R, "OTHER", "returning", None,
   "hdr 'going into an heliocentric orbit'", aliases=["Spektr-RG Booster"])
_k("39479/13074A", "Gaia", P, "EARTH_NEAR_HELIO", "escaped", "2025-03-27",
   "SE-L2 2014-2025, retired and moved to heliocentric orbit (mem: passivated 2025-03-27); BG ff4313e "
   "2025-05-15 'final trajectory from ESA'; hz -139479 min 1.36e6 km 2024-12-30; hdr comment: return "
   "'every 14 years' at >10e6 km (Giorgini)")
_k("26859/01027A", "WMAP", P, "EARTH_NEAR_HELIO", "escaped", "2010-10-20",
   "retired 2010 to heliocentric orbit (lm; date mem); BG TLEs (2 alternative fits wmap.tle/wmap2.tle) "
   "for its 2025 return, BG-sampled min 0.065 AU, hdr Earth MOID 0.0126-0.0138 AU")
_k("43435/18038A", "TESS", P, "EM_OTHER", "active", None,
   "P/2 lunar-resonant orbit; TLEs from Horizons -95 (BG c15febe, 2e3ecb5)")
_k("33401/08051A", "IBEX", P, "CISLUNAR_HEO", "active", None,
   "= TR93D0F; slight maneuvering (BG 97e6255); last obs-based update 2025-03 (BG 3994bc9); "
   "operating status (mem) not confirmed by BG")
# --- heliocentric departures / returners -------------------------------------
_k("49328/21093A", "Lucy", P, "EARTH_NEAR_HELIO", "active", None,
   "TLEs for Earth flybys 2021-10, 2022-10-16, 2024-12-12 (BG ae07f0a); next flyby 2030-12 (hz -49)")
_k("49329/21093B", "Lucy Atlas V Centaur", R, "EARTH_NEAR_HELIO", "returning", None,
   "BG 19b48a3 (2022 recovery); hz -490 min 6.05e6 km on 2024-11-08 (not in BG data)",
   aliases=["Lucy Centaur"])
_k("49497/21110A", "DART", P, "OTHER", "escaped", "2022-09-26",
   "destroyed by design impacting Dimorphos 2022-09-26 (mem); TLEs for 2021-11/2022-02 departure only")
_k("49498/21110B", "DART Falcon 9 stage 2", R, "EARTH_NEAR_HELIO", "escaped", None,
   "2025 return: BG-sampled min 2.55e6 km (hz -149498: 2.36e6 km 2025-05-15); hdr 21110b25 'returned to low "
   "visibility until May 2026'; BG 86826d6: no warning needed (recovered, ephems updated)",
   aliases=["DART Booster"])
_k("41757/16055A", "OSIRIS-APEX", P, "EARTH_NEAR_HELIO", "active", None,
   "TLEs for 2023-09-24 sample-return flyby and 2025-09 Earth flyby; renamed OSIRIS-APEX (hdr)",
   aliases=["OSIRIS-REx"], warn_names=["OSIRIS-APEX"])
_k("45167/20010A", "Solar Orbiter", P, "OTHER", "active", None,
   "TLEs for 2021-11-27 Earth flyby only; hz min 0.15 AU 2024-2030", aliases=["SolO"])
_k("56176/23053A", "JUICE", P, "EARTH_NEAR_HELIO", "active", None,
   "TLEs for 2023 launch and 2024-08 lunar-Earth flyby (BG 38dd407); BG Warn asks for TLEs for the "
   "2026-09 Earth flyby but tle_list has none", warn_names=["JUICE flyby"])
_k("56177/23053B", "JUICE Ariane 5 ESC-D stage", R, "OTHER", "returning", None,
   "hdr 'going to heliocentric orbit'", aliases=["JUICE booster"])
_k("58015/23154C", "Kuiper protoflight Atlas V Centaur", R, "OTHER", "escaped", None,
   "heliocentric a=1.61 AU (hdr); BG ae1f60f", aliases=["KuiperSat Centaur"])
_k("61507/24182A", "Europa Clipper", P, "EARTH_NEAR_HELIO", "active", None,
   "TLEs for 2024-10 departure (Horizons pre-launch ephems, BG 82ebbd0); Earth flyby 2026-12 per BG Warn",
   warn_names=["Europa Clipper flyby"])
_k("61508/24182B", "Europa Clipper Falcon Heavy stage 2", R, "EARTH_NEAR_HELIO", "escaped", None,
   "from Patrick Wiggins (718) + CSS obs (BG 82ebbd0, e597040)", aliases=["Falcon Heavy-011 Stage 2"])
_k("61449/24180A", "Hera", P, "EARTH_NEAR_HELIO", "active", None, "TLEs for 2024-10/11 departure; Horizons -91")
_k("99999/24180Z", "Hera Falcon 9 stage 2", R, "EARTH_NEAR_HELIO", "returning", None,
   "tle_list ID 99999 24180Z (no NORAD; intl made up); hyperbolic geocentric e=1.518 (hdr); BG 026d07f",
   aliases=["Hera upper stage"], warn_names=["Hera upper stage"])
_k("61448/24179A", "Vulcan Cert-2 Centaur", R, "EARTH_NEAR_HELIO", "returning", "2029-02-27",
   "=C43KHJ1 (BG eaad50d); annual returns; BG feb99e0 2025-11-16: 'good chance of re-entering our "
   "atmosphere on 2029 Feb 27'; hz -161448 min 33827 km 2029-02-27", aliases=["Vulcan Centaur test", "C43KHJ1"])
_k("66453/25260C", "ESCAPADE New Glenn GS2", R, "EARTH_NEAR_HELIO", "returning", None,
   "BG 0c5eb5b, 26d27f9; hz -9901495", aliases=["New Glenn GS2 F2", "EscaPADE stage"])
_k("43205/18017A", "Tesla Roadster + Falcon Heavy stage 2", P, "OTHER", "returning", None,
   "TLEs for 2018-02 departure (BG 341449f); hz -143205 min 0.58 AU 2024-2030", aliases=["Tesla Roadster"])
_k("25008/97061A", "Cassini", P, "OTHER", "escaped", "2017-09-15",
   "TLEs for 1999-08-18 Earth flyby only; mission ended in Saturn's atmosphere 2017-09-15 (mem)")
_k("43653/18080A", "BepiColombo", P, "OTHER", "active", None,
   "TLEs for 2020-04-10 Earth flyby only; hz min 0.56 AU 2024-2030")
_k("58049/23157A", "Psyche", P, "OTHER", "active", None, "TLEs for 2023-10 departure (BG 6022ca6)")
_k("58050/23157B", "Psyche Falcon Heavy stage 2", R, "OTHER", "escaped", None,
   "BG 6022ca6", aliases=["Psyche booster"])
_k("45918/20047A", "Emirates Mars Mission Hope", P, "OTHER", "active", None,
   "hdr 'interplanetary, not to be seen again'; Mars orbit", aliases=["EMM (Hope) spacecraft"])
_k("45919/20047B", "Hope H-IIA F42 stage 2", R, "OTHER", "returning", None,
   "hdr 'interplanetary, not to be seen again'", aliases=["EMM (Hope) booster"])
_k("45983/20052A", "Perseverance (Mars 2020)", P, "OTHER", "landed", "2021-02-18",
   "TLEs for 2020-07 departure only (BG c12a38d); landed on Mars 2021-02-18 (mem)", aliases=["Mars2020"])
_k("45984/20052B", "Mars 2020 Atlas V Centaur", R, "OTHER", "escaped", None,
   "hdr 'interplanetary, not to be seen again'")
_k("45935/20049A", "Tianwen-1", P, "OTHER", "active", None, "Mars orbiter (mem); hdr 'heliocentric object'")
_k("45936/20049B", "Tianwen-1 CZ-5 Y4 stage 2", R, "OTHER", "returning", None,
   "BG e2020d5 fixed IDs; hdr 'heliocentric object and we're losing it'", aliases=["TianweB"])
_k("02426/66084B", "Surveyor 2 Centaur (2020 SO)", R, "OTHER", "returning", None,
   "temporary geocentric capture 2020-11..2021-03 (BG 0a18f6c: SGP4 TLEs for a high object); "
   "'until the object returns a few decades hence' (hdr)", aliases=["2020 SO"], warn_names=["2020 SO"])
_k("79961/23999ZZL", "2023 NM", U, "CISLUNAR_HEO", "escaped", None,
   "unidentified temporary capture (BG ab3dc08); geocentric a=721071 km e=0.262 in 2023-07 (hdr, "
   "AMR 0.011 m2/kg); hdr 'No updates (in heliocentric orbit)'", aliases=["C9E9G52"])
# --- long-lived HEO ------------------------------------------------------------
_k("37755/11037A", "Spektr-R", P, "CISLUNAR_HEO", "derelict", "2019-01-10",
   "maneuvering object in early years (hdr); contact lost 2019-01 (mem)", aliases=["RadioAstron"])
_k("46736/11037ND", "Spektr-R Fregat booster", R, "CISLUNAR_HEO", "decayed", "2021-08-09",
   "hdr: re-enters over the Indian Ocean 2021-08-09; BG 71c1fdd; rd DECAY 2021-08-09",
   aliases=["Spektr-R booster", "TA29DCF"])
_k("27540/02048A", "INTEGRAL", P, "CISLUNAR_HEO", "derelict", "2025-02-28",
   "science operations ended 2025-02-28 (mem); Space-Track 'lost' it (BG e3df7ad 2025-11-10)")
_k("25989/99066A", "XMM-Newton", P, "CISLUNAR_HEO", "active", None,
   "BG TLEs only 2016-04..2017-01 (Space-Track covers it)", aliases=["S509474", "S509433"])
_k("25990/99066B", "XMM-Newton Ariane 5 stage", R, "CISLUNAR_HEO", "derelict", None,
   "BG 09ca704: Space-Track doing decently", aliases=["XMM-Newton booster"])
_k("26463/00045A", "Cluster II Rumba (FM5)", P, "CISLUNAR_HEO", "decayed", "2025-10-22",
   "BG 8b4a324: re-entry predicted 2025-10-22 20:28 UT; rd DECAY 2025-10-22")
_k("26464/00045B", "Cluster II Tango", P, "CISLUNAR_HEO", "decayed", None,
   "BG 903fbe6: disposal orbit, re-entry Aug 2026; hz -126464 arc ends at Earth surface 2026-09-01; "
   "exact re-entry date not in BG files", aliases=["Cluster Tango"])
_k("22049/92044A", "Geotail", P, "CISLUNAR_HEO", "derelict", "2022-11-28",
   "mission ended 2022-11-28 (lm)")
_k("13901/83020A", "Astron", P, "CISLUNAR_HEO", "derelict", None, "BG 86012b8, 11ba99b")
_k("20413/83020D", "Astron Proton Blok DM stage", R, "CISLUNAR_HEO", "derelict", None,
   "BG 18978fe, 11ba99b", aliases=["ASTRON booster"])
_k("10370/77093A", "Prognoz-6", P, "CISLUNAR_HEO", "decayed", "2019-01-16",
   "BG e381897 2019-01-09 're-entering in eight days'; rd DECAY 2019-01-16")
_k("32705/77093E", "Prognoz-6 Blok SO-L stage", R, "CISLUNAR_HEO", "derelict", None,
   "BG 890ac16", aliases=["Blok SO-L"])
_k("08748/76023C", "SOLRAD 11A", P, "CISLUNAR_HEO", "derelict", None,
   "= WB31C9F = XC83E0D; BG a73c234", aliases=["SR 11A"])
_k("08753/76023H", "SOLRAD 11 debris", D, "CISLUNAR_HEO", "derelict", None,
   "= S510923 (BG ca5381c)", aliases=["SOLRAD-11 debris"])
_k("06197/72073A", "IMP-7", P, "CISLUNAR_HEO", "derelict", None, "BG ce6f415", aliases=["IMP 7"])
_k("06893/73078A", "IMP-8", P, "CISLUNAR_HEO", "derelict", None, "BG c92f808")
_k("00879/64054A", "OGO 1", P, "CISLUNAR_HEO", "decayed", "2020-08-29",
   "= A107EL8 (BG 2a64085); BG 25d4aec re-entry ~2020-08-30; rd DECAY 2020-08-29")
_k("41929/15019C", "BeiDou Yuanzheng-1 Y1 upper stage", R, "CISLUNAR_HEO", "derelict", None,
   "= S509356 = WJ2AD07; BG e120bdf: Space-Track has it", aliases=["YZ-1 R/B"])
_k("89996/00000AAD", "XL8D89E", U, "CISLUNAR_HEO", "unknown", None,
   "'unidentified distant satellite', '~80-day Earth orbit' (BG 6163a00, ba80cac)")
_k("89983/00000AAQ", "Multijunk (C1AUGE1)", U, "CISLUNAR_HEO", "unknown", None,
   "BG 'Multijunk' = C1AUGE1 = ZTF0Edd with many linked desigs; hdr c1auge1_25 'awaiting survey recovery'")
_k("89997/00000AAC", "WT1190F", U, "CISLUNAR_HEO", "decayed", "2015-11-13",
   "hdr 'IMPACT at 13 Nov 2015 6:20:08.71' = re-entry off Sri Lanka (BG 6498a68)")
# Vela family
for _key, _nm in [("00674/63039A", "Vela 1A"), ("00692/63039C", "Vela 1B"), ("00836/64040A", "Vela 2A"),
                  ("00837/64040B", "Vela 2B"), ("01458/65058A", "Vela 3A"), ("01459/65058B", "Vela 3B"),
                  ("02765/67040A", "Vela 4A"), ("03954/69046D", "Vela 5A"), ("03955/69046E", "Vela 5B")]:
    _k(_key, _nm, P, "CISLUNAR_HEO", "derelict", None, None)
_k("02770/67040F", "Vela 4 Titan 3C Transtage 10", R, "CISLUNAR_HEO", "derelict", None,
   "BG 54d37e1, 4b07989", aliases=["Transtage 10"])
_k("03956/69046F", "Vela 5 Titan 3C Transtage", R, "CISLUNAR_HEO", "derelict", None,
   "BG 4b07989", aliases=["Vela booster"])
_k("60001/69046G", "Vela 5 spin interstage", D, "CISLUNAR_HEO", "derelict", None,
   "BG ca70fd0: Space-Track dropped it 2025-09; get_vect of 69046g25.tle vs ur29394_25.tle (UR29394, "
   "79960) agree to 0.3-1.0k km on 5 dates through 2025 (this extract), so probably the same object "
   "(inference, BG keeps separate files)")
_k("50000/70027C", "Vela 6 Titan 3C Transtage", R, "CISLUNAR_HEO", "derelict", None,
   "= 9O0DC57; 18 SPCS ID as 1970-027C, BG doubts it (BG 31dbf01)", aliases=["Vela 6 booster"])
_k("60000/67040G", "1967-040G hardware (ZTF0EWD)", D, "CISLUNAR_HEO", "derelict", None,
   "= C1A3AQ1 = A10JyDI; official NORAD 60000 / 1967-040G from 2024 (BG 0e5f772)")
_k("79904/67040Z", "1967-040 object (CC4G8Y2)", D, "CISLUNAR_HEO", "unknown", None,
   "BG e9f33cf, c368434; tle_list intl 67040Z is BG's")
_k("79851/69046Z", "1969-046 fragment (C45GF81)", D, "CISLUNAR_HEO", "derelict", None,
   "= CCUAFG2; BG c00ae13 2026-04-23 'reliable TLEs to the end of 2026'")
_k("79960/23999ZZK", "1969-046F fragment (UR29394)", D, "CISLUNAR_HEO", "unknown", None,
   "= C6ZAK42 = C4CJND2 (BG 15ca8df, 892991a); no 2026 TLEs; get_vect of ur29394_25.tle vs 69046g25.tle "
   "(1969-046G = NORAD 60001 Vela spin interstage) agree to 0.3-1.0k km on 5 dates through 2025 (this "
   "extract), so probably the same object (inference, BG keeps separate files)")
_k("89990/00000AAJ", "S509559 (probable Vela)", U, "CISLUNAR_HEO", "unknown", None, "BG fc940cb")
_k("79991/22999ZZR", "C7EW3F2 (probable Vela junk)", D, "CISLUNAR_HEO", "lost", None,
   "= C7PG7A2; BG 2d18e2c")
# ETBO / unidentified
_k("79959/23999ZZJ", "C9EYKK2", U, "CISLUNAR_HEO", "lost", None,
   "ETBO in four-day orbit, maneuvers oddly (BG 33b86ad, 0069ce5); hdr 'barring a near-miraculous recovery'")
_k("79894/25000ZZW", "C44GZR1", U, "CISLUNAR_HEO", "decayed", "2025-03-01",
   "ETBO (BG 16565b2); hdr 'will re-enter at end of TLEs' (Range end)")
_k("79871/25000ZYX", "A11qRqd", U, "CISLUNAR_HEO", "decayed", "2025-10-12",
   "BG 98c9b25: probably not an ETBO; hdr 're-enters mid-October 2025' (Range end 2025-10-12)")
_k("79861/25000ZYM", "CDT9AZ2", U, "CISLUNAR_HEO", "unknown", None, "ETBO with P=4.7 d (BG 65021a4)")
_k("79855/25000ZYF", "ST25K62", U, "CISLUNAR_HEO", "unknown", None, "ETBO in 2.3-day orbit (BG 6201614)")
_k("89948/00000ABZ", "C5VRL32", U, "CISLUNAR_HEO", "unknown", None, "ETBO in 3.3-3.8-day orbit (BG d0fbe31)")
_k("79935/00000ZZT", "XV902A4", U, "CISLUNAR_HEO", "unknown", None, "2016-2017 TLEs only")
_k("79934/00000ZZS", "YS16534", U, "CISLUNAR_HEO", "unknown", None, "= YS18A5C = A104ayW = A104lks (BG e43faa1)")
_k("file:a10bmlz.tle", "A10bMLz", U, "CISLUNAR_HEO", "lost", None,
   "retrograde ETBO, AMR 35 m2/kg (hdr; BG 821d974, ad780e0 'giving up')")
_k("file:c1jh4m2.tle", "C1JH4M2", U, "CISLUNAR_HEO", "unknown", None,
   "BG bde6e77; get_vect of c1jh4m2.tle vs ur29394_20.tle (UR29394 = 1969-046 fragment) agree to "
   "2.6-5.5k km in 2020-01 (this extract): possibly the same object (inference)")
_k("file:zv2f779.tle", "ZV2F779", U, "CISLUNAR_HEO", "unknown", None, "assumed linkage (BG 1d5db64)")
# --- current: SMILE -------------------------------------------------------------
_k("69123/26109A", "SMILE", P, "CISLUNAR_HEO", "active", None,
   "TLEs from NEOCP astrometry (BG 1894722); local ~/tles is one commit behind origin: upstream "
   "4b14176 2026-09-14 updated 26109a.tle (cached in work/cache/billgray/upstream); maneuvers would "
   "invalidate the TLEs (BG 1894722)")

# Objects that appear only in tle_list.txt '# Warn' lines (no TLE file).  'match'
# is a regex on the Warn text.  Horizons IDs are from the cached Horizons
# major-body list work/cache/horizons/mb_list.txt; distances from hz.
WARN_ONLY: list = [
    dict(match=r"^Planck", name="Planck", type=P, cospar="2009-026B", horizons_id="-489",
         regime="OTHER", status="returning", status_date="2027-06-09",
         note="heliocentric since 2013; hz -489 min 1.283e7 km (0.086 AU) on 2027-06-09"),
    dict(match=r"^Herschel", name="Herschel", type=P, cospar="2009-026A", horizons_id="-486",
         regime="EARTH_NEAR_HELIO", status="returning", status_date="2027-10-15",
         note="heliocentric since 2013; hz -486 min 8.29e6 km (0.055 AU) on 2027-10-15"),
    dict(match=r"^Hayabusa 2", name="Hayabusa2", type=P, horizons_id="-37",
         regime="EARTH_NEAR_HELIO", status="active",
         note="Earth flybys 2027-12 and 2028-06 per BG Warn (extended mission, mem); Horizons body list "
              "gives designation 2014-076K for -37, SATCAT 2014-076A (mem), so cospar left empty"),
    dict(match=r"J002E3", name="Apollo 12 S-IVB (J002E3)", type=R, cospar="1969-099B",
         horizons_id="-399120", aliases=["J002E3", "NEOCP J002E3 (Horizons -998)"],
         regime="OTHER", status="returning",
         note="temporary capture 2002-2003 (mem); next return 2032-07 per BG Warn"),
    dict(match=r"Solar Orbiter Centaur", name="Solar Orbiter Atlas V Centaur", type=R,
         regime="OTHER", status="returning",
         note="2020-010B (mem, not in BG files); return 2035-02 per BG Warn"),
    dict(match=r"2009-026B = Herschel/Planck booster", name="Herschel/Planck Ariane 5 ESC-A stage", type=R,
         regime="OTHER", status="returning",
         note="BG writes '2009-026B', but Horizons -489 and SATCAT give 2009-026B = Planck; the stage's "
              "own designation is not in BG files; return 2034-12 per BG Warn"),
    dict(match=r"2010 KQ", name="2010 KQ (RK252A5)", type=U, aliases=["RK252A5"],
         regime="OTHER", status="returning",
         note="suspected artificial (likely a rocket stage; mem); return 2035 per BG Warn"),
    dict(match=r"2018 AV2", name="2018 AV2", type=U, horizons_id="9901489",
         regime="OTHER", status="returning",
         note="return 2037-07 per BG Warn; hz 9901489 min 2.52e8 km 2024-2030"),
    dict(match=r"2024 BY15", name="2024 BY15", type=U, regime="OTHER", status="returning",
         note="BG Warn 2047-Jul-01 'returns in coming year?' (uncertain)"),
]


# --------------------------------------------------------------------------
# phase 2: per-object assembly, selection, classification, TSV
# --------------------------------------------------------------------------

MB_LIST = PROJECT / "work" / "cache" / "horizons" / "mb_list.txt"
COMMITS_TXT = UPSTREAM / "commit_messages.txt"
UPSTREAM_SMILE = UPSTREAM / "26109a.upstream.tle"
HILL_EARTH_KM = 1.5e6
AU_005_KM = 0.05 * AU_KM
WIN0 = datetime(2024, 1, 1, tzinfo=UTC)
WIN1 = datetime(2031, 1, 1, tzinfo=UTC)
R_EARTH = 6378.137


def load_mb_list(path: Path) -> tuple[dict, dict]:
    """Horizons major-body list (cached by the horizons sweep of this
    project).  Returns (id -> (name, designation, aliases), cospar -> id)."""
    by_id, by_cospar = {}, {}
    if not path.exists():
        return by_id, by_cospar
    for ln in path.read_text(errors="replace").splitlines():
        idt = ln[0:9].strip()
        if not re.fullmatch(r"-?\d+", idt):
            continue
        name, des, ali = ln[11:45].strip(), ln[46:58].strip(), ln[59:].strip()
        by_id[idt] = (name, des, ali)
        if re.fullmatch(r"\d{4}-\d{3}[A-Z]{1,3}", des):
            by_cospar.setdefault(des, idt)
    return by_id, by_cospar


def load_commits(path: Path) -> list[tuple[str, str, str]]:
    out = []
    if path.exists():
        for ln in path.read_text(errors="replace").splitlines():
            m = re.match(r"([0-9a-f]{7}) (\d{4}-\d{2}-\d{2}) (.*)", ln)
            if m:
                out.append(m.groups())
    return out


_RX_TARGET = re.compile(r"Target body name:\s*(.+?)\s*\((-?\d+)\)")
_RX_SRC_HZ = re.compile(r"JPL Horizons,?\s*(?:target\s*)?(-\d+)")


def horizons_ids_in_file(path: Path) -> list[tuple[str, str]]:
    """(id, name) pairs a TLE file header says it was built from."""
    out = []
    try:
        txt = path.read_text(errors="replace")
    except OSError:
        return out
    for m in _RX_TARGET.finditer(txt):
        out.append((m.group(2), m.group(1).strip()))
    for m in _RX_SRC_HZ.finditer(txt):
        out.append((m.group(1), ""))
    seen, uniq = set(), []
    for i, n in out:
        if i not in seen:
            seen.add(i)
            uniq.append((i, n))
    return uniq


def file_flags(path: Path) -> dict:
    txt = path.read_text(errors="replace")
    return {"sgp4_only": "SGP4 only" in txt,
            "no_updates": bool(re.search(r"(?m)^# No updates", txt))}


def ephem_txt_note(path: Path) -> str:
    """One-line summary of a find_orb ephemeris file (<stem>_ephem.txt) that
    sits beside a TLE file: header JD/step/count, frame, last line JD and
    the orbit-fit comment lines (observation arc, impact)."""
    lines = path.read_text(errors="replace").splitlines()
    head = lines[0].split() if lines else []
    data = [ln for ln in lines[1:] if ln[:1].isdigit()]
    com = [ln.lstrip("# ").strip() for ln in lines if ln.startswith("#") and
           re.search(r"observations|impact|fit", ln)]
    try:
        jd0, jd1 = float(data[0].split()[0]), float(data[-1].split()[0])
        span = f"{iso(datetime(2000, 1, 1, 12, tzinfo=UTC) + timedelta(days=jd0 - 2451545.0), False)}.." \
               f"{iso(datetime(2000, 1, 1, 12, tzinfo=UTC) + timedelta(days=jd1 - 2451545.0), False)}"
    except (IndexError, ValueError):
        span = "?"
    frame = " ".join(head[4:]) if len(head) > 4 else ""
    return (f"find_orb ephemeris {path.name} beside it ({len(data)} rows {span}, {frame}; eph2tle input)"
            + (": '" + "; ".join(com) + "'" if com else ""))


def fmt_num(v, nd):
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return ""
    return f"{v:.{nd}f}"


def tle_ids(rec) -> tuple[str, str]:
    """(NORAD field, intl field) as written in TLE line 1."""
    return rec.line1[2:7].strip(), rec.line1[9:17].strip()


def norad_out(n: str | None) -> str:
    """NORAD for the TSV: alpha-5 kept as given in tle_list (e.g. 100532),
    leading zeros dropped, 99999 (Bill Gray's 'no number') -> ''."""
    if not n or not n.isdigit() or int(n) == 99999:
        return ""
    return str(int(n))


def elements_for(eph: Ephem, jd: float, r: list, v: list) -> dict:
    """Frame choice + osculating elements for a geocentric J2000-equatorial
    state from get_vect.  Moon frame if inside the lunar sphere of influence
    and selenocentrically bound with apolune inside that sphere (so a
    weakly-bound approach arc with a 1e5-1e6 km 'apolune' gets geocentric
    elements instead); Earth if geocentrically bound or inside
    ~1.5e6 km (hyperbolic elements then); otherwise heliocentric (J2000
    ecliptic, Sun GM)."""
    rm, vm = eph.moon_geo(jd)
    rs = [a - b for a, b in zip(r, rm)]
    vs = [a - b for a, b in zip(v, vm)]
    kg = kepler(r, v, MU_E)
    ks = kepler(rs, vs, MU_M)
    if _norm(rs) < R_SOI_MOON and ks["energy"] < 0 and (ks.get("ra") or 0) < R_SOI_MOON:
        return {"frame": "moon", **ks, "r_geo": _norm(r), "r_moon": _norm(rs)}
    if kg["energy"] < 0 or _norm(r) < HILL_EARTH_KM:
        return {"frame": "earth", **kg, "r_geo": _norm(r), "r_moon": _norm(rs)}
    re_h, ve_h = eph.earth_helio(jd)
    rh = to_ecliptic([a + b for a, b in zip(r, re_h)])
    vh = to_ecliptic([a + b for a, b in zip(v, ve_h)])
    kh = kepler(rh, vh, MU_S)
    return {"frame": "sun", **kh, "r_geo": _norm(r), "r_moon": _norm(rs)}


def obj_type_guess(names: list[str]) -> str:
    s = " ".join(names).lower()
    if re.search(r"booster|stage|centaur|r/b|\bicps\b|fregat|blok|transtage|"
                 r"yuanzheng|cz-\d|new glenn|falcon|kick motor", s):
        return "rocket_body"
    if re.search(r"debris|fragment|\bbit\b|\bobj\b|interstage|junk", s):
        return "debris"
    if re.search(r"etbo|unid|temp capture", s):
        return "unknown"
    return "unknown"


def phase2(ctx: dict) -> int:
    tl: TleList = ctx["tl"]
    files: dict = ctx["files"]
    objs: list = ctx["objs"]
    stats_by_file: dict = ctx["stats_by_file"]
    eph: Ephem = ctx["eph"]
    unlisted: list = ctx["unlisted"]
    merge: dict = ctx["merge"]

    mb_by_id, mb_by_cospar = load_mb_list(MB_LIST)
    commits = load_commits(COMMITS_TXT)
    MERGE_KEYS = merge
    # upstream (GitHub Bill-Gray/tles origin) tle_list.txt, cached; differences
    # from the local working copy are reported per object
    upstream_only, local_only = [], []
    up_path = UPSTREAM / "tle_list.upstream.txt"
    if up_path.exists():
        utl = parse_tle_list(up_path)
        up_inc = {e.include for e in utl.entries}
        loc_inc = {e.include for e in tl.entries}
        upstream_only = [e for e in utl.entries if not e.old and e.include not in loc_inc]
        local_only = sorted(loc_inc - up_inc)
    up_only_files = {e.include for e in upstream_only}

    def last_commit(toks):
        best = None
        for sha, date, msg in commits:
            for t in toks:
                if t and re.search(r"(?<![A-Za-z0-9])" + re.escape(t) + r"(?![A-Za-z0-9])", msg):
                    if best is None or date > best[1]:
                        best = (sha, date)
                    break
        return best
    try:
        orbits = json.loads(ORBITS_JSON.read_text())["satellites"]
    except (OSError, KeyError, ValueError):
        orbits = []
    orbits_by_file = defaultdict(list)
    for s in orbits:
        orbits_by_file[s.get("include_file")].append(s)

    # ---- attach unlisted files: to an existing object (notes only) or as
    #      an orphan object of its own --------------------------------------
    key_of = {o["key"]: o for o in objs}
    norad_index = defaultdict(list)
    for o in objs:
        if o["key"][0] and o["key"][0] not in ("99999",):
            norad_index[o["key"][0]].append(o)
    orphans = []
    for f in unlisted:
        fd = files.get(f)
        if not fd or not fd.records:
            continue
        n, i = tle_ids(fd.records[0])
        k = merge.get((n, i), (n, i))
        tgt = key_of.get(k)
        if tgt is None and n in norad_index and not is_surrogate_norad(n):
            tgt = norad_index[n][0]
        if tgt is None and f.startswith("25010d26"):
            tgt = key_of.get(("62719", "25010D"))
        if tgt is None and f == "a11acbv.tle":           # SSA01 or SSA02 (L612-613)
            for kk in (("59228", "24048A"), ("60058", "24048D")):
                key_of[kk].setdefault("unlisted", []).append(f)
            continue
        if tgt is not None:
            if f not in up_only_files:
                tgt.setdefault("unlisted", []).append(f)
        else:
            orphans.append({"key": ("file", f), "entries": [], "dup_lines": [], "orphan_file": f})

    # ---- reference TLE per object (latest TLE inside a Range) -------------
    def entry_span(e: Entry):
        fd = files.get(e.include)
        s, t = e.range_start, e.range_end
        if s is None and fd is not None:
            s = fd.t_first
        if t is None and fd is not None:
            t = fd.cover_end
        return s, t

    def pick_ref(o):
        if o.get("orphan_file"):
            fd = files[o["orphan_file"]]
            recs = [r for r in fd.records if r.epoch_utc]
            cov = [r for r in recs if r.epoch_utc <= NOW]
            if fd.t_first and fd.cover_end and fd.t_first <= NOW <= fd.cover_end and cov:
                return o["orphan_file"], max(cov, key=lambda r: r.epoch_utc)
            return o["orphan_file"], max(recs, key=lambda r: r.epoch_utc)
        ents = [e for e in o["entries"] if not e.missing and e.include in files]
        cov = [e for e in ents if (lambda s, t: s and t and s <= NOW <= t)(*entry_span(e))]
        if cov:
            e = cov[-1]
            recs = [r for r in files[e.include].records if r.epoch_utc and r.epoch_utc <= NOW]
            if recs:
                return e.include, max(recs, key=lambda r: r.epoch_utc)
        if not ents:
            return None, None
        e = max(ents, key=lambda e: (entry_span(e)[1] or datetime(1900, 1, 1, tzinfo=UTC)))
        s, t = entry_span(e)
        recs = [r for r in files[e.include].records if r.epoch_utc]
        inside = [r for r in recs if (s is None or r.epoch_utc >= s - timedelta(days=1.5))
                  and (t is None or r.epoch_utc <= t)]
        pool = inside or recs
        return e.include, max(pool, key=lambda r: r.epoch_utc)

    all_objs = objs + orphans
    refs = {}
    for o in all_objs:
        refs[o["key"]] = pick_ref(o)
    if ("69123", "26109A") in refs and "upstream:26109a.tle" in files:
        _ur = [r for r in files["upstream:26109a.tle"].records if r.epoch_utc and r.epoch_utc <= NOW]
        if _ur:
            refs[("69123", "26109A")] = ("upstream 26109a.tle @4b14176", max(_ur, key=lambda r: r.epoch_utc))
    reqs = [(r.name, r.line1, r.line2, r.epoch_utc) for _, r in refs.values() if r is not None]
    eval_states(reqs)
    _GV_CACHE_PATH.write_text(json.dumps(_gv_cache))

    warns = tl.warns
    rows, rejected, review = [], [], []
    for o in all_objs:
        key = o["key"]
        if o.get("orphan_file"):
            fnames = [o["orphan_file"]]
            ents = []
        else:
            ents = o["entries"]
            fnames = [e.include for e in ents if not e.missing and e.include in files]
        fds = [files[f] for f in fnames]
        if not fds:
            rejected.append((key, "no TLE file on disk", [e.include for e in ents]))
            continue
        recs_all = [r for fd in fds for r in fd.records]
        samples_all = [dict(s, f=f) for f in fnames for s in stats_by_file.get(f, [])]
        # a sample state inside the Earth is a failed eph2tle fit (e.g. slim.tle
        # 2023-09-26: 'Worst residual: 17687.87 km', state ~ (0, 3, 0)); ignore it
        samples = [s for s in samples_all if s["rg"] >= R_EARTH]
        bogus = [s for s in samples_all if s["rg"] < R_EARTH]
        is_hex = any(fd.is_hex for fd in fds)
        p_list = [s["Pg"] for s in samples if s.get("Pg")]
        q_list = [s["rag"] for s in samples if s.get("rag")]
        for r in recs_all:
            if not r.is_extended_format and r.period_day:
                p_list.append(r.period_day)
                q_list.append(r.apogee_km + B.EARTH_RADIUS)
        maxP = max(p_list) if p_list else 0.0
        maxQ = max(q_list) if q_list else 0.0
        max_rg = max((s["rg"] for s in samples), default=0.0)
        keep = maxP > PERIOD_MIN_D or is_hex or maxQ >= APOGEE_MIN_KM or max_rg >= APOGEE_MIN_KM
        kstr = (f"file:{o['orphan_file']}" if o.get("orphan_file") else f"{key[0]}/{key[1]}")
        K = KNOW.get(kstr, {})
        if not keep and not K.get("force_keep"):
            rejected.append((key, f"P<={PERIOD_MIN_D} d, Q<1e5 km, classical", fnames))
            continue

        # ---- identifiers & names ----------------------------------------
        tokens = []
        for fd in reversed(fds):
            for s in [fd.header.get("first_name_line")] + [b.get("name") for b in fd.header["blocks"]] \
                    + [r.name for r in fd.records[:1]]:
                for t in split_name_line(s):
                    if t and t not in tokens:
                        tokens.append(t)
        if o.get("orphan_file"):
            n_raw, i_raw = tle_ids(fds[0].records[0])
        else:
            n_raw, i_raw = key
        norad = K.get("norad", norad_out(n_raw))
        surrogate = is_surrogate_norad(n_raw) and int(n_raw) != 99999
        cospar = K.get("cospar")
        if cospar is None:
            cospar = "" if (surrogate or n_raw == "99999" and not K.get("cospar")) else intl_to_cospar(i_raw)
            if surrogate:
                cospar = ""
        name = K.get("name") or common_name(tokens) or (tokens[0] if tokens else fnames[-1])
        aliases = []
        for t in tokens + K.get("aliases", []):
            t = t.strip()
            if not t or t == name or t == cospar or re.fullmatch(r"NORAD \d+", t) \
                    or t in aliases or t in K.get("drop_tokens", []):
                continue
            aliases.append(t)

        # ---- Horizons ids from file headers (listed by the source) --------
        hz = []
        for f in fnames + o.get("unlisted", []):
            for hid, hname in horizons_ids_in_file(TLE_ROOT / f):
                if hid not in [h for h, _ in hz]:
                    hz.append((hid, hname))
        horizons_id = K.get("horizons_id", hz[0][0] if hz else "")
        hz_note = ""
        if hz:
            mb = mb_by_id.get(horizons_id)
            hz_note = (f"Horizons {horizons_id} from TLE-file header" +
                       (f" (in Horizons body list as '{mb[0]}' {mb[1]})" if mb else
                        " (not in cached Horizons body list)"))
        elif cospar and cospar in mb_by_cospar and not K.get("no_mb"):
            horizons_id = mb_by_cospar[cospar]
            hz_note = (f"Horizons {horizons_id} = '{mb_by_id[horizons_id][0]}' matched by COSPAR in "
                       f"cached Horizons body list (not from BG files)")
        for hid, hname in hz:
            mb = mb_by_id.get(hid)
            if mb and mb[1] and mb[1] != cospar and mb[1] not in aliases:
                aliases.append(f"{mb[1]} (Horizons desig)")

        # ---- reference state & elements --------------------------------
        ref_file, ref = refs.get(key, (None, None))
        st = _gv_cache.get(_gv_key(ref.line1, ref.line2)) if ref is not None else None
        el = elements_for(eph, st[0], st[1], st[2]) if st else None

        # ---- coverage -----------------------------------------------------
        srcs, covers, stale, no_range, frac = [], False, [], [], []
        span_ends = []
        if o.get("orphan_file"):
            fd = fds[0]
            srcs.append(f"bgtle:{o['orphan_file']}[{dday(fd.t_first)}..{dday(fd.cover_end)}]")
            covers = bool(fd.t_first and fd.cover_end and fd.t_first <= NOW <= fd.cover_end)
            span_ends.append(fd.cover_end)
        for e in ents:
            if e.missing:
                continue
            s, t = entry_span(e)
            if e.range_start is None:
                no_range.append(e.include)
            srcs.append(f"bgtle:{e.include}[{dday(s)}..{dday(t)}]")
            if (s and s.hour) or (t and t.hour):
                frac.append(f"{e.include} Range {e.range_raw or (short_date(s) + ' ' + short_date(t))}")
            span_ends.append(t)
            if s and t and s <= NOW <= t:
                covers = True
        if key == ("69123", "26109A") and UPSTREAM_SMILE.exists():
            srcs.append("bgtle:github.com/Bill-Gray/tles@4b14176:26109a.tle[2026-06-22..2027-01-01]")
        up_only = [u for u in upstream_only if MERGE_KEYS.get((u.norad, u.intl), (u.norad, u.intl)) == key]
        for u in up_only:
            fdu = files.get(u.include)
            if fdu is not None:
                srcs.append(f"bgtle:{u.include}[{dday(u.range_start or fdu.t_first)}..{dday(u.range_end or fdu.cover_end)}]")
                if u.range_start and u.range_end and u.range_start <= NOW <= u.range_end:
                    covers = True
        last_end = max((t for t in span_ends if t), default=None)
        if not covers and last_end and NOW - timedelta(days=STALE_DAYS) <= last_end < NOW:
            stale.append(last_end)

        # ---- dynamics summary from samples --------------------------------
        def tdt(s):
            return datetime.fromisoformat(s["t"])
        lun = [s for s in samples if s["rm"] < R_SOI_MOON and s["es"] < 0]
        unb = [s for s in samples if s["eg"] >= 0]
        near_win = [s for s in samples if WIN0 <= tdt(s) < WIN1 and s["rg"] < AU_005_KM]
        rg_min = min((s["rg"] for s in samples), default=None)
        rm_min = min((s["rm"] for s in samples), default=None)
        t_first = min((fd.t_first for fd in fds if fd.t_first), default=None)
        t_last = max((fd.t_last for fd in fds if fd.t_last), default=None)

        # ---- regime (automatic, overridden by KNOW) -------------------------
        auto = "OTHER"
        if el:
            if el["frame"] == "moon":
                auto = "LUNAR_ORBIT"
            elif el["frame"] == "earth" and el["energy"] < 0:
                auto = "CISLUNAR_HEO" if (el.get("ra") or 0) >= APOGEE_MIN_KM else \
                       ("HEO" if (el.get("P_d") or 0) > PERIOD_MIN_D else "OTHER")
            elif el["frame"] in ("sun", "earth"):
                auto = "EARTH_NEAR_HELIO" if near_win else "OTHER"
        regime = K.get("regime", auto)
        otype = K.get("type", obj_type_guess([name] + aliases))
        status = K.get("status", "unknown")
        status_date = K.get("status_date", "")
        # latest Bill Gray commit mentioning the object (cospar / BG labels /
        # temp designations), as last-confirmation date for ongoing statuses
        ctoks = {cospar} | {intl_to_cospar(b) for _, b in
                            ({(e.norad, e.intl) for e in ents} or {(n_raw, i_raw)})}
        ctoks |= {t for t in [name] + aliases if is_temp_desig(t) or _COSPAR_RX.match(t)}
        ctoks |= set(K.get("commit_names", []))
        ctoks.discard("")
        lc = last_commit(sorted(ctoks))
        sd_from_commit = False
        if not status_date and lc and status in ("active", "derelict", "unknown", "lost") \
                and not K.get("no_commit_date"):
            status_date = lc[1]
            sd_from_commit = True

        # ---- warns ---------------------------------------------------------
        my_ids = {cospar} | {a for a in aliases} | {name}
        wnotes = []
        for w in warns:
            txt = w["text"]
            ids = set(re.findall(r"\b\d{4}-\d{3}[A-Z]{1,3}\b", txt))
            hit = bool(ids & my_ids) or any(nm in txt for nm in K.get("warn_names", []))
            if hit:
                wnotes.append(f"{'Warn' if w['active'] else 'inactive Warn'} {w['date_raw']} "
                              f"'{txt}' (L{w['line_no']})")

        # ---- notes ---------------------------------------------------------
        notes = []
        if o.get("orphan_file"):
            notes.append(f"file on disk in ~/tles but NOT Included in tle_list.txt "
                         f"(sat_id ignores it); TLE IDs {n_raw}/{i_raw}")
        else:
            lines = sorted({e.line_no for e in ents} | set(o.get("dup_lines", [])))
            notes.append("tle_list L" + ",".join(str(x) for x in lines))
            ids_seen = sorted({(e.norad, e.intl) for e in ents})
            if len(ids_seen) > 1:
                notes.append("merged tle_list IDs " + ", ".join(f"{a}/{b}" for a, b in ids_seen))
        if surrogate:
            notes.append(f"NORAD {int(n_raw)} is a Bill Gray surrogate (79xxx/89xxx), intl {i_raw} unofficial")
        elif n_raw == "99999":
            notes.append(f"tle_list ID 99999 (=no NORAD) intl {i_raw}")
        fl = [file_flags(TLE_ROOT / f) for f in fnames]
        fmt = "hex/extended (type H, SDP4-high integrator)" if is_hex else "classical"
        if is_hex and not all(fd.is_hex for fd in fds):
            fmt = "mixed hex+classical"
        sg = [f for f, x in zip(fnames, fl) if x["sgp4_only"]]
        if sg:
            fmt += ("; SGP4-only fit (ephemeris type 2: use SGP4 even though deep-space)"
                    + ("" if len(sg) == len(fnames) else " in " + ",".join(sg)))
        notes.append(f"{len(recs_all)} TLEs {short_date(t_first)}..{short_date(t_last)}, {fmt}; "
                     f"TEME mean-of-date elements, valid only inside the Range (each TLE from its "
                     f"epoch to the next)")
        for f in fnames:
            ep = TLE_ROOT / (Path(f).stem + "_ephem.txt")
            if ep.exists():
                notes.append(ephem_txt_note(ep))
        merrs = sorted({e.max_err for e in ents if e.max_err is not None})
        if merrs:
            notes.append("sat_id max error " + "/".join(f"{x:g}" for x in merrs) + " deg")
        if no_range:
            notes.append("no Range line for " + ",".join(no_range) + " (span from TLE epochs)")
        if K.get("note"):
            notes.append(K["note"])
        if lc:
            notes.append(f"latest BG commit {lc[0]} {lc[1]}" +
                         (" (= status_date)" if sd_from_commit else ""))
        if frac:
            notes.append("fractional-day Range: " + "; ".join(frac))
        for u in up_only:
            notes.append(f"upstream tle_list Includes {u.include} (Range {u.range_raw}); local tle_list "
                         f"replaced it")
        loc = [e.include for e in ents if e.include in local_only]
        if loc:
            notes.append("LOCAL-ONLY Include(s) not in upstream tle_list: " + ",".join(loc))
        notes.extend(wnotes)
        if stale:
            dd = (NOW - stale[0]).days
            notes.append(f"STALE: last Range ended {short_date(stale[0])} ({dd} d before {NOW:%Y-%m-%d})")
        if el:
            fr = {"earth": "geocentric J2000 equatorial", "moon": "selenocentric J2000 equatorial",
                  "sun": "heliocentric J2000 ecliptic"}[el["frame"]]
            notes.append(f"elements: osculating {fr}, from get_vect state of {ref_file} TLE "
                         f"epoch {iso(ref.epoch_utc)}"
                         + ("; period selenocentric" if el["frame"] == "moon" else "")
                         + (f"; heliocentric P={el['P_d']:.1f} d, a={el['a'] / AU_KM:.4f} AU (period_d left "
                            f"empty: not geocentric)" if el["frame"] == "sun" and el.get("P_d") else ""))
        if bogus:
            notes.append("ignored sample " + ", ".join(
                f"{s['f']} TLE {s['t'][:10]} (r_geo {s['rg']:.0f} km < Earth radius: failed fit or "
                f"post-impact)" for s in bogus))
        if samples:
            notes.append(f"TLE-sampled r_geo {rg_min/1e3:.0f}k-{max_rg/1e3:.0f}k km, "
                         f"min Moon dist {rm_min/1e3:.0f}k km"
                         + (f", {len(lun)} samples Moon-bound" if lun else "")
                         + (f", {len(unb)}/{len(samples)} geo-unbound" if unb else ""))
        if hz_note:
            notes.append(hz_note)
        if o.get("unlisted"):
            notes.append("also on disk, not Included: " + ",".join(sorted(o["unlisted"])))
        for f in fnames:
            for s in orbits_by_file.get(f, []):
                if s.get("norad_id") is not None and n_raw.isdigit() and int(s["norad_id"]) != int(n_raw):
                    notes.append(f"orbits.json {f} norad {s['norad_id']} differs")
        if not any(orbits_by_file.get(f) for f in fnames) and not o.get("orphan_file"):
            notes.append("not in highartsat orbits.json (2026-08-04)")

        row = {
            "src": "billgray",
            "src_ref": fnames[-1] if not o.get("orphan_file") else o["orphan_file"],
            "name": name,
            "aliases": ";".join(aliases),
            "norad": norad,
            "cospar": cospar,
            "horizons_id": horizons_id,
            "obj_type": otype,
            "regime": regime,
            "status": status,
            "status_date": status_date,
            "period_d": fmt_num(el.get("P_d"), 4) if el and el["frame"] != "sun" else "",
            "a_km": fmt_num(el.get("a"), 1) if el else "",
            "e": fmt_num(el.get("e"), 6) if el else "",
            "incl_deg": fmt_num(el.get("incl"), 4) if el else "",
            "perigee_km": fmt_num(el.get("rp"), 1) if el else "",
            "apogee_km": fmt_num(el.get("ra"), 1) if el else "",
            "elem_epoch": iso(ref.epoch_utc) if ref is not None else "",
            "frame_center": el["frame"] if el else "",
            "ephem_sources": ";".join(srcs),
            "covers_now": "Y" if covers else "N",
            "notes": " | ".join(notes),
        }
        rows.append(row)
        review.append((kstr, name, auto, regime, el["frame"] if el else "-",
                       el.get("P_d") if el else None, el.get("ra") if el else None,
                       rg_min, max_rg, rm_min, len(lun), len(unb), len(samples),
                       short_date(t_first), short_date(last_end), row["covers_now"],
                       bool(near_win), otype, status))

    # ---- Warn-only objects (no TLE files) --------------------------------
    for w in WARN_ONLY:
        lines = [x for x in warns if re.search(w["match"], x["text"])]
        wn = [f"{'Warn' if x['active'] else 'inactive Warn'} {x['date_raw']} '{x['text']}' (L{x['line_no']})"
              for x in lines]
        cos = w.get("cospar", "")
        hid = w.get("horizons_id", "")
        hzn = ""
        if not hid and cos and cos in mb_by_cospar:
            hid = mb_by_cospar[cos]
            hzn = f"Horizons {hid} = '{mb_by_id[hid][0]}' matched by COSPAR in cached Horizons body list"
        if hid and not hzn:
            mb = mb_by_id.get(hid)
            hzn = f"Horizons {hid}" + (f" in cached Horizons body list as '{mb[0]}' {mb[1]}" if mb else "")
        rows.append({
            "src": "billgray", "src_ref": "tle_list.txt:L" + ",".join(str(x["line_no"]) for x in lines),
            "name": w["name"], "aliases": ";".join(w.get("aliases", [])), "norad": w.get("norad", ""),
            "cospar": cos, "horizons_id": hid, "obj_type": w["type"], "regime": w["regime"],
            "status": w["status"], "status_date": w.get("status_date", ""),
            "period_d": "", "a_km": "", "e": "", "incl_deg": "", "perigee_km": "", "apogee_km": "",
            "elem_epoch": "", "frame_center": "", "ephem_sources": "", "covers_now": "N",
            "notes": " | ".join(["only in tle_list.txt Warn lines; no Bill Gray TLE file"] + wn +
                                ([w["note"]] if w.get("note") else []) + ([hzn] if hzn else [])),
        })

    # ---- write -------------------------------------------------------------
    for r in rows:
        for c in COLUMNS:
            r[c] = str(r.get(c, "")).replace("\t", " ").replace("\n", " ").replace("\r", " ")
    rows.sort(key=lambda r: (r["regime"], r["name"].lower()))
    OUT_TSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_TSV.open("w", encoding="utf-8", newline="") as fh:
        fh.write("\t".join(COLUMNS) + "\n")
        for r in rows:
            fh.write("\t".join(r[c] for c in COLUMNS) + "\n")

    # ---- side outputs for traceability (data, not reports) -------------------
    with (DERIVED / "rejected.tsv").open("w") as fh:
        fh.write("norad\tintl\treason\tfiles\n")
        for k, why, fl in rejected:
            fh.write(f"{k[0]}\t{k[1]}\t{why}\t{';'.join(fl)}\n")
    with (DERIVED / "review.tsv").open("w") as fh:
        fh.write("key\tname\tauto\tregime\tframe\tP_d\tQ_km\trg_min\trg_max\trm_min\tn_lun\tn_unb\t"
                 "n_samp\tt_first\tlast_end\tcovers\tnear_0.05AU_2024-30\ttype\tstatus\n")
        for t in review:
            fh.write("\t".join("" if x is None else (f"{x:.3f}" if isinstance(x, float) else str(x))
                               for x in t) + "\n")
    # orbits.json cross-check (Range parsing and coverage of Include list)
    xc = []
    mine = {e.include: e for e in tl.entries if not e.old}
    for s in orbits:
        f = s.get("include_file")
        e = mine.get(f)
        if e is None:
            xc.append(f"{f}: in orbits.json, not a live Include now")
            continue
        for lab, a, b in (("start", s.get("range_start_utc"), e.range_start),
                          ("end", s.get("range_end_utc"), e.range_end)):
            if a and b and abs((datetime.fromisoformat(a.replace("Z", "+00:00")) - b).total_seconds()) > 60:
                xc.append(f"{f}: range {lab} orbits.json {a} vs tle_list {iso(b)}")
            elif bool(a) != bool(b):
                xc.append(f"{f}: range {lab} orbits.json {a} vs tle_list {iso(b)}")
    ofiles = {s.get("include_file") for s in orbits}
    for f, e in mine.items():
        if f not in ofiles and not e.missing:
            xc.append(f"{f}: live Include now, absent from orbits.json")
    (DERIVED / "orbits_crosscheck.txt").write_text("\n".join(xc) + "\n")

    n_kept = len(rows)
    print(f"rows {n_kept} (TLE objects {n_kept - len(WARN_ONLY)}, warn-only {len(WARN_ONLY)}), "
          f"rejected {len(rejected)}, covers_now {sum(r['covers_now'] == 'Y' for r in rows)}")
    print("orbits.json cross-check lines:", len(xc))
    return 0


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main() -> int:
    DERIVED.mkdir(parents=True, exist_ok=True)
    global _gv_cache
    if _GV_CACHE_PATH.exists():
        _gv_cache = json.loads(_GV_CACHE_PATH.read_text())
    tl = parse_tle_list(TLE_LIST)
    eph = Ephem()

    live = [e for e in tl.entries if not e.old]
    n_old = sum(e.old for e in tl.entries)
    missing = sorted({e.include for e in live if e.missing})

    # ---- object grouping -------------------------------------------------
    MERGE = {  # (norad, intl) -> canonical (norad, intl); reasons in notes
        ("89972", "00000ABB"): ("60000", "67040G"),
        ("79936", "00000ZZU"): ("36109", "09068B"),
        ("99999", "01027AA"): ("26859", "01027A"),
        ("79998", "22999ZZY"): ("79960", "23999ZZK"),
        ("79932", "00000ZZQ"): ("79960", "23999ZZK"),
        # 25038b.tle is Odin (Horizons -10001001 header, TLE name lines
        # 'Odin = 2025-038B = NORAD 63100', BG b1c4799 '2025-038B = Odin');
        # Space-Track/GCAT 63100 = 2025-038B = Chimera GEO-1, Odin = 63102.
        ("63100", "25038B"): ("63102", "25038D"),
        # a116cgx.tle = A116CgX = A116I21 = DRO-A/B upper stage, BG's made-up
        # 79925/24999ZZO for 2024 (BG 5848115), Space-Track 2024-048E (BG 240f5fb)
        ("79925", "24999ZZO"): ("60326", "24048E"),
    }
    groups: dict[tuple, list[Entry]] = defaultdict(list)
    for e in live:
        if e.include in ("classfd.tle", "inttles.tle", "all_tle.txt"):
            continue
        k = (e.norad, e.intl)
        k = MERGE.get(k, k)
        groups[k].append(e)

    # files present on disk but not Included anywhere
    included = {e.include for e in tl.entries}
    unlisted = sorted(p.name for p in TLE_ROOT.glob("*.tle") if p.name not in included)

    files: dict[str, FileData] = {}
    for e in live:
        if not e.missing and e.include not in files:
            files[e.include] = load_tle_file(TLE_ROOT / e.include)
    for f in unlisted:
        files[f] = load_tle_file(TLE_ROOT / f)
    upstream_smile = UPSTREAM / "26109a.upstream.tle"
    if upstream_smile.exists():
        files["upstream:26109a.tle"] = load_tle_file(upstream_smile)

    # ---- evaluate states -------------------------------------------------
    reqs = []
    sample_index: dict[str, list] = {}
    for fname, fd in files.items():
        recs = [r for r in fd.records if r.epoch_utc]
        if not recs:
            continue
        if fd.is_hex:
            stride = max(1, math.ceil(len(recs) / MAX_SAMPLES))
            pick = recs[::stride]
            if recs[-1] not in pick:
                pick.append(recs[-1])
        else:
            pick = [recs[0], recs[len(recs) // 2], recs[-1]]
        sample_index[fname] = pick
        reqs += [(r.name, r.line1, r.line2, r.epoch_utc) for r in pick]
    # TLE at "now" for files covering now
    for fname, fd in files.items():
        recs = [r for r in fd.records if r.epoch_utc and r.epoch_utc <= NOW]
        if recs and fd.cover_end and fd.cover_end > NOW:
            r = max(recs, key=lambda r: r.epoch_utc)
            reqs.append((r.name, r.line1, r.line2, r.epoch_utc))
    states = eval_states(reqs)
    _GV_CACHE_PATH.write_text(json.dumps(_gv_cache))

    def state_of(rec):
        return states.get(_gv_key(rec.line1, rec.line2)) or _gv_cache.get(_gv_key(rec.line1, rec.line2))

    def sample_stats(fname):
        """Distance history for a file's samples."""
        out = []
        for rec in sample_index.get(fname, []):
            st = state_of(rec)
            if not st:
                continue
            jd, r, v = st
            rm, vm = eph.moon_geo(jd)
            rs = [a - b for a, b in zip(r, rm)]
            vs = [a - b for a, b in zip(v, vm)]
            re_h, ve_h = eph.earth_helio(jd)
            sun_dir = [-x for x in re_h]          # geocentric Sun direction
            rg = _norm(r)
            cos_sun = _dot(r, sun_dir) / (rg * _norm(sun_dir))
            # Earth-Moon rotating frame
            xh = [x / _norm(rm) for x in rm]
            hz = _cross(rm, vm)
            zh = [x / _norm(hz) for x in hz]
            yh = _cross(zh, xh)
            dm = _norm(rm)
            rot = [_dot(r, xh) / dm, _dot(r, yh) / dm, _dot(r, zh) / dm]
            kg = kepler(r, v, MU_E)
            ks = kepler(rs, vs, MU_M)
            out.append({"t": rec.epoch_utc.isoformat(), "rg": rg, "rm": _norm(rs),
                        "eg": kg["energy"], "es": ks["energy"], "cos_sun": cos_sun,
                        "rot": rot, "Pg": kg.get("P_d"), "rag": kg.get("ra"), "rpg": kg.get("rp")})
        return out

    stats_by_file = {f: sample_stats(f) for f in files}
    (DERIVED / "samples.json").write_text(json.dumps(stats_by_file))

    # ---- per object assembly --------------------------------------------
    objs = []
    for key, ents in groups.items():
        seen = set()
        uents = []
        for e in ents:
            if e.include in seen:
                continue
            seen.add(e.include)
            uents.append(e)
        uents.sort(key=lambda e: (e.range_start or datetime(1900, 1, 1, tzinfo=UTC)))
        objs.append({"key": key, "entries": uents, "dup_lines": [e.line_no for e in ents if e not in uents]})

    result = {"meta": {"n_include_total": len(tl.entries), "n_old": n_old,
                       "n_live": len(live), "missing": missing, "unlisted": unlisted,
                       "commented_includes": tl.commented_includes},
              "warns": [{**w, "date": iso(w["date"], False)} for w in tl.warns],
              "indented": tl.indented_comments, "hash_comments": tl.hash_comments,
              "objects": []}
    for o in objs:
        norad, intl = o["key"]
        rows = []
        for e in o["entries"]:
            fd = files.get(e.include)
            rows.append({"file": e.include, "line": e.line_no, "rs": iso(e.range_start),
                         "re": iso(e.range_end), "raw": e.range_raw, "norad": e.norad,
                         "intl": e.intl, "max_err": e.max_err, "missing": e.missing,
                         "hex": fd.is_hex if fd else None,
                         "n_tle": len(fd.records) if fd else 0,
                         "t_first": iso(fd.t_first) if fd else "", "t_last": iso(fd.t_last) if fd else "",
                         "cover_end": iso(fd.cover_end) if fd else "",
                         "name_line": (fd.header["first_name_line"] or (fd.records[0].name if fd and fd.records else None)) if fd else None,
                         "blocks": fd.header["blocks"] if fd else [],
                         "free_text": fd.header["free_text"] if fd else [],
                         "no_updates": fd.header["no_updates"] if fd else None})
        result["objects"].append({"norad": norad, "intl": intl, "files": rows,
                                  "dup_lines": o["dup_lines"]})
    (DERIVED / "objects_raw.json").write_text(json.dumps(result, indent=1, default=str))
    print("objects", len(objs), "live entries", len(live), "old", n_old, "missing", missing)
    print("unlisted", unlisted)
    ctx = {"tl": tl, "files": files, "objs": objs, "stats_by_file": stats_by_file, "eph": eph,
           "unlisted": unlisted, "merge": MERGE, "n_old": n_old, "n_live": len(live),
           "n_total": len(tl.entries)}
    return phase2(ctx)


if __name__ == "__main__":
    raise SystemExit(main())
