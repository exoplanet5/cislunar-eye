#!/usr/bin/env python3
"""
extract_lunarmission.py - cislunar-eye per-source candidate extract from the ../lunarmission
wiki (every lunar-mission object 1958-2026, compiled 2026-08-23), refreshed against JPL
Horizons and Space-Track as of the cislunar-eye reference epoch 2026-10-21T00:00Z.

Output:  catalog/sources/lunarmission.tsv   (columns / vocabularies of catalog/sources/SCHEMA.md)
         work/notes/lunarmission_extract_summary.json   (counts, excluded buckets, Horizons map)
         work/cache/lunarmission_horizons/_objdata_digest.txt   (Horizons OBJ_DATA notes, for review)

Inputs (read-only; nothing in ../lunarmission or ~/tles is modified or executed):
  ../lunarmission/gcat_lunar_objects.tsv   GCAT phase chain + "Now" bucket per object (944 objects)
  ../lunarmission/build_wiki.py            OVERRIDES / HZ_BY_NORAD / HZ_BY_NAME  (parsed with ast, NOT imported:
                                           importing it would re-render the wiki)
  ../lunarmission/missions_*.py            mission registry + FLYBY_MISSIONS (ast-parsed)
  ../lunarmission/horizons_status.tsv      Horizons IDs used by the wiki
  ../lunarmission/spacetrack_status.tsv    SATCAT decay + GP as of 2026-08-23
  ../lunarmission/surface_unlocated.tsv    lunar-orbit derelicts whose impact is only presumed
  ../lunarmission/lunar_missions.md        for line citations in `notes`
  ~/tles/tle_list.txt + ~/tles/*.tle       Bill Gray hex-TLE coverage ('# Ephem range:' MJD headers) and 'Warn' calendar

Network (cached, so re-runs are offline-capable with --offline):
  JPL Horizons API  -> work/cache/lunarmission_horizons/  (MB list, spans, states at 2026-10-21, elements,
                                                           1-day geocentric tables 2024-2030 for heliocentric objects)
  Space-Track (via the user's ~/.claude/skills/space-track/st.py query()) -> work/cache/lunarmission_spacetrack/
                                                          (current GP + SATCAT for every listed NORAD / COSPAR)

Selection (task text + SCHEMA.md):
  listed : wiki bucket L, L?, P, C (bound cislunar/HEO, incl. lost); E only if apogee >= 100 000 km or P > 2.4 d at any
           GCAT epoch, or a translunar history, or a Horizons ephemeris; H if any ephemeris / Warn entry / launched
           2019-2026 / GCAT heliocentric epoch >= 2024; lunar-orbit derelicts whose impact is only presumed
           (surface_unlocated.tsv orbiters, sub-satellites, ascent stages); and S/I/R/B/A/F objects only when they
           were launched 2019-2026 (or were resident in cislunar space after 2019) AND have a Horizons or Bill Gray
           ephemeris of their beyond-LEO trajectory.
  counted: everything else (surface, impact, reentered, returned, launch failure, 1959-2018 escape stages with no
           ephemeris) - totals in the summary JSON.

Run (idempotent):  ~/.venvs/astro313/bin/python -I scripts/extract_lunarmission.py [--offline] [--fresh-st]
"""
from __future__ import annotations

import ast
import collections
import concurrent.futures as cf
import csv
import datetime as dt
import glob
import hashlib
import json
import math
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _now import now  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
LM = ROOT.parent / "lunarmission"
TLES = Path.home() / "tles"
CACHE_HZ = ROOT / "work/cache/lunarmission_horizons"
CACHE_ST = ROOT / "work/cache/lunarmission_spacetrack"
OUT = ROOT / "catalog/sources/lunarmission.tsv"
SUMMARY = ROOT / "work/notes/lunarmission_extract_summary.json"
NOW = now("day").replace(tzinfo=None)      # CLE_NOW floored to the day, else today (scripts/_now.py)
NOW_S = NOW.strftime("%Y-%m-%d %H:%M")
WIKI_DATE = "2026-08-23"
OFFLINE = "--offline" in sys.argv
FRESH_ST = "--fresh-st" in sys.argv
GP_FRESH_DAYS = 14          # a Space-Track GP this close to NOW counts as covering NOW
KEPLER_MAX_GAP_YR = 8       # two-body propagation of a heliocentric arc edge only if the arc ended >= 2016

R_E, R_M = 6378.137, 1737.4
GM = {"earth": 398600.4418, "moon": 4902.800066, "sun": 1.32712440018e11}
AU = 149597870.7

COLS = ["src", "src_ref", "name", "aliases", "norad", "cospar", "horizons_id", "obj_type", "regime", "status",
        "status_date", "period_d", "a_km", "e", "incl_deg", "perigee_km", "apogee_km", "elem_epoch", "frame_center",
        "ephem_sources", "covers_now", "notes"]
REGIME_ORDER = ["LUNAR_ORBIT", "EM_L1", "EM_L2", "EM_DRO", "EM_OTHER", "CISLUNAR_HEO", "HEO", "TRANSLUNAR", "SE_L1",
                "SE_L2", "EARTH_NEAR_HELIO", "NEOCP_GEO", "OTHER"]
BADGE = {"ON_MOON": "S", "LUNAR_IMPACT": "I", "LUNAR_ORBIT": "L", "LUNAR_ORBIT_DECAYED_UNKNOWN": "L?",
         "RELAUNCHED_FROM_MOON": "A", "L_POINT": "P", "HELIOCENTRIC": "H", "CISLUNAR": "C", "EARTH_ORBIT": "E",
         "EARTH_ORBIT_DECAYED": "R", "RETURNED_EARTH": "B", "DESTROYED_LAUNCH": "F"}


# ============================================================ source parsing
def read_tsv(p):
    with open(p, encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def _lit(node):
    """ast literal evaluator that also understands dict(k=v) calls."""
    if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "dict":
        return {kw.arg: _lit(kw.value) for kw in node.keywords}
    if isinstance(node, ast.List):
        return [_lit(e) for e in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_lit(e) for e in node.elts)
    return ast.literal_eval(node)


def module_assigns(path, names):
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    out = {}
    for n in tree.body:
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and getattr(n.targets[0], "id", None) in names:
            out[n.targets[0].id] = _lit(n.value)
    return out


BW = module_assigns(LM / "build_wiki.py", {"OVERRIDES", "HZ_BY_NORAD", "HZ_BY_NAME", "BG_TLE"})
MISSIONS = []
for _f, _nm in (("missions_1958_1968.py", "MISSIONS_A"), ("missions_1969_1976.py", "MISSIONS_B"),
                ("missions_1977_2026.py", "MISSIONS_C")):
    MISSIONS += module_assigns(LM / _f, {_nm})[_nm]
FLYBY = module_assigns(LM / "missions_1977_2026.py", {"FLYBY_MISSIONS"})["FLYBY_MISSIONS"]
TAG2MISSION = {t: m for m in MISSIONS for t in m["tags"]}
TAG2FLYBY = {t: f for f in FLYBY for t in f["tags"]}

GCAT = read_tsv(LM / "gcat_lunar_objects.tsv")
ST_SRC = {int(r["NORAD"]): r for r in read_tsv(LM / "spacetrack_status.tsv") if r["NORAD"].isdigit()}
HZ_SRC = {r["Label"]: r for r in read_tsv(LM / "horizons_status.tsv")}
UNLOCATED = read_tsv(LM / "surface_unlocated.tsv")
WIKI_LINES = (LM / "lunar_missions.md").read_text(encoding="utf-8").splitlines()


def apply_overrides(tag, name, cat, now):
    """Replica of build_wiki.apply_overrides (ast-parsed OVERRIDES + the 'EEO, lost' -> presumed-heliocentric rule)."""
    for t, rx, (c2, n2) in BW["OVERRIDES"]:
        if t == tag and re.search(rx, name):
            return c2, n2, True
    if cat == "CISLUNAR" and re.search(r"\bEEO\b", now) and "lost" in now:
        return "HELIOCENTRIC", "presumed heliocentric (GCAT 'EEO, lost')", True
    return cat, now, False


# ============================================================ small helpers
MON = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
MON2 = {"ja": 1, "fe": 2, "mr": 3, "ap": 4, "my": 5, "jn": 6, "jl": 7, "au": 8, "se": 9, "oc": 10, "no": 11, "de": 12}


def norm_date(s):
    """GCAT-ish date ('1969-06?', '2023-02', '1970', '2022-11-14 00:38') -> 'YYYY-MM-DD[THH:MM]'."""
    m = re.match(r"(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?(?:[ T](\d{2}):(\d{2}))?", (s or "").strip())
    if not m:
        return ""
    y, mo, d, hh, mm = m.groups()
    out = f"{y}-{mo or '01'}-{d or '01'}"
    return out + (f"T{hh}:{mm}" if hh else "")


def exact_date(s):
    """Only a full, unflagged YYYY-MM-DD qualifies as a status_date."""
    m = re.match(r"(\d{4}-\d{2}-\d{2})(?![\d?])", (s or "").strip())
    return m.group(1) if m and "?" not in s.split()[0] else ""


def to_dt(s):
    s = norm_date(s)
    return dt.datetime.fromisoformat(s) if s else None


def hz_time(s):
    m = re.match(r"(\d{4})-([A-Za-z]{3})-(\d{2})(?:\s+(\d{2}):(\d{2})(?::(\d{2}))?)?", s.strip())
    if not m:
        return None
    y, mo, d, hh, mi, ss = m.groups()
    return dt.datetime(int(y), MON[mo.lower()], int(d), int(hh or 0), int(mi or 0), int(ss or 0))


def bg_date(s):
    s = s.strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})(\.\d+)?$", s) or re.match(r"(\d{4})(\d{2})(\d{2})(\.\d+)?$", s)
    if m:
        y, mo, d, fr = m.groups()
        return dt.datetime(int(y), int(mo), int(d)) + dt.timedelta(days=float(fr or 0))
    m = re.match(r"(\d{4})([a-z]{2})(\d{2})(\.\d+)?$", s)
    if m:
        y, mo, d, fr = m.groups()
        return dt.datetime(int(y), MON2[mo], int(d)) + dt.timedelta(days=float(fr or 0))
    return None


def mjd2dt(x):
    return dt.datetime(1858, 11, 17) + dt.timedelta(days=float(x))


def iso(d, minutes=False):
    if d is None:
        return ""
    return d.strftime("%Y-%m-%dT%H:%M") if minutes else d.strftime("%Y-%m-%d")


def iso_z(d):
    """SCHEMA elem_epoch format: ISO UTC with seconds and 'Z'."""
    return d.strftime("%Y-%m-%dT%H:%M:%SZ") if d else ""


def eph_item(kind, ref, s=None, e=None):
    """SCHEMA ephem_sources item 'kind:ref[start..end]' with YYYY-MM-DD bounds (empty = open/unknown)."""
    return f"{kind}:{ref}[{iso(s) if s else ''}..{iso(e) if e else ''}]"


def short_cospar(c):
    m = re.match(r"(\d{4})-(\d{3})([A-Z]+)$", c or "")
    return f"{m.group(1)[2:]}{m.group(2)}{m.group(3).lower()}" if m else ""


def long_cospar(s):
    m = re.match(r"(\d{2})(\d{3})([A-Z]+)$", s or "")
    if not m:
        return ""
    yy = int(m.group(1))
    return f"{1900 + yy if yy >= 57 else 2000 + yy}-{m.group(2)}{m.group(3)}"


def fmt(x, nd=1):
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return ""
    return f"{x:.{nd}f}"


def period_days(a, center):
    if not a or a <= 0:
        return None
    return 2 * math.pi * math.sqrt(a ** 3 / GM[center]) / 86400.0


