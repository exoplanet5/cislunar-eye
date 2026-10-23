#!/usr/bin/env python3
"""horizons_sweep.py -- empirical JPL Horizons sweep for cislunar-eye.

Builds two tables from the JPL Horizons API (https://ssd.jpl.nasa.gov/api/horizons.api):

  catalog/sources/horizons.tsv
      Candidate extract in the catalog/sources/SCHEMA.md format: one row per
      Horizons body that satisfies the SCHEMA selection rule, i.e.
        * within 1.0e7 km of Earth at now-15 d / now / now+15 d 00:00 UT (the display window edges;
          now = --now, else $CLE_NOW, else today, floored to the day)
          (except ordinary LEO/MEO/GEO: bound, P < 2.4 d and apogee < 100 000 km), or
        * ephemeris ends 2023-01-01 .. now while geocentric/cislunar (recently lost/ended), or
        * heliocentric with Earth distance < 0.05 AU at some daily sample 2024-01-01..2030-12-31, or
        * at any sampled epoch of its Horizons span: bound geocentric with P > 2.4 d or
          apogee >= 100 000 km, or bound to the Moon, or in the Earth-Moon / Sun-Earth L1/L2
          region (historical objects are kept with decayed / lunar_impact / landed / escaped status).
  catalog/sources/horizons_all_spacecraft.tsv
      Audit table: every spacecraft-type major body in the Horizons MB list (negative IDs plus
      the positive-ID artificial entries such as WT1190F 999787), plus the small-body and name
      probes, with coverage span, distance at the three epochs (columns sweep_now, d_m15_km, d_0_km,
      d_p15_km; 'no coverage'), 2024-2030 minimum Earth distance, regime guess and the in-scope
      decision / exclusion reason.

Method (per object, sequential requests ~0.3 s apart, every raw response cached gzip'ed
under work/cache/horizons/raw/ keyed by a hash of the request parameters, so re-runs are
idempotent and offline once cached):
  1. 3-epoch geocentric ICRF vectors (START now-15 d, STOP now+15 d, STEP 15 d, OBJ_DATA=YES)
     -> header (target name, {source:}, 'Revised:' date, Trajectory files table) and states.
  2. span probes: TLIST 1900-01-01 and 2199-12-30 -> "No ephemeris ... prior to / after A.D. X"
     (re-requested with --in-scope-only, so an extended span_end is seen).
  3. per-epoch fallback requests for epochs inside the span when (1) failed.
  4. last-state request (span end - 1 min) when the span ends before now+15 d.
  5. daily scan of span ∩ [2024-01-01, 2030-12-31]; coarse (<=1500 pt) scan of the pre-2024 span.
  6. selenocentric vectors (CENTER 500@301) at the classification epoch when within 1e5 km of Moon.
  7. small-body probes as COMMAND 'DES=<desig>' (no ';' needed); task-list and unresolved designations
     are re-tried as '<desig>;' and matched against MB names/aliases (audit column alt_probe; the TSV row
     of a matching artificial MB entry, e.g. 2020 SO -> -54054450, gets a note + alias); name probes
     record what resolves.
Status: geometry of the last state (reentry / lunar surface / escape) first, then keywords in the
header's NOTE paragraphs (lost / derelict), then a weak 'active' heuristic (header revised since
2026-06-01 and not TLE-driven).  Regime rules (classify): selenocentric bound -> LUNAR_ORBIT, or
EM_L2 when NRHO-like; Earth-Moon rotating-frame proximity to L1/L2 with v_rot < 0.8 km/s; SE_L1/L2
by distance 0.8-2.6e6 km and Sun-line angle; geocentric bound -> CISLUNAR_HEO (apogee >= 1e5 km;
EM_OTHER when P ~ half a sidereal month like TESS), HEO (P > 2.4 d), else ordinary Earth orbit.
Moon/Sun geocentric positions come from the local DE440 kernel (~/JPLDE/de440.bsp, skyfield).
Elements: osculating, mu_E = 398600.435507, mu_M = 4902.800118, mu_S = 1.32712440041e11 km^3/s^2;
inclination is to the ICRF equator for earth/moon-centred elements and to the J2000 ecliptic for
sun-centred elements.

Usage:
  ~/.venvs/astro313/bin/python -I scripts/horizons_sweep.py            # full run (uses cache)
  ~/.venvs/astro313/bin/python -I scripts/horizons_sweep.py --only -170,-557
  ~/.venvs/astro313/bin/python -I scripts/horizons_sweep.py --refresh  # re-download everything
  ~/.venvs/astro313/bin/python -I scripts/horizons_sweep.py --now 2026-12-06T13:00:00Z --in-scope-only
      `make sweep`: re-probe only the 3-epoch vectors and span_end of the in-scope IDs (in_scope=Y in
      the existing audit table) plus MB-list IDs not in it; the other audit rows are carried over unchanged
      (their d_0_km at their own sweep_now); no small-body / name probes except the in-scope ones.
  --max-age 7: exit 0 at once while every in-scope audit row's sweep_now is within 7 d of now, so the daily
      (`make daily` runs `sweep` before `catalog`, non-fatal) re-probes about weekly; `make sweep FORCE=1`
      drops the gate. A request that fails after every retry aborts the run (HorizonsDown, nothing cached,
      nothing written), so an outage leaves the previous tables in place.
  --offline: never contact Horizons; a request not in the cache is an error for that object, except that
      the three-epoch distances are then taken from the cached daily 2024-2030 scan (nearest sample within
      12 h, noted in errors) and the header from the newest cached 3-epoch response.
  --dry-run: print now, the epochs and the job list, then exit.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import math
import re
import sys
import time
from pathlib import Path

import numpy as np
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _now import now as _now  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "work" / "cache" / "horizons"
RAW = CACHE / "raw"
OUTDIR = ROOT / "catalog" / "sources"
OUT_TSV = OUTDIR / "horizons.tsv"
OUT_ALL = OUTDIR / "horizons_all_spacecraft.tsv"
DE_KERNEL = Path.home() / "JPLDE" / "de440.bsp"

API = "https://ssd.jpl.nasa.gov/api/horizons.api"
HALF_WINDOW_D = 15
NOW = dt.datetime(2026, 10, 21)        # set_now(): --now / $CLE_NOW / today, floored to the day
EPOCHS = ["2026-09-21", "2026-10-21", "2026-11-20"]
WEND = dt.datetime(2026, 11, 20)       # EPOCHS[2] as a datetime (window end)
OFFLINE = False                        # --offline: hz() raises OfflineMiss instead of fetching
WIN0, WIN1 = dt.datetime(2024, 1, 1), dt.datetime(2030, 12, 31)
RECENT0 = dt.datetime(2023, 1, 1)
MU_E, MU_M, MU_S = 398600.435507, 4902.800118, 1.32712440041e11
AU = 149597870.7
R_E, R_M = 6378.137, 1737.4
HILL_M = 66000.0          # ~ lunar Hill-sphere radius (61 500 km) with margin
NEAR_KM = 1.0e7           # task distance criterion
HELIO_KM = 0.05 * AU      # SCHEMA heliocentric criterion
OBLIQ = math.radians(84381.448 / 3600.0)
SLEEP = 0.3
RETRIES = 6

VEC = dict(EPHEM_TYPE="VECTORS", CENTER="500@399", REF_SYSTEM="ICRF", REF_PLANE="FRAME",
           VEC_CORR="NONE", OUT_UNITS="KM-S", VEC_TABLE="2", CSV_FORMAT="YES", TIME_TYPE="UT")

# Small-body probes.  COMMAND form 'DES=<designation>' (verified 2026-10-20 to resolve small
# bodies without the ';' terminator, e.g. DES=2016 HO3 -> 469219 Kamo`oalewa, DES=2024 PT5), so
# the same string is usable inside ';'-separated catalog list fields.  Designations: the task
# list plus others recalled from memory (temporary captures / minimoons, Earth quasi-satellites,
# horseshoe & Trojan co-orbitals, Arjuna-class low-v_inf NEAs, retracted artificial MPC desigs).
SB_PROBES = [
    ("2020 SO", "DES=2020 SO", "task"), ("J002E3", "DES=J002E3", "task"), ("WT1190F", "DES=WT1190F", "task"),
    ("6Q0B44E", "DES=6Q0B44E", "memory"), ("2024 PT5", "DES=2024 PT5", "task"),
    ("2022 NX1", "DES=2022 NX1", "task"), ("2006 RH120", "DES=2006 RH120", "task"),
    ("2018 AV2", "DES=2018 AV2", "task"), ("1991 VG", "DES=1991 VG", "task"), ("2010 KQ", "DES=2010 KQ", "task"),
    ("2013 QW1", "DES=2013 QW1", "task"), ("2020 CD3", "DES=2020 CD3", "memory"),
    ("2023 FW13", "DES=2023 FW13", "memory"), ("2025 PN7", "DES=2025 PN7", "memory"),
    ("469219 Kamo`oalewa (2016 HO3)", "DES=2016 HO3", "memory"), ("2014 OL339", "DES=2014 OL339", "memory"),
    ("2013 LX28", "DES=2013 LX28", "memory"), ("164207 (2004 GU9)", "DES=2004 GU9", "memory"),
    ("277810 (2006 FV35)", "DES=2006 FV35", "memory"), ("2010 TK7", "DES=2010 TK7", "memory"),
    ("2020 XL5", "DES=2020 XL5", "memory"), ("2015 SO2", "DES=2015 SO2", "memory"),
    ("3753 Cruithne (1986 TO)", "DES=1986 TO", "memory"), ("2002 AA29", "DES=2002 AA29", "memory"),
    ("2003 YN107", "DES=2003 YN107", "memory"), ("2010 SO16", "DES=2010 SO16", "memory"),
    ("2000 SG344", "DES=2000 SG344", "memory"),
    ("2007 UN12", "DES=2007 UN12", "memory"), ("2009 BD", "DES=2009 BD", "memory"),
    ("2008 UA202", "DES=2008 UA202", "memory"), ("2015 HP116 (Gaia, retracted)", "DES=2015 HP116", "memory"),
    ("2007 VN84 (Rosetta, retracted)", "DES=2007 VN84", "memory"), ("2011 MD", "DES=2011 MD", "memory"),
]
NAME_PROBES = ["Spektr-RG", "SRG", "Queqiao", "Queqiao-2", "IBEX", "Geotail", "Chang", "Chang'e 6",
               "Chang'e 7", "Tianwen-2", "Peregrine", "Odysseus", "Athena", "Blue Ghost", "Resilience",
               "Hakuto", "Lunar IceCube", "LunaH-Map", "ArgoMoon", "Tiandu", "DRO-A", "Lunar Pathfinder",
               "Proba-3", "XMM", "THEMIS", "ARTEMIS", "Tesla Roadster", "Starman", "Mangalyaan", "Psyche",
               "Gateway", "Orion", "Luna 25", "Kosmos", "Spektr"]

def set_now(value: str | None = None):
    """NOW (day-floored, naive UTC), the three epochs now-15 d / now / now+15 d and WEND."""
    global NOW, EPOCHS, WEND
    NOW = _now("day", value).replace(tzinfo=None)
    EPOCHS = [iso(NOW + dt.timedelta(days=k)) for k in (-HALF_WINDOW_D, 0, HALF_WINDOW_D)]
    WEND = NOW + dt.timedelta(days=HALF_WINDOW_D)
    return EPOCHS


# ----------------------------------------------------------------------------- HTTP + cache
_SESSION = requests.Session()
_last = [0.0]
STATS = {"net": 0, "cache": 0, "offline_miss": 0}


class OfflineMiss(LookupError):
    pass


class HorizonsDown(RuntimeError):
    """No usable response after every retry: nothing is cached and the sweep aborts (the old tables stay)."""


def _q(v: str) -> str:
    return f"'{v}'"


def hz(params: dict, tag: str, cmd: str, refresh: bool = False) -> str:
    """One Horizons API request (format=text), cached gzip'ed under RAW."""
    p = {"format": "text", "OBJ_DATA": "NO", "MAKE_EPHEM": "YES"}
    p.update(params)
    p["COMMAND"] = cmd
    q = {k: (v if k == "format" else _q(v)) for k, v in p.items()}
    key = hashlib.sha1(json.dumps(q, sort_keys=True).encode()).hexdigest()[:12]
    slug = re.sub(r"[^A-Za-z0-9+-]+", "_", cmd).strip("_") or "x"
    path = RAW / f"{slug}__{tag}__{key}.txt.gz"
    if path.exists() and (not refresh or OFFLINE):
        STATS["cache"] += 1
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return f.read()
    if OFFLINE:
        STATS["offline_miss"] += 1
        raise OfflineMiss(f"{cmd} {tag}: not cached (offline)")
    text, ok = "", False
    for attempt in range(RETRIES):
        wait = SLEEP - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        try:
            r = _SESSION.get(API, params=q, timeout=180)
            _last[0] = time.time()
            text = r.text
            if r.status_code == 200 or "API VERSION" in text:
                ok = True
                break
            print(f"  HTTP {r.status_code} for {cmd} {tag}; retry", file=sys.stderr)
        except requests.RequestException as e:  # network hiccup
            _last[0] = time.time()
            print(f"  {type(e).__name__} for {cmd} {tag}; retry", file=sys.stderr)
        time.sleep(5 * (attempt + 1))
    if not ok:
        raise HorizonsDown(f"{cmd} {tag}: no response after {RETRIES} attempts")
    STATS["net"] += 1
    RAW.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(text)
    with open(CACHE / "requests.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"file": path.name, "params": q,
                            "fetched": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}) + "\n")
    return text


