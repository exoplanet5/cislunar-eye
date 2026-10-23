#!/usr/bin/env python3
"""neocp_hz_check.py -- cross-check unidentified NEOCP objects against JPL
Horizons spacecraft that are *not* in TLE catalogues (Sun-Earth L1/L2 and
Earth-near heliocentric probes), which Bill Gray's sat_id (TLE based) cannot
identify.

For every row of ``catalog/sources/neocp.tsv`` whose ``name`` equals its
``src_ref`` (= not identified) and that has a cached 80-column obs file
(``work/cache/neocp/obs/<desig>.txt``), the first and last observation
(time, astrometric J2000 RA/Dec, MPC station) are compared with the Horizons
astrometric RA/Dec of each candidate spacecraft:

  1. geocentric (CENTER 500@399), one request per candidate with a TLIST of
     all obs epochs -> angular separation (parallax at 0.01 AU is ~0.25 deg,
     so a coarse 2-deg screen is used here), then
  2. topocentric (CENTER '<station>@399') for every (object, candidate) pair
     that passed the screen -> separations in arcsec at first/last obs.

A match needs < 60 arcsec topocentric at both ends of the arc.  Results go to
``work/cache/neocp/hz_check/result.json`` (raw Horizons answers cached under
``work/cache/neocp/hz_check/raw/``; reruns reuse them).  neocp_live.py reads
result.json, if present, into the row notes.  Read-only for everything else.

Usage:
  ~/.venvs/astro313/bin/python -I scripts/neocp_hz_check.py [DESIG ...]
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import math
import os
import re
import sys
import time

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "work", "cache", "neocp")
OUT = os.path.join(CACHE, "hz_check")
RAW = os.path.join(OUT, "raw")
TSV = os.path.join(ROOT, "catalog", "sources", "neocp.tsv")
API = "https://ssd.jpl.nasa.gov/api/horizons.api"

# Horizons IDs (from work/cache/horizons/mb_list.txt, Horizons 'MB' list)
CANDIDATES = {
    "-170": "JWST", "-680": "Euclid", "-139479": "Gaia", "-211": "Roman Space Telescope",
    "-21": "SOHO", "-78": "DSCOVR", "-92": "ACE", "-8": "Wind", "-156": "Aditya-L1",
    "-43": "IMAP", "-231": "SWFO-L1", "-171": "Carruthers", "-165": "WMAP",
    "-489": "Planck", "-486": "Herschel", "-141043": "LISA Pathfinder",
    "-999742": "LISA Pathfinder PRM", "-95": "TESS", "-557": "Spektr-R",
    "-143205": "SpaceX Roadster",
}
SCREEN_DEG = 2.0
MATCH_ARCSEC = 60.0


def obs80(line):
    y, m, d = int(line[15:19]), int(line[20:22]), float(line[23:32])
    t = dt.datetime(y, m, 1, tzinfo=dt.timezone.utc) + dt.timedelta(days=d - 1)
    h, mi, s = line[32:44].split()
    ra = (int(h) + int(mi) / 60 + float(s) / 3600) * 15
    ds = line[44:56].strip()
    sg = -1 if ds.startswith("-") else 1
    dd, dm, dsec = ds.lstrip("+-").split()
    de = sg * (int(dd) + int(dm) / 60 + float(dsec) / 3600)
    return {"t": t, "ra": ra, "dec": de, "stn": line[77:80]}


def jd(t):
    return 2440587.5 + t.timestamp() / 86400.0


def sep_deg(ra1, de1, ra2, de2):
    r = math.radians
    c = (math.sin(r(de1)) * math.sin(r(de2))
         + math.cos(r(de1)) * math.cos(r(de2)) * math.cos(r(ra1 - ra2)))
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def horizons(cmd, center, jds):
    params = {"format": "text", "COMMAND": f"'{cmd}'", "OBJ_DATA": "'NO'",
              "MAKE_EPHEM": "'YES'", "EPHEM_TYPE": "'OBSERVER'",
              "CENTER": f"'{center}'", "TLIST_TYPE": "'JD'", "TIME_TYPE": "'UT'",
              "TLIST": " ".join(f"'{x:.6f}'" for x in jds),
              "QUANTITIES": "'1'", "ANG_FORMAT": "'DEG'", "CSV_FORMAT": "'YES'",
              "EXTRA_PREC": "'YES'"}
    key = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:16]
    path = os.path.join(RAW, f"{cmd}_{center.replace('@', 'at')}_{key}.txt")
    if os.path.exists(path):
        with open(path) as f:
            return f.read()
    r = requests.get(API, params=params, timeout=60)
    time.sleep(0.4)
    os.makedirs(RAW, exist_ok=True)
    with open(path, "w") as f:
        f.write(r.text)
    return r.text


def parse_eph(text):
    if "$$SOE" not in text:
        return None
    out = []
    for ln in text.split("$$SOE")[1].split("$$EOE")[0].strip().splitlines():
        p = [x.strip() for x in ln.split(",")]
        try:
            out.append((float(p[3]), float(p[4])))
        except (IndexError, ValueError):
            out.append(None)
    return out


def targets(desigs):
    rows = list(csv.DictReader(open(TSV), delimiter="\t"))
    tg = {}
    for r in rows:
        d = r["src_ref"]
        if desigs and d not in desigs:
            continue
        if not desigs and r["name"] != d:
            continue
        p = os.path.join(CACHE, "obs", f"{d}.txt")
        if not os.path.exists(p):
            continue
        lines = [ln.rstrip("\n") for ln in open(p) if len(ln.rstrip("\n")) >= 80]
        obs = sorted((obs80(ln) for ln in lines), key=lambda o: o["t"])
        if obs:
            tg[d] = [obs[0], obs[-1]]
    return tg


def main(argv):
    tg = targets(set(argv))
    epochs = [(d, k, o) for d, ob in tg.items() for k, o in enumerate(ob)]
    jds = [jd(o["t"]) for _, _, o in epochs]
    res = {"run_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "candidates": CANDIDATES, "screen_deg": SCREEN_DEG,
           "match_arcsec": MATCH_ARCSEC, "objects": {}}
    for d, ob in tg.items():
        res["objects"][d] = {"obs": [{"t": o["t"].isoformat(), "ra": o["ra"], "dec": o["dec"],
                                      "stn": o["stn"]} for o in ob],
                             "geo_screen": {}, "topo": {}, "match": []}
    for cmd, name in CANDIDATES.items():
        eph = parse_eph(horizons(cmd, "500@399", jds))
        if eph is None:
            print(f"  {name} ({cmd}): no geocentric ephemeris", file=sys.stderr)
            continue
        for (d, k, o), e in zip(epochs, eph):
            if e is None:
                continue
            s = sep_deg(o["ra"], o["dec"], e[0], e[1])
            res["objects"][d]["geo_screen"].setdefault(name, [None, None])[k] = round(s, 3)
    for d, rec in res["objects"].items():
        for name, ss in rec["geo_screen"].items():
            if None in ss or max(ss) > SCREEN_DEG:
                continue
            cmd = next(c for c, n in CANDIDATES.items() if n == name)
            topo = []
            for o in tg[d]:
                eph = parse_eph(horizons(cmd, f"{o['stn'].strip()}@399", [jd(o["t"])]))
                topo.append(round(sep_deg(o["ra"], o["dec"], *eph[0]) * 3600, 1)
                            if eph and eph[0] else None)
            rec["topo"][name] = topo
            if None not in topo and max(topo) < MATCH_ARCSEC:
                rec["match"].append(name)
        best = sorted(((max(v), n) for n, v in rec["geo_screen"].items() if None not in v))[:3]
        rec["closest_geo"] = [{"name": n, "max_sep_deg": s} for s, n in best]
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "result.json"), "w") as f:
        json.dump(res, f, indent=1)
    for d, rec in res["objects"].items():
        print(d, "match:", rec["match"] or "none", "| closest:", rec["closest_geo"],
              "| topo:", rec["topo"])


if __name__ == "__main__":
    main(sys.argv[1:])
