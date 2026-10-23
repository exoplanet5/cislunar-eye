#!/usr/bin/env python3
"""Probe two JPL Horizons behaviours that shape the cislunar-eye ephemeris pipeline.

1. Header layout of a small-body (``DES=...;``) geocentric VECTORS request: which
   lines carry the target name, centre, frame, units and state type, so the
   MMX header check (MMX src/ephemeris.py:60-65, which looks for ``"(<target>)"``)
   can be generalised.
2. What the API returns when the requested window extends past the end of a
   spacecraft SPK (CAPSTONE -1176 ends 2026-08-14 11:57 UT per the news sweep):
   an error, a truncated table, or something else.

Requests use exactly the MMX vector convention: CENTER='500@399', REF_SYSTEM='ICRF',
REF_PLANE='FRAME', TIME_TYPE='UT', VEC_CORR='NONE', OUT_UNITS='KM-S', VEC_TABLE='2',
CSV_FORMAT='YES'.  Raw JSON responses are cached under
work/cache/arch_probe/<key>.json; re-runs reuse the cache (pass --refresh to
re-download).  Read-only, no side effects outside that cache directory.

Usage:
    ~/.venvs/astro313/bin/python -I scripts/arch_horizons_probe.py [--refresh]
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://ssd.jpl.nasa.gov/api/horizons.api"
ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "work" / "cache" / "arch_probe"

BASE = dict(format="json", EPHEM_TYPE="VECTORS", CENTER="500@399", REF_SYSTEM="ICRF",
            REF_PLANE="FRAME", TIME_TYPE="UT", VEC_CORR="NONE", OUT_UNITS="KM-S",
            VEC_TABLE="2", CSV_FORMAT="YES", CAL_FORMAT="CAL", OBJ_DATA="NO")

PROBES = {
    "smallbody_2020SO": dict(COMMAND="DES=2020 SO;", START_TIME="2026-10-21 00:00",
                             STOP_TIME="2026-10-21 02:00", STEP_SIZE="1 h"),
    "name_2020SO": dict(COMMAND="2020 SO", START_TIME="2026-10-21 00:00",
                        STOP_TIME="2026-10-21 02:00", STEP_SIZE="1 h"),
    "smallbody_2024PT5": dict(COMMAND="DES=2024 PT5;", START_TIME="2026-10-21 00:00",
                              STOP_TIME="2026-10-21 02:00", STEP_SIZE="1 h"),
    "capstone_past_end": dict(COMMAND="-1176", START_TIME="2026-08-13 00:00",
                              STOP_TIME="2026-08-16 00:00", STEP_SIZE="6 h"),
}


def fetch(key: str, params: dict, refresh: bool) -> dict:
    path = CACHE / f"{key}.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text())
    q = {k: (v if k == "format" else f"'{v}'") for k, v in {**BASE, **params}.items()}
    url = API + "?" + urllib.parse.urlencode(q)
    with urllib.request.urlopen(url, timeout=120) as r:
        data = json.loads(r.read().decode())
    data["_url"] = url
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    time.sleep(0.5)
    return data


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    for key, params in PROBES.items():
        d = fetch(key, params, args.refresh)
        res = d.get("result", "")
        print(f"===== {key}  error={d.get('error')!r}")
        head, _, rest = res.partition("$$SOE")
        keep = [ln for ln in head.splitlines()
                if any(t in ln for t in ("Target body name", "Center body name", "Reference frame",
                                         "Output units", "cartesian states", "Calendar Date",
                                         "No ephemeris", "Start time", "Stop  time", "Step-size",
                                         "Center-site name", "Rec #", "{source"))]
        print("\n".join(keep))
        rows = [ln for ln in rest.partition("$$EOE")[0].splitlines() if ln.strip()]
        print(f"rows={len(rows)}  first={rows[0][:60] if rows else None}  last={rows[-1][:60] if rows else None}")
        if not rows:
            tail = [ln for ln in res.splitlines() if ln.strip()][-6:]
            print("tail:", *tail, sep="\n  ")


if __name__ == "__main__":
    main()