# ----------------------------------------------------------------------------- parsing
MON = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}


def hzdate(s: str) -> dt.datetime | None:
    m = re.match(r"(\d{4})-([A-Za-z]{3})-(\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?)?", s.strip())
    if not m:
        return None
    y, mon, d, hh, mm, ss, fr = m.groups()
    t = dt.datetime(int(y), MON[mon.upper()], int(d), int(hh or 0), int(mm or 0), int(ss or 0))
    if fr:
        t += dt.timedelta(seconds=float("0." + fr))
    return t


def iso(t: dt.datetime | None, sec: bool = False) -> str:
    if t is None:
        return ""
    return t.strftime("%Y-%m-%dT%H:%M:%SZ" if sec else "%Y-%m-%d")


def hzfmt(t: dt.datetime) -> str:
    return t.strftime("%Y-%m-%d %H:%M")


def parse(text: str) -> dict:
    d: dict = {"target": "", "source": "", "states": [], "err": "", "err_kind": "", "err_date": None,
               "revised": "", "traj": [], "multi": [], "sb": False, "zero_rows": 0}
    m = re.search(r"Target body name:\s*(.+?)\s*\{source:\s*([^}]*)\}", text)
    if m:
        d["target"], d["source"] = m.group(1).strip(), m.group(2).strip()
        d["sb"] = d["source"].startswith("JPL#") or "Small perturbers" in text
    m = re.search(r"Revised:\s*([A-Z][a-z]{2})\s+(\d{1,2}),\s*(\d{4})", text)
    if m:
        d["revised"] = dt.datetime.strptime(" ".join(m.groups()), "%b %d %Y").strftime("%Y-%m-%d")
    if "$$SOE" in text:
        body = text.split("$$SOE", 1)[1].split("$$EOE", 1)[0]
        for line in body.strip().splitlines():
            f = [x.strip() for x in line.split(",")]
            if len(f) >= 8:
                try:
                    jd = float(f[0])
                    t = hzdate(f[1].replace("A.D. ", ""))
                    xyz = [float(x) for x in f[2:8]]
                except ValueError:
                    continue
                if all(x == 0.0 for x in xyz[:3]):
                    d["zero_rows"] += 1  # Horizons emits 0,0,0 rows in some SPK gaps / span edges
                    continue
                d["states"].append((jd, t, xyz))
    m = re.search(r'No ephemeris for target "(.+?)" (prior to|after) A\.D\. (\S+ \S+)', text)
    if m:
        d["target"] = d["target"] or m.group(1)
        d["err_kind"] = "prior" if m.group(2) == "prior to" else "after"
        d["err_date"] = hzdate(m.group(3))
        d["err"] = m.group(0)
    elif not d["states"]:
        lines = [ln.strip() for ln in text.splitlines()
                 if ln.strip() and not ln.startswith("API ") and not set(ln.strip()) <= set("*=")
                 and not ln.strip().startswith("Toolkit version")]
        d["err"] = " | ".join(lines[:4])[:300]
        if "Multiple" in text and "match" in text:
            for ln in text.splitlines():
                mm = re.match(r"^\s*(-?\d+)\s{2,}(\S.*)$", ln)
                if mm:
                    d["multi"].append((mm.group(1), mm.group(2).strip()[:40]))
    # Trajectory table(s) in the object header
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        # header variants seen: "Trajectory files  Start (TDB)  End (TDB)", "Trajectory name  Start
        # Stop", "Name  Start (TDB)  Stop (TDB)" (MOM), "Trajectory name  Start  Stop" (SOHO)
        if re.search(r"\b(Start|Begin)\b", ln) and re.search(r"\b(Stop|End)\b", ln) and i + 1 < len(lines) \
                and re.match(r"^\s*-{4,}\s+-{4,}", lines[i + 1]):
            for row in lines[i + 2:]:
                if not row.strip() or row.strip().startswith("*"):
                    break
                mm = re.match(r"^\s*(.+?)\s{2,}(\d{4}-[A-Za-z]{3}-\d{2}(?: \d{2}:\d{2}(?::\d{2})?)?)\s+"
                              r"(\d{4}-[A-Za-z]{3}-\d{2}(?: \d{2}:\d{2}(?::\d{2})?)?|\S+)", row)
                if mm:
                    d["traj"].append((mm.group(1).strip(), mm.group(2), mm.group(3)))
    return d


def parse_mb(text: str) -> list[dict]:
    rows, on = [], False
    for ln in text.splitlines():
        if re.match(r"^\s*-{5,}\s+-{10,}", ln):
            on = True
            continue
        if not on:
            continue
        if "Number of matches" in ln:
            break
        if not ln.strip():
            continue
        idf = ln[:9].strip()
        if not re.fullmatch(r"-?\d+", idf):
            continue
        rows.append(dict(id=idf, name=ln[11:45].strip(), desig=ln[46:57].strip(), alias=ln[59:].strip()))
    seen, out = set(), []
    for r in rows:
        if r["id"] not in seen:
            seen.add(r["id"])
            out.append(r)
    return out


# ----------------------------------------------------------------------------- geometry
_EPH = {}


def eph():
    if not _EPH:
        from skyfield.api import load_file, load
        e = load_file(str(DE_KERNEL))
        _EPH.update(ts=load.timescale(builtin=True), earth=e["earth"], moon=e["moon"], sun=e["sun"])
    return _EPH


def moon_sun(jd_ut: np.ndarray):
    """Geocentric ICRF Moon r,v (km, km/s) and Sun r (km) at UT Julian dates."""
    E = eph()
    t = E["ts"].ut1_jd(np.atleast_1d(jd_ut))
    m = (E["moon"] - E["earth"]).at(t)
    s = (E["sun"] - E["earth"]).at(t)
    return m.position.km.T, m.velocity.km_per_s.T, s.position.km.T, s.velocity.km_per_s.T


def coe(r, v, mu, ecl=False) -> dict:
    r, v = np.asarray(r, float), np.asarray(v, float)
    rn, vn = np.linalg.norm(r), np.linalg.norm(v)
    h = np.cross(r, v)
    if ecl:
        h = np.array([h[0], math.cos(OBLIQ) * h[1] + math.sin(OBLIQ) * h[2],
                      -math.sin(OBLIQ) * h[1] + math.cos(OBLIQ) * h[2]])
    hn = np.linalg.norm(h)
    eps = vn * vn / 2 - mu / rn
    ev = ((vn * vn - mu / rn) * r - np.dot(r, v) * v) / mu
    e = float(np.linalg.norm(ev))
    out = dict(r=rn, v=vn, eps=eps, e=e, i=math.degrees(math.acos(max(-1, min(1, h[2] / hn)))),
               a=None, P=None, rp=hn * hn / mu / (1 + e), ra=None)
    if eps < 0:
        a = -mu / (2 * eps)
        out.update(a=a, P=2 * math.pi * math.sqrt(a ** 3 / mu) / 86400.0, rp=a * (1 - e), ra=a * (1 + e))
    else:
        out["a"] = -mu / (2 * eps) if eps > 0 else None
    return out


