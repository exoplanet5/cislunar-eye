#!/usr/bin/env python3
"""neocp_live.py -- live MPC NEOCP geocentric / S-flag extractor for cislunar-eye.

Builds ``catalog/sources/neocp.tsv`` (columns and vocabulary per
``catalog/sources/SCHEMA.md``) from the *live* NEOCP:

1. Bill Gray's summary  https://www.projectpluto.com/neocp2/summary.htm
   ("(geo)" in the MOID column = find_orb fitted geocentric elements), and
2. MPC  https://www.minorplanetcenter.net/iau/NEO/neocp.txt, parsed with
   ``../neocpsat/scraper.parse_neocp_line`` (imported read-only); objects with
   'S' in the note field are MPC geocentric suspects, and
3. Bill Gray's NEOCP-artsat identification log, section "Artsats removed from
   NEOCP" of https://www.projectpluto.com/sat_id2.htm
   ("X is/was not a minor planet (date) = 2026-199B = NGRST upper stage").
   Entries dated "??.??" are objects Bill has declared artificial but that
   are (or were until recently) still on the NEOCP; they are selected even
   when they are no longer listed.

For every selected object it fetches the pseudo-MPEC
``https://www.projectpluto.com/neocp2/mpecs/<desig>.htm`` and parses epoch,
q, e, i, node, peri (+ a, Q, P, M, n for bound orbits), H, number of obs,
arc, mean residual, first/last obs, station codes, the geocentric and
heliocentric state vectors, find_orb "GEO score", encounter velocities,
TLE comment lines, the 48-h geocentric ephemeris table, and any extra
header text Bill Gray adds.  80-column astrometry is cached in
``work/cache/neocp/obs/<desig>.txt`` (merged and de-duplicated with the same
astrometric key as neocpsat ``scraper.save_observations``) from the Pluto page
(Bill blanks/withholds it while the object is on the NEOCP) and from the MPC
NEOCP obs API (get-obs-neocp, only while the object is listed).
Each object is cross-checked against ``../neocpsat/catalog.csv``,
``data/`` and ``mpecs/`` (exact-case: the macOS volume is case-insensitive,
so ``ZTF10Gu`` would otherwise match neocpsat's ``ZTF10GU`` = Tiandu-1), and
against ``~/tles/tle_list.txt`` (Bill Gray TLE index: '# ID:' NORAD/intl).

Selection
  sticky  : 'geo' (summary MOID=(geo)), 'S' (neocp.txt note), 'satid' (listed
            object with a sat_id2 log entry; O/0 typo-tolerant match),
            'satid_current' (sat_id2 entry dated ??.??), 'neocpsat' (listed
            object already in the neocpsat catalog).  Sticky objects stay in
            the TSV --keep-days after they were last selected.
  re-evaluated each run (non-geo artificial *candidates*, EARTH_NEAR_HELIO):
            'earthlike_helio'  heliocentric fit 0.9<a<1.1 AU, e<0.1, i<3 deg,
                               Earth MOID<0.05 AU;
            'lowvenc_helio'    heliocentric fit with Earth encounter velocity
                               < 3 km/s AND (min geocentric distance in the
                               48-h MPEC table < 0.05 AU or MOID < 0.05 AU).
            In hourly mode only summary objects with Tisserand(Earth) >= 2.97
            (from the summary a/e/i) and MOID <= 0.05 AU get their page
            fetched for this test; --all-mpecs fetches every page.
Regime: NEOCP_GEO unless identified (neocpsat identification or a sat_id2
COSPAR / named identification); identified objects get their regime from the
identified object's orbit (neocpsat note q/Q/P, "(heliocentric)" orbit flag,
else the current fit).  Status is always ``unknown`` (the merge step takes
status from Horizons / TLE sources); Bill's status text goes into notes.

Idempotent and safe to run hourly: downloads are reused while younger than
``--max-age`` minutes (Bill Gray regenerates summary + pages hourly at about
hh:35 UTC; neocp.txt changes continuously; sat_id2.htm is edited by hand);
pseudo-MPECs are refetched only if older than the summary regeneration.
"Now" (sat_id2 '??.??' recency, covers_now, the MPEC row nearest now) is the
reference day from ``--now``, else CLE_NOW, else the wall clock (scripts/_now.py);
the --keep-days lifecycle is measured from the summary regeneration stamp.
Lifecycle columns: ``listed_now`` (Y/N: in the current summary or neocp.txt)
and ``last_listed`` (regeneration stamp of the last listing seen).  Objects
that were live in the previous parsed.json and are absent from the listings
now go to parsed.json "removed" (with the removal time) for --keep-days; the
cle neocp adapter builds build/neocp.json "removed" from it.

Cache layout (all under work/cache/neocp/):
  summary.htm, neocp.txt                  latest raw downloads
  satid/sat_id2.htm, satid/sat_id2_log.json   Bill Gray artsat log + parse
  snapshots/<stamp>_{summary.htm,neocp.txt,sat_id2.htm}  each distinct version
  mpecs/<desig>.htm, mpecs/history/<desig>/<created>.htm
  pluto_mpecs/<name>.htm                  Bill's per-object pages linked from sat_id2
  mpc_obs/<desig>.txt                     raw MPC get-obs-neocp OBS80
  obs/<desig>.txt                         merged 80-col astrometry (viewer dots)
  parsed.json                             everything parsed (incl. ephemeris xyz) + "removed"
  history.json                            first/last seen per designation
  fetch_log.jsonl                         one line per HTTP request

Never writes into sibling projects: sys.dont_write_bytecode is set before the
neocpsat modules are imported, and neocpsat main/scan/deploy code is never
called.

Usage:
  ~/.venvs/astro313/bin/python -I scripts/neocp_live.py            # hourly mode
  ~/.venvs/astro313/bin/python -I scripts/neocp_live.py --all-mpecs
  ~/.venvs/astro313/bin/python -I scripts/neocp_live.py --offline  # reparse cache
  ... --offline --cache DIR --tsv F --now 2026-10-28T00:00:00Z     # replay a snapshot
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import html
import json
import math
import os
import re
import shutil
import sys
import time

sys.dont_write_bytecode = True  # never drop __pycache__ into ../neocpsat

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CIS = os.path.dirname(ROOT)
NEOCPSAT = os.path.join(CIS, "neocpsat")
CACHE = os.path.join(ROOT, "work", "cache", "neocp")
TSV_OUT = os.path.join(ROOT, "catalog", "sources", "neocp.tsv")
TLES = os.path.expanduser("~/tles")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # -I drops the script dir
import _now  # noqa: E402  (scripts/_now.py: CLE_NOW else wall clock)

sys.path.insert(0, NEOCPSAT)
import requests  # noqa: E402
import scraper as ns_scraper  # noqa: E402  (../neocpsat/scraper.py, read-only)
import pluto as ns_pluto  # noqa: E402      (../neocpsat/pluto.py, read-only)
import config as ns_config  # noqa: E402    (../neocpsat/config.py, read-only)

SUMMARY_URL = ns_config.PLUTO_SUMMARY
NEOCP_URL = ns_config.NEOCP_URL
MPEC_BASE = ns_config.PLUTO_MPEC_BASE
OBS_API = ns_config.NEOCP_OBS_API
SATID_URL = "https://www.projectpluto.com/sat_id2.htm"
PLUTO_ROOT = "https://www.projectpluto.com"
HEADERS = dict(ns_pluto._HEADERS)  # browser UA + Accept (openresty 415 otherwise)

NOW = _now.now("day")   # reference day; main() rebinds it from --now / CLE_NOW
AU_KM = 149597870.7
MU_EARTH = 398600.4418  # km^3/s^2
R_EARTH = 6378.137
VINF_MAX = 3.0          # km/s, non-geo artificial-candidate threshold
NEAR_AU = 0.05
STICKY = {"geo", "S", "satid", "satid_current", "neocpsat"}
SATID_RECENT_D = 60     # sat_id2 entries older than this are not matched
SIG_NOW_MAX = 3600.0    # arcsec; MPEC ephemeris 1-sigma at NOW above this -> covers '?' 
GENERIC_IDENTS = {"ETBO", "unid", "?", ""}
HZ_REGIME = {"JWST": "SE_L2", "Euclid": "SE_L2", "Roman Space Telescope": "SE_L2",
             "SOHO": "SE_L1", "DSCOVR": "SE_L1", "ACE": "SE_L1", "Wind": "SE_L1",
             "Aditya-L1": "SE_L1", "IMAP": "SE_L1", "SWFO-L1": "SE_L1",
             "Carruthers": "SE_L1", "TESS": "EM_OTHER", "Spektr-R": "CISLUNAR_HEO"}
MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
     "nov", "dec"])}

COLUMNS = ["src", "src_ref", "name", "aliases", "norad", "cospar",
           "horizons_id", "obj_type", "regime", "status", "status_date",
           "period_d", "a_km", "e", "incl_deg", "perigee_km", "apogee_km",
           "elem_epoch", "frame_center", "ephem_sources", "covers_now",
           "notes", "listed_now", "last_listed"]   # the last two: neocp.tsv only


# --------------------------------------------------------------------------
# small utilities
# --------------------------------------------------------------------------

def utcnow():
    return dt.datetime.now(dt.timezone.utc)


def iso(t, sec=True):
    if t is None:
        return ""
    if sec:
        return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def parse_iso(s):
    if not s:
        return None
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=dt.timezone.utc)


def jd_to_dt(jd):
    """JD (any scale, treated as UTC) -> aware datetime."""
    return (dt.datetime(2000, 1, 1, 12, tzinfo=dt.timezone.utc)
            + dt.timedelta(days=jd - 2451545.0))


def jdtt_to_utc(jd_tt):
    """TT Julian date -> UTC datetime (astropy if available, else TT-69.184 s)."""
    try:
        from astropy.time import Time
        t = Time(jd_tt, format="jd", scale="tt").utc
        return t.to_datetime(timezone=dt.timezone.utc)
    except Exception:  # pragma: no cover
        return jd_to_dt(jd_tt) - dt.timedelta(seconds=69.184)


def fnum(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def fmt(x, nd):
    if x is None:
        return ""
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return ""
    return f"{x:.{nd}f}"


def sha(b):
    return hashlib.sha256(b).hexdigest()


def write_atomic(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    mode = "wb" if isinstance(data, bytes) else "w"
    with open(tmp, mode) as f:
        f.write(data)
    os.replace(tmp, path)


def exact_listdir(d):
    try:
        return set(os.listdir(d))
    except FileNotFoundError:
        return set()


def case_safe_name(desig, directory, ext):
    """File name for `desig` that cannot collide on a case-insensitive volume.

    Plain '<desig><ext>' unless a *different-case* sibling already exists, in
    which case '<desig>~<hex uppercase-mask><ext>' is used (deterministic).
    """
    plain = f"{desig}{ext}"
    names = exact_listdir(directory)
    if plain in names:
        return plain
    if not any(n.lower() == plain.lower() for n in names):
        return plain
    mask = sum(1 << i for i, c in enumerate(desig) if c.isupper())
    return f"{desig}~{mask:x}{ext}"


def log_fetch(entry):
    os.makedirs(CACHE, exist_ok=True)
    with open(os.path.join(CACHE, "fetch_log.jsonl"), "a") as f:
        f.write(json.dumps(entry, sort_keys=True) + "\n")


def http_get(url, headers=None, data=None, timeout=40, tries=3):
    last = None
    for k in range(tries):
        try:
            r = requests.get(url, headers=headers, data=data, timeout=timeout)
            log_fetch({"t": iso(utcnow()), "url": url, "status": r.status_code,
                       "bytes": len(r.content)})
            return r
        except requests.RequestException as e:  # pragma: no cover
            last = e
            log_fetch({"t": iso(utcnow()), "url": url, "error": str(e)})
            time.sleep(2 + 3 * k)
    raise last


def cached_fetch(url, path, max_age_s, offline, headers=None):
    """Return (bytes, fetched_now: bool). Reuse `path` while fresh."""
    if os.path.exists(path):
        age = time.time() - os.path.getmtime(path)
        if offline or age < max_age_s:
            with open(path, "rb") as f:
                return f.read(), False
    if offline:
        return None, False
    try:
        r = http_get(url, headers=headers)
    except requests.RequestException as e:      # keep the cached copy; nothing downstream is written partially
        print(f"  WARN {url} -> {e}", file=sys.stderr)
        r = None
    if r is None or r.status_code != 200:
        if r is not None:
            print(f"  WARN {url} -> HTTP {r.status_code}", file=sys.stderr)
        if os.path.exists(path):
            with open(path, "rb") as f:
                return f.read(), False
        return None, False
    write_atomic(path, r.content)
    return r.content, True


def snapshot(content, kind, stamp):
    """Keep each distinct version of summary/neocp.txt/sat_id2 under snapshots/."""
    sdir = os.path.join(CACHE, "snapshots")
    os.makedirs(sdir, exist_ok=True)
    h = sha(content)
    for n in sorted(os.listdir(sdir)):
        if n.endswith("_" + kind):
            with open(os.path.join(sdir, n), "rb") as f:
                if sha(f.read()) == h:
                    return n
    name = f"{stamp}_{kind}"
    write_atomic(os.path.join(sdir, name), content)
    return name


def mtime_utc(path):
    return dt.datetime.fromtimestamp(os.path.getmtime(path), dt.timezone.utc)


# --------------------------------------------------------------------------
# 80-column astrometry helpers
# --------------------------------------------------------------------------

def obs80_time(line):
    """UTC datetime of an MPC 80-col line (cols 16-32 'YYYY MM DD.dddddd')."""
    try:
        y = int(line[15:19])
        m = int(line[20:22])
        d = float(line[23:32])
    except ValueError:
        return None
    base = dt.datetime(y, m, 1, tzinfo=dt.timezone.utc)
    return base + dt.timedelta(days=d - 1)


def astrometric_key(line):
    """Same dedup key as neocpsat scraper.save_observations._astrometric_key."""
    if len(line) < 80:
        return line.rstrip()
    return (line[15:32] + "|" + line[32:44].strip() + "|"
            + line[44:56].strip() + "|" + line[77:80])


def merge_obs(path, new_lines):
    """Merge 80-col lines into `path` (dedup by astrometric key). -> n_added."""
    existing, seen = [], set()
    if os.path.exists(path):
        with open(path) as f:
            for raw in f:
                ln = raw.rstrip("\n")
                if ln.strip():
                    existing.append(ln)
                    seen.add(astrometric_key(ln))
    added = 0
    for ln in new_lines:
        k = astrometric_key(ln)
        if k in seen:
            continue
        existing.append(ln)
        seen.add(k)
        added += 1
    existing.sort(key=lambda l: (obs80_time(l) or NOW, l[77:80]))
    if existing:
        write_atomic(path, "\n".join(existing) + "\n")
    return added


def obs_stats(lines):
    ts = [t for t in (obs80_time(l) for l in lines) if t]
    stns = sorted({l[77:80] for l in lines if len(l) >= 80})
    if not ts:
        return {"n": len(lines), "first": None, "last": None, "stations": stns}
    return {"n": len(lines), "first": iso(min(ts)), "last": iso(max(ts)),
            "stations": stns,
            "arc_h": round((max(ts) - min(ts)).total_seconds() / 3600, 3)}


# --------------------------------------------------------------------------
# parsers: summary.htm, neocp.txt, pseudo-MPEC
# --------------------------------------------------------------------------

SUMMARY_ROW = re.compile(
    r'^(?P<desig>\S+)\s+'
    r'(?:(?P<a>[\d.]+) \+/-\s?(?P<sa>\S*)\s+)?'
    r'(?P<e>[\d.]+) \+/-\s?(?P<se>\S*)\s+'
    r'(?P<i>[\d.]+) \+/-\s?(?P<si>\S*)\s+'
    r'(?P<moid>\(geo\)|[\d.]+)\s+'
    r'(?P<ra>\d\d \d\d \d\d)\s+(?P<dec>[+-]\d\d \d\d \d\d)\s+'
    r'(?P<elong>[\d.]+)\s+(?P<mag>\S+)\s+(?P<sig1>\S+)\s+(?P<sig2>\S+)\s*$')


def sig_to_arcsec(s):
    """Pluto summary sigma: plain = arcsec, trailing ' = arcmin, d = degrees."""
    if not s:
        return None
    m = re.match(r"^([\d.]+)([d']?)$", s)
    if not m:
        return None
    v = float(m.group(1))
    return v * {"": 1.0, "'": 60.0, "d": 3600.0}[m.group(2)]