def wiki_line(name, mission_name=None, flyby=False):
    """1-based line of the object's ledger row in lunar_missions.md (inside its mission section)."""
    if flyby:
        for i, l in enumerate(WIKI_LINES):
            if l.startswith("## 4. "):
                return i + 1
    start = 0
    if mission_name:
        for i, l in enumerate(WIKI_LINES):
            if l.startswith("#### ") and l[5:].split(" — ")[0] == mission_name:
                start = i
                break
    for i in range(start, len(WIKI_LINES)):
        l = WIKI_LINES[i]
        if i > start and start and l.startswith("#### "):
            break
        if l.startswith(f"| {name} |") or l.startswith(f"| {name} *("):
            return i + 1
    return start + 1 if start else None


# ============================================================ Bill Gray ~/tles
def parse_tle_list():
    entries, rng, ident, warns = [], None, None, []
    for line in (TLES / "tle_list.txt").read_text(errors="ignore").splitlines():
        if line.startswith("# Range:"):
            p = line.split()[2:4]
            rng = (bg_date(p[0]), bg_date(p[1])) if len(p) == 2 else None
        elif line.startswith("# ID:"):
            p = line.split()
            ident = (p[2], p[3] if len(p) > 3 else "")
        elif line.startswith("# Include"):
            fn = line.split()[2]
            entries.append(dict(file=fn, rng=rng, norad=ident[0] if ident else "",
                                cospar=long_cospar(ident[1]) if ident else ""))
            rng, ident = None, None
        elif line.startswith("# Warn"):
            m = re.match(r"# Warn (\S+) \| (.*)", line)
            if m:
                warns.append((m.group(1), m.group(2).strip()))
    return entries, warns


TLE_LIST, WARNS = parse_tle_list()


def ephem_range_header(path):
    try:
        with open(path, errors="ignore") as f:
            for i, line in enumerate(f):
                if i > 150:
                    break
                m = re.match(r"# Ephem range:\s+([\d.]+)\s+([\d.]+)", line)
                if m:
                    return mjd2dt(m.group(1)), mjd2dt(m.group(2))
    except OSError:
        pass
    return None


def bg_files(norad, cospar):
    """[(file, start, end)] Bill Gray TLE files for an object; range from the file header, else tle_list."""
    out = {}
    sc = short_cospar(cospar)
    for e in TLE_LIST:
        if (norad and e["norad"] == str(int(norad))) or (cospar and e["cospar"] == cospar):
            out[e["file"]] = e["rng"]
    if sc:
        rx = re.compile(rf"^{sc}(\d\d)?(v\d+)?(_\d)?\.tle$")
        for p in glob.glob(str(TLES / f"{sc}*.tle")):
            fn = Path(p).name
            if rx.match(fn) and fn not in out:
                out[fn] = None
    res = []
    for fn, rng in sorted(out.items()):
        hdr = ephem_range_header(TLES / fn) if (TLES / fn).exists() else None
        rng = hdr or rng or (None, None)
        res.append((fn, rng[0], rng[1]))
    return res


def bg_files_by_name(tag, name, have):
    """Bill Gray files filed under a surrogate COSPAR/NORAD (e.g. equuleus.tle = 79970 2022-156Z, neascout.tle,
    wmap.tle = 99999 2001-027AA): same launch tag and file stem == object name (alphanumerics, lower case)."""
    key = re.sub(r"[^a-z0-9]", "", name.lower())
    out = []
    for e in TLE_LIST:
        if e["file"] in have or not e["cospar"].startswith(tag + "-") and e["cospar"][:8] != tag:
            continue
        stem = re.sub(r"[^a-z0-9]", "", Path(e["file"]).stem.lower())
        if key and stem == key:
            hdr = ephem_range_header(TLES / e["file"]) if (TLES / e["file"]).exists() else None
            rng = hdr or e["rng"] or (None, None)
            out.append((e["file"], rng[0], rng[1], e["norad"], e["cospar"]))
    return out


def tle_list_norad(cospar):
    for e in TLE_LIST:
        if cospar and e["cospar"] == cospar and e["norad"].isdigit():
            return e["norad"]
    return ""


def warn_for(cospar, aliases):
    hits = []
    for d, txt in WARNS:
        if cospar and cospar in txt:
            hits.append((d, txt))
        elif any(a and re.search(rf"(^|\W){re.escape(a)}(\W|$)", txt) for a in aliases):
            # a name match does not count when the Warn line names a different COSPAR piece
            # (e.g. '2023-053B = JUICE upper stage returns' must not attach to JUICE 2023-053A)
            other = [c for c in re.findall(r"\d{4}-\d{3}[A-Z]+", txt) if c != cospar]
            if not other:
                hits.append((d, txt))
    return hits


# ============================================================ Horizons
HZ_API = "https://ssd.jpl.nasa.gov/api/horizons.api"


def hz_get(params, tag):
    key = hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()[:16]
    f = CACHE_HZ / f"{re.sub(r'[^A-Za-z0-9]+', '_', tag)}_{key}.txt"
    if f.exists():
        txt = f.read_text()
        # short replies are valid when they are Horizons' "No ephemeris ... prior to/after" span answers
        if len(txt) > 200 or "No ephemeris for target" in txt:
            return txt
    if OFFLINE:
        return ""
    qs = "&".join(f"{k}={urllib.parse.quote(v, safe='')}" for k, v in params.items())
    for attempt in range(5):
        try:
            req = urllib.request.Request(f"{HZ_API}?{qs}", headers={"User-Agent": "cislunar-eye/extract_lunarmission"})
            with urllib.request.urlopen(req, timeout=180) as r:
                txt = r.read().decode("utf-8", errors="ignore")
            if "API ERROR" in txt[:400] or "503 Service" in txt[:400]:
                raise OSError(txt[:200])
            f.write_text(txt)
            return txt
        except OSError:
            time.sleep(4 * (attempt + 1))
    return ""