def classify(r, v, mr, mv, sr) -> dict:
    """Regime guess for one geocentric ICRF state, given geocentric Moon r,v and Sun r."""
    r, v, mr, mv, sr = (np.asarray(x, float) for x in (r, v, mr, mv, sr))
    d = float(np.linalg.norm(r))
    rel, vrel = r - mr, v - mv
    dm = float(np.linalg.norm(rel))
    g = coe(r, v, MU_E)
    out = dict(d=d, dm=dm, geo=g, sel=None, regime="", tag="")
    cs = float(np.dot(r, sr) / (d * np.linalg.norm(sr)))
    out["cos_sun"] = cs
    if d < R_E + 100:
        out.update(regime="OTHER", tag="at/below Earth surface")
        return out
    if dm < 1.5 * HILL_M:
        s = coe(rel, vrel, MU_M)
        out["sel"] = s
        if s["eps"] < 0 and s["ra"] < 1.2e5:
            # NRHO-like: strongly elongated selenocentric ellipse, apolune far out, plane roughly
            # perpendicular to the Moon's orbit plane (CAPSTONE 9:2 L2 NRHO: ~3e3 x 7e4 km).
            hs = np.cross(rel, vrel)
            zm = np.cross(mr, mv)
            inc_m = math.degrees(math.acos(max(-1.0, min(1.0, float(np.dot(hs, zm) / (np.linalg.norm(hs) * np.linalg.norm(zm)))))))
            out["inc_moonplane"] = inc_m
            if s["ra"] >= 4.0e4 and s["rp"] <= 2.0e4 and 60.0 <= inc_m <= 120.0:
                out.update(regime="EM_L2", tag=f"NRHO-like selenocentric {s['rp']:.0f}x{s['ra']:.0f} km, "
                                              f"incl to lunar orbit plane {inc_m:.0f} deg")
                return out
            if s["ra"] < HILL_M:
                out.update(regime="LUNAR_ORBIT", tag="selenocentric bound")
                return out
        if dm < R_M + 50:  # selenocentric-unbound arrival at the surface: impactor on approach
            out.update(regime="TRANSLUNAR", tag=f"at lunar surface on a selenocentric-unbound (impact) "
                                                f"trajectory, v_sel {s['v']:.2f} km/s")
            return out
    # Earth-Moon rotating frame
    D = float(np.linalg.norm(mr))
    xh = mr / D
    zh = np.cross(mr, mv)
    zh /= np.linalg.norm(zh)
    yh = np.cross(zh, xh)
    x, y, z = (float(np.dot(r, u)) / D for u in (xh, yh, zh))
    dl1 = math.sqrt((x - 0.849) ** 2 + y * y + z * z) * D
    dl2 = math.sqrt((x - 1.168) ** 2 + y * y + z * z) * D
    out["rot"] = (x, y, z)
    # speed in the Earth-Moon rotating frame (libration orbits are slow there)
    omega = np.cross(mr, mv) / (D * D)
    vrot = float(np.linalg.norm(v - np.cross(omega, r)))
    out["vrot"] = vrot
    if dl2 < 0.25 * D and vrot < 0.8:
        out.update(regime="EM_L2", tag=f"{dl2:.0f} km from EM-L2, v_rot {vrot:.2f} km/s")
        return out
    if dl1 < 0.20 * D and vrot < 0.8:
        out.update(regime="EM_L1", tag=f"{dl1:.0f} km from EM-L1, v_rot {vrot:.2f} km/s")
        return out
    if 0.8e6 <= d <= 2.6e6 and abs(cs) > 0.55 and g["v"] < 1.2:
        out.update(regime="SE_L1" if cs > 0 else "SE_L2", tag=f"cos(sun)={cs:+.2f}, v_geo {g['v']:.2f} km/s")
        return out
    if g["eps"] < 0 and g["ra"] < 1.3e6:
        if g["ra"] >= 1.0e5:
            if 13.0 <= g["P"] <= 14.4 and g["ra"] >= 3.0e5 and g["rp"] >= 5.0e4:
                out.update(regime="EM_OTHER", tag=f"geocentric P={g['P']:.2f} d ~ half sidereal month "
                                                  f"(P/2 lunar-resonance-like), apogee {g['ra']:.0f} km")
            else:
                out.update(regime="CISLUNAR_HEO", tag=f"geocentric bound, apogee {g['ra']:.0f} km >= 1e5")
        elif g["P"] > 2.4:
            out.update(regime="HEO", tag=f"geocentric bound, P={g['P']:.2f} d > 2.4")
        else:
            out.update(regime="NEAR_EARTH", tag=f"geocentric P={g['P']:.3f} d")
        return out
    if d < 1.5e6:
        if dm < 1.0e5:
            out.update(regime="TRANSLUNAR", tag=f"within 1e5 km of Moon ({dm:.0f} km), not lunar-bound")
        elif g["eps"] < 0:
            out.update(regime="CISLUNAR_HEO", tag=f"weakly bound, osculating apogee {g['ra']:.3g} km > 1.3e6")
        else:
            out.update(regime="EARTH_NEAR_HELIO", tag="geocentric hyperbolic inside Earth Hill sphere "
                                                       "(departure / flyby)")
        return out
    out.update(regime="EARTH_NEAR_HELIO" if d < NEAR_KM else "HELIO_FAR", tag="heliocentric")
    return out


OKREG = ("LUNAR_ORBIT", "EM_L1", "EM_L2", "EM_DRO", "EM_OTHER", "SE_L1", "SE_L2", "CISLUNAR_HEO", "HEO",
         "TRANSLUNAR")
HELIO_CLASSES = ("EARTH_NEAR_HELIO", "HELIO_FAR")


def scan_stats(states, step_min: float) -> dict:
    """Summaries over a list of (jd, t, xyz) geocentric states sampled every step_min minutes.

    Besides the plain minimum distance (which for a launch arc is the departure itself) it records
    the minimum over *heliocentric-phase* samples only (classify() -> EARTH_NEAR_HELIO / HELIO_FAR,
    i.e. not geocentric-bound, lunar or libration), the first heliocentric pass inside 0.05 AU after
    'now', and the time spent in each regime (days), so that a few transient samples (e.g. an
    Earth-trailing observatory drifting through the SE-L2 distance band) can be told apart from a
    real libration orbit."""
    if not states:
        return {}
    jd = np.array([s[0] for s in states])
    X = np.array([s[2] for s in states])
    mr, mv, sr, _ = moon_sun(jd)
    d = np.linalg.norm(X[:, :3], axis=1)
    dm = np.linalg.norm(X[:, :3] - mr, axis=1)
    inscope, regimes, regs = [], {}, []
    for k in range(len(jd)):
        c = classify(X[k, :3], X[k, 3:], mr[k], mv[k], sr[k])
        rg = c["regime"]
        regs.append(rg)
        regimes[rg] = regimes.get(rg, 0) + 1
        inscope.append(rg in OKREG)
    i0, j0 = int(np.argmin(d)), int(np.argmin(dm))
    out = dict(n=len(jd), min_d=float(d[i0]), min_t=states[i0][1], max_d=float(d.max()),
               min_dm=float(dm[j0]), min_dm_t=states[j0][1], regimes=regimes,
               reg_days={k: n * step_min / 1440.0 for k, n in regimes.items()},
               any_inscope=any(inscope), first_t=states[0][1], last_t=states[-1][1],
               inscope_t=[states[k][1] for k in range(len(jd)) if inscope[k]][:1])
    hel = [k for k in range(len(jd)) if regs[k] in HELIO_CLASSES]
    if hel:
        kh = min(hel, key=lambda k: d[k])
        out.update(helio_min_d=float(d[kh]), helio_min_t=states[kh][1])
        # first heliocentric pass inside 0.05 AU strictly after 'now' (minimum of that pass)
        nxt = [k for k in hel if states[k][1] > NOW and d[k] < HELIO_KM]
        if nxt:
            k = kb = nxt[0]
            while k + 1 < len(jd) and d[k + 1] < HELIO_KM:
                k += 1
                if d[k] < d[kb]:
                    kb = k
            out.update(next_close_d=float(d[kb]), next_close_t=states[kb][1])
    return out


# ----------------------------------------------------------------------------- per-object pipeline
def vectors(cmd, refresh, tag, center="500@399", **kw):
    p = dict(VEC)
    p["CENTER"] = center
    p.update(kw)
    return parse(hz(p, tag, cmd, refresh))


def scan(cmd, refresh, tag, a0, a1, st, errs, depth=0):
    """Vector scan a0..a1 at st minutes; on a non-coverage error (e.g. 'Mean eccentricity ...
    beyond allowed bounds ... bad TLE set' inside a TLE-driven span) retry as 8 sub-chunks, and
    failing chunks once more as 4 sub-chunks, keeping whatever succeeds."""
    pw = vectors(cmd, refresh, tag, START_TIME=hzfmt(a0), STOP_TIME=hzfmt(a1), STEP_SIZE=f"{st} m")
    if pw["zero_rows"]:
        errs.append(f"{tag}: {pw['zero_rows']} zero-vector rows dropped")
    if pw["states"] or pw["err_kind"]:
        if not pw["states"]:
            errs.append(f"{tag}: " + pw["err"])
        return pw["states"]
    if depth >= 2 or (a1 - a0) < dt.timedelta(minutes=4 * st):
        errs.append(f"{tag} {hzfmt(a0)[:10]}..{hzfmt(a1)[:10]} failed: " + pw["err"][:100])
        return []
    if depth == 0:
        errs.append(f"{tag}: " + pw["err"][:120] + " -> chunked")
    out, n = [], (8 if depth == 0 else 4)
    edges = [a0 + (a1 - a0) * k / n for k in range(n + 1)]
    for k in range(n):
        c0 = edges[k].replace(second=0, microsecond=0)
        c1 = edges[k + 1].replace(second=0, microsecond=0)
        if c1 <= c0:
            continue
        out.extend(scan(cmd, refresh, f"{tag}c{k}", c0, c1, st, errs, depth + 1))
    out.sort(key=lambda x: x[0])
    return out


def step_for(t0, t1, npts):
    mins = (t1 - t0).total_seconds() / 60.0
    return max(60, int(math.ceil(mins / npts)))


def ep3_params() -> dict:
    return dict(START_TIME=EPOCHS[0], STOP_TIME=EPOCHS[2], STEP_SIZE=f"{HALF_WINDOW_D} d", OBJ_DATA="YES")


def cached_ep3(cmd: str) -> str:
    """Offline fallback: the newest cached 3-epoch response of this command (any epochs), '' if none."""
    slug = re.sub(r"[^A-Za-z0-9+-]+", "_", cmd).strip("_") or "x"
    files = sorted(RAW.glob(f"{slug}__ep3__*.txt.gz"), key=lambda f: f.stat().st_mtime)
    if not files:
        return ""
    with gzip.open(files[-1], "rt", encoding="utf-8") as f:
        return f.read()


def fill_epochs_from_scan(o: dict, states, tol_h: float = 12.0):
    """Offline fallback: the missing EPOCHS from the nearest cached daily-scan sample within tol_h."""
    for e in EPOCHS:
        if e in o["epochs"] or not states:
            continue
        te = dt.datetime.fromisoformat(e)
        k = min(range(len(states)), key=lambda i: abs((states[i][1] - te).total_seconds()))
        off = (states[k][1] - te).total_seconds() / 3600
        if abs(off) <= tol_h:
            o["epochs"][e] = states[k]
            o["errs"].append(f"{e}: from cached daily scan sample {states[k][1]:%Y-%m-%d %H:%M} ({off:+.1f} h)")