def tisserand_earth(a, e, i_deg):
    """Tisserand parameter w.r.t. Earth (a in AU, heliocentric ecliptic i)."""
    if a is None or e is None or i_deg is None or a <= 0 or e >= 1:
        return None
    return 1.0 / a + 2.0 * math.sqrt(a * (1 - e * e)) * math.cos(math.radians(i_deg))


def parse_summary(text):
    """Parse the 'Sorted by designation' table of summary.htm."""
    out = {"regenerated": None, "rows": {}, "unparsed": []}
    m = re.search(r"Last regenerated (\d{4}) (\w{3})\.? +(\d+) +(\d+):(\d+):(\d+) UTC",
                  text)   # 'Oct 21  1:35:01': hour is space-padded
    if m:
        y, mo, d, hh, mm, ss = m.groups()
        out["regenerated"] = iso(dt.datetime(
            int(y), MONTHS[mo.lower()], int(d), int(hh), int(mm), int(ss),
            tzinfo=dt.timezone.utc))
    sec = re.search(r'name="desig">(.*?)(?:<h3>|</pre>)', text, re.DOTALL)
    block = sec.group(1) if sec else ""
    for raw in block.splitlines():
        if 'href="mpecs/' not in raw:
            continue
        hm = re.search(r'href="mpecs/([^"]+)\.htm"', raw)
        line = html.unescape(re.sub(r"<[^>]+>", "", raw)).rstrip()
        rm = SUMMARY_ROW.match(line)
        if not rm:
            out["unparsed"].append(line)
            continue
        g = rm.groupdict()
        desig = hm.group(1) if hm else g["desig"]
        row = {
            "desig": desig,
            "a_au": fnum(g["a"]), "e": fnum(g["e"]), "incl": fnum(g["i"]),
            "moid_au": None if g["moid"] == "(geo)" else fnum(g["moid"]),
            "geo": g["moid"] == "(geo)",
            "ra": g["ra"], "dec": g["dec"], "elong": fnum(g["elong"]),
            "mag": fnum(g["mag"]), "mag_raw": g["mag"],
            "sig1_arcsec": sig_to_arcsec(g["sig1"]),
            "sig2_arcsec": sig_to_arcsec(g["sig2"]),
            "sig1_raw": g["sig1"], "sig2_raw": g["sig2"],
            "line": line,
        }
        row["tisserand_earth"] = (None if row["geo"] else
                                  tisserand_earth(row["a_au"], row["e"], row["incl"]))
        out["rows"][desig] = row
    return out


NEOCP_TAIL = re.compile(
    r"^(?P<desig>\S+)\s+(?P<score>\d+)\s+(?P<disc>\d{4} \d\d \d\d\.\d)\s+"
    r"(?P<ra>[\d.]+)\s+(?P<dec>[+-][\d.]+)\s+(?P<v>[\d.]+)\s+"
    r"(?P<upd>(?:Added|Updated) \S+ [\d.]+ UT)(?P<note>.*?)\s+"
    r"(?P<nobs>\d+)\s+(?P<arc>[\d.]+)\s+(?P<h>[\d.]+)\s+(?P<ns>[\d.]+)\s*$")


def parse_neocp_txt(text):
    """MPC neocp.txt via neocpsat scraper.parse_neocp_line (fixed width).

    Fallback regex only for lines the scraper rejects (its fixed slices fail
    when the arc is >= 100 d, e.g. 'P22oLCM ... 52 101.76').  The note/flag
    field is then taken from the same fixed columns [70:80] the scraper uses.
    """
    rows, fallback = {}, []
    for line in text.splitlines():
        if not line.strip():
            continue
        o = ns_scraper.parse_neocp_line(line)
        if o is None:
            m = NEOCP_TAIL.match(line)
            if not m:
                fallback.append({"line": line, "parsed": False})
                continue
            g = m.groupdict()
            o = {"desig": g["desig"], "score": int(g["score"]),
                 "discovery": g["disc"], "ra": float(g["ra"]),
                 "dec": float(g["dec"]), "v_mag": float(g["v"]),
                 "updated": g["upd"], "note": line[70:80].strip(),
                 "nobs": int(g["nobs"]), "arc": float(g["arc"]),
                 "h_mag": float(g["h"]), "not_seen": float(g["ns"])}
            fallback.append({"line": line, "parsed": True})
        o["s_flag"] = "S" in (o.get("note") or "")
        rows[o["desig"]] = o
    return rows, fallback


def strip_tags(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s))


_STD_HEADER = re.compile(r"Pseudo-MPEC\" for|Created \d{4}|explanation of pseudo-MPECs"
                         r"|Orbit Simulator View", re.I)
_ID_PAT = re.compile(
    r"(artificial|satellite|spacecraft|rocket|booster|upper stage|\bstage\b|"
    r"identif|NORAD|\b(?:19|20)\d\d-\d{3}[A-Z]{0,3}\b|\bR/B\b|probe|"
    r"\bJWST\b|Spektr|Chang.?e|Tiandu|Falcon|Starship|Centaur|TESS|Gaia)",
    re.I)


