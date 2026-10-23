#!/usr/bin/env python3
"""Latest Space-Track GP for master-list objects that lack a fresh ephemeris.

Takes every object in catalog/master_list.tsv with a real NORAD number (< 79000 or >= 100000)
that is not decayed/impacted/landed and whose primary ephemeris does not cover the window,
asks Space-Track class/gp for the newest element set (space-track skill CLI), and writes
catalog/sources/spacetrack.tsv in SCHEMA.md form. Run build_master.py afterwards.
obj_type / status are copied from the master row (the GP itself says nothing about them).

Usage:  ~/.venvs/astro313/bin/python -I scripts/extract_spacetrack_gp.py [--offline]
        --offline: no Space-Track query, rebuild from the cached dumps in work/cache/spacetrack/
"""
from __future__ import annotations

import csv
import json
import math
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _now import now  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ST = Path.home() / ".claude/skills/space-track/st.py"
PY = Path.home() / ".venvs/astro313/bin/python"
OUT = ROOT / "catalog/sources/spacetrack.tsv"
CACHE = ROOT / "work/cache/spacetrack"
NOW = now("day")              # CLE_NOW floored to the day, else today (scripts/_now.py)
MU, RE = 398600.4418, 6378.137
COLS = ["src", "src_ref", "name", "aliases", "norad", "cospar", "horizons_id", "obj_type", "regime",
        "status", "status_date", "period_d", "a_km", "e", "incl_deg", "perigee_km", "apogee_km",
        "elem_epoch", "frame_center", "ephem_sources", "covers_now", "notes"]


def main(offline=False):
    with open(ROOT / "catalog/master_list.tsv", newline="") as f:
        master = list(csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE))
    want = {}
    for r in master:
        n = r["norad"]
        if not n.isdigit() or 79000 <= int(n) <= 99999 or r["status"] in ("decayed", "lunar_impact", "landed"):
            continue
        if r["covers_window"] != "Y" and r["regime"] not in ("OTHER",):
            want[n] = r
    CACHE.mkdir(parents=True, exist_ok=True)
    gp = []
    ids = sorted(want, key=int)
    for k in range(0, len(ids), 80):
        dump = CACHE / f"gp_{k}.json"
        if not offline:
            subprocess.run([str(PY), str(ST), "raw", f"class/gp/NORAD_CAT_ID/{','.join(ids[k:k + 80])}", "--json", str(dump)],
                           capture_output=True, text=True)
        if dump.exists():
            gp += json.loads(dump.read_text())
    rows = []
    for g in gp:
        n = str(g["NORAD_CAT_ID"])
        m = want.get(n, {})
        mm, e = float(g["MEAN_MOTION"]), float(g["ECCENTRICITY"])
        a = (MU / (mm * 2 * math.pi / 86400) ** 2) ** (1 / 3)
        ep = g["EPOCH"]
        age = (NOW - datetime.fromisoformat(ep).replace(tzinfo=timezone.utc)).days
        # Space-Track carries placeholder GP sets for deep-space objects it no longer tracks
        # (fresh epoch, LEO-like period); launch-day captures are just as useless here.
        if 1 / mm < 1.0 or age > 365:
            continue
        rows.append({
            "src": "spacetrack", "src_ref": f"class/gp:{n}", "name": m.get("name") or g["OBJECT_NAME"],
            "aliases": g["OBJECT_NAME"], "norad": n, "cospar": g.get("OBJECT_ID", ""), "horizons_id": "",
            "obj_type": m.get("obj_type") or "unknown", "regime": m.get("regime", ""),
            "status": m.get("status") or "unknown", "status_date": m.get("status_date", "") if m.get("status") else "",
            "period_d": f"{1 / mm:.4f}", "a_km": f"{a:.1f}", "e": f"{e:.6f}", "incl_deg": g["INCLINATION"],
            "perigee_km": f"{a * (1 - e):.1f}", "apogee_km": f"{a * (1 + e):.1f}", "elem_epoch": ep[:19] + "Z",
            "frame_center": "earth", "ephem_sources": f"spacetrack_gp:{n}[{ep[:10]}..{ep[:10]}]",
            "covers_now": "Y" if abs(age) <= 60 else "N",
            "notes": f"latest Space-Track GP, epoch {ep[:10]} (age {age} d); SGP4/SDP4 mean elements, TEME",
        })
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, COLS, delimiter="\t", quoting=csv.QUOTE_NONE, escapechar="\\", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print(f"asked {len(ids)}{' (offline, cached dumps)' if offline else ''}, got {len(gp)} GP sets, kept {len(rows)} -> {OUT}")
    for r in sorted(rows, key=lambda r: r["elem_epoch"], reverse=True):
        print(f"  {r['norad']:>6} {r['name'][:34]:34} {r['elem_epoch'][:10]} P={r['period_d']} d")


if __name__ == "__main__":
    main(offline="--offline" in sys.argv)