def sweep_object(cmd: str, refresh: bool, is_sb: bool = False, reprobe: bool = False) -> dict:
    o: dict = dict(cmd=cmd, epochs={}, span0=None, span1=None, span0_open=False, span1_open=False,
                   errs=[])
    try:
        p1 = vectors(cmd, refresh, "ep3", **ep3_params())
    except OfflineMiss:
        old = cached_ep3(cmd)
        if not old:
            o["errs"].append("ep3: not cached (offline)")
            o["hdr"], o["target"], o["source"], o["resolved"] = {"revised": ""}, "", "", False
            return o
        p1 = parse(old)
        p1["states"], p1["fallback"] = [], bool(p1["target"] or p1["err_kind"])
        o["errs"].append("ep3: not cached for these epochs (offline); header from an older cached response")
    o["hdr"] = p1
    o["target"], o["source"] = p1["target"], p1["source"]
    if p1["multi"] or (not p1["states"] and not p1["err_kind"] and not p1.get("fallback")):
        o["errs"].append(p1["err"])
        o["resolved"] = False
        return o
    o["resolved"] = True
    for jd, t, xyz in p1["states"]:
        o["epochs"][iso(t)] = (jd, t, xyz)
    if p1["err_kind"] == "prior":
        o["span0"] = p1["err_date"]
    elif p1["err_kind"] == "after":
        o["span1"] = p1["err_date"]
    if not is_sb:
        if o["span0"] is None:
            ps = vectors(cmd, refresh, "span0", TLIST="1900-01-01")
            if ps["err_kind"] == "prior":
                o["span0"] = ps["err_date"]
            elif ps["states"]:
                o["span0"], o["span0_open"] = dt.datetime(1900, 1, 1), True
            else:
                o["errs"].append("span0: " + ps["err"])
        if o["span1"] is None or reprobe:
            pe = vectors(cmd, refresh or reprobe, "span1", TLIST="2199-12-30")
            if pe["err_kind"] == "after":
                o["span1"] = pe["err_date"]
            elif pe["states"]:
                o["span1"], o["span1_open"] = dt.datetime(2199, 12, 30), True
            else:
                o["errs"].append("span1: " + pe["err"])
    else:
        o["span0"], o["span1"] = dt.datetime(1900, 1, 1), dt.datetime(2199, 12, 30)
        o["span0_open"] = o["span1_open"] = True
    s0, s1 = o["span0"], o["span1"]
    # per-epoch fallback
    if not p1["states"] and not OFFLINE:
        for e in EPOCHS:
            te = dt.datetime.fromisoformat(e)
            if (s0 is None or te >= s0) and (s1 is None or te <= s1):
                pe = vectors(cmd, refresh, "ep1", TLIST=e)
                if pe["states"]:
                    jd, t, xyz = pe["states"][0]
                    o["epochs"][iso(t)] = (jd, t, xyz)
                else:
                    o["errs"].append(f"{e}: {pe['err']}")
    # last state if span ends before the display window end
    o["last"] = None
    if s1 is not None and not o["span1_open"] and s1 < WEND:
        tl = s1 - dt.timedelta(minutes=1)
        if s0 is not None and tl < s0:
            tl = s0 + (s1 - s0) / 2
        pl = vectors(cmd, refresh, "last", TLIST=tl.strftime("%Y-%m-%d %H:%M:%S"))
        if pl["states"]:
            o["last"] = pl["states"][0]
        elif pl["zero_rows"]:
            o["errs"].append(f"last: zero vector at {hzfmt(tl)}")
        else:
            o["errs"].append("last: " + pl["err"])
    # scans
    o["win"], o["hist"], o["_lastvalid"] = {}, {}, []
    if s0 is not None and s1 is not None:
        a0 = max(s0 + dt.timedelta(minutes=1), WIN0)
        a1 = min(s1 - dt.timedelta(minutes=1), WIN1)
        a0 = a0.replace(second=0, microsecond=0) + (dt.timedelta(minutes=1) if a0.second or a0.microsecond else dt.timedelta(0))
        a1 = a1.replace(second=0, microsecond=0)
        if a1 > a0 + dt.timedelta(hours=2):
            st = 1440 if (a1 - a0) > dt.timedelta(days=60) else step_for(a0, a1, 1500)
            sts = scan(cmd, refresh, "win", a0, a1, st, o["errs"])
            if sts:
                o["win"] = scan_stats(sts, st)
                o["win"]["step_min"] = st
                o["_lastvalid"].append(sts[-1])
                if OFFLINE:
                    fill_epochs_from_scan(o, sts)
        if not is_sb and s0 < WIN0:
            b0 = (s0 + dt.timedelta(minutes=2)).replace(second=0, microsecond=0)
            b1 = min(s1 - dt.timedelta(minutes=1), WIN0).replace(second=0, microsecond=0)
            if b1 > b0 + dt.timedelta(hours=2) and not o["span0_open"]:
                st = step_for(b0, b1, 1500)
                sts = scan(cmd, refresh, "hist", b0, b1, st, o["errs"])
                if sts:
                    o["hist"] = scan_stats(sts, st)
                    o["hist"]["step_min"] = st
                    o["_lastvalid"].append(sts[-1])
    # if the span ends before the window end and the edge request failed / is stale, use the
    # latest valid scanned state
    if s1 is not None and not o["span1_open"] and s1 < WEND and o["_lastvalid"]:
        cand = max(o["_lastvalid"], key=lambda st_: st_[0])
        if o["last"] is None or cand[0] > o["last"][0]:
            o["last"] = cand
    return o


# ----------------------------------------------------------------------------- row building
SIM_RE = re.compile(r"\(simulation\)")


def clean_name(n: str) -> str:
    n = re.sub(r"\s*\((?:spacecraft|Spacecraft|SPACECRAFT FRAGMENT|spacecraft2?)\)\s*", " ", n)
    n = re.sub(r"\s*\(-?\d+\)\s*$", "", n).strip()
    n = re.sub(r"\s*\((?:spac\w*|spacecr\w*)$", "", n).strip()
    return re.sub(r"\s{2,}", " ", n)


def obj_type_of(name: str, is_sb: bool) -> str:
    if is_sb:
        return "natural"
    n = name.lower().replace("_", " ")
    if "debris" in n or "fragment" in n or "lisa pathfinder propulsion" in n:
        return "debris"
    if re.search(r"\brb\b|booster|stage|s-ivb|centaur|rocket|icps|third stage|propulsion module", n):
        return "rocket_body"
    if re.match(r"^(neocp|wt1190f|2018 av2|9o0dc57|9u01ff6|6q0b44e|j002e3)", n):
        return "unknown"
    return "payload"


def fmt(x, nd=1):
    if x is None:
        return ""
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return ""
    return f"{x:.{nd}f}"


def evaluate(o: dict) -> dict:
    """Classify the object at now (or nearest covered epoch, or last state)."""
    ev = dict(cls=None, cls_epoch=None, dists={}, covers_now=iso(NOW) in o["epochs"])
    allst = list(o["epochs"].items())        # keyed by the requested epoch (a cached-scan sample may be +-12 h off)
    if allst:
        jd = np.array([s[0] for _, s in allst])
        mr, mv, sr, sv = moon_sun(jd)
        for k, (key, s) in enumerate(allst):
            c = classify(s[2][:3], s[2][3:], mr[k], mv[k], sr[k])
            c["sun_r"], c["sun_v"] = sr[k], sv[k]
            ev["dists"][key] = c
    if ev["covers_now"]:
        ev["cls"], ev["cls_epoch"] = ev["dists"][iso(NOW)], NOW
        ev["cls_state"] = o["epochs"][iso(NOW)]
    elif ev["dists"]:
        k = sorted(ev["dists"], key=lambda e: abs((dt.datetime.fromisoformat(e) - NOW).days))[0]
        ev["cls"], ev["cls_epoch"], ev["cls_state"] = ev["dists"][k], dt.datetime.fromisoformat(k), o["epochs"][k]
    elif o.get("last"):
        s = o["last"]
        mr, mv, sr, sv = moon_sun(np.array([s[0]]))
        c = classify(s[2][:3], s[2][3:], mr[0], mv[0], sr[0])
        c["sun_r"], c["sun_v"] = sr[0], sv[0]
        ev["cls"], ev["cls_epoch"], ev["cls_state"] = c, s[1], s
    return ev


SE_MIN_DAYS = 60.0        # SE-L1/L2-band samples count as a libration phase only if >= this long
CLOSE_FLYBY_KM = 2.5e4    # lunar distance below which an escaped arc is called a lunar-flyby (TRANSLUNAR)
BEYOND_GEO_KM = 5.0e4     # apogee radius for the 'recently ended beyond-GEO HEO' inclusion (e.g. SWC-1)


def span_regimes(o: dict):
    """({regime: days} kept, {regime: days} dropped as transient, mindm) over the win+hist scans."""
    days = {}
    for x in (o.get("win") or {}, o.get("hist") or {}):
        for k, v in (x.get("reg_days") or {}).items():
            days[k] = days.get(k, 0.0) + v
    keep = {k: v for k, v in days.items() if k in OKREG and (k not in ("SE_L1", "SE_L2") or v >= SE_MIN_DAYS)}
    drop = {k: v for k, v in days.items() if k in ("SE_L1", "SE_L2") and v < SE_MIN_DAYS}
    mindm = min([x.get("min_dm", 1e12) for x in (o.get("win") or {}, o.get("hist") or {}) if x] or [1e12])
    return keep, drop, mindm


def no_match_text(err: str, sb: bool = False) -> str:
    """Short wording for a Horizons lookup failure (error text from parse()); sb: a 'DES=' lookup,
    which searches the small-body index only."""
    if "Small-body Index Search" in err or "Matching small-bodies" in err:
        return ("" if sb else "no major-body match and ") + "no match in the JPL small-body index"
    return err.strip()[:120] or "no ephemeris returned"