def parse_mpec(page, desig):
    """Parse a Project Pluto pseudo-MPEC (find_orb HTML) into a dict."""
    r = {"desig": desig, "found": True}
    tm = re.search(r'"Pseudo-MPEC" for\s*(.*?)\s*</', page, re.DOTALL | re.I)
    r["title"] = " ".join(strip_tags(tm.group(1)).split()) if tm else ""
    nm = re.search(r"NORAD\s+(\d+)", r["title"])
    r["norad"] = nm.group(1) if nm else ""
    m = re.search(r"Created (\d{4}) (\w{3}) +(\d+) +(\d+):(\d+):(\d+) UT", page)
    if m:
        y, mo, d, hh, mm, ss = m.groups()
        r["created"] = iso(dt.datetime(int(y), MONTHS[mo.lower()], int(d),
                                       int(hh), int(mm), int(ss),
                                       tzinfo=dt.timezone.utc))
    # ---- header paragraphs beyond the boilerplate = Bill Gray's own text
    head = page.split('<a name="ast">')[0]
    head = head.split("<BODY>", 1)[-1] if "<BODY>" in head else head
    extra = []
    for p in re.findall(r"<p>(.*?)</p>", head, re.DOTALL | re.I):
        t = " ".join(strip_tags(p).split())
        if t and not _STD_HEADER.search(t):
            extra.append(t)
    r["header_text"] = extra

    # ---- astrometry
    am = re.search(r'<a name="ast"></a>(.*?)<a name="(?:stations|elements)"></a>',
                   page, re.DOTALL)
    ablock = am.group(1) if am else ""
    r["astrometry_redacted"] = bool(ns_pluto._REDACTED_RE.search(ablock))
    txt = re.sub(r"<a [^>]*>|</a>", "", ablock)
    txt = re.sub(r"<b>.*?</b>", "", txt, flags=re.DOTALL)
    obs = []
    if not r["astrometry_redacted"]:
        for raw in txt.splitlines():
            ln = html.unescape(re.sub(r"<[^>]+>", "", raw)).rstrip()
            if ns_pluto.looks_like_obs80(ln):
                obs.append(ln)
    r["obs_lines"] = obs
    r["astrometry_withheld"] = not obs
    # ---- stations
    sm = re.search(r'<a name="stations"></a>(.*?)<a name="elements"></a>', page,
                   re.DOTALL)
    stations = {}
    if sm:
        for code, nm_ in re.findall(r"\((\w{3})\)\s+([^(\n]+?)\s+\(",
                                    strip_tags(sm.group(1))):
            stations[code] = " ".join(nm_.split())
    r["station_names"] = stations

    # ---- elements block
    em = re.search(r'<a name="elements"></a>(.*?)</pre>', page, re.DOTALL)
    eb = strip_tags(em.group(1)) if em else ""
    r["elements_text"] = eb.strip()
    el = {}
    for ln in eb.splitlines():
        s = ln.strip()
        mm = re.match(r"^(Peri\w+)\s+(\d{4}) (\w{3})\s+([\d.]+)"
                      r"(?: \+/- (\S+))? TT.*?\(JD ([\d.]+)\)", s)
        if mm:
            el["peri_word"] = mm.group(1)
            el["tp_jd_tt"] = float(mm.group(6))
            el["tp_sigma_d"] = fnum(mm.group(5))
        mm = re.match(r"^Epoch (\d{4}) (\w{3})\s+([\d.]+) TT(?: = JDT ([\d.]+))?", s)
        if mm:
            if mm.group(4):
                el["epoch_jd_tt"] = float(mm.group(4))
            else:
                y_, mo_, d_ = mm.group(1), mm.group(2), float(mm.group(3))
                t0 = dt.datetime(int(y_), MONTHS[mo_.lower()], 1, tzinfo=dt.timezone.utc)
                el["epoch_jd_tt"] = 2451545.0 + (
                    t0 + dt.timedelta(days=d_ - 1) - jd_to_dt(2451545.0)
                ).total_seconds() / 86400.0
            last_tok = s.split()[-1]
            el["fit_tag"] = last_tok if re.match(r"^[A-Za-z_]+$", last_tok) else ""
            am_ = re.search(r"AMR\s+([-\d.]+)", s)
            if am_:
                el["amr"] = float(am_.group(1))
        mm = re.search(r"Earth MOID:\s*([\d.]+)", s)
        if mm:
            el["earth_moid_au"] = float(mm.group(1))
        mm = re.search(r"\((J2000(?:\.0)?(?: (?:equator|ecliptic))?)\)", s)
        if mm:
            el["frame"] = mm.group(1)
        for key, pat in (("M", r"^M\s*([-\d.]+)"), ("n", r"^n\s*([\d.]+)"),
                         ("a", r"^a\s*([\d.]+)"), ("e", r"^e\s*([\d.]+)"),
                         ("q", r"^q\s*([\d.]+)")):
            mm = re.match(pat + r"(?: \+/- (\S+))?", s)
            if mm:
                el[key] = float(mm.group(1))
                el[key + "_sigma"] = fnum(mm.group(2))
        mm = re.search(r"\bQ\s+([\d.]+)(?: \+/- (\S+))?", s)
        if mm:
            el["Q"] = float(mm.group(1))
            el["Q_sigma"] = fnum(mm.group(2))
        for key, pat in (("peri", r"Peri\.\s+([\d.]+)"),
                         ("node", r"Node\s+([\d.]+)"),
                         ("incl", r"Incl\.\s+([\d.]+)")):
            mm = re.search(pat + r"(?: \+/- (\S+))?", s)
            if mm:
                el[key] = float(mm.group(1))
                el[key + "_sigma"] = fnum(mm.group(2))
        if s.startswith("P"):
            for pat, conv in ((r"^P\s*([\d.]+)m/([\d.]+)d", lambda g: float(g[1])),
                              (r"^P\s*([\d.]+)/([\d.]+)d", lambda g: float(g[1])),
                              (r"^P\s*([\d.]+)d", lambda g: float(g[0])),
                              (r"^P\s*([\d.]+)y", lambda g: float(g[0]) * 365.25),
                              (r"^P\s*([\d.]+)h", lambda g: float(g[0]) / 24)):
                mm = re.match(pat, s)
                if mm:
                    el["P_d"] = conv(mm.groups())
                    break
        mm = re.search(r"\bH\s+([\d.]+)\s+G\s+([\d.]+)", s)
        if mm:
            el["H"] = float(mm.group(1))
            el["G"] = float(mm.group(2))
        mm = re.search(r"\bU\s+([\d.]+)\s*$", s)
        if mm:
            el["U"] = float(mm.group(1))
        mm = re.search(r"(?:From (\d+) observations|(\d+) of (\d+) observations)"
                       r"\s+(.*?)(?:\s+\(([^)]*)\))?;\s*mean residual"
                       r"\s+(\d*)\"\.?(\d*)", s)
        if mm:
            if mm.group(1):
                el["n_used"] = el["n_obs"] = int(mm.group(1))
            else:
                el["n_used"] = int(mm.group(2))
                el["n_obs"] = int(mm.group(3))
            el["obs_dates_text"] = mm.group(4).strip()
            el["arc_text"] = mm.group(5) or ""
            el["mean_resid_arcsec"] = float(f"{mm.group(6) or 0}.{mm.group(7) or 0}")
    if "P_d" not in el and el.get("n"):
        el["P_d"] = 360.0 / el["n"]
    r["elements"] = el

    # ---- HTML comments (state vectors, scores, TLE, heliocentric $-elements)
    comments = [c.strip() for c in re.findall(r"<!--(.*?)-->", page, re.DOTALL)]
    cm = {}
    tle = []
    for k, c in enumerate(comments):
        if c.startswith("State vector (heliocentric equatorial J2000)") and k + 2 < len(comments):
            cm["sv_helio_au"] = [float(x) for x in comments[k + 1].split()[:3]]
            cm["sv_helio_mau_d"] = [float(x) for x in comments[k + 2].split()[:3]]
        if c.startswith("State vector relative to central body") and k + 2 < len(comments):
            cm["sv_central_au"] = [float(x) for x in comments[k + 1].split()[:3]]
            cm["sv_central_mau_d"] = [float(x) for x in comments[k + 2].split()[:3]]
        if re.match(r"^1 \d{5}U ", c) or re.match(r"^2 \d{5} ", c):
            tle.append(c)
        for key, pat in (("geo_score", r"GEO score\s+(-?\d+)"),
                         ("earth_enc_kms", r"Earth encounter velocity\s+([-\d.]+) km/s"),
                         ("barbee_kms", r"Barbee-style encounter velocity:\s*([-\d.]+) km/s"),
                         ("tisserand_earth", r"Tisserand relative to Earth:\s*([-\d.]+)"),
                         ("diam_m", r"Diameter\s+([\d.]+) meters"),
                         ("score", r"^Score:\s*([-\d.]+)")):
            mm = re.search(pat, c)
            if mm:
                cm[key] = float(mm.group(1))
        for key, pat in (("elements_written", r"Elements written:\s*(.*)"),
                         ("full_range", r"Full range of obs:\s*(.*)"),
                         ("perturbers", r"Perturbers:\s*(.*)"),
                         ("find_orb_ver", r"Find_Orb ver:\s*(.*)")):
            mm = re.search(pat, c)
            if mm:
                cm[key] = mm.group(1).strip()
        for key in ("a", "ecc", "Incl", "Node", "Peri", "q", "EpJD", "T", "H"):
            mm = re.search(r"\$" + key + r"=([-\d.]+)", c)
            if mm:
                cm.setdefault("helio_el", {})[key] = float(mm.group(1))
        if "MOIDs:" in c:
            for pl, v in re.findall(r"(\w\w)\s+([\d.]+)", c.split("MOIDs:", 1)[1]):
                cm.setdefault("moids", {})[pl] = float(v)
    if tle:
        cm["tle"] = tle
    idc = [c for c in comments if _ID_PAT.search(c)
           and not re.search(r"State vector|Perturbers|Find_Orb ver|\$Name", c)]
    r["comments"] = cm
    r["id_text"] = [t for t in extra if _ID_PAT.search(t)] + idc
    std = re.compile(r"^(Orbital elements|Peri\w+ \d{4}|Epoch |M |n |a|e |q|"
                     r"P|H |Node|Incl|From |\d+ of \d+ obs|Peri\.)")
    r["elements_extra"] = [ln.strip() for ln in eb.splitlines()
                           if ln.strip() and not std.match(ln.strip())]

    # ---- ephemeris table (geocentric)
    eph = []
    xm = re.search(r'<a name="eph"></a>(.*?)</pre>', page, re.DOTALL)
    if xm:
        eh = strip_tags(xm.group(1))
        r["eph_header"] = eh.strip().splitlines()[0] if eh.strip() else ""
        for ln in eh.splitlines():
            mm = re.match(r"^(\d{4}) (\d\d) (\d\d)(?: (\d\d))?(?::(\d\d))?\s+"
                          r"(\d\d) (\d\d) (\d\d\.\d+)\s+([+-])(\d\d) (\d\d) (\d\d\.\d+)\s+"
                          r"([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+(\S+)\s+(\S+)\s+(\S+)", ln)
            if not mm:
                continue
            g = mm.groups()
            t = dt.datetime(int(g[0]), int(g[1]), int(g[2]), int(g[3] or 0),
                            int(g[4] or 0), tzinfo=dt.timezone.utc)
            ra = (int(g[5]) + int(g[6]) / 60 + float(g[7]) / 3600) * 15
            de = (int(g[9]) + int(g[10]) / 60 + float(g[11]) / 3600)
            de = -de if g[8] == "-" else de
            # find_orb prints delta in AU with a decimal point ('.05523'), but
            # as an integer number of km for close objects ('182991').
            if "." in g[12]:
                delta = float(g[12])
                rk = delta * AU_KM
            else:
                rk = float(g[12])
                delta = rk / AU_KM
            cr, sr = math.cos(math.radians(ra)), math.sin(math.radians(ra))
            cd, sd = math.cos(math.radians(de)), math.sin(math.radians(de))
            eph.append({"t": iso(t), "ra_deg": round(ra, 6), "dec_deg": round(de, 6),
                        "delta_au": delta, "delta_km": round(rk, 1),
                        "delta_raw": g[12], "r_au": float(g[13]),
                        "elong": float(g[14]), "mag": fnum(g[15]),
                        "sig_arcsec": sig_to_arcsec(g[16]) if g[16] != "?" else None,
                        "sig_pa": fnum(g[17]),
                        "xyz_km": [round(rk * cd * cr, 1), round(rk * cd * sr, 1),
                                   round(rk * sd, 1)]})
    r["ephemeris"] = eph
    return r


def page_center(el):
    pw = el.get("peri_word", "")
    if pw == "Perigee" or el.get("frame") == "J2000 equator":
        return "earth"
    if pw == "Perihelion":
        return "sun"
    if pw in ("Perilune", "Periselene"):
        return "moon"
    return ""


def page_span(p):
    """(first, last) obs dates of a parsed pseudo-MPEC (ISO dates or '')."""
    el, cm = p.get("elements", {}), p.get("comments", {})
    st = obs_stats(p.get("obs_lines") or [])
    if st["first"]:
        return st["first"][:10], st["last"][:10]
    for txt in (cm.get("full_range"), el.get("obs_dates_text")):
        if txt:
            a, b = parse_range_text(txt)
            if a:
                return a, b
    return "", ""


