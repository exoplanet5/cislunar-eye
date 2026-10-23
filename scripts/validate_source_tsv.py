#!/usr/bin/env python3
"""validate_source_tsv.py -- check catalog/sources/<src>.tsv files against
catalog/sources/SCHEMA.md (columns/order, controlled vocabularies, formats).

Checks: exact header (neocp.tsv may add the trailing listed_now, last_listed
columns); field count per row; no quotes/CR; src key; unique src_ref;
obj_type / regime / status / frame_center / covers_now vocab; status_date
YYYY-MM-DD; elem_epoch ISO UTC; cospar YYYY-NNNX; numeric columns;
ephem_sources items 'kind:ref[start..end]' with an allowed kind and
YYYY-MM-DD (or empty) bounds; e >= 0, e < 1 implies apogee >= perigee.

Usage:  ~/.venvs/astro313/bin/python -I scripts/validate_source_tsv.py catalog/sources/neocp.tsv ...
Exit status 1 if any file has an error, 0 otherwise.
"""

import re
import sys

COLUMNS = ["src", "src_ref", "name", "aliases", "norad", "cospar", "horizons_id",
           "obj_type", "regime", "status", "status_date", "period_d", "a_km", "e",
           "incl_deg", "perigee_km", "apogee_km", "elem_epoch", "frame_center",
           "ephem_sources", "covers_now", "notes"]
NEOCP_EXTRA = ["listed_now", "last_listed"]       # optional trailing columns of neocp.tsv (neocp_live.py)
SRC = {"neocpsat", "billgray", "rocketdebris", "lunarmission", "horizons", "neocp", "news",
       "spacetrack", "manual"}
OBJ = {"payload", "rocket_body", "debris", "unknown", "natural"}
REGIME = {"LUNAR_ORBIT", "EM_L1", "EM_L2", "EM_DRO", "EM_OTHER", "CISLUNAR_HEO", "HEO",
          "TRANSLUNAR", "SE_L1", "SE_L2", "EARTH_NEAR_HELIO", "NEOCP_GEO", "OTHER"}
STATUS = {"active", "derelict", "decayed", "lunar_impact", "landed", "escaped",
          "returning", "lost", "unknown"}
KINDS = {"horizons", "bgtle", "neocpsat_fit", "neocp_obs", "pluto_mpec", "spacetrack_gp",
         "tle_other"}
NUM = ["period_d", "a_km", "e", "incl_deg", "perigee_km", "apogee_km"]


def main(path):
    errs, warns = [], []
    with open(path, encoding="utf-8", newline="") as f:
        raw = f.read()
    if "\r" in raw:
        errs.append("CR characters present")
    lines = raw.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    hdr = lines[0].split("\t")
    cols = COLUMNS + NEOCP_EXTRA if hdr == COLUMNS + NEOCP_EXTRA else COLUMNS
    if hdr != cols:
        errs.append(f"header mismatch: {hdr}")
    refs = set()
    for n, ln in enumerate(lines[1:], start=2):
        f_ = ln.split("\t")
        if len(f_) != len(cols):
            errs.append(f"line {n}: {len(f_)} fields")
            continue
        r = dict(zip(cols, f_))
        w = f"line {n} ({r['src_ref']})"
        if '"' in ln:
            warns.append(f"{w}: contains a double quote (no quoting allowed; fine if literal)")
        if r["src"] not in SRC:
            errs.append(f"{w}: src {r['src']!r}")
        if cols is not COLUMNS:
            if r["src"] != "neocp":
                errs.append(f"{w}: listed_now/last_listed columns on src {r['src']!r}")
            if r["listed_now"] not in ("Y", "N", ""):
                errs.append(f"{w}: listed_now {r['listed_now']!r}")
            if r["last_listed"] and not re.fullmatch(r"\d{4}-\d\d-\d\d(T\d\d:\d\d(:\d\d)?Z)?", r["last_listed"]):
                errs.append(f"{w}: last_listed {r['last_listed']!r}")
        if not r["src_ref"]:
            errs.append(f"{w}: empty src_ref")
        if r["src_ref"] in refs:
            errs.append(f"{w}: duplicate src_ref")
        refs.add(r["src_ref"])
        if r["obj_type"] not in OBJ:
            errs.append(f"{w}: obj_type {r['obj_type']!r}")
        if r["regime"] not in REGIME:
            errs.append(f"{w}: regime {r['regime']!r}")
        if r["status"] not in STATUS:
            errs.append(f"{w}: status {r['status']!r}")
        if r["status_date"] and not re.fullmatch(r"\d{4}-\d\d-\d\d", r["status_date"]):
            errs.append(f"{w}: status_date {r['status_date']!r}")
        if r["elem_epoch"] and not re.fullmatch(
                r"\d{4}-\d\d-\d\dT\d\d:\d\d(:\d\d(\.\d+)?)?Z", r["elem_epoch"]):
            errs.append(f"{w}: elem_epoch {r['elem_epoch']!r}")
        for c in filter(None, r["cospar"].split(";")):
            if not re.fullmatch(r"\d{4}-\d{3}[A-Z]{1,3}", c):
                errs.append(f"{w}: cospar {c!r}")
        if r["norad"] and not r["norad"].isdigit():
            errs.append(f"{w}: norad {r['norad']!r}")
        if r["frame_center"] not in ("", "earth", "moon", "sun"):
            errs.append(f"{w}: frame_center {r['frame_center']!r}")
        if r["covers_now"] not in ("Y", "N", "?"):
            errs.append(f"{w}: covers_now {r['covers_now']!r}")
        vals = {}
        for c in NUM:
            if r[c]:
                try:
                    vals[c] = float(r[c])
                except ValueError:
                    errs.append(f"{w}: {c} not numeric {r[c]!r}")
        if any(vals.get(c) is not None for c in ("a_km", "e", "perigee_km")) and \
                not r["frame_center"]:
            errs.append(f"{w}: elements without frame_center")
        if "e" in vals:
            if vals["e"] < 0:
                errs.append(f"{w}: e < 0")
            if vals["e"] < 1 and "apogee_km" in vals and "perigee_km" in vals and \
                    vals["apogee_km"] < vals["perigee_km"]:
                errs.append(f"{w}: apogee < perigee")
            if vals["e"] >= 1 and (r["apogee_km"] or r["period_d"]):
                errs.append(f"{w}: unbound orbit with apogee/period")
        if r["ephem_sources"]:
            for it in r["ephem_sources"].split(";"):
                m = re.fullmatch(r"([a-z_]+):(.+)\[([^\[\]]*)\.\.([^\[\]]*)\]", it)
                if not m:
                    errs.append(f"{w}: ephem_sources item {it!r}")
                    continue
                if m.group(1) not in KINDS:
                    errs.append(f"{w}: ephem kind {m.group(1)!r}")
                for b in m.group(3, 4):
                    if b and not re.fullmatch(r"\d{4}-\d\d-\d\d", b):
                        errs.append(f"{w}: ephem bound {b!r} in {it!r}")
                if not m.group(3) or not m.group(4):
                    warns.append(f"{w}: open/unknown span in {it[:70]!r}")
        elif r["covers_now"] == "Y":
            errs.append(f"{w}: covers_now Y without ephem_sources")
    print(f"{path}: {len(lines) - 1} rows, {len(errs)} errors, {len(warns)} warnings")
    for e in errs:
        print("  ERROR", e)
    for x in warns:
        print("  warn ", x)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(max([main(p) for p in sys.argv[1:]], default=0))