def decide(o: dict, ev: dict, is_sim: bool) -> tuple[bool, str, str]:
    """(in_scope, regime, reason) following the SCHEMA selection rule.

    In scope if any of
      (A) within 1e7 km of Earth at one of the three epochs (and not ordinary LEO-GEO there),
      (C) heliocentric-phase sample within 0.05 AU of Earth in 2024-2030,
      (B) ephemeris ended 2023-01-01..now while lunar / cislunar / libration / within 1e7 km,
          or in a beyond-GEO HEO (apogee >= 5e4 km; e.g. Artemis II cubesat SWC-1),
      (D) span scan shows an in-scope regime (geocentric P>2.4 d or apogee >= 1e5 km, lunar orbit,
          EM/SE libration [SE band only if >= 60 d], translunar).
    Regime: the state at the element epoch when it is itself in scope; LEO-GEO at that epoch -> span
    regime; heliocentric at that epoch -> EARTH_NEAR_HELIO if (A)/(C), else OTHER when the ephemeris
    still covers now (do not advertise a past cislunar phase for an object now far away), else the
    dominant span regime of the ended arc (TRANSLUNAR when it escaped via a close lunar flyby)."""
    if not o.get("resolved"):
        return False, "", "not resolved in Horizons (" + no_match_text((o.get("errs") or [""])[0],
                                                             str(o.get("cmd", "")).startswith("DES=")) + ")"
    if is_sim:
        return False, "OTHER", "PDC/TTX planetary-defence exercise (fictitious body)"
    c = ev["cls"]
    creg = c["regime"] if c else ""
    win = o.get("win") or {}
    s0, s1 = o.get("span0"), o.get("span1")
    ended = s1 is not None and not o.get("span1_open") and s1 < NOW
    near = [e for e, cc in ev["dists"].items() if cc["d"] < NEAR_KM]
    hreg, hdrop, mindm = span_regimes(o)
    hsorted = sorted(hreg, key=lambda k: -hreg[k])
    hmin = win.get("helio_min_d")
    near_helio = hmin is not None and hmin < HELIO_KM
    reasons, beyond_geo = [], False
    if near and creg != "NEAR_EARTH":
        reasons.append("<1e7 km at " + ",".join(near))
    if near_helio:
        reasons.append(f"heliocentric-phase min Earth distance 2024-30 {hmin:.3g} km ({hmin / AU:.4f} AU) "
                       f"on {iso(win['helio_min_t'])}")
    if ended and s1 >= RECENT0 and c:
        if creg in OKREG or (creg in HELIO_CLASSES and c["d"] < NEAR_KM):
            reasons.append(f"ephemeris ended {iso(s1)} while {creg}")
        elif creg == "NEAR_EARTH" and (c["geo"].get("ra") or 0) >= BEYOND_GEO_KM:
            beyond_geo = True
            reasons.append(f"ephemeris ended {iso(s1)} in a beyond-GEO HEO (P={c['geo']['P']:.2f} d, apogee "
                           f"{c['geo']['ra']:.0f} km) below the SCHEMA P>2.4 d / apogee>=1e5 km thresholds")
        elif creg == "OTHER" and c["d"] < R_E + 100 and \
                max((o.get("hist") or {}).get("max_d", 0), win.get("max_d", 0)) > 1.0e6:
            # Earth-return capsule whose arc comes in from interplanetary space and ends at the surface,
            # e.g. the OSIRIS-REx sample-return capsule (2023-09-24)
            reasons.append(f"ephemeris ended {iso(s1)} at the Earth's surface after an interplanetary arc "
                           f"(max sampled distance {max((o.get('hist') or {}).get('max_d', 0), win.get('max_d', 0)):.3g} "
                           f"km): Earth-returning object")
    if hreg and not reasons:
        reasons.append("in-scope regime in span scan (" + ",".join(f"{k}:{hreg[k]:.1f}d" for k in hsorted[:3])
                       + ")")
    # ---- regime (computed for every object; used for folded / audit rows too)
    if creg in OKREG:
        regime = creg
    elif creg == "NEAR_EARTH":
        regime = hsorted[0] if hsorted else ("OTHER" if beyond_geo else "NEAR_EARTH")
        if hsorted and reasons:
            reasons[-1] += f" (P<2.4 d and apogee<1e5 km at element epoch {iso(ev.get('cls_epoch'))})"
    elif near or near_helio:
        regime = "EARTH_NEAR_HELIO"
    elif ev.get("covers_now") and creg in HELIO_CLASSES:
        regime = "OTHER"
    elif hsorted:
        regime = hsorted[0]
    elif creg == "EARTH_NEAR_HELIO":
        regime = "EARTH_NEAR_HELIO"
    else:
        regime = creg or ""
    short = s0 and s1 and not o.get("span1_open") and (s1 - s0) < dt.timedelta(days=120)
    if reasons and ended:
        if short and regime != "LUNAR_ORBIT" and mindm < 1.0e5:
            regime = "TRANSLUNAR"   # short mission arc that passed the Moon and did not stay in lunar orbit
        elif creg in HELIO_CLASSES and regime != "EARTH_NEAR_HELIO" and mindm < CLOSE_FLYBY_KM \
                and "LUNAR_ORBIT" not in hreg:
            regime = "TRANSLUNAR"   # escaped to heliocentric space via a close lunar flyby
        elif creg == "TRANSLUNAR" and not short and hsorted and hsorted[0] == "CISLUNAR_HEO":
            regime = "CISLUNAR_HEO"  # long cislunar arc ending in a lunar impact (e.g. WE0913A)
    o["regime_calc"] = regime
    if reasons:
        return True, regime, "; ".join(reasons)
    tail = (f"; SE-L1/L2-band samples only {','.join(f'{k}:{v:.0f}d' for k, v in hdrop.items())} "
            f"(< {SE_MIN_DAYS:.0f} d: transient drift, not a libration orbit)") if hdrop else ""
    if not o["epochs"] and not o.get("last"):
        return False, regime, "no state at epochs; " + (f"span {iso(s0)}..{iso(s1)}" if s0 else "no coverage")
    if creg == "NEAR_EARTH":
        g = c["geo"]
        return False, "NEAR_EARTH", f"ordinary Earth orbit P={g['P']:.3f} d apogee {g['ra']:.0f} km" + tail
    dd = c["d"] if c else None
    hm = f"{hmin / AU:.3f} AU (heliocentric phase)" if hmin is not None else \
        (f"{win['min_d'] / AU:.3f} AU" if win else "n/a")
    return False, creg, f"far: {dd:.3g} km at {iso(ev['cls_epoch'])}; 2024-30 min {hm}" + tail


STATUS_KW = [  # applied only to the header's NOTE paragraphs (see header_notes)
    (r"impact(ed)? (the )?(lunar|moon)|lunar impact|crash(ed)? (on|into) the moon", "lunar_impact"),
    (r"re-?entered|reentry (occurred|took place)|burned up|splash(ed)? ?down", "decayed"),
    (r"contact (was |were )?lost|communications? (was |were )?lost|loss of (contact|signal|communications?)|"
     r"lost contact|no longer in contact|ceased (transmitting|communicating)|not been heard", "lost"),
    (r"no further updates|completed (\w+ )?mission|mission (has )?(ended|completed|concluded)|"
     r"decommission|deactivat|end of (the )?mission|retired|passivat|turned off|switched off|"
     r"ended (science )?operations|no longer operat", "derelict"),
    (r"\blanded\b|soft[- ]landing|touchdown", "landed"),
]
NOTE_DATE = re.compile(r"(\d{4})-([A-Za-z]{3})[a-z]*-(\d{1,2})")


def header_notes(hdr: str) -> list[str]:
    """NOTE paragraphs of a Horizons object header (they carry end-of-mission / loss notices)."""
    out, lines = [], hdr.splitlines()
    for i, ln in enumerate(lines):
        if re.match(r"^\s*NOTE\b", ln):
            para, j = [ln.strip()], i + 1
            while j < len(lines) and lines[j].strip() and not lines[j].strip().startswith("*"):
                para.append(lines[j].strip())
                j += 1
            out.append(" ".join(para))
    return out


def note_status(hdr: str):
    """(status, date, snippet) from NOTE paragraphs, or None.  Loss notices that also say the
    contact was recovered / restored are ignored."""
    for para in header_notes(hdr):
        for pat, st in STATUS_KW:
            m = re.search(pat, para, re.I)
            if not m:
                continue
            if st == "lost" and re.search(r"recover|restor|re-?establish|regain", para, re.I):
                continue
            md = NOTE_DATE.search(para)
            date = ""
            if md and md.group(2).upper() in MON:
                date = f"{md.group(1)}-{MON[md.group(2).upper()]:02d}-{int(md.group(3)):02d}"
            lo = max(0, m.start() - 40)
            return st, date, para[lo:m.end() + 40].strip()
    return None


def status_guess(o: dict, ev: dict, regime: str, is_sb: bool, otype: str = "payload") -> tuple[str, str, str]:
    """(status, status_date, note) from geometry + header; heuristic (see STATUS_FIX for curated ones)."""
    win = o.get("win") or {}
    if is_sb:
        if win.get("next_close_t"):
            return "returning", iso(win["next_close_t"]), \
                f"natural; next heliocentric pass inside 0.05 AU {win['next_close_d']:.3g} km on {iso(win['next_close_t'])}"
        if win.get("helio_min_t") and win.get("helio_min_d", 1e30) < HELIO_KM:
            return "escaped", iso(win["helio_min_t"]), "natural; last pass inside 0.05 AU before now, none later in 2030"
        return "escaped", "", "natural; no pass inside 0.05 AU of Earth 2024-2030"
    s0, s1 = o.get("span0"), o.get("span1")
    ended = s1 is not None and not o.get("span1_open") and s1 < NOW
    c = ev.get("cls")
    hdr = (o.get("hdr_text") or "")
    ns = note_status(hdr)
    nsnote = f"Horizons header NOTE: '...{ns[2]}...'" if ns else ""
    if s0 is not None and not o.get("span0_open") and s0 > NOW:
        return "unknown", iso(s0), f"Horizons trajectory starts {iso(s0)} (after now; pre-launch / planning arc)"
    if ended and c:
        g = c["geo"]
        if c["d"] < R_E + 200 or (c["d"] < R_E + 3000 and g["rp"] < R_E + 120):
            return "decayed", iso(s1), f"ephemeris ends at {c['d'] - R_E:.0f} km altitude, perigee below 120 km (reentry)"
        if c["dm"] < R_M + 30:
            k = "landed" if re.search(r"land", hdr, re.I) and not re.search(r"impact", hdr, re.I) else "lunar_impact"
            return k, iso(s1), f"ephemeris ends at lunar surface ({c['dm'] - R_M:.0f} km altitude)"
        if c["regime"] in HELIO_CLASSES and c["d"] < HELIO_KM and c["d"] > 1.5e6:
            return "unknown", iso(s1), (f"arc ends heliocentric only {c['d']:.3g} km ({c['d'] / AU:.4f} AU) from "
                                        f"Earth; later motion not in Horizons")
        if c["regime"] in ("HELIO_FAR",) or (c["regime"] == "EARTH_NEAR_HELIO" and c["d"] > 1.5e6):
            return "escaped", iso(s1), "ephemeris ends heliocentric"
        if g["eps"] > 0 and c["d"] > 5.0e5 and c["dm"] > HILL_M:
            return "escaped", iso(s1), f"ephemeris ends geocentric-hyperbolic at {c['d']:.3g} km (Earth escape)"
        # ephemeris stops on an impacting trajectory (e.g. WT1190F: span ends the day before reentry)
        js = ev.get("cls_state")
        if js is not None:
            r_, v_ = np.asarray(js[2][:3]), np.asarray(js[2][3:])
            if g["rp"] < R_E + 100 and float(np.dot(r_, v_)) < 0:
                return "decayed", iso(s1), (f"ephemeris ends inbound on an Earth-impacting trajectory "
                                            f"(osculating perigee {g['rp'] - R_E:.0f} km altitude) at "
                                            f"{c['d']:.0f} km; reentry soon after {iso(s1)}")
            sl = c.get("sel")
            if sl and c["dm"] < HILL_M and sl["rp"] < R_M:
                return "lunar_impact", iso(s1), (f"ephemeris ends on a lunar-impacting trajectory (selenocentric "
                                                 f"periapsis {sl['rp']:.0f} km < R_Moon) {c['dm']:.0f} km from Moon")
        if ns and ns[0] in ("lost", "derelict"):
            return ns[0], ns[1] or iso(s1), nsnote
        return "unknown", iso(s1), f"ephemeris ends {iso(s1)} while {c['regime']} (object presumably still there)"
    rev = o["hdr"].get("revised")
    if ns and ns[0] in ("lost", "derelict"):
        return ns[0], ns[1] or rev or "", nsnote
    if ev.get("covers_now"):
        helio_now = bool(c and c["regime"] in HELIO_CLASSES)
        tle = any("TLE" in n.upper() for n, _, _ in (o["hdr"].get("traj") or [])) or "TLE" in o.get("source", "").upper()
        # 'active' heuristic: payload whose (non-TLE) Horizons header was revised recently; a launch after
        # the header revision (pre-launch planning file, e.g. MMX) dates the status at the span start
        if otype == "payload" and rev and rev >= "2026-06-01" and not tle:
            sd = max(rev, iso(s0)) if s0 is not None and not o.get("span0_open") and s0 <= NOW else rev
            note = f"heuristic: Horizons header revised {rev}"
            if helio_now and win.get("next_close_t"):
                note += f"; next Earth pass {win['next_close_d']:.3g} km on {iso(win['next_close_t'])}"
            return "active", sd, note
        if helio_now:
            if win.get("next_close_t"):
                return "returning", iso(win["next_close_t"]), \
                    f"heliocentric; next pass inside 0.05 AU {win['next_close_d']:.3g} km on {iso(win['next_close_t'])}"
            # date only when the 2024-now heliocentric minimum really was a pass inside 0.05 AU
            last = iso(win["helio_min_t"]) if win.get("helio_min_t") and win["helio_min_t"] <= NOW \
                and win.get("helio_min_d", 1e30) < HELIO_KM else ""
            return "escaped", last, "heliocentric now; no pass inside 0.05 AU of Earth later in 2026-2030" + \
                (f" (last inside 0.05 AU {last})" if last else
                 (f" (none in 2024-2030: heliocentric min {win['helio_min_d']:.3g} km)" if win.get("helio_min_d") else ""))
        if otype in ("rocket_body", "debris"):
            return "derelict", rev or "", f"{otype.replace('_', ' ')} (never operating); Horizons header revised {rev}"
        if tle:
            return "unknown", rev or "", "TLE-driven Horizons ephemeris (header revision is not an activity signal)"
    return "unknown", rev or "", ""