# --------------------------------------------------------------------------
# Bill Gray sat_id2.htm NEOCP artsat log
# --------------------------------------------------------------------------

_SATID_LINE = re.compile(
    r"^(?P<desig>[A-Za-z0-9_]{4,9})\s+(?P<verb>[^()]*?)\s*"
    r"\((?P<date>[^()]*?)\)\s*(?P<rest>.*)$")
_SATID_VERB = re.compile(r"minor planet|confirmed|artificial|^=\s*\S+", re.I)
_TEMP_DESIG = re.compile(r"^[A-Za-z0-9]{5,8}$")
_COSPAR = re.compile(r"\b((?:19|20)\d\d-\d{3}[A-Z]{1,3})\b")


def parse_satid_date(s, year_hint):
    """'2026 Oct. 15.86 UT' / 'Dec. 7.71 UT' / '2026 Oct. ??.?? UT' -> (iso, current)."""
    m = re.match(r"(?:(\d{4}) )?([A-Za-z]+)\.? +([\d?]+)(?:\.([\d?]+))?", s.strip())
    if not m or m.group(2)[:3].lower() not in MONTHS:
        return "", False, year_hint
    y = int(m.group(1)) if m.group(1) else year_hint
    mon = MONTHS[m.group(2)[:3].lower()]
    day_s = m.group(3) + "." + (m.group(4) or "0")
    if "?" in day_s or y is None:
        # Bill writes '??.??' both for objects still on the NEOCP *now* and for
        # old entries whose removal date he never filled in (2020 Dec. ??.??,
        # 2023 Mar. 1?.??).  Only a '??' date in the current or previous
        # month (<= SATID_RECENT_D before NOW) counts as "current".
        current = False
        if "?" in day_s and y:
            m0 = dt.datetime(y, mon, 1, tzinfo=dt.timezone.utc)
            current = m0 <= NOW and (NOW - m0).days <= SATID_RECENT_D
        return f"{y}-{mon:02d}" if y else "", current, y
    t = dt.datetime(y, mon, 1, tzinfo=dt.timezone.utc) + dt.timedelta(days=float(day_s) - 1)
    return iso(t), False, y


def parse_satid(text):
    """Parse the 'Artsats removed from NEOCP' log of sat_id2.htm into entries."""
    a = text.find('name="removed"')
    b = text.find("name='etbo'", a)
    block = text[a:b if b > 0 else len(text)]
    entries, cur, year = [], None, None
    for k, raw in enumerate(block.splitlines()):
        if not raw.strip():
            cur = None
            continue
        hrefs = re.findall(r"href=['\"]([^'\"]+)['\"]", raw)
        links = [(h, " ".join(strip_tags(t).split())) for h, t in re.findall(
            r"<a\s+href=['\"]([^'\"]+)['\"][^>]*>(.*?)</a>", raw, re.I | re.S)]
        txt = " ".join(strip_tags(raw).split())
        if raw[:1].isspace() and cur is not None and not _SATID_LINE.match(txt):
            cur["comments"].append(txt)
            continue
        m = _SATID_LINE.match(txt)
        if not m or not _SATID_VERB.search(m.group("verb")):
            cur = None
            continue
        g = m.groupdict()
        date_iso, current, year = parse_satid_date(g["date"], year)
        rest = g["rest"].strip()
        ident = re.sub(r"^=\s*", "", rest)
        e = {"desig": g["desig"], "verb": g["verb"], "date_raw": g["date"],
             "date_utc": date_iso, "current": current, "text": ident,
             "line": txt, "hrefs": hrefs, "links": links, "comments": []}
        cos = []
        for c in _COSPAR.findall(ident):
            if c not in cos:
                cos.append(c)
        e["cospar"] = cos
        names = []
        for c in cos:
            nm = re.search(re.escape(c) + r"\s*=\s*(?:=\s*)?([^,;(=]+)", ident)
            if nm and nm.group(1).strip():
                names.append(nm.group(1).strip())
        names += [q for q in re.findall(r"'([^']+)'", ident) if q not in names]
        e["names"] = names
        e["etbo"] = "ETBO" in ident
        e["unid"] = ident.lower().startswith("unid")
        linked = []
        va = re.match(r"^=\s*(\S+)", g["verb"])
        if va:
            linked.append(va.group(1))
        for tok in re.split(r"\s*=\s*", ident):
            tok = tok.strip().strip(",;")
            tok = tok.split()[0] if tok else ""
            if (_TEMP_DESIG.match(tok) and any(ch.isdigit() for ch in tok)
                    and tok != g["desig"] and not _COSPAR.match(tok)
                    and tok not in linked):
                linked.append(tok)
        e["linked"] = linked
        orb = {}
        num = r"(\d+(?:\.\d+)?|\.\d+)"   # strict: '342.3...' -> 342.3
        for key, pat in (("i", r"\bi\s*[=~]\s*" + num),
                         ("node", r"(?:Ω|\bO)\s*[=~]\s*" + num),
                         ("amr", r"AMR\s*=\s*" + num)):
            mm = re.search(pat, ident)
            if mm:
                orb[key] = float(mm.group(1))
        mm = re.search(r"\bP\s*[=~]\s*" + num + r"\s*(d|m|h)", ident)
        if mm:
            orb["P_d"] = float(mm.group(1)) * {"d": 1, "h": 1 / 24, "m": 1 / 1440}[mm.group(2)]
        e["orbit_hint"] = orb
        if e["cospar"] and e["names"]:
            e["ident"] = f"{e['cospar'][0]} = {e['names'][0]}"
        elif e["names"]:
            e["ident"] = e["names"][0]
        elif e["etbo"]:
            e["ident"] = "ETBO"
        elif e["unid"]:
            e["ident"] = "unid"
        else:
            e["ident"] = ident[:80]
        e["identified"] = bool(e["cospar"] or (e["names"] and not e["unid"]))
        e["line_no"] = k
        entries.append(e)
        cur = e
    return entries


class SatIdLog:
    def __init__(self, entries):
        self.entries = entries
        self.by = {}
        self.fuzzy = {}
        for e in entries:
            self.by.setdefault(e["desig"], []).append(e)
            self.fuzzy.setdefault(e["desig"].replace("O", "0"), []).append(e)

    @staticmethod
    def _recent(e, days):
        if e["current"] or days is None:
            return True
        t = parse_iso(e["date_utc"]) if len(e["date_utc"]) == 20 else None
        return bool(t and (NOW - t).total_seconds() <= days * 86400)

    def lookup(self, desig, days=SATID_RECENT_D):
        """-> (entries, how): exact first, then O/0-typo tolerant; only entries
        dated ??.?? or within `days` of NOW (temp desigs are reused over years)."""
        ex = [e for e in self.by.get(desig, []) if self._recent(e, days)]
        if ex:
            return ex, "exact"
        f = [e for e in self.fuzzy.get(desig.replace("O", "0"), [])
             if e["desig"] != desig and self._recent(e, days)]
        return (f, "fuzzy O/0") if f else ([], "")

    def current(self):
        return [e for e in self.entries if e["current"]]

    def pages_for_name(self, name):
        """pluto/mpecs pages Bill links *as* a named identification, newest
        entry first: only links whose anchor text contains `name` and that
        have no '#fragment' (so 'not an <a ..ztf00v9.htm#etbo>ETBO</a>' or
        'Not <a ..>'Multijunk'</a> but in a similar orbit' style
        cross-references to *other* pages are not taken)."""
        out = []
        for e in self.entries:
            for h, t in e.get("links", []):
                if "pluto/mpecs/" in h and "#" not in h and name in t and h not in out:
                    if re.search(r"\bnot\s*$", e["line"].split(t)[0], re.I):
                        continue    # '... Not <a>'Multijunk'</a> but ...'
                    out.append(h)
        return out


# --------------------------------------------------------------------------
# neocpsat cross-check (exact-case) and Bill Gray TLE index
# --------------------------------------------------------------------------

class NeocpsatIndex:
    """Read-only, exact-case index of ../neocpsat catalog/data/mpecs."""

    def __init__(self, root=NEOCPSAT):
        self.root = root
        self.rows = []
        with open(os.path.join(root, "catalog.csv"), newline="") as f:
            for row in csv.DictReader(f):
                self.rows.append(row)
        self.data = exact_listdir(os.path.join(root, "data"))
        self.mpecs = exact_listdir(os.path.join(root, "mpecs"))
        self.unconf = exact_listdir(os.path.join(root, "unconfirmed_obs"))

    def lookup(self, desig):
        tok = re.compile(r"(?<![A-Za-z0-9])" + re.escape(desig) + r"(?![A-Za-z0-9])")
        direct = [r for r in self.rows if r.get("Temp Desig") == desig]
        linked = [r for r in self.rows if r not in direct
                  and tok.search(r.get("Note") or "")]
        out = {"direct": direct, "linked_in_notes": linked,
               "data_file": f"data/{desig}.txt" if f"{desig}.txt" in self.data else "",
               "mpec_file": f"mpecs/{desig}.html" if f"{desig}.html" in self.mpecs else "",
               "unconfirmed": sorted(n for n in self.unconf
                                     if n.split(".")[0].split("_")[0] == desig)}
        idents = []
        for r in direct + linked:
            ident = (r.get("Indentification") or "").strip()
            if ident and ident != "?" and ident not in idents:
                idents.append(ident)
        out["idents"] = idents
        aliases = []
        for r in direct + linked:
            note = r.get("Note") or ""
            for chain in re.findall(r"((?:[A-Za-z0-9']+ = )+[A-Za-z0-9']+)", note):
                members = [a.strip("'") for a in chain.split(" = ")]
                if tok.search(chain) or any(i in members for i in idents):
                    for a in members:
                        if a and a != desig and a not in aliases:
                            aliases.append(a)
        out["aliases"] = aliases
        out["combined"] = self.combined_for(idents)
        cos = []
        for r in direct + linked:
            for c in _COSPAR.findall(r.get("Note") or ""):
                if c not in cos:
                    cos.append(c)
        out["cospar"] = cos
        return out

    def combined_for(self, idents):
        out = []
        for ident in idents:
            out += sorted(n for n in self.mpecs if n.startswith(ident + "_combined"))
        return out

    def case_insensitive(self, desig):
        """catalog rows whose Temp Desig equals `desig` only case-insensitively
        (MPC temp desigs are case-sensitive: ZTF10Gu != ZTF10GU)."""
        return [r for r in self.rows if (r.get("Temp Desig") or "") != desig
                and (r.get("Temp Desig") or "").lower() == desig.lower()]

    def rows_for_ident(self, name):
        return [r for r in self.rows if (r.get("Indentification") or "").strip() == name]

    def rows_for_cospar(self, cospar):
        return [r for r in self.rows if cospar in (r.get("Note") or "")]

    def file_obs_span(self, rel):
        """Obs span of a neocpsat mpec/data file: 80-col lines, else 'Full range'."""
        p = os.path.join(self.root, rel)
        try:
            with open(p, errors="replace") as f:
                txt = f.read()
        except OSError:
            return None, None
        lines = []
        for raw in re.sub(r"<[^>]+>", "", txt).splitlines():
            ln = html.unescape(raw.rstrip())
            if ns_pluto.looks_like_obs80(ln):
                lines.append(ln)
        st = obs_stats(lines)
        if st["first"]:
            return st["first"][:10], st["last"][:10]
        m = re.search(r"Full range of obs:\s*(.*)", txt)
        if m:
            return parse_range_text(m.group(1))
        return None, None