def hz_vec(cmd, center, t0, t1=None, step="1", obj=False):
    t1 = t1 or (dt.datetime.strptime(t0, "%Y-%m-%d %H:%M") + dt.timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M")
    p = dict(format="text", COMMAND=f"'{cmd}'", OBJ_DATA="YES" if obj else "NO", MAKE_EPHEM="YES",
             EPHEM_TYPE="VECTORS", CENTER=f"'{center}'", REF_PLANE="'FRAME'", START_TIME=f"'{t0}'",
             STOP_TIME=f"'{t1}'", STEP_SIZE=f"'{step}'", VEC_TABLE="'2'", CSV_FORMAT="'YES'", OUT_UNITS="'KM-S'")
    return hz_get(p, f"vec_{cmd}_{center}")


def hz_elem(cmd, center, t0, plane):
    t1 = (dt.datetime.strptime(t0, "%Y-%m-%d %H:%M") + dt.timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M")
    p = dict(format="text", COMMAND=f"'{cmd}'", OBJ_DATA="NO", MAKE_EPHEM="YES", EPHEM_TYPE="ELEMENTS",
             CENTER=f"'{center}'", REF_PLANE=f"'{plane}'", START_TIME=f"'{t0}'", STOP_TIME=f"'{t1}'",
             STEP_SIZE="'1'", CSV_FORMAT="'YES'", OUT_UNITS="'KM-S'")
    return hz_get(p, f"elem_{cmd}_{center}")


def soe_rows(txt):
    if "$$SOE" not in txt:
        return []
    body = txt.split("$$SOE", 1)[1].split("$$EOE", 1)[0]
    return [[c.strip() for c in l.split(",")] for l in body.strip().splitlines() if l.strip()]


def no_ephem(txt):
    m = re.search(r'No ephemeris for target "([^"]+)" (prior to|after) A\.D\. (\S+ \S+)', txt)
    if m:
        return m.group(2), hz_time(m.group(3).title())
    return None


def hz_profile(cmd, lunar_hint=False):
    """Span, coverage of NOW, state + osculating elements at NOW (or at the nearest arc edge)."""
    res = dict(cmd=cmd, ok=False)
    t_now = hz_vec(cmd, "500@399", NOW_S, obj=True)
    if not t_now:
        res["err"] = "no reply"
        return res
    res["objdata"] = t_now.split("$$SOE")[0][:8000]
    m = re.search(r"Target body name:\s*(.+?)\s{2,}", t_now)
    res["target_name"] = m.group(1).strip() if m else ""
    if "No ephemeris" not in t_now and "$$SOE" not in t_now:
        res["err"] = re.sub(r"\s+", " ", t_now[-300:])
        return res
    res["ok"] = True
    res["covers_now"] = bool(soe_rows(t_now))
    early = no_ephem(hz_vec(cmd, "500@399", "1957-10-05 00:00"))
    late = no_ephem(hz_vec(cmd, "500@399", "2199-12-30 00:00"))
    res["start"] = early[1] if early and early[0] == "prior to" else None
    res["end"] = late[1] if late and late[0] == "after" else None
    ne = no_ephem(t_now)
    if ne and ne[0] == "after":
        res["end"] = ne[1]
    if ne and ne[0] == "prior to":
        res["start"] = ne[1]
    if res["covers_now"]:
        ep = NOW
    elif res["end"] and res["end"] < NOW:
        ep = res["end"] - dt.timedelta(minutes=10)
    elif res["start"] and res["start"] > NOW:
        ep = res["start"] + dt.timedelta(minutes=10)
    else:
        res["ok"] = False
        res["err"] = "no usable epoch (gap?)"
        return res
    eps = ep.strftime("%Y-%m-%d %H:%M")
    res["epoch"] = ep
    ve = soe_rows(t_now) if res["covers_now"] else soe_rows(hz_vec(cmd, "500@399", eps))
    vm = soe_rows(hz_vec(cmd, "500@301", eps))
    if not ve or not vm:
        res["ok"] = False
        res["err"] = "no vectors at epoch"
        return res
    rE = np.array([float(x) for x in ve[0][2:5]])
    rM = np.array([float(x) for x in vm[0][2:5]])
    res["dE"], res["dM"] = float(np.linalg.norm(rE)), float(np.linalg.norm(rM))
    if res["dM"] < 66100 or (lunar_hint and res["dM"] < 100000):
        center = "moon"
    elif res["dE"] < 0.93e6:
        center = "earth"
    else:
        center = "sun"
    res["center"] = center
    cid, plane = {"moon": ("500@301", "BODY EQUATOR"), "earth": ("500@399", "FRAME"),
                  "sun": ("500@10", "ECLIPTIC")}[center]
    el = soe_rows(hz_elem(cmd, cid, eps, plane))
    if el:
        r = el[0]
        ec, qr, inc, om, w, tp, n, ma, ta, a, ad, pr = (float(x) for x in r[2:14])
        res["el"] = dict(e=ec, q=qr, i=inc, om=om, w=w, ma=ma, a=a if ec < 1 else None,
                         Q=ad if ec < 1 else None, P=pr / 86400.0 if ec < 1 else None, jd=float(r[0]))
    return res


def hz_elements_at(cmd, ep, lunar_hint=False):
    """Osculating elements of Horizons body `cmd` at datetime `ep` about the dominant body (same centre rule as
    hz_profile).  Used for objects whose arc ends on a surface (impact / reentry), at end - 5 d."""
    eps = ep.strftime("%Y-%m-%d %H:%M")
    ve, vm = soe_rows(hz_vec(cmd, "500@399", eps)), soe_rows(hz_vec(cmd, "500@301", eps))
    if not ve or not vm:
        return None
    dE = float(np.linalg.norm([float(x) for x in ve[0][2:5]]))
    dM = float(np.linalg.norm([float(x) for x in vm[0][2:5]]))
    center = "moon" if (dM < 66100 or (lunar_hint and dM < 100000)) else ("earth" if dE < 0.93e6 else "sun")
    cid, plane = {"moon": ("500@301", "BODY EQUATOR"), "earth": ("500@399", "FRAME"),
                  "sun": ("500@10", "ECLIPTIC")}[center]
    el = soe_rows(hz_elem(cmd, cid, eps, plane))
    if not el:
        return None
    ec, qr, inc, om, w, tp, n, ma, ta, a, ad, pr = (float(x) for x in el[0][2:14])
    return dict(epoch=ep, center=center, e=ec, q=qr, i=inc, a=a if ec < 1 else None, Q=ad if ec < 1 else None,
                P=pr / 86400.0 if ec < 1 else None)


def hz_earth_min(cmd, t0, t1):
    """Minimum geocentric distance (AU) and date over [t0, t1] from a 1-day Horizons table."""
    t = hz_vec(cmd, "500@399", t0.strftime("%Y-%m-%d %H:%M"), t1.strftime("%Y-%m-%d %H:%M"), step="1 d")
    rows = soe_rows(t)
    if not rows:
        return None
    best = min(rows, key=lambda r: sum(float(x) ** 2 for x in r[2:5]))
    d = math.sqrt(sum(float(x) ** 2 for x in best[2:5])) / AU
    return d, best[1].replace("A.D. ", "")[:11]


def earth_helio_table():
    """Earth heliocentric ecliptic positions 2024-01-01..2030-12-31 (1 d) for two-body proximity estimates."""
    p = dict(format="text", COMMAND="'399'", OBJ_DATA="NO", MAKE_EPHEM="YES", EPHEM_TYPE="VECTORS",
             CENTER="'500@10'", REF_PLANE="'ECLIPTIC'", START_TIME="'2024-01-01 00:00'",
             STOP_TIME="'2030-12-31 00:00'", STEP_SIZE="'1 d'", VEC_TABLE="'2'", CSV_FORMAT="'YES'", OUT_UNITS="'KM-S'")
    rows = soe_rows(hz_get(p, "earth_helio_2024_2030"))
    return np.array([float(r[0]) for r in rows]), np.array([[float(x) for x in r[2:5]] for r in rows])


def kepler_positions(el, jd):
    """Two-body heliocentric ecliptic positions (km) for Horizons elements el at Julian dates jd."""
    a = el["q"] / (1 - el["e"])
    n = math.sqrt(GM["sun"] / a ** 3) * 86400.0
    M = math.radians(el["ma"]) + n * (jd - el["jd"])
    E = M.copy()
    for _ in range(30):
        E = E - (E - el["e"] * np.sin(E) - M) / (1 - el["e"] * np.cos(E))
    xo, yo = a * (np.cos(E) - el["e"]), a * math.sqrt(1 - el["e"] ** 2) * np.sin(E)
    O, w, i = (math.radians(el[k]) for k in ("om", "w", "i"))
    cO, sO, cw, sw, ci, si = math.cos(O), math.sin(O), math.cos(w), math.sin(w), math.cos(i), math.sin(i)
    x = (cO * cw - sO * sw * ci) * xo + (-cO * sw - sO * cw * ci) * yo
    y = (sO * cw + cO * sw * ci) * xo + (-sO * sw + cO * cw * ci) * yo
    z = (sw * si) * xo + (cw * si) * yo
    return np.stack([x, y, z], axis=1)


def parse_mb_list():
    legacy = CACHE_HZ / "mb_list.txt"          # fetched 2026-10-21 by the first run of this extractor
    if legacy.exists() and legacy.stat().st_size > 10000:
        t = legacy.read_text()
    else:
        t = hz_get(dict(format="text", COMMAND="'MB'", OBJ_DATA="YES", MAKE_EPHEM="NO"), "mb_list")
    out = {}
    for line in t.splitlines():
        m = re.match(r"^\s*(-\d+)\s+(.{34})(.*)$", line)
        if m:
            c = re.search(r"(\d{4}-\d{3}[A-Z]+)", m.group(2) + m.group(3))
            out[m.group(1)] = dict(name=m.group(2).strip(), cospar=c.group(1) if c else "")
    return out


# Explicit Horizons map (launch tag, GCAT name) -> COMMANDs.  Starts from the wiki's own map
# (build_wiki.HZ_BY_NORAD/HZ_BY_NAME -> horizons_status.tsv) and adds IDs found in the Horizons MB list of
# 2026-10-21 by COSPAR + name, each checked by hand (the MB list mislabels some COSPARs: -399101 'Apollo 10 LM'
# carries 1969-059C = Apollo 11 Eagle; -18900 and -169 share their sibling's COSPAR).
HZ_EXTRA = {
    ("2007-039", "Ouna"): ["-39"],
    ("1997-086", "HGS-1"): ["-125126"],
    ("1994-071", "Wind"): ["-8"],
    ("2018-038", "TESS"): ["-95"],
    ("1978-079", "ISEE 3"): ["-111"],
    ("2001-027", "WMAP"): ["-165"],
    ("1998-041", "Nozomi"): ["-178"],
    ("2006-047", "STEREO A"): ["-234"],
    ("2006-047", "STEREO B"): ["-235"],
    ("2006-047", "Star 48B"): ["-234900"],
    ("2023-053", "JUICE"): ["-28"],
    ("1994-004", "Clementine 1"): ["-40000", "-40"],
    ("2025-010", "Falcon 9-427 Stage 2"): ["-162719"],
    ("2026-069", "SWC-1"): ["-168540"],
    ("2025-038", "Odin"): ["-10001001"],
    ("2019-042", "Vikram"): ["-153"],
    ("1969-099", "Saturn S-IVB-507"): ["-399120", "-998"],
}
HZ_DROP = {("1998-001", "Celestis-02")}   # shares NORAD 25131 with Lunar Prospector (folded)


# ============================================================ Space-Track
def spacetrack_current(norads, cospars):
    CACHE_ST.mkdir(parents=True, exist_ok=True)
    fgp, fsc, fsi = CACHE_ST / "gp_current.json", CACHE_ST / "satcat.json", CACHE_ST / "satcat_by_intldes.json"
    need = FRESH_ST or not (fgp.exists() and fsc.exists() and fsi.exists())
    if need and not OFFLINE:
        sys.path.insert(0, str(Path.home() / ".claude/skills/space-track"))
        import st  # noqa: E402  (user's stdlib Space-Track CLI module)
        ids = ",".join(str(n) for n in sorted(norads))
        fgp.write_text(json.dumps(st.query(["class", "gp", "NORAD_CAT_ID", ids], ttl_h=6), indent=1))
        fsc.write_text(json.dumps(st.query(["class", "satcat", "NORAD_CAT_ID", ids], ttl_h=24), indent=1))
        fsi.write_text(json.dumps(st.query(["class", "satcat", "INTLDES", ",".join(sorted(cospars))], ttl_h=24)
                                  if cospars else [], indent=1))
    load = lambda f: json.loads(f.read_text()) if f.exists() else []  # noqa: E731
    gp = {int(r["NORAD_CAT_ID"]): r for r in load(fgp) if isinstance(r, dict) and "NORAD_CAT_ID" in r}
    sc = {int(r["NORAD_CAT_ID"]): r for r in load(fsc) if isinstance(r, dict) and "NORAD_CAT_ID" in r}
    si = {r["INTLDES"]: r for r in load(fsi) if isinstance(r, dict) and "INTLDES" in r}
    return gp, sc, si


# ============================================================ GCAT "Now" / phase parsing
def gcat_elements(now):
    """-> dict(center, rp, ra, i, epoch, oporbit) from the GCAT-derived Now string (altitudes -> radii)."""
    m = re.search(r"(selenocentric|geocentric) (-?[\d.]+|-)x(-?[\d.]+|-) km i=([\d.]+|-)(?: (\S+))? \(epoch ([^)]*)\)", now)
    if not m:
        m2 = re.search(r"(geocentric) (-?[\d.]+|-)x(-?[\d.]+|-) km i=([\d.]+|-)(?: (\S+))?$", now.strip())
        if m2:
            return dict(center="earth", rp=float(m2.group(2)) + R_E, ra=float(m2.group(3)) + R_E,
                        i=float(m2.group(4)), epoch="", oporbit=m2.group(5) or "")
    if m:
        body = "moon" if m.group(1) == "selenocentric" else "earth"
        R = R_M if body == "moon" else R_E
        hp, ha = m.group(2), m.group(3)
        rp = float(hp) + R if hp != "-" else None
        ra = float(ha) + R if ha != "-" and float(ha) > 0 else None
        if rp is not None and rp <= R + 1 and ra is None:
            rp = None
        return dict(center=body, rp=rp, ra=ra, i=float(m.group(4)) if m.group(4) != "-" else None,
                    epoch=norm_date(m.group(6)), oporbit=m.group(5) or "")
    m = re.search(r"heliocentric q=([\d.]+|-) Q=([\d.]+|-) AU i=([\d.]+|-) \(epoch ([^)]*)\)", now)
    if m:
        q, Q = m.group(1), m.group(2)
        return dict(center="sun", rp=float(q) * AU if q != "-" else None, ra=float(Q) * AU if Q != "-" else None,
                    i=float(m.group(3)) if m.group(3) != "-" else None, epoch=norm_date(m.group(4)), oporbit="HCO")
    m = re.search(r"at (EML1|EML2|SEL1|SEL2) \(epoch ([^)]*)\)", now)
    if m:
        return dict(center="", rp=None, ra=None, i=None, epoch=norm_date(m.group(2)), oporbit=m.group(1))
    return None


def max_geo_apogee(phases):
    """Largest geocentric apogee radius (km) among bound geocentric orbits in the GCAT phase chain."""
    best = 0.0
    for _a, b in re.findall(r"\((?:CLO|DSO|VHEO|HEO|GTO|MEO|GEO/\w+) (-?\d+)x(-?\d+) i", phases):
        if int(b) > 0:
            best = max(best, int(b) + R_E)
    for _a, b in re.findall(r"S\d+:\w+@[^ ]+(?: \d\d:\d\d\??)? (?:CLO|DSO|VHEO|HEO|GEO/\w+) (-?\d+)x(-?\d+)x", phases):
        if int(b) > 0:
            best = max(best, int(b) + R_E)
    return best


def max_geo_period(phases):
    best = 0.0
    for a, b in re.findall(r"\((?:CLO|DSO|VHEO|HEO|GTO|MEO|GEO/\w+) (-?\d+)x(-?\d+) i", phases):
        if int(b) > 0:
            best = max(best, period_days((int(a) + int(b)) / 2 + R_E, "earth") or 0)
    return best


def gcat_last_lunar_orbit(phases):
    """Last bound selenocentric orbit in the GCAT phase chain -> dict(epoch, rp, ra, i) (altitudes -> radii), or None.

    A phase reads 'P=<parent> <start> -> Luna -> <event> @<end> (PCO <hp>x<ha> i<incl>)'; the orbit in parentheses
    is the one flown from <start>, so <start> is used as the element epoch."""
    best = None
    for m in re.finditer(r"P=\S+ (\d{4}-\d\d-\d\d(?: \d\d:\d\d)?)\?? -> Luna -> (\S+)[^(>]*\(PCO (-?\d+)x(-?\d+) i(-|[\d.]+)\)",
                         phases):
        hp, ha = int(m.group(3)), int(m.group(4))
        if hp >= 0 and ha >= hp:
            best = dict(epoch=to_dt(m.group(1)), rp=hp + R_M, ra=ha + R_M,
                        i=float(m.group(5)) if m.group(5) != "-" else None)
        if m.group(2).startswith("L:"):        # landed: later phases (TX, ...) are on the surface
            break
    return best


def gcat_last_geo_orbit(phases):
    """Last bound geocentric orbit in the GCAT phase chain ('P=<parent> <start> -> Earth -> <event> @<end> (<OPORBIT>
    <hp>x<ha> i<incl>)') -> dict(epoch, rp, ra, i) with radii, or None.  Used for decayed 2019-2026 mission objects."""
    best = None
    for m in re.finditer(r"P=\S+ (\d{4}-\d\d-\d\d(?: \d\d:\d\d)?)\?? -> Earth -> \S+[^(>]*"
                         r"\((?:CLO|DSO|VHEO|HEO|GTO|MEO|TA|GEO/\w+|LLEO/\w+|LEO/\w+) (-?\d+)x(-?\d+) i(-|[\d.]+)\)", phases):
        hp, ha = int(m.group(2)), int(m.group(3))
        if ha > 0 and ha >= hp and hp > -R_E:
            best = dict(epoch=to_dt(m.group(1)), rp=hp + R_E, ra=ha + R_E,
                        i=float(m.group(4)) if m.group(4) != "-" else None)
    return best


def helio_departure(phases):
    m = re.search(r"EO:HCO @(\S+)", phases)
    return to_dt(m.group(1).rstrip("?")) if m else None


def end_date(now):
    m = re.search(r"(?:impacted Moon|reentered(?: attached)?|landed/splashed|on lunar surface since|"
                  r"planned suborbital reentry|launch failure|lifted off from Moon) (\d{4}(?:-\d\d)?(?:-\d\d)?)", now)
    return m.group(1) if m else ""


# ============================================================ curated facts
# (launch tag, GCAT name) -> regime / status / status_date / name / note.  Notes cite the wiki section; facts not in
# the wiki are labelled (Horizons OBJ_DATA), (Space-Track) or (memory).
HAND = {
    # --- lunar orbit, operating
    ("2009-031", "Lunar Reconnaissance Orbiter"): dict(status="active", status_date=WIKI_DATE, name="LRO",
        note="operating, ~66x112 km polar (wiki §3 LRO+LCROSS)"),
    ("2019-042", "Chandrayaan-2"): dict(status="active", status_date=WIKI_DATE, name="Chandrayaan-2 orbiter",
        note="operating ~100 km polar orbit (wiki §3 Chandrayaan-2)"),
    ("2022-094", "Danuri"): dict(status="active", status_date=WIKI_DATE, name="Danuri (KPLO)",
        note="operating ~100 km polar, extended to >=2027 (wiki §3 Danuri); Horizons KPLO_merged tag-up + prediction to 2027-05-06"),
    ("2007-004", "THEMIS B"): dict(status="active", status_date=WIKI_DATE, name="ARTEMIS P1 (THEMIS B)",
        note="operating, high eccentric retrograde lunar orbit (wiki §3 ARTEMIS P1/P2)"),
    ("2007-004", "ARTEMIS P2"): dict(status="active", status_date=WIKI_DATE, name="ARTEMIS P2 (THEMIS C)",
        note="operating, high eccentric lunar orbit (wiki §3 ARTEMIS P1/P2)"),
    ("2024-051", "Queqiao-2"): dict(status="active", status_date="2024-09-16",
        note="relay for Chang'e 6/7/8 in a 24-h elliptical frozen lunar orbit (ELFO) since 2024-03-24 (wiki §3 Queqiao-2); "
             "status_date = last GCAT orbit epoch; no public ephemeris"),
    ("2024-051", "Tiandu-2"): dict(status="unknown", status_date="2024-04-03",
        note="navigation test sat, 289x10000 km lunar orbit per GCAT 2024-04-03; operating status not stated (wiki §3 Queqiao-2)"),
    ("2022-070", "CAPSTONE"): dict(regime="EM_L2", status="derelict", status_date="2026-07-28",
        note="9:2 L2 southern NRHO since 2022-11-13 (wiki §3 CAPSTONE); Horizons OBJ_DATA 2026-07-28: 'no further updates "
             "for this completed NASA mission', nav data through 2026-07-15, prediction to 2026-08-14 (operations after "
             "mission end not stated: 'derelict' is an inference)"),
    ("2024-048", "DRO-A"): dict(regime="EM_DRO", status="unknown", status_date="",
        note="Earth-Moon DRO since 2024-07-15 after rescue (wiki §3 DRO-A/DRO-B); GCAT books it selenocentric; "
             "tracked by Space-Track (geocentric mean elements of a DRO: SDP4 propagation is only a rough guide); "
             "operating status not stated in the wiki; status_date = latest Space-Track GP epoch"),
    ("2024-048", "DRO-B"): dict(regime="EM_DRO", status="unknown", status_date="",
        note="companion DRO; NORAD 60058 per wiki §5.5 / Bill Gray tle_list (GCAT Satcat NNA); Bill Gray recovered it "
             "2025-10/11 (wiki §3 DRO-A/DRO-B); Space-Track geocentric mean elements of a DRO are only a rough guide; "
             "status_date = latest Space-Track GP epoch"),
    # --- lunar orbit, derelict / unknown
    ("2008-052", "Chandrayaan-1"): dict(status="derelict", status_date="2016-07-02",
        note="contact lost 2009-08-29; re-found by Goldstone radar 2016-07, ~200 km polar; Horizons -86 arc 2016-2022 "
             "(wiki §3 Chandrayaan-1)"),
    ("1973-039", "RAE 2"): dict(status="derelict", status_date="",
        note="Explorer 49 (RAE-B); GCAT selenocentric, never tracked since (wiki §5.1, §7); operations ended 1977 (memory)"),
    ("1973-039", "RAE Lunar Insertion Motor"): dict(status="derelict", status_date="1973-06-18",
        note="released in lunar orbit (GCAT; wiki §5.1)"),
    ("1973-039", "RAE VCPS"): dict(status="derelict", status_date="1973-07-09",
        note="velocity-control propulsion system released in lunar orbit (GCAT; wiki §5.1)"),
    ("1990-007", "Hagoromo"): dict(status="lost", status_date="1990-03-18",
        note="transmitter failed before release; 7400x20000 km lunar orbit inferred optically; KM-L motor attached; "
             "no observation since 1990 (wiki §3 Hiten, §7)"),
    ("2020-087", "Chang'e-5 Lianje Zhijia"): dict(status="unknown", status_date="2020-11-28",
        name="Chang'e 5 orbiter-lander adapter",
        note="adapter left in a 75x220 km lunar orbit (GCAT 2020-11-28); such a low orbit probably decayed since, but no "
             "record (wiki §3 Chang'e 5)"),
    ("2024-083", "Lianje Zhijia"): dict(status="unknown", status_date="2024-05-08", name="Chang'e 6 orbiter-lander adapter",
        note="adapter left in a 200x8600 km lunar orbit (GCAT 2024-05-08) (wiki §3 Chang'e 6)"),
    ("1967-070", "Explorer 35"): dict(status="unknown", status_date="1973-06-24",
        note="L? operated in lunar orbit until 1973-06-24; impact date unknown (wiki §5.1, §7)"),
    ("1967-070", "Star 13"): dict(status="unknown", status_date="1967-07-22", name="Explorer 35 Star 13 insertion motor",
        note="L? released in lunar orbit 1967-07-22; impact date unknown (wiki §5.1)"),
    ("1969-U01", "Eagle"): dict(status="unknown", status_date="1969-07-21", name="Apollo 11 LM Eagle ascent stage",
        norad="4041", cospar="1969-059C",
        note="L? jettisoned 1969-07-21 into 99x117 km orbit; Meador (2021) suggests it may still orbit (wiki §5.1, §7)"),
    # --- libration points
    ("2018-045", "Queqiao"): dict(regime="EM_L2", status="active", status_date=WIKI_DATE, name="Queqiao (Chang'e 4 relay)",
        note="EM-L2 halo relay since 2018-06-14, still on station (wiki §3 Queqiao-1; GCAT 'in orbit at EML2'); "
             "no Horizons ephemeris; Bill Gray 18045a.tle covers launch week only"),
    ("1994-071", "Wind"): dict(regime="SE_L1", status="active", status_date=WIKI_DATE,
        note="non-lunar mission; lunar swing-bys 1994-2004, at SE-L1 since 2004 (wiki §4); operating (memory)"),
    # --- cislunar / HEO
    ("2018-038", "TESS"): dict(regime="EM_OTHER", status="active", status_date=WIKI_DATE,
        note="P/2 lunar-resonant HEO since the 2018-05-17 lunar flyby (wiki §4); operating (memory)"),
    ("1992-044", "Geotail"): dict(status="derelict", status_date="2022-11-28",
        note="switched off 2022-11-28; lunar swing-bys 1992-94 (wiki §4)"),
    ("1992-044", "Star 48B"): dict(name="Geotail Delta 212 Star 48B third stage",
        note="lost in lunar-transfer ellipse 1992 (GCAT 184x360211 km; wiki §4)"),
    ("1992-044", "Star 48 yo weight"): dict(name="Geotail Star 48 yo weight", note="lost 1992 (GCAT; wiki §4)"),
    ("2013-070", "CZ-3B Y23 Stage 3"): dict(name="Chang'e 3 CZ-3B Y23 stage 3",
        note="lunar-perturbed cislunar orbit since 2013, still tracked (wiki §3 Chang'e 3); Horizons -139459 = 2021 arc only"),
    ("2023-098", "Chandrayaan-3 PM"): dict(status="lost", status_date="2025-12-29", name="Chandrayaan-3 propulsion module",
        note="returned to HEO 2023-11; lunar flybys; GCAT 2025-12-29 365307x983024 km 'lost' (wiki §3 Chandrayaan-3); "
             "Horizons -169 covers only the 2023 outbound arc"),
    ("2024-051", "Tiandu-1"): dict(status="unknown", status_date="2025-05-24",
        note="left lunar orbit ~2025-03, now 50097x305374 km Earth orbit (GCAT 2025-05-24; wiki §3 Queqiao-2, §5.5)"),
    ("2024-083", "Chang'e-6 Guidao Qi"): dict(status="unknown", status_date="2025-12-30", name="Chang'e 6 orbiter",
        note="extended mission: SE-L2 2024-09, back to cislunar; GCAT 2025-12-30 350556x560657 km i83 (wiki §3 Chang'e 6); "
             "operating status not stated"),
    ("2025-038", "Chimera GEO 1"): dict(status="lost", status_date="2025-03-06",
        note="Epic Aerospace GEO tug left in a 40000x380000 km orbit, lost (wiki §3 IM-2)"),
    ("2025-038", "Orbit Guard 2"): dict(status="unknown", status_date="2025-03-06",
        note="flew with Chimera GEO-1 (wiki §3 IM-2: 'Chimera GEO-1 with Orbit Guard 2'); GCAT gives it its own row with "
             "Chimera's 40000x380000 km orbit - may be the same physical object"),
    ("2018-045", "Longjiang 1"): dict(status="lost", status_date="2018-05-25",
        note="lost attitude after the lunar-transfer burn; ~20000x400000 km Earth orbit (wiki §3 Queqiao-1)"),
    ("2022-156", "EQUULEUS"): dict(status="lost", status_date="2023-08-24",
        note="headed for EM-L2 via lunar flyby; GCAT lost 60387x549327 km 2022-11-25 (wiki §3 Artemis I, which saw Horizons "
             "-101 end 2022-11-23); Horizons -101 OBJ_DATA (revised 2023-09-05): 'weekly tracking data tag-up through Aug 24 "
             "[2023], predicts thereafter' to 2023-12-31, so status_date = 2023-08-24 (year inferred from the revision date)"),
    ("2022-156", "Lunar IceCube"): dict(status="lost", status_date="2022-11-23",
        note="lost in cislunar space, no orbit published (wiki §3 Artemis I)"),
    ("1998-041", "KM-V1"): dict(status="lost", status_date="1998-07-22", name="Nozomi KM-V1 kick motor",
        note="lost in 1916x592196 km Earth orbit (wiki §4)"),
    ("2024-048", "Yuanzheng-1S Y11"): dict(regime="CISLUNAR_HEO", status="derelict",
        note="DRO-A/B kick stage, ~1000x125000 km alt: P ~2.1 d (< 2.4 d) but apogee radius > 100000 km "
             "(wiki §3 DRO-A/DRO-B, §5.2a)"),
    ("1997-086", "HGS-1"): dict(regime="OTHER", status="derelict", status_date="2002-01-01",
        note="OTHER: now an inclined GEO (P ~1 d); listed for its two circumlunar flybys 1998-05/06 (wiki §3 HGS-1); retired 2002"),
    ("2026-069", "SWC-1"): dict(regime="OTHER", status="unknown", status_date="2026-04-17",
        note="OTHER: Artemis II cubesat left in a 492x70228 km HEO, P ~1 d < 2.4 d; listed because a 2026 mission object "
             "with Horizons -168540 (wiki §3 Artemis II)"),
    ("1963-008", "Luna-4"): dict(name="Luna 4",
        note="GCAT lost cislunar 90000x700000 km 1963 (wiki §5.2b); other sources say it escaped to solar orbit (wiki §7); "
             "ALS lander capsule and two jettisonable compartments were attached"),
    ("1966-058", "Explorer 33"): dict(note="AIMP-D: failed lunar orbit insertion, left in 63200x430000 km Earth orbit "
                                          "(GCAT 1967-08-25), untracked (wiki §5.2b)"),
    # --- heliocentric
    ("2022-156", "BioSentinel 1"): dict(status="active", status_date=WIKI_DATE, name="BioSentinel",
        note="heliocentric and alive per wiki §3 Artemis I"),
    ("2023-053", "JUICE"): dict(status="active", status_date=WIKI_DATE,
        note="non-lunar mission; LEGA 2024-08 (wiki §4); Bill Gray Warn 'JUICE flyby in September' (2026 Earth flyby)"),
    ("2006-047", "STEREO A"): dict(status="active", status_date=WIKI_DATE,
        note="non-lunar mission, lunar swing-by 2006-12-15 (wiki §4); operating (memory)"),
    ("2006-047", "STEREO B"): dict(status="lost", status_date="2014-10-01",
        note="non-lunar mission (wiki §4); contact lost 2014-10 (memory)"),
    ("1978-079", "ISEE 3"): dict(status="derelict", status_date="2014-09-16", name="ISEE-3 / ICE",
        note="non-lunar mission; returned past Earth 2014-08-10 (wiki §4); contact lost 2014-09 (memory)"),
    ("2001-027", "WMAP"): dict(status="derelict", status_date="2010-10-20",
        note="non-lunar mission; retired 2010 to a heliocentric graveyard (wiki §4); date (memory)"),
    ("1998-041", "Nozomi"): dict(status="derelict", status_date="2003-12-09",
        note="non-lunar mission; Mars mission failed 2003 (wiki §4); date (memory)"),
    ("2020-087", "Chang'e-5 Guidao Qi"): dict(status="unknown", status_date="2024-04-01", name="Chang'e 5 orbiter",
        note="extended mission SE-L1 2021 -> DRO 2022 -> Earth co-orbital heliocentric since 2024-04 (GCAT; wiki §3 Chang'e 5, "
             "§7); last Space-Track GP 2024-02-22"),
    ("2010-050", "CZ-3C Y7 Stage 3"): dict(name="Chang'e 2 CZ-3C Y7 stage 3",
        note="~9 yr cislunar, escaped 2019-2020 (Bill Gray 10050b*.tle; wiki §3 Chang'e 2, §5.5)"),
    ("2010-050", "Chang'e-2"): dict(status="unknown", name="Chang'e 2",
        note="SE-L2 2011, Toutatis flyby 2012-12-13, heliocentric (wiki §3 Chang'e 2)"),
    ("2018-103", "CZ-3B Y30 Stage 3"): dict(name="Chang'e 4 CZ-3B Y30 stage 3",
        note="~4 yr cislunar (Space-Track GP 2024-04-05 705392x897754 km), heliocentric per Bill Gray 18103b24.tle "
             "(wiki §3 Chang'e 4, §5.5)"),
    ("1966-084", "Centaur AC-7"): dict(name="Surveyor 2 Centaur AC-7 (2020 SO)",
        note="= 2020 SO, temporarily captured 2020-11..2021-03 (memory); not in JPL SBDB (checked 2026-10-20); wiki §5.3-5.4"),
    ("1969-099", "Saturn S-IVB-507"): dict(name="Apollo 12 S-IVB-507 (J002E3)",
        note="= J002E3, temporary capture 2002-2003 (memory); Horizons -998 'NEOCP J002E3' (wiki §5.3-5.4, §7)"),
    ("1969-043", "Snoopy"): dict(name="Apollo 10 LM-4 Snoopy ascent stage",
        note="candidate = 2018 AV2 (Bill Gray, unconfirmed; Warn 2037-May; not in JPL SBDB, checked 2026-10-20) (wiki §5.4, §7)"),
    ("2022-070", "Photon Lunar"): dict(name="CAPSTONE Lunar Photon kick stage",
        note="override: GCAT 'lost in Earth orbit' vs Bill Gray 22070c / rocketdebris heliocentric (wiki §5.5, §7)"),
    ("2024-006", "Centaur V-001"): dict(name="Peregrine / Vulcan Cert-1 Centaur V-001",
        note="heliocentric via lunar flyby, Celestis 'Enterprise Station' attached (wiki §3 Peregrine)"),
    ("2025-038", "Falcon 9-444 Stage 2"): dict(name="IM-2 Falcon 9-444 stage 2",
        note="heliocentric with ESPA ring (wiki §3 IM-2, §5.4)"),
    ("2024-030", "Falcon 9-301 Stage 2"): dict(name="IM-1 Falcon 9-301 stage 2",
        note="heliocentric via lunar flyby 2024-02 (wiki §3 IM-1, §5.4)"),
    ("2022-168", "Falcon 9-190 Stage 2"): dict(name="HAKUTO-R M1 Falcon 9-190 stage 2", note="heliocentric (wiki §3 HAKUTO-R M1)"),
    ("2022-094", "Falcon 9-169 Stage 2"): dict(name="Danuri Falcon 9-169 stage 2", note="heliocentric 0.990x1.001 AU per GCAT (wiki §3 Danuri)"),
    ("2023-053", "Ariane 5 L5120 ESC-D"): dict(name="JUICE Ariane 5 ESC-D upper stage", note="heliocentric (wiki §4)"),
    ("2011-046", "Delta 356"): dict(name="GRAIL Delta II stage 2 (Delta 356)", note="heliocentric (wiki §3 GRAIL)"),
    ("2022-156", "ICPS 01"): dict(name="Artemis I ICPS", note="heliocentric with OSA + Spacecraft Adapter Cone attached (wiki §3 Artemis I)"),
    ("2018-038", "Falcon 9-052 Stage 2"): dict(name="TESS Falcon 9-052 stage 2", note="heliocentric via own lunar flyby (wiki §4)"),
    ("2025-038", "Lunar Trailblazer"): dict(status="escaped", note="lost power hours after separation 2025-02-27; heliocentric (wiki §3 IM-2)"),
    ("2025-038", "Odin"): dict(status="escaped", name="Odin (AstroForge)", note="contact lost; heliocentric (wiki §3 IM-2)"),
    ("2022-168", "Lunar Flashlight"): dict(status="escaped", note="thrusters clogged, never reached lunar orbit; heliocentric (wiki §3 HAKUTO-R M1)"),
    ("2022-156", "OMOTENASHI"): dict(status="escaped", note="lost, heliocentric (wiki §3 Artemis I)"),
    ("2022-156", "NEA Scout"): dict(status="escaped", note="never contacted, heliocentric (wiki §3 Artemis I)"),
    # --- surface / impact / reentered / returned, listed only because a 2019-2026 ephemeris exists
    ("2019-042", "Vikram"): dict(regime="LUNAR_ORBIT", name="Chandrayaan-2 lander Vikram",
        note="crashed 2019-09-06 with Pragyan (wiki §3 Chandrayaan-2)"),
    ("2014-065", "CZ-3C Y12 Stage 3"): dict(regime="CISLUNAR_HEO", name="Chang'e 5-T1 CZ-3C Y12 stage 3 (WE0913A)",
        note="= WE0913A, ~7 yr cislunar; impacted far side 2022-03-04 12:25 UTC (wiki §3 Chang'e 5-T1); 4M payload attached; "
             "listed although a 2014 launch because it was a cislunar resident until 2022 with Horizons -78000"),
    ("2022-156", "Artemis I"): dict(regime="TRANSLUNAR", name="Artemis I Orion CM002",
        note="DRO test flight; splashdown 2022-12-11 (wiki §3 Artemis I)"),
    ("2022-168", "Hakuto-R M1"): dict(regime="LUNAR_ORBIT", name="HAKUTO-R M1 lander",
        note="low-energy transfer, lunar orbit 2023-03-21, crashed 2023-04-25 (wiki §3 HAKUTO-R M1)"),
    ("2023-098", "Chandrayaan-3"): dict(regime="LUNAR_ORBIT", name="Chandrayaan-3 lander Vikram",
        note="landed Shiv Shakti 2023-08-23 (wiki §3 Chandrayaan-3)"),
    ("2023-118", "Fregat No. 122-10"): dict(regime="CISLUNAR_HEO", name="Luna 25 Fregat No. 122-10",
        note="own lunar flyby into high Earth orbit; reentered 2024-02-26 (wiki §3 Luna 25)"),
    ("2023-137", "SLIM"): dict(regime="LUNAR_ORBIT", note="landed 2024-01-19 (wiki §3 SLIM)"),
    ("2023-137", "H-2A F47 Stage 2"): dict(regime="CISLUNAR_HEO", name="SLIM/XRISM H-IIA F47 stage 2",
        note="left in SLIM's 561x97819 km injection orbit (GCAT), reentered 2025-01-03 (wiki §3 SLIM)"),
    ("2024-006", "Peregrine Mission One"): dict(regime="CISLUNAR_HEO",
        note="propellant leak; flew to ~390000 km and back, reentered 2024-01-18 (wiki §3 Peregrine)"),
    ("2024-030", "IM-1"): dict(regime="LUNAR_ORBIT", name="IM-1 Odysseus", note="landed Malapert A 2024-02-22 (wiki §3 IM-1)"),
    ("2025-010", "Blue Ghost"): dict(regime="LUNAR_ORBIT", name="Blue Ghost M1", note="landed Mare Crisium 2025-03-02 (wiki §3 Blue Ghost M1)"),
    ("2025-010", "Resilience"): dict(regime="LUNAR_ORBIT", name="HAKUTO-R M2 Resilience",
        note="lunar orbit 2025-05-06, crashed 2025-06-05 (wiki §3 Blue Ghost M1)"),
    ("2025-010", "Falcon 9-427 Stage 2"): dict(regime="CISLUNAR_HEO", name="Blue Ghost M1 Falcon 9-427 stage 2",
        note="lunar-perturbed cislunar orbit 2025-26 (Space-Track GP 2026-08-14 145686x398407 km); struck the Moon "
             "2026-08-05 06:35 UTC 19.6N 93.4W (GCAT; wiki §3 Blue Ghost M1)"),
    ("2025-010", "MPC Upper Section"): dict(regime="CISLUNAR_HEO", name="Blue Ghost M1 payload-canister upper section",
        note="left in the 189x322453 km transfer orbit (GCAT), reentered 2025-03-15 (wiki §3 Blue Ghost M1)"),
    ("2020-087", "CZ-5 Y5 Stage 2"): dict(regime="CISLUNAR_HEO", name="Chang'e 5 CZ-5 Y5 stage 2",
        note="left on the 181x295830 km translunar ellipse, reentered 2020-11-30 (wiki §3 Chang'e 5)"),
    ("2026-069", "Artemis II"): dict(regime="TRANSLUNAR", name="Artemis II Orion Integrity",
        note="crewed free-return lunar flyby 2026-04-06/07; splashdown 2026-04-11 (wiki §3 Artemis II)"),
    # --- added on review (2026-10-21)
    ("1969-043", "LM 4 DS"): dict(name="Apollo 10 LM-4 Snoopy descent stage",
        note="descent stage jettisoned in lunar orbit 1969-05-22 (wiki §3 Apollo 10)"),
    ("1972-U02", "Orion"): dict(name="Apollo 16 LM-11 Orion ascent stage", norad="6005", cospar="1972-031C",
        note="ascent stage lost attitude control after jettison 1972-04-24 and was not deorbited (wiki §3 Apollo 16, §6.2); "
             "GCAT carries the post-jettison stage as 1972-U02"),
    ("2007-039", "Ouna"): dict(
        note="VRAD sub-satellite left in its ~100x800 km orbit 2009-06 (wiki §3 Kaguya); Horizons -39 is flagged "
             "'OBSOLETE: Ouna nominal trajectory ... a propagation ... for testing purposes only' (OBJ_DATA), so it is not "
             "an observed ephemeris"),
    ("1967-021", "Kosmos-146"): dict(status="decayed", status_date="1967-03-18",
        note="GCAT books it lost in a 250x400000 km CLO (epoch 1967-03-11) while Space-Track SATCAT gives DECAY 1967-03-18; "
             "a perigee-250 km orbit of P ~10 d makes a reentry at the first perigee return plausible (inference) (wiki §3 "
             "Kosmos 146); listed for its CLO epoch"),
    ("2024-051", "CZ-8 Y3 Stage 2"): dict(regime="CISLUNAR_HEO", name="Queqiao-2 CZ-8 Y3 stage 2",
        note="left in the 76x373791 km Earth-Moon transfer orbit (GCAT), reentered 2024-04-24 07:06 UTC 14.8W 36.2S "
             "(wiki §3 Queqiao-2)"),
    ("2024-083", "ICUBE-Q"): dict(name="ICUBE-Q (Chang'e 6 cubesat)",
        note="Pakistan's first lunar satellite, released 2024-05-08, in a 323x8704 km lunar orbit until its decay (wiki §3 "
             "Chang'e 6)"),
}


# ============================================================ main build
def main():
    CACHE_HZ.mkdir(parents=True, exist_ok=True)
    CACHE_ST.mkdir(parents=True, exist_ok=True)

    # ---- 1. objects with wiki buckets (same logic as build_wiki.py): lunar missions + §4 flyby missions
    objs = []
    for o in GCAT:
        if o["Now_category"] == "SEE_PARENT":
            continue
        tag = o["Launch_Tag"]
        if tag not in TAG2MISSION and tag not in TAG2FLYBY:
            continue
        cat, now, ovr = apply_overrides(tag, o["Name"], o["Now_category"], o["Now"])
        reg = TAG2MISSION.get(tag) or TAG2FLYBY.get(tag)
        objs.append(dict(o, cat=cat, now=now, overridden=ovr, flyby=tag in TAG2FLYBY and tag not in TAG2MISSION,
                         mission=reg["name"], mdate=reg["date"],
                         sat=o["Satcat"].strip() if o["Satcat"].strip().isdigit() else ""))
    scope_counts = collections.Counter(BADGE.get(o["cat"], o["cat"]) for o in objs if not o["flyby"])
    flyby_counts = collections.Counter(BADGE.get(o["cat"], o["cat"]) for o in objs if o["flyby"])

    # ---- 2. fold attached components into their carrier (same tag + same Satcat; plus Luna 4's ALS)
    by_key = collections.defaultdict(list)
    for o in objs:
        if o["sat"]:
            by_key[(o["Launch_Tag"], o["sat"])].append(o)
    folded = {}
    for grp in by_key.values():
        if len(grp) < 2:
            continue
        prim = [g for g in grp if g["Type"][:1] in "PR" and g["Type"][:2] != "PX"]
        if not prim:
            continue
        p = prim[0]
        for g in grp:
            if g is p:
                continue
            attached = (g["Now_category"] == p["Now_category"] or "attached" in g["Now"] or g["Type"][2:3] == "A"
                        or g["cat"] == p["cat"])
            if (g["Type"][:1] == "C" or g["Type"][:2] == "PX") and attached:
                folded[g["JCAT"]] = p["JCAT"]
    luna4 = [o for o in objs if o["Launch_Tag"] == "1963-008" and o["Name"] == "Luna-4"]
    for o in objs:
        if o["Launch_Tag"] == "1963-008" and o["Name"] == "ALS" and luna4:
            folded[o["JCAT"]] = luna4[0]["JCAT"]      # lander capsule never separated (Satcat 00563 is the Blok-I)
        if o["Launch_Tag"] == "1963-008" and o["Name"].startswith("Otdelyaemiy") and luna4:
            folded[o["JCAT"]] = luna4[0]["JCAT"]
    for o in objs:
        o["folded_into"] = folded.get(o["JCAT"], "")
        o["attached_items"] = [x["Name"] for x in objs if folded.get(x["JCAT"]) == o["JCAT"]]

    # ---- 3. Horizons map (explicit) + MB-list cross-check
    mb = parse_mb_list()
    for o in objs:
        key = (o["Launch_Tag"], o["Name"])
        ids = []
        lab = BW["HZ_BY_NORAD"].get(int(o["sat"])) if o["sat"] else None
        lab = lab or BW["HZ_BY_NAME"].get(o["Name"])
        if lab and lab in HZ_SRC and HZ_SRC[lab]["HorizonsID"] and key not in HZ_DROP and not o["folded_into"]:
            ids.append(HZ_SRC[lab]["HorizonsID"])
        for h in HZ_EXTRA.get(key, []):
            if h not in ids:
                ids.append(h)
        if key == ("1994-004", "Clementine 1"):
            ids = ["-40000", "-40"]
        o["hzids"] = ids
    mapped = {h for o in objs for h in o["hzids"]}
    lunar_pieces = {o["Piece"].strip() for o in objs if re.match(r"\d{4}-\d{3}[A-Z]+$", o["Piece"].strip())}
    mb_unmapped = {k: v for k, v in mb.items() if v["cospar"] in lunar_pieces and k not in mapped}

    def cospar_of(o):
        if re.match(r"\d{4}-\d{3}[A-Z]+$", o["Piece"].strip()):
            return o["Piece"].strip()
        if o["sat"] and int(o["sat"]) in ST_SRC:
            return ST_SRC[int(o["sat"])]["INTLDES"]
        return ""

    # ---- 4. candidate selection
    unloc = {(r["Launch_Tag"], r["Object"]) for r in UNLOCATED
             if r["Object"] not in ("Sbrasivaemiy otsek", "8K72E No. I1-7B")}
    cands, excluded, excluded_eph = [], collections.Counter(), []
    for o in objs:
        if o["folded_into"]:
            continue
        o["cospar"] = cospar_of(o)
        if not o["sat"] and o["cospar"]:
            o["sat_bg"] = tle_list_norad(o["cospar"])
        o["bg"] = bg_files(o["sat"] or o.get("sat_bg", ""), o["cospar"]) if (o["sat"] or o["cospar"]) else []
        o["bg_alias"] = bg_files_by_name(o["Launch_Tag"], o["Name"], {f[0] for f in o["bg"]})
        o["bg"] += [x[:3] for x in o["bg_alias"]]
        has_eph = bool(o["hzids"] or o["bg"])
        c = o["cat"]
        g = gcat_elements(o["now"]) or {}
        reason = None
        ed = end_date(o["now"])
        # 2019-2026 mission objects, or older objects whose Horizons arc shows them cislunar after 2019 (WE0913A)
        recent = o["mdate"] >= "2019" or (bool(o["hzids"]) and ed >= "2019")
        beyond = bool(re.search(r"\b(CLO|DSO|EEO|VHEO)\b|Luna|EML|SEL", o["Phases"]))
        if c in ("LUNAR_ORBIT", "LUNAR_ORBIT_DECAYED_UNKNOWN", "L_POINT", "CISLUNAR"):
            reason = c
        elif c == "EARTH_ORBIT":
            apo = max(max_geo_apogee(o["Phases"]), (g.get("ra") or 0) if g.get("center") == "earth" else 0)
            if apo >= 100000 or max_geo_period(o["Phases"]) > 2.4 or o["hzids"] or "Luna" in o["Phases"]:
                reason = "EARTH_ORBIT_HIGH"
        elif c == "HELIOCENTRIC":
            if has_eph or warn_for(o["cospar"], []) or o["mdate"] >= "2019" or (g.get("epoch") or "") >= "2024":
                reason = "HELIOCENTRIC"
        elif (o["Launch_Tag"], o["Name"]) in unloc:
            reason = "PRESUMED_IMPACT"
        elif has_eph and recent and beyond:
            reason = "EPHEM_" + c
        if reason:
            o["reason"], o["g"] = reason, g
            cands.append(o)
        else:
            excluded[(BADGE.get(c, c), "flyby" if o["flyby"] else "lunar")] += 1
            if has_eph:
                excluded_eph.append(f"{o['Name']} ({o['Launch_Tag']}, {BADGE.get(c, c)}): "
                                    + ",".join(o["hzids"] + [b[0] for b in o["bg"]]))

    # ---- 5. network: Horizons profiles, Space-Track current GP / SATCAT
    hz_all = sorted({h for o in objs for h in o["hzids"]})
    lunar_ids = {h for o in cands if o["cat"] in ("LUNAR_ORBIT", "LUNAR_ORBIT_DECAYED_UNKNOWN", "L_POINT")
                 or o["reason"] == "PRESUMED_IMPACT" for h in o["hzids"]}
    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        prof = dict(zip(hz_all, ex.map(lambda h: hz_profile(h, h in lunar_ids), hz_all)))
    norads = {int(o["sat"] or o.get("sat_bg") or 0) for o in cands} - {0}
    nna_cospars = {o["cospar"] for o in cands if not o["sat"] and o["cospar"]}
    gp_now, satcat_now, satcat_id = spacetrack_current(norads, nna_cospars)
    ejd, exyz = earth_helio_table()

    # ---- 6. build rows
    rows = []
    for o in cands:
        key = (o["Launch_Tag"], o["Name"])
        h = HAND.get(key, {})
        g = o["g"]
        ed = end_date(o["now"])          # (was leaking from the selection loop -> wrong status_date)
        c = o["cat"]
        sat = o["sat"] or o.get("sat_bg", "") or h.get("norad", "")
        cospar = o["cospar"] if re.match(r"\d{4}-\d{3}[A-Z]+$", o["cospar"] or "") else (h.get("cospar") or o["cospar"])
        notes, aliases = [f"mission {o['mission']} ({o['mdate']})"], []
        if not o["sat"] and o.get("sat_bg"):
            notes.append(f"NORAD {sat} from Bill Gray tle_list.txt (GCAT Satcat NNA)")
        elif not o["sat"] and h.get("norad"):
            nm = (ST_SRC.get(int(sat)) or {}).get("OBJ_NAME", "")
            notes.append(f"NORAD {sat} / {cospar} from Space-Track '{nm}' (wiki spacetrack_status.tsv; GCAT row is NNA)")
        sc_id = None
        if not sat and cospar in satcat_id:
            sc_id = satcat_id[cospar]
            sat = sc_id["NORAD_CAT_ID"]
            notes.append(f"NORAD {sat} from Space-Track SATCAT by INTLDES (GCAT Satcat NNA; SATNAME '{sc_id.get('SATNAME')}')")
        if o["PLName"] not in ("", "-", o["Name"]):
            aliases.append(o["PLName"])
        name = h.get("name") or o["Name"]
        if name != o["Name"]:
            aliases.append(o["Name"])
        aliases += o["attached_items"]
        if o["flyby"]:
            notes[0] = f"non-lunar mission {o['mission']} ({o['mdate']}) (wiki §4 lunar swing-bys)"
        if h.get("note"):
            hn = re.sub(r"^non-lunar mission[;,]?\s*", "", h["note"]) if o["flyby"] else h["note"]
            notes.append(hn)
        if o["attached_items"]:
            notes.append("attached/folded: " + ", ".join(o["attached_items"]))
        if o["overridden"]:
            notes.append("wiki bucket from build_wiki OVERRIDES / 'EEO lost' rule")
        st_now = gp_now.get(int(sat)) if sat else None
        sc_now = (satcat_now.get(int(sat)) if sat else None) or sc_id
        st_old = ST_SRC.get(int(sat)) if sat else None
        for nm in ((st_now or {}).get("OBJECT_NAME"), (sc_now or {}).get("SATNAME"), (st_old or {}).get("OBJ_NAME")):
            if nm and nm not in aliases and nm != name:
                aliases.append(nm)
        if not cospar and sc_now:
            cospar = sc_now.get("INTLDES", "")
        # ---------------- ephemeris sources
        eph, covers, hz_best = [], False, None
        for hid in o["hzids"]:
            p = prof.get(hid) or {}
            if not p.get("ok") and not p.get("start") and not p.get("end"):
                notes.append(f"Horizons {hid}: {p.get('err', 'not resolvable')[:60]}")
                continue
            s, e = p.get("start"), p.get("end")
            eph.append(eph_item("horizons", hid, s, e))
            if "OBSOLETE" not in (p.get("objdata") or ""):
                covers = covers or bool(p.get("covers_now"))
            tn = (p.get("target_name") or "").replace(" (spacecraft)", "").replace(" (Spacecraft)", "")
            tn = re.sub(r"\s*\(-?\d+\)$", "", tn)
            msrc = re.search(r"Target body name:.*\{source:\s*([^}]+)\}", p.get("objdata") or "")
            if p.get("covers_now") and msrc:
                notes.append(f"Horizons {hid} source '{msrc.group(1).strip()}' covers {NOW:%Y-%m-%d}")
            if tn and tn not in aliases and tn != name:
                aliases.append(tn)
            if p.get("ok") and (hz_best is None or (p.get("covers_now") and not hz_best.get("covers_now")) or
                                (not hz_best.get("covers_now") and p.get("epoch") and hz_best.get("epoch")
                                 and p["epoch"] > hz_best["epoch"])):
                hz_best = p
        for fn, nb_, cb_, nor_, cos_ in o.get("bg_alias", []):
            notes.append(f"Bill Gray {fn} is filed under surrogate NORAD {nor_} / COSPAR {cos_} in tle_list.txt")
        for fn, s, e in o["bg"]:
            eph.append(eph_item("bgtle", fn, s, e))
            if s and e and s <= NOW <= e:
                covers = True
        gp_ep = None
        # Space-Track GP counts as covering NOW only for objects still in a geocentric (or Earth-Moon DRO) orbit
        geo_now = (c not in ("HELIOCENTRIC", "LUNAR_ORBIT", "LUNAR_ORBIT_DECAYED_UNKNOWN", "L_POINT")
                   and o["reason"] not in ("PRESUMED_IMPACT",) and not o["reason"].startswith("EPHEM_")) \
            or h.get("regime") == "EM_DRO"
        if st_now and st_now.get("EPOCH"):
            gp_ep = dt.datetime.fromisoformat(st_now["EPOCH"][:19])
            eph.append(eph_item("spacetrack_gp", sat, gp_ep, gp_ep))
            if geo_now and abs((gp_ep - NOW).total_seconds()) <= GP_FRESH_DAYS * 86400:
                covers = True
                notes.append(f"Space-Track GP epoch {iso(gp_ep, True)} within {GP_FRESH_DAYS} d of {NOW:%Y-%m-%d} counted as covering now"
                             + (" (epoch is AFTER the reference date: Space-Track published a future-epoch set)"
                                if gp_ep > NOW else ""))
        elif st_old and st_old.get("GP_EPOCH"):
            g_old = dt.datetime.fromisoformat(st_old["GP_EPOCH"][:19].replace(" ", "T"))
            eph.append(eph_item("spacetrack_gp", sat, g_old, g_old))
        # ---------------- elements: Horizons at NOW > newest of Horizons arc edge / Space-Track GP / GCAT
        cand_el = []
        if hz_best and hz_best.get("el"):
            el = hz_best["el"]
            body_r = {"moon": R_M, "earth": R_E, "sun": 0.0}[hz_best["center"]]
            if "OBSOLETE" in (hz_best.get("objdata") or ""):
                notes.append(f"Horizons {hz_best['cmd']} elements not used (nominal propagation flagged OBSOLETE)")
            elif not hz_best.get("covers_now") and el["q"] < body_r:
                pre = hz_elements_at(hz_best["cmd"], hz_best["epoch"] - dt.timedelta(days=5),
                                     hz_best["cmd"] in lunar_ids)
                pre_r = {"moon": R_M, "earth": R_E, "sun": 0.0}[pre["center"]] if pre else 0
                if pre and pre["q"] >= pre_r:
                    cand_el.append(dict(src=f"Horizons {hz_best['cmd']} at arc end - 5 d (arc ends suborbital)",
                                        epoch=pre["epoch"], center=pre["center"], a=pre["a"], e=pre["e"], i=pre["i"],
                                        rp=pre["q"], ra=pre["Q"], P=pre["P"]))
                else:
                    notes.append(f"Horizons {hz_best['cmd']} arc-end state ({iso(hz_best['epoch'], True)}) is suborbital "
                                 f"(pericentre {el['q']:.0f} km < {hz_best['center']} radius: descent/impact/reentry), "
                                 "Horizons elements not used")
            else:
                cand_el.append(dict(src=f"Horizons {hz_best['cmd']}", epoch=hz_best["epoch"], center=hz_best["center"],
                                    a=el["a"], e=el["e"], i=el["i"], rp=el["q"], ra=el["Q"], P=el["P"]))
        if geo_now and st_now and st_now.get("EPOCH") and st_now.get("SEMIMAJOR_AXIS"):
            a = float(st_now["SEMIMAJOR_AXIS"])
            e = float(st_now["ECCENTRICITY"])
            cand_el.append(dict(src="Space-Track GP (mean, TEME)", epoch=gp_ep, center="earth", a=a, e=e,
                                i=float(st_now["INCLINATION"]), rp=a * (1 - e), ra=a * (1 + e),
                                P=float(st_now["PERIOD"]) / 1440.0 if st_now.get("PERIOD") else period_days(a, "earth")))
        if o["reason"].startswith("EPHEM_") and c in ("EARTH_ORBIT_DECAYED", "RETURNED_EARTH"):
            lg = gcat_last_geo_orbit(o["Phases"])
            if lg and lg["epoch"]:
                a = (lg["rp"] + lg["ra"]) / 2
                cand_el.append(dict(src="GCAT phase chain (last Earth orbit)", epoch=lg["epoch"], center="earth", a=a,
                                    e=(lg["ra"] - lg["rp"]) / (lg["ra"] + lg["rp"]), i=lg["i"], rp=lg["rp"], ra=lg["ra"],
                                    P=period_days(a, "earth")))
            if st_now and st_now.get("EPOCH") and st_now.get("SEMIMAJOR_AXIS") and float(st_now["ECCENTRICITY"]) < 1:
                a = float(st_now["SEMIMAJOR_AXIS"])
                e_ = float(st_now["ECCENTRICITY"])
                cand_el.append(dict(src="Space-Track GP (mean, TEME; last set before decay)", epoch=gp_ep, center="earth",
                                    a=a, e=e_, i=float(st_now["INCLINATION"]), rp=a * (1 - e_), ra=a * (1 + e_),
                                    P=float(st_now["PERIOD"]) / 1440.0 if st_now.get("PERIOD") else period_days(a, "earth")))
        if g and g.get("center") and g.get("rp") and g.get("epoch"):
            rp, ra = g["rp"], g["ra"]
            a = (rp + ra) / 2 if ra else None
            cand_el.append(dict(src="GCAT", epoch=to_dt(g["epoch"]), center=g["center"], a=a,
                                e=(ra - rp) / (ra + rp) if ra else None, i=g["i"], rp=rp, ra=ra,
                                P=period_days(a, g["center"]) if a else None))
        if c in ("LUNAR_ORBIT", "LUNAR_ORBIT_DECAYED_UNKNOWN", "LUNAR_IMPACT", "ON_MOON") or o["reason"] == "PRESUMED_IMPACT":
            lo = gcat_last_lunar_orbit(o["Phases"])
            if lo and lo["epoch"] and not any(x["center"] == "moon" and x["src"] == "GCAT" and x["epoch"] == lo["epoch"]
                                              for x in cand_el):
                a = (lo["rp"] + lo["ra"]) / 2
                cand_el.append(dict(src="GCAT phase chain (last lunar orbit)", epoch=lo["epoch"], center="moon", a=a,
                                    e=(lo["ra"] - lo["rp"]) / (lo["ra"] + lo["rp"]), i=lo["i"], rp=lo["rp"], ra=lo["ra"],
                                    P=period_days(a, "moon")))
        cand_el = [x for x in cand_el if x["epoch"]]
        best = max(cand_el, key=lambda x: (x["epoch"], x["src"].startswith("Horizons"))) if cand_el else None
        if hz_best and hz_best.get("covers_now") and cand_el and cand_el[0]["src"].startswith("Horizons"):
            best = cand_el[0]
        # ---------------- regime
        regime = h.get("regime")
        apo_any = max_geo_apogee(o["Phases"])
        heli_info = ""
        if not regime:
            if c in ("LUNAR_ORBIT", "LUNAR_ORBIT_DECAYED_UNKNOWN") or o["reason"] == "PRESUMED_IMPACT":
                regime = "LUNAR_ORBIT"
            elif c == "L_POINT":
                regime = {"EML1": "EM_L1", "EML2": "EM_L2", "SEL1": "SE_L1", "SEL2": "SE_L2"}.get(g.get("oporbit"), "OTHER")
            elif c in ("CISLUNAR", "EARTH_ORBIT"):
                ra = g.get("ra") if g.get("center") == "earth" else None
                if best and best["center"] == "earth" and best.get("ra"):
                    ra = max(ra or 0, best["ra"])
                ra = max(ra or 0, apo_any) or None
                if ra is None or ra >= 100000:
                    regime = "CISLUNAR_HEO"
                elif max_geo_period(o["Phases"]) > 2.4 or (best and (best.get("P") or 0) > 2.4):
                    regime = "HEO"
                else:
                    regime = "OTHER"
                    notes.append("OTHER: geocentric, P < 2.4 d and apogee < 100000 km")
            elif c == "HELIOCENTRIC":
                regime = None
            elif apo_any >= 100000:
                regime = "CISLUNAR_HEO"       # e.g. a stage left on a bound Earth-Moon transfer ellipse
            elif re.search(r"OI:Luna|-> Luna -> ", o["Phases"]):
                regime = "LUNAR_ORBIT"
            else:
                regime = "TRANSLUNAR"
        if c == "HELIOCENTRIC" and not h.get("regime"):
            w24, w30 = dt.datetime(2024, 1, 1), dt.datetime(2030, 12, 31)
            near, how = None, ""
            for hid in o["hzids"]:
                p = prof.get(hid) or {}
                if not p.get("ok"):
                    continue
                s, e = p.get("start") or dt.datetime(1900, 1, 1), p.get("end") or dt.datetime(2199, 1, 1)
                if e > w24 and s < w30:
                    r = hz_earth_min(hid, max(s, w24) + dt.timedelta(minutes=5), min(e, w30) - dt.timedelta(minutes=5))
                    if r:
                        near = r[0]
                        how = (f"Horizons {hid}: min geocentric distance {r[0]:.4f} AU on {r[1]} "
                               f"(overlap of arc with 2024-2030)")
                        break
            if near is None:
                for hid in o["hzids"]:
                    p = prof.get(hid) or {}
                    el = p.get("el")
                    end = p.get("end")
                    if (p.get("ok") and p.get("center") == "sun" and el and el["e"] < 1 and end and end < w24
                            and end.year >= w24.year - KEPLER_MAX_GAP_YR):
                        d = np.linalg.norm(kepler_positions(el, ejd) - exyz, axis=1) / AU
                        k = int(np.argmin(d))
                        near = float(d[k])
                        how = (f"two-body propagation of Horizons {hid} arc-end elements ({iso(p['epoch'])}): "
                               f"est. min geocentric distance {near:.3f} AU on "
                               f"{(w24 + dt.timedelta(days=k)).strftime('%Y-%m-%d')} (2024-2030)")
                        break
            if near is None:
                bg24 = [f for f in o["bg"] if f[2] and f[2] >= w24]
                dep = helio_departure(o["Phases"]) or to_dt(g.get("epoch", ""))
                if bg24:
                    near, how = 0.0, f"Bill Gray TLE coverage in {bg24[-1][2].year} ({bg24[-1][0]}) implies Earth vicinity 2024+"
                elif dep and dep >= w24:
                    near, how = 0.0, f"left the Earth-Moon system {iso(dep)} (GCAT EO:HCO), i.e. within 0.05 AU in 2024+"
                elif g.get("rp") and g.get("ra") and dep and dep.year >= 2016:
                    a = (g["rp"] + g["ra"]) / 2 / AU
                    ecc = (g["ra"] - g["rp"]) / (g["ra"] + g["rp"])
                    P = a ** 1.5 * 365.25636
                    syn = 1 / abs(1 / 365.25636 - 1 / P) if abs(P - 365.25636) > 1e-6 else 1e12
                    ts = np.arange(0, (w30 - w24).days + 1, 5.0)
                    lam = 2 * math.pi * ((w24 - dep).days + ts) / syn
                    est = float(max(0.0, (2 * a * np.abs(np.sin(lam / 2)) - 2 * a * ecc).min()))
                    near = est
                    how = (f"rough est. from GCAT a={a:.4f} AU e={ecc:.4f}, Earth departure {iso(dep)} (synodic drift "
                           f"{syn / 365.25:.0f} yr minus epicycle): min geocentric distance ~{est:.3f} AU in 2024-2030; no ephemeris")
            if near is not None and near <= 0.05:
                regime = "EARTH_NEAR_HELIO"
            else:
                regime = "OTHER"
                how = ("OTHER: heliocentric, not shown within 0.05 AU of Earth in 2024-2030; "
                       + (how or "no ephemeris overlapping 2024-2030, proximity undetermined"))
            heli_info = how
        # ---------------- status
        status, status_date = h.get("status"), h.get("status_date", "")
        warn = warn_for(cospar, {"Saturn S-IVB-507": ["J002E3"], "Centaur AC-7": ["2020 SO"],
                                 "Snoopy": ["2018 AV2"], "JUICE": ["JUICE"]}.get(o["Name"], []))
        dec = (sc_now or {}).get("DECAY") or (st_old or {}).get("DECAY") or ""
        if not status:
            if c == "ON_MOON":
                # landing event date from the GCAT 'L:' phase (GCAT 'on lunar surface since' is the end of operations)
                ml = re.search(r"-> L:[^@]*@(\d{4}-\d\d-\d\d)", o["Phases"])
                status, status_date = "landed", (ml.group(1) if ml else "") or exact_date(ed) or dec
            elif c == "LUNAR_IMPACT" or o["reason"] == "PRESUMED_IMPACT":
                status = "lunar_impact"
                status_date = exact_date(ed) or (dec if c == "LUNAR_IMPACT" and o["reason"] != "PRESUMED_IMPACT" else "")
            elif c == "EARTH_ORBIT_DECAYED":
                status, status_date = "decayed", exact_date(ed) or dec
            elif c == "RETURNED_EARTH":
                status, status_date = "landed", exact_date(ed) or dec
            elif c == "HELIOCENTRIC":
                status = "returning" if warn else "escaped"
                status_date = iso(helio_departure(o["Phases"])) or ""
            elif c in ("CISLUNAR", "EARTH_ORBIT"):
                tracked = covers or (gp_ep and gp_ep >= dt.datetime(2024, 8, 1)) or \
                    any(e and e >= dt.datetime(2024, 1, 1) for _f, _s, e in o["bg"])
                status = "derelict" if tracked else "lost"
                if status == "lost":
                    notes.append("untracked: no ephemeris or GP since 2024 (status 'lost' = location uncertain)")
            else:
                status = "derelict" if c == "LUNAR_ORBIT" else "unknown"
        if not status_date and status in ("derelict", "lost", "unknown"):
            status_date = (iso(gp_ep) if gp_ep and (status == "derelict" or h.get("regime") == "EM_DRO") else "") \
                or g.get("epoch", "")[:10]
        if not status_date and status in ("escaped", "returning"):
            status_date = iso(helio_departure(o["Phases"])) or ""
        if status == "escaped" and warn:
            status = "returning"
        for d, txt in warn:
            notes.append(f"Bill Gray Warn {d}: '{txt}'")
        if o["reason"] == "PRESUMED_IMPACT":
            gy = re.search(r"impact(?:ed Moon)? (\S+)", next((r["Note"] for r in UNLOCATED
                                                              if (r["Launch_Tag"], r["Object"]) == key), ""))
            notes.append(f"lunar-orbit derelict, impact presumed by GCAT ('{gy.group(1) if gy else '?'}'), unobserved; "
                         "no observed post-mission ephemeris (wiki §6.2 surface_unlocated.tsv, §7)")
        if o["reason"].startswith("EPHEM_"):
            notes.append(f"listed only for its 2019-2026 ephemeris (wiki bucket {BADGE.get(c, c)})")
        if heli_info:
            notes.append(heli_info)
        if dec and status not in ("decayed", "lunar_impact", "landed") and c != "HELIOCENTRIC":
            notes.append(f"Space-Track SATCAT DECAY {dec}")
        # ---------------- obj_type
        t = o["Type"]
        otype = {"R": "rocket_body", "P": "payload", "D": "debris"}.get(t[:1], "unknown")
        if t[:1] == "C":
            otype = "rocket_body" if t[3:4] == "M" else "debris"
        # ---------------- elements formatting
        if best:
            P = best.get("P")
            el_cols = dict(period_d=fmt(P, 4) if P else "", a_km=fmt(best.get("a"), 1) if best.get("a") else "",
                           e=fmt(best.get("e"), 5) if best.get("e") is not None else "",
                           incl_deg=fmt(best.get("i"), 2) if best.get("i") is not None else "",
                           perigee_km=fmt(best.get("rp"), 1) if best.get("rp") else "",
                           apogee_km=fmt(best.get("ra"), 1) if best.get("ra") else "",
                           elem_epoch=iso_z(best["epoch"]), frame_center=best["center"])
            plane = {"earth": "Earth equator ICRF/TEME", "moon": "lunar body equator", "sun": "ecliptic J2000"}[best["center"]]
            notes.append(f"elements: {best['src']} ({plane}{'; selenocentric period' if best['center'] == 'moon' else ''})")
        else:
            el_cols = dict(period_d="", a_km="", e="", incl_deg="", perigee_km="", apogee_km="", elem_epoch="",
                           frame_center="")
        line = wiki_line(o["Name"], o["mission"], o["flyby"])
        notes.append(f"wiki bucket {BADGE.get(c, c)}; lunar_missions.md:{line}" if line else f"wiki bucket {BADGE.get(c, c)}")
        hz_id = next((x for x in o["hzids"] if (prof.get(x) or {}).get("covers_now")), "")
        if not hz_id and o["hzids"]:
            okk = [x for x in o["hzids"] if (prof.get(x) or {}).get("ok")]
            hz_id = max(okk, key=lambda x: prof[x].get("end") or dt.datetime(1900, 1, 1)) if okk else ""
        rows.append(dict(src="lunarmission", src_ref=f"gcat_lunar_objects.tsv:{o['JCAT'].strip()}", name=name,
                         aliases=";".join(dict.fromkeys(a for a in aliases if a and a != name)),
                         norad=str(int(sat)) if sat else "", cospar=cospar, horizons_id=hz_id, obj_type=otype,
                         regime=regime, status=status, status_date=(status_date or "")[:10], **el_cols,
                         ephem_sources=";".join(eph), covers_now="Y" if covers else "N",
                         notes="; ".join(n.replace("\t", " ") for n in notes),
                         _mission=o["mission"], _mdate=o["mdate"], _cat=c, _reason=o["reason"]))

    # ---- 7. write
    rows.sort(key=lambda r: (REGIME_ORDER.index(r["regime"]), r["covers_now"] != "Y", r["_mdate"], r["name"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        f.write("\t".join(COLS) + "\n")
        for r in rows:
            f.write("\t".join(str(r[k]).replace("\t", " ").replace("\n", " ") for k in COLS) + "\n")

    not_listed = collections.Counter()
    for (b, _s), n in excluded.items():
        not_listed[b] += n
    summ = dict(rows=len(rows), covers_now=sum(r["covers_now"] == "Y" for r in rows),
                regime_counts=dict(collections.Counter(r["regime"] for r in rows)),
                status_counts=dict(collections.Counter(r["status"] for r in rows)),
                reason_counts=dict(collections.Counter(r["_reason"] for r in rows)),
                listed_by_wiki_bucket=dict(collections.Counter(BADGE.get(r["_cat"], r["_cat"]) for r in rows)),
                wiki_bucket_counts_lunar_missions=dict(scope_counts), wiki_bucket_counts_flyby_missions=dict(flyby_counts),
                not_listed_by_bucket=dict(not_listed),
                not_listed_detail={f"{b}/{s}": n for (b, s), n in sorted(excluded.items())},
                not_listed_but_have_ephemeris=excluded_eph, folded_components=len(folded),
                folded_by_bucket=dict(collections.Counter(BADGE.get(o["cat"], o["cat"]) for o in objs if o["folded_into"])),
                wiki_bucket_totals_all=dict(collections.Counter(BADGE.get(o["cat"], o["cat"]) for o in objs)),
                wiki_objects_total=len(objs),
                horizons_map={h: dict(target=(prof.get(h) or {}).get("target_name", ""),
                                      ok=(prof.get(h) or {}).get("ok"),
                                      covers_now=(prof.get(h) or {}).get("covers_now"),
                                      span=f"{iso((prof.get(h) or {}).get('start'), True)}..{iso((prof.get(h) or {}).get('end'), True)}")
                              for h in hz_all},
                mb_entries_with_lunar_cospar_not_mapped=mb_unmapped)
    SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY.write_text(json.dumps(summ, indent=1, default=str))
    print(json.dumps({k: v for k, v in summ.items() if k not in ("horizons_map",)}, indent=1, default=str))
    with open(CACHE_HZ / "_objdata_digest.txt", "w") as f:
        for k, v in sorted(prof.items()):
            f.write(f"===== {k} {v.get('target_name', '')} ok={v.get('ok')} covers={v.get('covers_now')} "
                    f"span={iso(v.get('start'), True)}..{iso(v.get('end'), True)} center={v.get('center')} "
                    f"dE={fmt(v.get('dE'), 0)} dM={fmt(v.get('dM'), 0)} err={v.get('err', '')[:80]}\n")
            for l in v.get("objdata", "").splitlines():
                if re.search(r"(?i)note|through|end of|ended|complete|decommission|lost|predict|tag-up|status|"
                             r"trajectory|\d{4}-[A-Z][a-z]{2}-\d\d \d\d:\d\d \d{4}-", l):
                    f.write("   " + l.strip()[:150] + "\n")


if __name__ == "__main__":
    main()