# Curated status facts: Horizons object-header text read 2026-10-21 ("hdr") or memory ("memory").
# id -> (status, status_date, note).  Applied after status_guess().
STATUS_FIX = {
    "-3": ("escaped", "2022-10-02", "hdr NOTE: contact lost 2022-04, mission formally ended 2022-10-02; in Mars orbit"),
    "-25": ("lunar_impact", "1999-07-31", "memory: deliberate impact near the south pole 1999-07-31; Horizons arc "
                                          "covers only transfer+LOI 1998-01-07..01-20"),
    "-39": ("unknown", "", "hdr: 'OBSOLETE ... propagation done in 2015 ... for testing purposes only'; fate unobserved"),
    "-40": ("escaped", "1994-08-08", "hdr: left lunar orbit 1994-May-03, mission terminated 1994-Aug-8, left in "
                                     "heliocentric space; extended runout is SCID -40000 (folded here)"),
    "-47": ("escaped", "2004-12-02", "hdr: bus hibernated 2004-Dec-02, left EM-Sun L1 for heliocentric orbit early 2005"),
    "-75": ("escaped", "2022-11-22", "hdr NOTE: recovery attempts ended 2022-11-22; heliocentric after lunar flyby"),
    "-86": ("derelict", "2009-08-29", "hdr: contact lost 2009-08-29; inactive, still in lunar orbit (Goldstone radar "
                                      "2016)"),
    "-153": ("lunar_impact", "2019-09-06", "hdr NOTE: Vikram hard landing (crash) 2019-09-06"),
    "-158": ("landed", "2023-08-23", "hdr UPDATE: soft landing 2023-Aug-23 12:32 UTC, Shiv Shakti Point"),
    "-164": ("escaped", "2023-06-27", "hdr: final update 2023-06-27, left in heliocentric orbit (never reached "
                                      "lunar orbit)"),
    "-165": ("escaped", "2010-10-20", "memory: retired 2010 to a heliocentric graveyard orbit (date approx.); "
                                      "hdr: 'Operations are scheduled to end Sep 2010'"),
    "-182": ("escaped", "2022-11-16", "hdr NOTE: contact never established after deployment, spacecraft lost; "
                                      "heliocentric"),
    "-198": ("derelict", "2025-02-28", "memory: INTEGRAL science operations ended 2025-02-28 (re-entry expected "
                                       "~2029); hdr silent, arc TLE-driven"),
    "-235": ("lost", "2016-09-23", "hdr NOTE: contact lost 2014-10-01, briefly regained 2016-08-21, last contact "
                                   "2016-09-23; heliocentric (prediction)"),
    "-240": ("landed", "2024-01-19", "hdr NOTE: landed 2024-Jan-19 15:19:57 UTC"),
    "-242": ("escaped", "2025-07-31", "hdr NOTE: mission formally ended 2025-Jul-31, contact never re-established; "
                                      "heliocentric"),
    "-486": ("escaped", "2013-06-17", "memory: Herschel passivated 2013-06-17 in a heliocentric disposal orbit"),
    "-489": ("escaped", "2013-10-23", "hdr NOTE: moved from SE-L2 to a heliocentric orbit, last contact 2013-10-23"),
    "-557": ("derelict", "2019-01-10", "memory: contact lost 2019-01-10, mission ended 2019-05-30; hdr silent, arc "
                                       "TLE-driven"),
    "-100836": ("derelict", "", "memory: Vela 2A (1964) long inactive; Horizons arc only 2021-04-05..05-31"),
    "-106197": ("derelict", "", "memory: IMP-7 (1972) mission ended 1978; Horizons arc only 2021-04-05..05-31"),
    "-113901": ("derelict", "", "memory: Astron (1983) mission ended 1989; Horizons arc only 2021-04-05..05-31"),
    "-139479": ("escaped", "2025-03-27", "hdr NOTE: science ended 2025-Jan-25, placed in heliocentric disposal "
                                         "orbit; passivation date 2025-03-27 from memory"),
    "-141043": ("escaped", "2017-07-18", "memory (moderate confidence): science ended 2017-06-30, moved off SE-L1 "
                                         "and passivated 2017-07-18; Horizons arc ends 2017-06-30"),
    "999787": ("decayed", "2015-11-13", "memory: re-entry 2015-11-13 ~06:18 UTC off Sri Lanka; Horizons arc ends "
                                        "2015-11-12 06:20 (hdr: drag not modelled)"),
    "-64090": ("decayed", "2023-09-24", "memory: sample-return capsule re-entered and soft-landed by parachute at "
                                        "the Utah Test and Training Range 2023-09-24 ~14:52 UTC; hdr NOTE 2023-Sep-18: "
                                        "trajectory accurate within atmospheric/weather/wind uncertainties; elements "
                                        "at the arc end (surface) are not meaningful"),
    "-10001001": ("lost", "2025-02-27", "hdr NOTE: 'Aside from a couple short bursts of telemetry after launch, there "
                                        "has been no contact with the spacecraft (or confirmed optical sightings)'; "
                                        "launched 2025-02-27; arc is an untracked post-launch OPM prediction "
                                        "(2025-02-27..2026-03-02) whose accuracy 'will degrade quickly'"),
    "-239": ("active", "2026-10-19", "hdr (revised 2026-10-07): launch 2026-10-19 19:41:03 UTC on H3; the arc is the "
                                     "JAXA PRE-LAUNCH planning trajectory 20261019_20281231_MMX_V0.2 (starts ~1h18m "
                                     "after launch); actual launch confirmed in catalog/sources/news.tsv (JAXA press "
                                     "release 2026-10-20, SATCAT 2026-236A)"),
}
# Curated regime fixes: id -> (regime, note)
REGIME_FIX = {
    "-169": ("CISLUNAR_HEO", "propulsion module left lunar orbit 2023-10 and returned to a high Earth orbit "
                             "2023-11 (memory; also ../lunarmission); Horizons arc ends 2023-11-10 just after the "
                             "lunar flyby, geocentric-bound P=21.5 d"),
    "-999789": ("EARTH_NEAR_HELIO", "Horizons 'SPECIAL CASE' man-made debris on an Earth-like heliocentric orbit "
                                    "(hdr elements q=0.907 AU, e=0.058, i=0.25 deg ecliptic, radial non-grav A1, fit "
                                    "2023-03-17..07-10); the arc ends 2023-12-31 only 0.021 AU from Earth, i.e. still "
                                    "inside 0.05 AU at the start of 2024; the span-scan CISLUNAR_HEO/SE samples (15.6-d "
                                    "sampling) are weakly geocentric-bound phases of slow close approaches"),
}
# Same physical object under two Horizons IDs: primary row (identity) <- secondary (later arc)
LINKS = {"-40": "-40000", "-399120": "-998"}


def read_audit(path: Path = OUT_ALL) -> dict:
    """{horizons_id: row} of the existing audit table (empty when there is none)."""
    if not path.exists():
        return {}
    lines = path.read_text(encoding="utf-8").splitlines()
    cols = lines[0].split("\t")
    return {r["horizons_id"]: r for r in (dict(zip(cols, ln.split("\t"))) for ln in lines[1:]) if r.get("horizons_id")}