def parse_range_text(s):
    """'2026 Oct. 6-9 (..)' / '2026 Sept. 28-Oct. 2' / '2025 Dec. 29-2026 Jan. 3'."""
    s = s.split("(")[0].strip()
    m = re.match(r"(\d{4}) ([A-Za-z]+)\.? (\d+)(?:-(?:(\d{4}) )?(?:([A-Za-z]+)\.? )?(\d+))?", s)
    if not m or m.group(2)[:3].lower() not in MONTHS:
        return None, None
    y1, m1, d1, y2, m2, d2 = m.groups()
    mo1 = MONTHS[m1[:3].lower()]
    a = dt.date(int(y1), mo1, int(d1))
    if d2:
        b = dt.date(int(y2 or y1), MONTHS[(m2 or m1)[:3].lower()], int(d2))
    else:
        b = a
    return a.isoformat(), b.isoformat()


def span_covers(src, day):
    """True if an ephem_sources entry 'kind:ref[a..b]' has a <= day <= b."""
    m = re.search(r"\[([\d-]*)\.\.([\d-]*)\]$", src)
    return bool(m and m.group(1) and m.group(2) and m.group(1) <= day <= m.group(2))


def _range_date(tok):
    """tle_list '# Range:' token -> 'YYYY-MM-DD' ('2026-04-10.9', '20221211.5')."""
    m = re.match(r"^(\d{4})-?(\d\d)-?(\d\d)", tok)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""


class TleIndex:
    """Read-only index of ~/tles/tle_list.txt (# Range / # ID / # Include)."""

    def __init__(self, root=TLES):
        self.blocks = []
        path = os.path.join(root, "tle_list.txt")
        try:
            with open(path, errors="replace") as f:
                lines = f.read().splitlines()
        except OSError:
            return
        rng, ident = None, None
        for ln in lines:
            if ln.startswith("# Range:"):
                t = ln.split(":", 1)[1].split()
                rng = (_range_date(t[0]) if t else "", _range_date(t[1]) if len(t) > 1 else "")
            elif ln.startswith("# ID:"):
                t = ln.split(":", 1)[1].split()
                ident = (t[0] if t else "", t[1] if len(t) > 1 else "")
            elif ln.startswith("# Include"):
                fn = ln.split(None, 2)[2].strip() if len(ln.split()) > 2 else ""
                self.blocks.append({"file": fn, "range": rng or ("", ""),
                                    "norad": (ident or ("", ""))[0],
                                    "intl": (ident or ("", ""))[1]})
                rng, ident = None, None

    def for_cospar(self, cospar):
        m = re.match(r"^(?:19|20)(\d\d)-(\d{3})([A-Z]+)$", cospar)
        if not m:
            return []
        intl = f"{m.group(1)}{m.group(2)}{m.group(3)}"
        return [b for b in self.blocks if b["intl"] == intl]

    def for_stem(self, stem):
        stem = stem.lower()
        return [b for b in self.blocks
                if re.match(re.escape(stem) + r"(?:_\d\d)?\.tle$", b["file"].lower())]


# --------------------------------------------------------------------------
# classification
# --------------------------------------------------------------------------

def earth_like(a, e, i, moid):
    return (a is not None and e is not None and i is not None and moid is not None
            and 0.9 < a < 1.1 and e < 0.1 and i < 3.0 and moid < NEAR_AU)


def page_vinf(p):
    c = p.get("comments", {})
    if c.get("earth_enc_kms") is not None:
        return abs(c["earth_enc_kms"])
    if c.get("barbee_kms") is not None:
        return abs(c["barbee_kms"])
    return None


def page_min_delta_au(p):
    eph = p.get("ephemeris") or []
    return min(x["delta_au"] for x in eph) if eph else None


def candidate_reasons(p, srow):
    """Re-evaluated (non-sticky) artificial-candidate reasons from a page/summary."""
    rs = []
    el = p.get("elements", {}) if p and p.get("found") else {}
    if srow and not srow["geo"] and earth_like(srow["a_au"], srow["e"], srow["incl"],
                                               srow["moid_au"]):
        rs.append("earthlike_helio")
    elif not srow and page_center(el) == "sun" and earth_like(
            el.get("a"), el.get("e"), el.get("incl"), el.get("earth_moid_au")):
        rs.append("earthlike_helio")
    if el and page_center(el) == "sun":
        v = page_vinf(p)
        dmin = page_min_delta_au(p)
        moid = el.get("earth_moid_au")
        near = (dmin is not None and dmin < NEAR_AU) or (moid is not None and moid < NEAR_AU)
        if v is not None and v < VINF_MAX and near:
            rs.append("lowvenc_helio")
    return rs


def derive_geocentric(el):
    """Geocentric q/e -> a, apogee, period, v_inf (km, d, km/s)."""
    q, e = el.get("q"), el.get("e")
    d = {}
    if q is None or e is None:
        return d
    d["perigee_km"] = q
    if e < 1:
        a = el.get("a") or q / (1 - e)
        d["a_km"] = a
        d["apogee_km"] = el.get("Q") or a * (1 + e)
        d["period_d"] = el.get("P_d") or 2 * math.pi * math.sqrt(a ** 3 / MU_EARTH) / 86400
    else:
        d["a_km"] = q / (1 - e)  # negative for hyperbola
        d["v_inf_kms"] = math.sqrt(MU_EARTH * (e - 1) / q) if e > 1 else 0.0
    return d


def regime_for_bound(per, apo):
    if per is None or apo is None:
        return None
    if per <= 2.4:
        return "OTHER"
    return "CISLUNAR_HEO" if apo >= 100000 else "HEO"


def parse_ns_note_orbit(note):
    """q/Q/P from a neocpsat catalog note ('q=22k km, Q=150k km, P=2.91d')."""
    out = {}
    m = re.search(r"\bP=([\d.]+)\s*d", note)
    if m:
        out["P_d"] = float(m.group(1))
    m = re.search(r"\bq=([\d.]+)k km", note)
    if m:
        out["q_km"] = float(m.group(1)) * 1000
    m = re.search(r"\bQ=([\d.]+)k km", note)
    if m:
        out["Q_km"] = float(m.group(1)) * 1000
    return out


def ident_orbit(rows):
    """Latest neocpsat row (file order) whose note gives P, q and Q; helio flag."""
    best, helio = None, False
    for r in rows:
        if "heliocentric" in (r.get("Orbit") or "").lower():
            helio = True
        o = parse_ns_note_orbit(r.get("Note") or "")
        if {"P_d", "Q_km"} <= set(o):
            best = (r.get("Temp Desig"), o)
    return best, helio


def infer_obj_type(texts):
    t = " ".join(texts)
    if re.search(r"stage|R/B|rocket body|booster|Falcon|Centaur|Blok|\bS2\b|"
                 r"\bS-IVB\b|\bdeb\b", t, re.I):
        return "rocket_body"
    if re.search(r"Multijunk|ETBO|junk|debris", t, re.I):
        return "debris"
    if _COSPAR.search(t) or re.search(r"Tiandu|Spektr|JWST|Chang.?e|TESS|SMILE|"
                                      r"Euclid|CAPSTONE|THEMIS|IMP-|Vela|Artemis",
                                      t, re.I):
        return "payload"
    return "unknown"


# --------------------------------------------------------------------------
# main pipeline
# --------------------------------------------------------------------------

def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def migrate_hist(hist):
    """history.json v1 ('reasons','last_seen') -> v2 fields."""
    for h in hist.values():
        if "reasons" in h and "reasons_ever" not in h:
            h["reasons_ever"] = h.pop("reasons")
        if "last_seen" in h and "last_selected" not in h:
            h["last_selected"] = h.pop("last_seen")
        if "last_in_neocp_txt" in h and "last_listed" not in h:
            h["last_listed"] = h.pop("last_in_neocp_txt")
        h.setdefault("reasons_ever", [])
    return hist