def carry_row(r: dict) -> dict:
    """An audit row carried over unchanged from a previous sweep, in the current column set: dated
    d_YYYYMMDD_km columns become sweep_now (their middle date) + d_0_km; the +-15 d cells are left empty."""
    r = dict(r)
    dated = sorted(k for k in r if re.fullmatch(r"d_\d{8}_km", k))
    if dated and not r.get("sweep_now"):
        mid = dated[len(dated) // 2]
        r["sweep_now"] = f"{mid[2:6]}-{mid[6:8]}-{mid[8:10]}"
        r["d_0_km"], r["d_m15_km"], r["d_p15_km"] = r.get(mid, ""), "", ""
    for k in dated:
        r.pop(k, None)
    return r


def audit_age(prev: dict) -> int | None:
    """Days between NOW and the oldest sweep_now of the in-scope audit rows (dated columns count by their middle
    date); None when there is no in-scope row or one of them was never swept, i.e. a sweep is due."""
    dates = []
    for r in prev.values():
        if r.get("in_scope") != "Y":
            continue
        d = carry_row(r).get("sweep_now")
        if not d:
            return None
        dates.append(dt.datetime.fromisoformat(d))
    return max(abs((NOW - d).days) for d in dates) if dates else None


def traj_summary(traj) -> str:
    if not traj:
        return ""
    t = traj[-3:]
    s = ",".join(f"{n}[{a}..{b}]" for n, a, b in t)
    return ("+" + str(len(traj) - 3) + " earlier," if len(traj) > 3 else "") + s


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true", help="ignore cache, re-download")
    ap.add_argument("--only", default="", help="comma-separated Horizons IDs to process (debug)")
    ap.add_argument("--no-probes", action="store_true", help="skip small-body / name probes")
    ap.add_argument("--now", default="", help="reference epoch ISO UTC (default $CLE_NOW, else today); floored to the day")
    ap.add_argument("--in-scope-only", action="store_true",
                    help="re-probe only the in-scope IDs of the existing audit table plus new MB-list IDs; carry the rest")
    ap.add_argument("--offline", action="store_true", help="cache only: never contact Horizons")
    ap.add_argument("--dry-run", action="store_true", help="print now, epochs and the job list; no requests")
    ap.add_argument("--max-age", type=int, metavar="DAYS",
                    help="do nothing while every in-scope audit row was swept within DAYS of now (the daily's gate)")
    args = ap.parse_args(argv)
    global OFFLINE
    OFFLINE = args.offline
    set_now(args.now or None)
    if args.max_age is not None and not args.refresh:
        age = audit_age(read_audit(OUT_ALL))
        if age is not None and age <= args.max_age:
            print(f"sweep: audit table swept {age} d from {iso(NOW)} (<= {args.max_age} d); nothing to do"
                  " (FORCE=1 / without --max-age to re-probe)", file=sys.stderr)
            return None
        print(f"sweep due: audit table {'never swept' if age is None else f'{age} d old'} (> {args.max_age} d)", file=sys.stderr)
    RAW.mkdir(parents=True, exist_ok=True)
    reprobe = args.in_scope_only and not OFFLINE

    mbtext = hz({"MAKE_EPHEM": "YES", "OBJ_DATA": "YES"}, "mb", "MB", args.refresh or reprobe)
    (CACHE / "mb_list.txt").write_text(mbtext, encoding="utf-8")
    mb = parse_mb(mbtext)
    targets = []
    for r in mb:
        i = int(r["id"])
        art = i < 0 or "(spacecraft" in r["name"] or r["name"].startswith("WT1190F")
        if art:
            targets.append(r)
    if args.only:
        keep = set(args.only.split(","))
        targets = [r for r in targets if r["id"] in keep]
    print(f"MB list: {len(mb)} bodies; {len(targets)} spacecraft/artificial entries to sweep", file=sys.stderr)

    jobs = [("mb", r["id"], r) for r in targets]
    if not args.no_probes and not args.only:
        jobs += [("sb", cmd, dict(id=cmd, name=lab, desig="", alias="", why=why)) for lab, cmd, why in SB_PROBES]
        jobs += [("name", nm, dict(id=nm, name=nm, desig="", alias="", why="name probe")) for nm in NAME_PROBES]
    carried = []
    if args.in_scope_only:
        prev = read_audit(OUT_ALL)
        want = {h for h, r in prev.items() if r.get("in_scope") == "Y"}
        want |= {s for p_, s in LINKS.items() if p_ in want} | {p_ for p_, s in LINKS.items() if s in want}
        jobs = [j for j in jobs if (j[0] == "mb" and (j[1] in want or j[1] not in prev)) or (j[0] == "sb" and j[1] in want)]
        swept = {j[1] for j in jobs}
        carried = [carry_row(r) for h, r in prev.items() if h not in swept]
        print(f"in-scope-only: {len(jobs)} jobs ({len(want)} in scope + new IDs), {len(carried)} audit rows carried",
              file=sys.stderr)
    print(f"now {iso(NOW)}: epochs {EPOCHS}; {len(jobs)} jobs" + (" (offline)" if OFFLINE else ""), file=sys.stderr)
    if args.dry_run:
        for kind, cmd, meta in jobs:
            print(f"  {kind:4s} {cmd:>12s} {meta['name'][:40]}")
        return [], []

    rows_tsv, rows_all = [], []
    cands, order = {}, []       # hid -> candidate row info; order of in-scope hids
    sb_links = []               # (desig, sb command, sb resolved?, probe text, [MB ids carrying that desig])
    for n, (kind, cmd, meta) in enumerate(jobs, 1):
        is_sb = kind == "sb"
        is_sim = bool(SIM_RE.search(meta["name"])) or meta["name"].startswith(("2015 PDC", "TTX"))
        t0 = time.time()
        if kind == "name":
            try:
                p = parse(hz(dict(VEC, TLIST=EPOCHS[1], OBJ_DATA="NO"), "name", cmd, args.refresh))
            except OfflineMiss as e:
                p = dict(multi=[], target="", source="", err=str(e))
            res = ("multiple MB matches: " + ";".join(f"{a} {b}" for a, b in p["multi"][:8])) if p["multi"] else \
                  (f"resolved to {p['target']} {{{p['source']}}}" if p["target"] else no_match_text(p["err"]))
            rows_all.append(dict(horizons_id=cmd, name=cmd, kind="name_probe", resolved="Y" if p["target"] else "N",
                                 target=p["target"], source=p["source"], in_scope="N", reason=res))
            continue
        if is_sim:
            o = dict(cmd=cmd, resolved=True, epochs={}, hdr={"revised": ""}, target=meta["name"], source="",
                     span0=None, span1=None, errs=["simulation not swept"], win={}, hist={})
        else:
            try:
                o = sweep_object(cmd, args.refresh, is_sb, reprobe)
            except OfflineMiss as e:
                o = dict(cmd=cmd, resolved=False, epochs={}, hdr={"revised": ""}, target="", source="",
                         span0=None, span1=None, errs=[f"not cached (offline): {e}"], win={}, hist={})
        if not is_sim and o.get("resolved"):
            # header text (cached ep3 response) for NOTE-paragraph status keywords
            try:
                hdr_text = hz(dict(VEC, **ep3_params()), "ep3", cmd, False)
            except OfflineMiss:
                hdr_text = cached_ep3(cmd)
            o["hdr_text"] = hdr_text.split("Ephemeris / API_USER")[0][:20000]
        ev = evaluate(o) if o.get("resolved") and not is_sim else dict(cls=None, dists={}, covers_now=False)
        ins, regime, reason = decide(o, ev, is_sim)
        if not ins and cmd in LINKS.values() and o.get("regime_calc"):
            regime = o["regime_calc"]   # rule regime of an out-of-scope arc that is folded into another row
        # selenocentric query near Moon
        sel = None
        c = ev.get("cls")
        if ins and c and c["dm"] < 1.0e5:
            jd, t, _ = ev["cls_state"]
            try:
                ps = vectors(cmd, args.refresh, "moon", center="500@301", TLIST=t.strftime("%Y-%m-%d %H:%M:%S"))
            except OfflineMiss as e:
                ps = dict(states=[], err=str(e))
            if ps["states"]:
                sx = ps["states"][0][2]
                sel = coe(sx[:3], sx[3:], MU_M)
                nrho = "NRHO" in c.get("tag", "") and sel["eps"] < 0 and sel["rp"] <= 2.0e4 and sel["ra"] >= 4.0e4
                if regime in ("LUNAR_ORBIT", "EM_L2", "EM_L1", "TRANSLUNAR", "CISLUNAR_HEO") and sel["eps"] < 0 \
                        and sel["ra"] < HILL_M and not nrho:
                    regime = "LUNAR_ORBIT"
            else:
                o["errs"].append("moon-centred request failed: " + ps["err"][:100])
        rnote = ""
        if ins and cmd in REGIME_FIX:
            regime, rnote = REGIME_FIX[cmd]
            rnote = f"regime fix: {rnote}"
        raw_name = clean_name(o.get("target") or meta["name"]) if o.get("target") else clean_name(meta["name"])
        name = re.sub(r"_+", " ", raw_name).strip() or raw_name
        if is_sb and o.get("target"):
            name = raw_name = o["target"].strip("()").strip()
        otype = obj_type_of(name, is_sb)
        st, sd, snote = status_guess(o, ev, regime, is_sb, otype) if o.get("resolved") and not is_sim else ("", "", "")
        if cmd in STATUS_FIX and o.get("resolved"):
            st, sd, snote = STATUS_FIX[cmd][0], STATUS_FIX[cmd][1], "status fix: " + STATUS_FIX[cmd][2]
        # small-body probes: also try the '<desig>;' form and look for an artificial-object MB entry
        alt_probe = ""
        if is_sb and (not o.get("resolved") or meta.get("why") == "task"):
            desig = cmd.split("=", 1)[1]
            try:
                pp = parse(hz(dict(VEC, TLIST=EPOCHS[1], OBJ_DATA="NO"), "semi", desig + ";", args.refresh))
            except OfflineMiss as e:
                pp = dict(target="", source="", err=str(e))
            alt_probe = f"'{desig};' -> " + (f"resolved to {pp['target']} {{{pp['source']}}}" if pp["target"]
                                             else "no match in the JPL small-body database")
            mbm = [r for r in mb if desig in r["name"].replace("_", " ") or desig == r["desig"]
                   or re.search(r"(^|\s)" + re.escape(desig) + r"(\s|$)", r["alias"])]
            if mbm:
                alt_probe += "; Horizons major-body entry " + ", ".join(f"{r['id']} ({r['name']})" for r in mbm)
                sb_links.append((desig, cmd, bool(o.get("resolved")), alt_probe, [r["id"] for r in mbm]))
        # elements
        el = dict(P=None, a=None, e=None, i=None, rp=None, ra=None)
        fc, eepoch = "", None
        if c:
            jd, t, xyz = ev["cls_state"]
            eepoch = t
            gs = sel or c.get("sel")
            if regime in ("LUNAR_ORBIT", "EM_L1", "EM_L2") and gs and gs["eps"] < 0 and c["dm"] < 1.5 * HILL_M:
                g = gs  # selenocentric (Horizons 500@301 state when fetched, else DE440 Moon offset)
                fc = "moon"
            elif regime in ("EARTH_NEAR_HELIO",) or c["regime"] in HELIO_CLASSES:
                rh = np.array(xyz[:3]) - c["sun_r"]
                vh = np.array(xyz[3:]) - c["sun_v"]
                g = coe(rh, vh, MU_S, ecl=True)
                fc = "sun"
            else:
                g = c["geo"]
                fc = "earth"
            el = dict(P=g["P"], a=g["a"], e=g["e"], i=g["i"], rp=g["rp"], ra=g["ra"])
        s0, s1 = o.get("span0"), o.get("span1")
        span = ""
        if s0 or s1:
            span = f"{'' if o.get('span0_open') else iso(s0)}..{'' if o.get('span1_open') else iso(s1)}"
            if is_sb:
                span = ".."
        hid = cmd
        dstr = {e: (fmt(ev["dists"][e]["d"], 0) if e in ev["dists"] else "no coverage") for e in EPOCHS}
        win, hist = o.get("win") or {}, o.get("hist") or {}
        cospar = meta.get("desig", "")
        cospar = cospar if re.fullmatch(r"\d{4}-\d{3}[A-Z]{1,3}", cospar) else ""
        norad = ""
        try:
            iv = int(cmd)
            if -199999 <= iv <= -100001:
                norad = str(-iv - 100000)
        except ValueError:
            pass
        # aliases: MB name / raw Horizons name if different, MB alias column split into tokens
        # (the MB list space-separates aliases and truncates at column 80), non-COSPAR MB designation
        al_tok = meta.get("alias", "").replace("Nancy Grace", "Nancy_Grace").replace("2020 SO", "2020_SO").split()
        al_tok = [t.replace("_", " ") for t in al_tok if len(t) >= 2]
        aliases = []
        for x in ([clean_name(meta["name"]) if not is_sb else meta["name"], raw_name] + al_tok
                  + [meta.get("desig", "") if not cospar else ""]):
            x = x.strip()
            if x and x != name and x not in aliases and x != cmd:
                aliases.append(x)
        traj = traj_summary(o.get("hdr", {}).get("traj") if o.get("hdr") else None)
        cnow = ev["dists"].get(iso(NOW)) if ev.get("dists") else None
        gnow = cnow["geo"] if cnow else {}
        notes_hdr = " / ".join(header_notes(o.get("hdr_text") or ""))[:300]
        rows_all.append(dict(
            dmoon_now_km=fmt(cnow["dm"], 0) if cnow else "",
            geo_a_km=fmt(gnow.get("a"), 1), geo_e=fmt(gnow.get("e"), 6), geo_i_deg=fmt(gnow.get("i"), 3),
            geo_P_d=fmt(gnow.get("P"), 4), geo_rp_km=fmt(gnow.get("rp"), 1), geo_ra_km=fmt(gnow.get("ra"), 1),
            header_note=notes_hdr, status_guess=st, status_date=sd,
            horizons_id=hid, name=name, kind="sb_probe" if is_sb else ("simulation" if is_sim else "mb"),
            mb_designation=meta.get("desig", ""), mb_aliases=meta.get("alias", ""),
            target=o.get("target", ""), source=o.get("source", ""), revised=(o.get("hdr") or {}).get("revised", ""),
            span_start="" if (o.get("span0_open") or not s0) else iso(s0, True),
            span_end="" if (o.get("span1_open") or not s1) else iso(s1, True),
            span_note=("open" if o.get("span0_open") or o.get("span1_open") else ""),
            covers_now="Y" if ev.get("covers_now") else "N",
            sweep_now=iso(NOW), d_m15_km=dstr[EPOCHS[0]], d_0_km=dstr[EPOCHS[1]], d_p15_km=dstr[EPOCHS[2]],
            min_d_2024_2030_km=fmt(win.get("min_d"), 0), min_d_date=iso(win.get("min_t")),
            helio_min_d_2024_2030_km=fmt(win.get("helio_min_d"), 0), helio_min_date=iso(win.get("helio_min_t")),
            next_pass_005au_date=iso(win.get("next_close_t")), next_pass_005au_km=fmt(win.get("next_close_d"), 0),
            min_dmoon_km=fmt(min([x.get("min_dm", 1e30) for x in (win, hist) if x] or [float("nan")]), 0),
            last_state_epoch=iso(o["last"][1], True) if o.get("last") else "",
            last_state_d_km=fmt(float(np.linalg.norm(o["last"][2][:3])), 0) if o.get("last") else "",
            class_epoch=iso(ev.get("cls_epoch"), True) if ev.get("cls_epoch") else "",
            regime_guess=regime, geom_tag=c["tag"] if c else "", in_scope="Y" if ins else "N", reason=reason,
            trajectory_files=traj, errors=" | ".join(o.get("errs", []))[:400], alt_probe=alt_probe,
            resolved="Y" if o.get("resolved") else "N"))
        if o.get("resolved") and not is_sim:
            notes = []
            if o.get("source"):
                notes.append(f"Horizons source {o['source']}")
            if traj:
                notes.append(f"traj files {traj}")
            notes.append("d(km) " + "/".join(f"{e[5:]}={dstr[e]}" for e in EPOCHS))
            if win:
                notes.append(f"2024-30 min d {win['min_d']:.4g} km {iso(win['min_t'])} (step {win['step_min']} min)")
                if win.get("helio_min_d") is not None and win.get("helio_min_t") != win.get("min_t"):
                    notes.append(f"heliocentric-phase min {win['helio_min_d']:.4g} km {iso(win['helio_min_t'])}")
                if win.get("next_close_t"):
                    notes.append(f"next pass inside 0.05 AU {win['next_close_d']:.4g} km {iso(win['next_close_t'])}")
            if c:
                notes.append(f"class@{iso(eepoch)}: {c['tag']}")
                if regime != c["regime"]:
                    notes.append(f"regime {regime} from span scan / rules; state at element epoch is {c['regime']}")
                if regime == "OTHER" and c["regime"] in HELIO_CLASSES and ev.get("covers_now"):
                    notes.append("regime OTHER: Horizons covers now and the object is heliocentric, not within "
                                 "0.05 AU of Earth 2024-30; kept only for its historical in-scope phase")
            notes.append(reason)
            if rnote:
                notes.append(rnote)
            if snote:
                notes.append(snote)
            if norad:
                notes.append("NORAD from Horizons -1NNNNN ID convention")
            if fc == "sun":
                notes.append("helio elements: ecliptic J2000 incl")
            elif fc == "moon":
                notes.append("seleno elements (period selenocentric), incl to ICRF equator")
            elif fc == "earth" and regime in ("SE_L1", "SE_L2", "EM_L1", "EM_L2"):
                notes.append("geocentric osculating elements not physical for libration orbit")
            elif fc == "earth" and regime == "CISLUNAR_HEO" and el["ra"] and el["ra"] < 2.0e5:
                notes.append("apogee < 2e5 km: SCHEMA examples call Chandra/INTEGRAL/Vela HEO, numeric rule "
                             "(apogee >= 1e5 km) used")
            if is_sb:
                notes.append(f"sb probe ({meta.get('why')}); JPL small-body integration, any epoch")
                if alt_probe:
                    notes.append(alt_probe)
            if o.get("errs"):
                notes.append("errs: " + " | ".join(o["errs"])[:200])
            row = dict(
                src="horizons", src_ref=hid, name=name, aliases=";".join(aliases), norad=norad, cospar=cospar,
                horizons_id=hid, obj_type=otype, regime=regime, status=st, status_date=sd,
                period_d=fmt(el["P"], 4), a_km=fmt(el["a"], 1), e=fmt(el["e"], 6), incl_deg=fmt(el["i"], 3),
                perigee_km=fmt(el["rp"], 1), apogee_km=fmt(el["ra"], 1),
                elem_epoch=iso(eepoch, True) if eepoch else "", frame_center=fc,
                ephem_sources=f"horizons:{hid}[{span}]",
                covers_now="Y" if ev.get("covers_now") else "N", notes="; ".join(notes).replace("\t", " "))
            cands[hid] = dict(row=row, ins=ins, audit=rows_all[-1])
            if ins:
                order.append(hid)
        print(f"[{n}/{len(jobs)}] {cmd:>10s} {name[:34]:34s} {'IN ' if ins else 'out'} {regime:16s} "
              f"{reason[:70]} {st} ({time.time()-t0:.1f}s net={STATS['net']})", file=sys.stderr)

    # fold Horizons IDs that are the same physical object (primary keeps identity, secondary = later arc)
    for pid, sid in LINKS.items():
        if pid not in cands or sid not in cands or not (cands[pid]["ins"] or cands[sid]["ins"]):
            continue
        P, S = cands[pid]["row"], cands[sid]["row"]
        m = dict(P)
        al = [x for x in P["aliases"].split(";") + [S["name"]] + S["aliases"].split(";") + [sid] if x]
        m["aliases"] = ";".join(dict.fromkeys(x for x in al if x != P["name"]))
        m["horizons_id"] = sid
        m["ephem_sources"] = P["ephem_sources"] + ";" + S["ephem_sources"]
        m["covers_now"] = "Y" if "Y" in (P["covers_now"], S["covers_now"]) else "N"
        for k in ("regime", "period_d", "a_km", "e", "incl_deg", "perigee_km", "apogee_km", "elem_epoch",
                  "frame_center"):
            m[k] = S[k]
        if pid not in STATUS_FIX:
            m["status"], m["status_date"] = S["status"], S["status_date"]
        m["norad"] = P["norad"] or S["norad"]
        m["cospar"] = P["cospar"] or S["cospar"]
        m["notes"] = (f"FOLDED: Horizons {pid} ({P['name']}) and {sid} ({S['name']}) are the same physical object; "
                      f"horizons_id/elements/regime from the later arc {sid}"
                      + ("" if pid in STATUS_FIX else ", status too") + f" || {pid}: {P['notes'][:700]} || "
                      f"{sid}: {S['notes'][:700]}")
        cands[pid]["row"] = m
        cands[pid]["ins"] = True
        if pid not in order:
            order.append(pid)
        if sid in order:
            order.remove(sid)
        cands[sid]["audit"]["tsv_row"] = pid
        cands[sid]["audit"]["reason"] = f"folded into {pid}; " + cands[sid]["audit"]["reason"]
    # small-body designations that Horizons carries as artificial major-body entries (e.g. 2020 SO ->
    # -54054450, WT1190F -> 999787): note the probe result on the TSV row that holds that MB entry
    sec2pri = {s: p for p, s in LINKS.items()}
    for desig, scmd, sres, ptxt, ids in sb_links:
        for mid in ids:
            hid = mid if mid in order else sec2pri.get(mid)
            if not hid or hid not in order:
                continue
            r = cands[hid]["row"]
            r["notes"] += (f"; sb probe {scmd!r}: " + ("resolves in the JPL small-body DB too" if sres else
                                                       "does not resolve") + f" ({ptxt})")
            al = [x for x in r["aliases"].split(";") if x]
            if desig not in al and desig != r["name"] and desig.replace(" ", "_") not in r["name"]:
                r["aliases"] = ";".join(al + [desig])
    for hid in order:
        rows_tsv.append(cands[hid]["row"])
        cands[hid]["audit"].setdefault("tsv_row", hid)
        cands[hid]["audit"]["regime_guess"] = cands[hid]["row"]["regime"]
        cands[hid]["audit"]["status_guess"] = cands[hid]["row"]["status"]
        cands[hid]["audit"]["status_date"] = cands[hid]["row"]["status_date"]
    rows_all += carried

    cols = ["src", "src_ref", "name", "aliases", "norad", "cospar", "horizons_id", "obj_type", "regime", "status",
            "status_date", "period_d", "a_km", "e", "incl_deg", "perigee_km", "apogee_km", "elem_epoch",
            "frame_center", "ephem_sources", "covers_now", "notes"]
    if not args.only:
        with open(OUT_TSV, "w", encoding="utf-8") as f:
            f.write("\t".join(cols) + "\n")
            for r in rows_tsv:
                f.write("\t".join(str(r[k]).replace("\t", " ").replace("\n", " ") for k in cols) + "\n")
        acols = ["horizons_id", "name", "kind", "resolved", "mb_designation", "mb_aliases", "target", "source",
                 "revised", "span_start", "span_end", "span_note", "covers_now", "sweep_now", "d_m15_km", "d_0_km",
                 "d_p15_km", "dmoon_now_km", "geo_a_km", "geo_e", "geo_i_deg", "geo_P_d", "geo_rp_km",
                 "geo_ra_km", "min_d_2024_2030_km", "min_d_date", "min_dmoon_km", "last_state_epoch",
                 "last_state_d_km", "class_epoch", "regime_guess", "geom_tag", "in_scope", "reason",
                 "status_guess", "status_date", "header_note", "trajectory_files", "alt_probe", "errors"]
        with open(OUT_ALL, "w", encoding="utf-8") as f:
            f.write("\t".join(acols) + "\n")
            for r in rows_all:
                f.write("\t".join(str(r.get(k, "")).replace("\t", " ").replace("\n", " ") for k in acols) + "\n")
    if args.only:
        for r in rows_tsv:
            print("\n".join(f"  {k}: {v}" for k, v in r.items()), "\n")
        for r in rows_all:
            print(" | ".join(f"{k}={v}" for k, v in r.items() if v), "\n")
    print(f"done: {len(rows_tsv)} in-scope rows, {len(rows_all)} audit rows; net={STATS['net']} cache={STATS['cache']}"
          + (f" offline_miss={STATS['offline_miss']}" if OFFLINE else ""), file=sys.stderr)
    return rows_tsv, rows_all


if __name__ == "__main__":
    main()