def main(argv=None):
    global NOW, CACHE
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--offline", action="store_true", help="no network; reparse cache")
    ap.add_argument("--max-age", type=float, default=50.0,
                    help="reuse summary/neocp.txt/sat_id2 younger than this (minutes)")
    ap.add_argument("--all-mpecs", action="store_true",
                    help="fetch every summary object's pseudo-MPEC")
    ap.add_argument("--keep-days", type=float, default=7.0,
                    help="keep sticky rows this long after they were last selected")
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between requests")
    ap.add_argument("--tsv", default=TSV_OUT)
    ap.add_argument("--cache", default=CACHE, help="cache directory (snapshot replays)")
    ap.add_argument("--now", help="reference day, ISO UTC (default CLE_NOW, else wall clock)")
    args = ap.parse_args(argv)

    NOW = _now.now("day", args.now)
    CACHE = args.cache
    os.makedirs(CACHE, exist_ok=True)
    max_age = args.max_age * 60
    run_t = utcnow()

    # 1. summary + neocp.txt + sat_id2 ------------------------------------------
    sb, _ = cached_fetch(SUMMARY_URL, os.path.join(CACHE, "summary.htm"),
                         max_age, args.offline, HEADERS)
    nb, _ = cached_fetch(NEOCP_URL, os.path.join(CACHE, "neocp.txt"),
                         max_age, args.offline)
    satid_path = os.path.join(CACHE, "satid", "sat_id2.htm")
    ib, _ = cached_fetch(SATID_URL, satid_path, max_age, args.offline, HEADERS)
    if sb is None or nb is None:
        sys.exit("no summary.htm / neocp.txt available (offline with empty cache?)")
    summ = parse_summary(sb.decode("utf-8", "replace"))
    regen = summ["regenerated"] or iso(run_t)
    regen_t = parse_iso(regen)
    snap_s = snapshot(sb, "summary.htm", regen.replace("-", "").replace(":", ""))
    ntime = mtime_utc(os.path.join(CACHE, "neocp.txt"))
    snap_n = snapshot(nb, "neocp.txt", iso(ntime).replace("-", "").replace(":", ""))
    neocp, neocp_fallback = parse_neocp_txt(nb.decode("utf-8", "replace"))
    entries = parse_satid(ib.decode("utf-8", "replace")) if ib else []
    snap_i = (snapshot(ib, "sat_id2.htm",
                       iso(mtime_utc(satid_path)).replace("-", "").replace(":", ""))
              if ib else "")
    write_atomic(os.path.join(CACHE, "satid", "sat_id2_log.json"),
                 json.dumps(entries, indent=1, ensure_ascii=False))
    satid = SatIdLog(entries)
    listed = set(summ["rows"]) | set(neocp)
    print(f"summary regenerated {regen}: {len(summ['rows'])} rows "
          f"({sum(r['geo'] for r in summ['rows'].values())} geo), "
          f"{len(summ['unparsed'])} unparsed")
    print(f"neocp.txt: {len(neocp)} objects, {sum(o['s_flag'] for o in neocp.values())} S-flag, "
          f"{len(neocp_fallback)} needed fallback parse")
    print(f"sat_id2.htm: {len(entries)} log entries, {len(satid.current())} dated ??.??; "
          f"now {iso(NOW)}")

    ns = NeocpsatIndex()
    tles = TleIndex()

    # 2. sticky selection -------------------------------------------------------
    hist = migrate_hist(load_json(os.path.join(CACHE, "history.json"), {}))
    reasons = {}

    def add(d, r):
        reasons.setdefault(d, [])
        if r not in reasons[d]:
            reasons[d].append(r)

    for d in sorted(listed):
        r = summ["rows"].get(d)
        if r and r["geo"]:
            add(d, "geo")
        if d in neocp and neocp[d]["s_flag"]:
            add(d, "S")
        if satid.lookup(d)[0]:
            add(d, "satid")
        lk = ns.lookup(d)
        if lk["direct"]:
            add(d, "neocpsat")
    # sat_id2 '??.??' (current) entries.  Bill sometimes types a NEOCP desig
    # with 0 for O (sat_id2 'TK00003' = NEOCP 'TKO0003'): if the logged desig
    # is unknown here but exactly one known desig (listed, history or cached
    # pseudo-MPEC) matches it O/0-insensitively, use the known spelling.
    mdir = os.path.join(CACHE, "mpecs")
    known = listed | set(hist) | {n[:-4] for n in exact_listdir(mdir) if n.endswith(".htm")}
    fz = {}
    for k in known:
        fz.setdefault(k.replace("O", "0"), set()).add(k)
    satid_alias = {}
    for e in satid.current():
        d = e["desig"]
        if d not in known:
            alt = sorted(fz.get(d.replace("O", "0"), set()) - {d})
            if len(alt) == 1:
                satid_alias[d] = alt[0]
                d = alt[0]
        add(d, "satid_current")

    # 3. pseudo-MPECs ----------------------------------------------------------
    fetch_set = set(reasons)
    fetch_set |= {d for d in hist if d in summ["rows"]}
    for d, r in summ["rows"].items():  # cheap prefilter for low-v_inf candidates
        T = r.get("tisserand_earth")
        if (T is not None and T >= 2.97 and r["moid_au"] is not None
                and r["moid_au"] <= NEAR_AU) or earth_like(r["a_au"], r["e"], r["incl"],
                                                           r["moid_au"]):
            fetch_set.add(d)
    if args.all_mpecs:
        fetch_set |= set(summ["rows"])
    fetch_set |= set(hist)  # parse cached pages of history objects (no refetch)

    os.makedirs(mdir, exist_ok=True)
    pages, http_status = {}, {}
    for d in sorted(fetch_set):
        fn = case_safe_name(d, mdir, ".htm")
        path = os.path.join(mdir, fn)
        in_summary = d in summ["rows"]
        if os.path.exists(path):
            with open(path, "rb") as f:
                old = f.read()
            cm = re.search(rb"Created (\d{4}) (\w{3}) +(\d+) +(\d+):(\d+):(\d+) UT", old)
            created = None
            if cm:
                y, mo, dd, hh, mi, ss = [x.decode() for x in cm.groups()]
                created = dt.datetime(int(y), MONTHS[mo.lower()], int(dd), int(hh),
                                      int(mi), int(ss), tzinfo=dt.timezone.utc)
            age = time.time() - os.path.getmtime(path)
            if not in_summary:
                # off the summary: Bill no longer regenerates the page; refetch
                # selected ones at most daily, history-only ones never
                need = d in reasons and age > 86400
            elif created and regen_t and created >= regen_t - dt.timedelta(minutes=5):
                need = False
            else:
                need = age >= max_age
        else:
            need = in_summary or d in reasons
        need = need and not args.offline
        if need:
            try:
                r = http_get(f"{MPEC_BASE}/{d}.htm", headers=HEADERS)
            except requests.RequestException as e:
                r = None
                print(f"  WARN pseudo-MPEC {d}: {e}", file=sys.stderr)
            http_status[d] = r.status_code if r is not None else 0
            time.sleep(args.delay)
            if r is not None and r.status_code == 200:
                write_atomic(path, r.content)
            elif r is not None:
                print(f"  WARN pseudo-MPEC {d}: HTTP {r.status_code}", file=sys.stderr)
        if os.path.exists(path):
            with open(path, "rb") as f:
                page = f.read().decode("utf-8", "replace")
            pages[d] = parse_mpec(page, d)
            pages[d]["cache_file"] = os.path.relpath(path, ROOT)
            pages[d]["cache_mtime"] = iso(mtime_utc(path))
            created = pages[d].get("created")
            if created:
                hdir = os.path.join(mdir, "history", fn.rsplit(".", 1)[0])
                hp = os.path.join(hdir, created.replace("-", "").replace(":", "") + ".htm")
                if not os.path.exists(hp):
                    os.makedirs(hdir, exist_ok=True)
                    shutil.copy2(path, hp)
        else:
            pages[d] = {"desig": d, "found": False,
                        "http_status": http_status.get(d)}

    # 4. candidate (re-evaluated) reasons + keep decision ------------------------
    cand = {}
    for d, p in pages.items():
        rs = candidate_reasons(p, summ["rows"].get(d))
        if rs:
            cand[d] = rs
            for r in rs:
                add(d, r)
    keep = {}
    for d in set(reasons) | set(hist):
        cur = reasons.get(d, [])
        h = hist.get(d, {})
        if cur:
            keep[d] = cur
            continue
        sticky_ever = sorted(set(h.get("reasons_ever", [])) & STICKY)
        ls = parse_iso(h.get("last_selected"))
        if sticky_ever and ls and regen_t and \
                (regen_t - ls).total_seconds() <= args.keep_days * 86400:
            keep[d] = []      # kept on history only
    for d in keep:
        h = hist.setdefault(d, {"first_seen": regen, "reasons_ever": []})
        if keep[d]:
            h["last_selected"] = max(h.get("last_selected") or regen, regen)
            h["reasons_last"] = keep[d]
        h["reasons_ever"] = sorted(set(h.get("reasons_ever", [])) | set(keep[d]))
        if d in listed:
            h["last_listed"] = regen
    for d, h in hist.items():   # history objects not kept: record only
        if d not in keep and d in listed:
            h["last_listed"] = regen
    write_atomic(os.path.join(CACHE, "history.json"),
                 json.dumps(hist, indent=1, sort_keys=True))

    # removed = live in the previous parsed.json, absent from both listings now
    # (set membership, not a regen/fetch time comparison); kept --keep-days
    # after the last listing, dropped when listed again.  A parsed.json from
    # before this field is seeded from history.json last_listed.
    prev = load_json(os.path.join(CACHE, "parsed.json"), {})
    prev_live = {d for d, o in prev.get("objects", {}).items() if o.get("live")}
    if "removed" not in prev:
        prev_live |= {d for d, h in hist.items() if h.get("last_listed")}
    listing_t = max(regen_t, ntime)
    removed = dict(prev.get("removed") or {})
    for d in prev_live - listed:
        removed.setdefault(d, {"last_listed": hist.get(d, {}).get("last_listed", ""),
                               "removed_utc": iso(listing_t)})
    for d in list(removed):
        ll = parse_iso(removed[d].get("last_listed")) or parse_iso(removed[d]["removed_utc"])
        if d in listed or (listing_t - ll).total_seconds() > args.keep_days * 86400:
            del removed[d]

    # 5. Bill's linked per-object pages (pluto/mpecs/*.htm) ----------------------
    pdir = os.path.join(CACHE, "pluto_mpecs")
    linked_pages = {}

    def linked_page(href):
        if href in linked_pages:
            return linked_pages[href]
        url = href if href.startswith("http") else f"{PLUTO_ROOT}/{href.lstrip('/')}"
        fn = re.sub(r"[^A-Za-z0-9_.-]", "_", url.split("/")[-1])
        path = os.path.join(pdir, case_safe_name(fn.rsplit(".", 1)[0], pdir, ".htm"))
        b, _ = cached_fetch(url, path, 24 * 3600, args.offline, HEADERS)
        if b is None:
            linked_pages[href] = None
            return None
        p = parse_mpec(b.decode("utf-8", "replace"), fn)
        p["url"] = url
        p["cache_file"] = os.path.relpath(path, ROOT)
        linked_pages[href] = p
        if not args.offline:
            time.sleep(args.delay * 0.5)
        return p

    # 6. observations ------------------------------------------------------------
    odir = os.path.join(CACHE, "obs")
    rdir = os.path.join(CACHE, "mpc_obs")
    os.makedirs(odir, exist_ok=True)
    os.makedirs(rdir, exist_ok=True)
    obsinfo = {}
    for d in sorted(keep):
        p = pages.get(d, {})
        lines = list(p.get("obs_lines") or [])
        src_used = ["pluto_page"] if lines else []
        rpath = os.path.join(rdir, case_safe_name(d, rdir, ".txt"))
        mpc_lines = []
        if d in neocp and not args.offline:
            fresh = os.path.exists(rpath) and time.time() - os.path.getmtime(rpath) < max_age
            if not fresh:
                body = json.dumps({"trksubs": [d], "output_format": ["OBS80"]})
                try:
                    r = http_get(OBS_API, headers={"Content-Type": "application/json"},
                                 data=body)
                    time.sleep(args.delay)
                    js = r.json() if r.status_code == 200 else None
                    txt = js[0].get("OBS80") if js and isinstance(js, list) and js else None
                    if txt:
                        write_atomic(rpath, txt)
                except (ValueError, requests.RequestException) as e:
                    print(f"  WARN MPC obs {d}: {e}", file=sys.stderr)
        if os.path.exists(rpath):
            with open(rpath) as f:
                mpc_lines = [ln.rstrip("\n") for ln in f if ln.strip()]
        if mpc_lines:
            src_used.append("mpc_neocp_api")
        opath = os.path.join(odir, case_safe_name(d, odir, ".txt"))
        added = merge_obs(opath, lines + mpc_lines)
        merged = []
        if os.path.exists(opath):
            with open(opath) as f:
                merged = [ln.rstrip("\n") for ln in f if ln.strip()]
        st = obs_stats(merged)
        st.update({"path": os.path.relpath(opath, ROOT), "added_this_run": added,
                   "sources": src_used, "n_pluto": len(lines), "n_mpc": len(mpc_lines)})
        obsinfo[d] = st

    # 7. rows ---------------------------------------------------------------------
    # optional Horizons spacecraft cross-check written by scripts/neocp_hz_check.py
    hzc = load_json(os.path.join(CACHE, "hz_check", "result.json"), {})
    hz_ids = {v: k for k, v in (hzc.get("candidates") or {}).items()}
    rows, parsed = [], {}
    for d in sorted(keep, key=lambda x: (x.lower(), x)):
        p = pages.get(d, {"found": False})
        srow = summ["rows"].get(d)
        nrow = neocp.get(d)
        lk = ns.lookup(d)
        sent, how = satid.lookup(d)
        el = p.get("elements", {}) if p.get("found") else {}
        cm = p.get("comments", {}) if p.get("found") else {}
        ob = obsinfo.get(d, {})
        rs = keep[d]
        h = hist.get(d, {})
        live = d in listed
        center = page_center(el)
        geo = derive_geocentric(el) if center == "earth" else {}
        if center == "sun":
            if el.get("a"):
                geo["a_km"] = el["a"] * AU_KM
            if el.get("q"):
                geo["perigee_km"] = el["q"] * AU_KM
            if el.get("Q"):
                geo["apogee_km"] = el["Q"] * AU_KM
        e = el.get("e")

        # ---- identification (neocpsat system of record, then Bill's log)
        s_ident = [x for x in sent if x["identified"]]
        bill_names, bill_cos, bill_linked = [], [], []
        for x in sent:
            for n in x["names"]:
                if n not in bill_names:
                    bill_names.append(n)
            for c in x["cospar"]:
                if c not in bill_cos:
                    bill_cos.append(c)
            for l_ in [x["desig"]] + x["linked"]:   # x['desig'] != d for O/0 typos
                if l_ != d and l_ not in bill_linked:
                    bill_linked.append(l_)
        idents = [i for i in lk["idents"] if i not in GENERIC_IDENTS]
        generic = [i for i in lk["idents"] if i in GENERIC_IDENTS]
        identified = bool(idents) or bool(s_ident)
        name = idents[0] if idents else (bill_names[0] if s_ident and bill_names else d)
        hzr = (hzc.get("objects") or {}).get(d)
        hz_match = hzr["match"][0] if hzr and len(hzr.get("match", [])) == 1 else ""
        if hz_match and not identified:
            identified, name = True, hz_match
        cospar = list(lk["cospar"]) + [c for c in bill_cos if c not in lk["cospar"]]
        aliases = []
        for a in ([d] if name != d else []) + lk["aliases"] + bill_linked + \
                [n for n in bill_names if n != name]:
            if a and a != name and a not in aliases:
                aliases.append(a)

        notes = []
        # ---- regime
        regime, regime_basis = "NEOCP_GEO", ""
        if hz_match and name == hz_match:
            regime = HZ_REGIME.get(hz_match, "EARTH_NEAR_HELIO")
            regime_basis = f"Horizons {hz_ids.get(hz_match)} topocentric match"
        elif not identified:
            if ("geo" not in rs and "S" not in rs and center == "sun"
                    and set(rs) & {"earthlike_helio", "lowvenc_helio"}):
                regime, regime_basis = "EARTH_NEAR_HELIO", "non-geo heliocentric candidate"
            elif "geo" not in rs and "S" not in rs and center == "sun" and \
                    not set(rs) & STICKY and "geo" not in h.get("reasons_ever", []):
                regime, regime_basis = "EARTH_NEAR_HELIO", "heliocentric fit"
        else:
            orows = lk["direct"] + lk["linked_in_notes"]
            if not orows:
                for n in idents + bill_names:
                    orows += ns.rows_for_ident(n)
                for c in cospar:
                    orows += [r for r in ns.rows_for_cospar(c) if r not in orows]
            best, helio = ident_orbit(orows)
            if best:
                regime = regime_for_bound(best[1]["P_d"], best[1]["Q_km"]) or "OTHER"
                regime_basis = (f"neocpsat orbit of {best[0]} (P={best[1]['P_d']}d "
                                f"q={best[1].get('q_km', 0) / 1000:.0f}k "
                                f"Q={best[1]['Q_km'] / 1000:.0f}k km)")
            elif helio:
                regime, regime_basis = "EARTH_NEAR_HELIO", "neocpsat orbit column '(heliocentric)'"
            elif center == "earth" and e is not None and e < 1:
                regime = regime_for_bound(geo.get("period_d"), geo.get("apogee_km")) or "OTHER"
                regime_basis = "current find_orb geocentric fit"
            elif center == "sun":
                regime, regime_basis = "EARTH_NEAR_HELIO", "current find_orb heliocentric fit"
            else:
                regime, regime_basis = "OTHER", "identified but no orbit available"
        if hzr:
            if hzr.get("match"):
                notes.append("Horizons spacecraft check: MATCH " + ", ".join(
                    f"{m} ({hz_ids.get(m)}) topo sep {hzr['topo'].get(m)} arcsec"
                    for m in hzr["match"]))
            else:
                cl = hzr.get("closest_geo") or []
                notes.append(f"Horizons spacecraft check ({len(hz_ids)} SE-L1/L2 and "
                             f"near-Earth heliocentric probes, {hzc.get('run_utc', '')}): no "
                             f"match" + (f", closest {cl[0]['name']} {cl[0]['max_sep_deg']} deg"
                                         if cl else ""))
        # fit inconsistent with identification -> blank elements
        fit_conflict = (identified and regime in ("CISLUNAR_HEO", "HEO", "OTHER")
                        and center == "sun")
        if regime == "OTHER":
            notes.append("regime OTHER: identified object's geocentric period <= 2.4 d"
                         if regime_basis.startswith("neocpsat orbit") or
                         regime_basis.startswith("current") else
                         "regime OTHER: " + regime_basis)

        # ---- NORAD / TLE (Bill Gray tle_list.txt)
        norad, bgtle = "", []
        tle_blocks = []
        for c in cospar:
            tle_blocks += tles.for_cospar(c)
        for n in idents + bill_names:
            for href in satid.pages_for_name(n):
                tle_blocks += [b for b in tles.for_stem(href.rsplit("/", 1)[-1].split(".")[0])
                               if b not in tle_blocks]
        norad_src = ""
        for b in tle_blocks:
            bgtle.append(f"bgtle:~/tles/{b['file']}[{b['range'][0]}..{b['range'][1]}]")
            if not norad and b["norad"]:
                norad, norad_src = b["norad"], f"~/tles/tle_list.txt ({b['file']})"

        # ---- linked pluto/mpecs pages from Bill's log
        lp_sources, lp_notes = [], []
        hrefs = []
        for x in sent:
            hrefs += [hh for hh in x["hrefs"] if "pluto/mpecs/" in hh and "#" not in hh
                      and hh not in hrefs]
        for n in idents + bill_names:   # fallback: Bill's newest page for the name
            if hrefs:
                break
            hrefs += satid.pages_for_name(n)[:1]
        for href in hrefs:
            lp = linked_page(href)
            if not lp:
                continue
            a_, b_ = page_span(lp)
            lp_sources.append(f"pluto_mpec:{lp['url']}[{a_}..{b_}]")
            if lp.get("norad") and not norad:
                norad, norad_src = lp["norad"], f"title of {lp['url']}"
            lp_notes.append(f"Bill Gray page '{lp.get('title', '')}' (created "
                            f"{lp.get('created', '?')})"
                            + (": " + " ".join(lp['header_text'])[:240]
                               if lp.get("header_text") else ""))

        # ---- notes: flags
        flag = []
        if srow and srow["geo"]:
            flag.append("Pluto summary MOID=(geo)")
        elif srow:
            flag.append(f"Pluto summary heliocentric a={srow['a_au']} e={srow['e']} "
                        f"i={srow['incl']} MOID={srow['moid_au']}")
        if nrow:
            flag.append(f"MPC neocp.txt score {nrow['score']} note '{nrow['note']}'"
                        + (" S-flag" if nrow["s_flag"] else " not S-flagged")
                        + f" {nrow['nobs']} obs arc {nrow['arc']} d, {nrow['updated']}")
        else:
            flag.append("not in current MPC neocp.txt")
        if not live:
            flag.append(f"not in current NEOCP listings (last listed "
                        f"{h.get('last_listed') or 'never seen listed by this script'}"
                        f"{', pseudo-MPEC created ' + p['created'] if p.get('created') else ''})")
        if not rs:
            flag.append(f"kept from history (sticky reasons "
                        f"{','.join(sorted(set(h.get('reasons_ever', [])) & STICKY))}, "
                        f"last selected {h.get('last_selected')})")
        elif live and "geo" in h.get("reasons_ever", []) and "geo" not in rs:
            flag.append("was geo-flagged on an earlier run, not now")
        for x in sent:
            flag.append(f"Bill Gray sat_id2 ({how}{' ' + x['desig'] if how != 'exact' else ''}):"
                        f" '{x['line']}'"
                        + (" -- " + " ".join(x["comments"]) if x["comments"] else ""))
        if "earthlike_helio" in rs:
            flag.append("non-geo artificial candidate: Earth-like heliocentric orbit")
        if "lowvenc_helio" in rs:
            flag.append(f"non-geo artificial candidate: Earth encounter v<{VINF_MAX:g} km/s "
                        f"and within {NEAR_AU} AU")
        if identified:
            flag.append(f"identification: {name}"
                        + (f" [{', '.join(cospar)}]" if cospar else "")
                        + f"; regime from {regime_basis}")
        notes = flag + notes + lp_notes
        if center == "earth" and e is not None and e >= 1:
            notes.append(
                f"hyperbolic geocentric fit e={e:.3f}: Earth flyby, perigee "
                f"{iso(jdtt_to_utc(el['tp_jd_tt']), False) if el.get('tp_jd_tt') else '?'} "
                f"q={el.get('q', 0):.0f} km (alt {el.get('q', 0) - R_EARTH:.0f} km), "
                f"v_inf {geo.get('v_inf_kms', 0):.2f} km/s"
                + (" (>> typical artificial escape v_inf, likely natural)"
                   if geo.get("v_inf_kms", 0) > 5 else ""))
        if fit_conflict:
            notes.append("current NEOCP fit is heliocentric but the object is identified "
                         "as geocentric: elements left blank, MPEC ephemeris unreliable")
        if el:
            s = (f"find_orb {el.get('fit_tag', '')} fit {el.get('frame', '')} epoch "
                 f"JDT {el.get('epoch_jd_tt')}: ")
            if center == "earth" and e is not None and e < 1:
                s += (f"P={geo.get('period_d', 0):.3f} d q={el.get('q', 0):.0f} "
                      f"Q={geo.get('apogee_km', 0):.0f} km ")
            elif center == "sun":
                s += f"a={el.get('a')} AU q={el.get('q')} AU "
            s += (f"e={e} i={el.get('incl')}(+/-{el.get('incl_sigma')}) "
                  f"node={el.get('node')} peri={el.get('peri')} H={el.get('H')}")
            if el.get("amr") is not None:
                s += f" AMR={el['amr']}"
            notes.append(s)
            arc_t = f" ({el['arc_text']})" if el.get("arc_text") else ""
            notes.append(f"{el.get('n_used', '?')}/{el.get('n_obs', '?')} obs "
                         f"{el.get('obs_dates_text', '')}{arc_t} "
                         f"mean resid {el.get('mean_resid_arcsec')} arcsec")
        elif p.get("http_status"):
            notes.append(f"pseudo-MPEC HTTP {p['http_status']}")
        if ob:
            notes.append(f"astrometry cached {ob.get('n', 0)} lines "
                         f"(pluto {ob.get('n_pluto', 0)}, MPC API {ob.get('n_mpc', 0)})"
                         f" {ob.get('first', '')}..{ob.get('last', '')} stations "
                         f"{' '.join(ob.get('stations', []))}")
        if p.get("found") and p.get("astrometry_withheld"):
            notes.append("Pluto page withholds astrometry"
                         + (" (redacted)" if p.get("astrometry_redacted") else ""))
        if cm.get("geo_score") is not None:
            notes.append(f"find_orb GEO score {cm['geo_score']:.0f}")
        if cm.get("barbee_kms") is not None:
            notes.append(f"Barbee enc. vel {cm['barbee_kms']} km/s")
        if cm.get("earth_enc_kms") is not None:
            notes.append(f"Earth enc. vel {cm['earth_enc_kms']} km/s")
        if cm.get("tisserand_earth") is not None:
            notes.append(f"T_Earth {cm['tisserand_earth']}")
        he = cm.get("helio_el")
        if he and center == "earth":
            notes.append(f"heliocentric osc. a={he.get('a')} AU e={he.get('ecc')} "
                         f"i={he.get('Incl')} (ecl)")
        if cm.get("sv_central_au"):
            rr = math.sqrt(sum(x * x for x in cm["sv_central_au"])) * AU_KM
            notes.append(f"{'geo' if center == 'earth' else 'helio'}centric dist at epoch "
                         f"{rr:.0f} km")
        eph = p.get("ephemeris") or []
        eph_span = (eph[0]["t"], eph[-1]["t"]) if eph else None
        now_row = None
        if eph:
            now_row = min(eph, key=lambda x: abs((parse_iso(x["t"]) - NOW).total_seconds()))
            if abs((parse_iso(now_row["t"]) - NOW).total_seconds()) > 3 * 3600:
                now_row = None
        if now_row:
            notes.append(f"MPEC eph row nearest NOW {now_row['t']}: delta "
                         f"{now_row['delta_km']:.0f} km "
                         f"mag {now_row['mag']} sigma {now_row['sig_arcsec']} arcsec")
        if eph_span:
            notes.append(f"MPEC ephemeris table {eph_span[0]}..{eph_span[1]}")
            if center == "sun":
                dk = [x["delta_au"] for x in eph]
                notes.append(f"geocentric delta {min(dk):.4f}..{max(dk):.4f} AU in table, "
                             f"Earth MOID {el.get('earth_moid_au', '?')} AU")
        if srow:
            notes.append(f"summary Sig1 {srow['sig1_raw']} Sig2 {srow['sig2_raw']}")
        if cm.get("diam_m") is not None:
            notes.append(f"D~{cm['diam_m']} m @10% albedo")
        if cm.get("tle"):
            notes.append("TLE in MPEC comment")
        if p.get("id_text"):
            notes.append("pseudo-MPEC id text: " + " / ".join(p["id_text"])[:300])
        if lk["direct"] or lk["linked_in_notes"]:
            allr = lk["direct"] + lk["linked_in_notes"]
            notes.append("neocpsat catalog: "
                         + ", ".join(f"{r['Temp Desig']}={r['Indentification']}"
                                     for r in allr[:4])
                         + (f" (+{len(allr) - 4} more)" if len(allr) > 4 else "")
                         + ("" if lk["direct"] else " (linked in notes only)"))
        else:
            ci = ns.case_insensitive(d)
            notes.append("neocpsat: not catalogued (exact-case check)"
                         + (" -- different designation(s) differing only in case: "
                            + ", ".join(f"{r['Temp Desig']}={r['Indentification']}"
                                        for r in ci[:3]) if ci else ""))
        if norad:
            notes.append(f"NORAD {norad} from {norad_src}"
                         + (" (Bill Gray 8xxxx/9xxxx surrogate number)"
                            if re.match(r"^[89]\d{4}$", norad) else ""))
        if generic:
            notes.append(f"neocpsat generic identification '{generic[0]}' (not a "
                         f"specific object)")
        # SCHEMA: no quoting -> no literal double quotes either (arcsec marks
        # in Bill's text become two apostrophes)
        notes = [n.replace("\t", " ").replace(";", ",").replace("\n", " ")
                 .replace('"', "''") for n in notes if n]

        # ---- ephemeris sources
        es = []
        first, last = (ob.get("first") or "")[:10], (ob.get("last") or "")[:10]
        if not first and p.get("found"):
            first, last = page_span(p)
        if p.get("found"):
            es.append(f"pluto_mpec:{MPEC_BASE}/{d}.htm[{first}..{last}]")
        if ob.get("n"):
            es.append(f"neocp_obs:{ob['path']}[{first}..{last}]")
        if lk["mpec_file"]:
            a_, b_ = ns.file_obs_span(lk["mpec_file"])
            es.append(f"neocpsat_fit:../neocpsat/{lk['mpec_file']}[{a_ or ''}..{b_ or ''}]")
        combined = list(lk["combined"])
        for n in bill_names:
            combined += [c for c in ns.combined_for([n]) if c not in combined]
        for cf in combined:
            a_, b_ = ns.file_obs_span("mpecs/" + cf)
            es.append(f"neocpsat_fit:../neocpsat/mpecs/{cf}[{a_ or ''}..{b_ or ''}]")
        if lk["data_file"]:
            a_, b_ = ns.file_obs_span(lk["data_file"])
            es.append(f"neocp_obs:../neocpsat/{lk['data_file']}[{a_ or ''}..{b_ or ''}]")
        es += lp_sources + bgtle

        # covers_now: the pseudo-MPEC's 48-h ephemeris table spans NOW and the
        # fit is usable there (1-sigma at NOW <= SIG_NOW_MAX), or a Bill Gray
        # TLE block's '# Range:' spans NOW.  A table spanning NOW with a
        # multi-degree sigma (short-arc / hyperbolic AutoNEOCP fits) is '?':
        # the position is not actually known.
        today = NOW.date().isoformat()
        tle_now = [x for x in bgtle if span_covers(x, today)]
        # SCHEMA: "covers 2026-10-21" -> the table must overlap that UTC day
        # (a page regenerated at hh:35 starts its table after 00:00Z NOW).
        eph_now = bool(eph_span and parse_iso(eph_span[0]) < NOW + dt.timedelta(days=1)
                       and parse_iso(eph_span[1]) >= NOW and not fit_conflict)
        # 1-sigma at NOW; when the table starts hours after NOW (page made at
        # hh:35 on 2026-10-21) use its row nearest NOW, else summary Sig1
        # ('current ephemeris uncertainty' at regeneration time).
        sig_now, sig_at = None, ""
        if now_row:
            sig_now, sig_at = now_row["sig_arcsec"], f"at {now_row['t']}"
        elif eph_now:
            near_row = min(eph, key=lambda x: abs((parse_iso(x["t"]) - NOW).total_seconds()))
            sig_now, sig_at = near_row.get("sig_arcsec"), f"at table row {near_row['t']}"
            if sig_now is None and srow and srow.get("sig1_arcsec") is not None:
                sig_now, sig_at = srow["sig1_arcsec"], f"(summary Sig1, {regen})"
        if tle_now:
            covers = "Y"
        elif eph_now and (sig_now is None or sig_now <= SIG_NOW_MAX):
            covers = "Y"
        elif eph_now:
            covers = "?"
            notes.append(f"MPEC ephemeris covers {today} but 1-sigma {sig_at} is "
                         f"{sig_now / 3600:.1f} deg (> {SIG_NOW_MAX / 3600:g} deg): "
                         f"covers_now '?'")
        elif p.get("found") and el.get("epoch_jd_tt") or es:
            covers = "?"
        else:
            covers = "N"

        status_date = (ob.get("last") or "")[:10] or last
        elem_epoch = (iso(jdtt_to_utc(el["epoch_jd_tt"]))
                      if el.get("epoch_jd_tt") and not fit_conflict else "")
        bl = fit_conflict
        row = {
            "src": "neocp", "src_ref": d, "name": name,
            "aliases": ";".join(aliases),
            "norad": norad, "cospar": ";".join(cospar),
            "horizons_id": hz_ids.get(hz_match, "") if hz_match and name == hz_match else "",
            "obj_type": infer_obj_type([name] + cospar + bill_names + idents)
            if identified else "unknown",
            "regime": regime, "status": "unknown", "status_date": status_date,
            "period_d": "" if bl else (fmt(geo.get("period_d"), 4) if center == "earth" else ""),
            "a_km": "" if bl else fmt(geo.get("a_km"), 1),
            "e": "" if bl or e is None else fmt(e, 6),
            "incl_deg": "" if bl else fmt(el.get("incl"), 4),
            "perigee_km": "" if bl else fmt(geo.get("perigee_km"), 1),
            "apogee_km": "" if bl else fmt(geo.get("apogee_km"), 1),
            "elem_epoch": elem_epoch, "frame_center": "" if bl else center,
            "ephem_sources": ";".join(es), "covers_now": covers,
            "notes": " | ".join(notes),
            "listed_now": "Y" if live else "N", "last_listed": h.get("last_listed") or "",
        }
        rows.append(row)
        parsed[d] = {"selection": rs, "live": live, "summary": srow, "neocp_txt": nrow,
                     "mpec": {k: v for k, v in p.items() if k != "obs_lines"},
                     "derived": geo, "obs": ob, "satid": sent, "satid_match": how,
                     "linked_pages": {hh: ({k: v for k, v in linked_pages[hh].items()
                                            if k in ("url", "title", "norad", "created",
                                                     "header_text", "elements",
                                                     "cache_file")}
                                           if linked_pages.get(hh) else None)
                                      for hh in hrefs},
                     "regime_basis": regime_basis, "history": h,
                     "neocpsat": {
                         "direct": [r["Temp Desig"] for r in lk["direct"]],
                         "linked": [r["Temp Desig"] for r in lk["linked_in_notes"]],
                         "idents": idents, "aliases": lk["aliases"],
                         "data_file": lk["data_file"], "mpec_file": lk["mpec_file"],
                         "combined": combined, "cospar": lk["cospar"]},
                     "tsv_row": row}

    # 8. write outputs ------------------------------------------------------------
    order = {"NEOCP_GEO": 0, "CISLUNAR_HEO": 1, "HEO": 2, "SE_L1": 3, "SE_L2": 3,
             "EARTH_NEAR_HELIO": 4, "OTHER": 5}
    rows.sort(key=lambda r: (order.get(r["regime"], 9), r["src_ref"].lower(), r["src_ref"]))
    os.makedirs(os.path.dirname(args.tsv), exist_ok=True)
    out = ["\t".join(COLUMNS)]
    for r in rows:
        out.append("\t".join(str(r[c]).replace("\t", " ") for c in COLUMNS))
    write_atomic(args.tsv, "\n".join(out) + "\n")

    non_geo_summary = [d for d, r in summ["rows"].items() if not r["geo"]]
    near = {}
    for d in non_geo_summary:
        p = pages.get(d)
        if p and p.get("found"):
            dm = page_min_delta_au(p)
            if dm is not None and dm < NEAR_AU:
                near[d] = {"min_delta_au": round(dm, 5), "vinf_kms": page_vinf(p),
                           "selected": d in keep}
    stats = {
        "run_utc": iso(run_t), "now": iso(NOW), "summary_regenerated": regen,
        "summary_snapshot": snap_s, "neocp_snapshot": snap_n, "satid_snapshot": snap_i,
        "neocp_txt_mtime": iso(ntime),
        "n_summary": len(summ["rows"]),
        "n_summary_geo": len(summ["rows"]) - len(non_geo_summary),
        "n_summary_non_geo": len(non_geo_summary),
        "n_neocp_txt": len(neocp),
        "n_neocp_s_flag": sum(o["s_flag"] for o in neocp.values()),
        "n_neocp_non_s": sum(not o["s_flag"] for o in neocp.values()),
        "n_listed_union": len(listed),
        "only_in_summary": sorted(set(summ["rows"]) - set(neocp)),
        "only_in_neocp_txt": sorted(set(neocp) - set(summ["rows"])),
        "neocp_txt_fallback_lines": neocp_fallback,
        "summary_unparsed": summ["unparsed"],
        "satid_entries": len(entries),
        "satid_current": [e["line"] for e in satid.current()],
        "satid_listed_matches": {d: satid.lookup(d)[1] for d in sorted(listed)
                                 if satid.lookup(d)[0]},
        "satid_desig_alias": satid_alias,
        "non_geo_within_0.05AU_in_mpec_table": near,
        "all_mpecs": bool(args.all_mpecs),
        "rows": len(rows),
        "rows_listed_now": sum(r["listed_now"] == "Y" for r in rows),
        "rows_covers_now": sum(r["covers_now"] == "Y" for r in rows),
        "regime_counts": {k: sum(r["regime"] == k for r in rows)
                          for k in sorted({r["regime"] for r in rows})},
        "mpecs_parsed": sorted(pages),
        "selection": {d: keep[d] for d in sorted(keep)},
    }
    other = {}
    for d in non_geo_summary:
        if d in pages and pages[d].get("found") and d not in keep:
            c = pages[d].get("comments", {})
            other[d] = {k: c.get(k) for k in ("geo_score", "earth_enc_kms",
                                              "barbee_kms", "tisserand_earth")}
            other[d]["min_delta_au"] = page_min_delta_au(pages[d])
            other[d]["id_text"] = pages[d].get("id_text")
    stats["non_selected_mpec_scores"] = other
    write_atomic(os.path.join(CACHE, "parsed.json"),
                 json.dumps({"stats": stats, "objects": parsed, "removed": removed},
                            indent=1, sort_keys=True, ensure_ascii=False))
    print(json.dumps({**{k: stats[k] for k in (
        "now", "summary_regenerated", "n_summary", "n_summary_geo", "n_summary_non_geo",
        "n_neocp_txt", "n_neocp_s_flag", "satid_entries", "satid_current",
        "satid_listed_matches", "rows", "rows_listed_now", "rows_covers_now",
        "regime_counts", "only_in_summary", "only_in_neocp_txt", "selection")},
        "removed": removed}, indent=1, ensure_ascii=False))
    print(f"wrote {args.tsv}")
    return stats


if __name__ == "__main__":
    main()
