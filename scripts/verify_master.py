#!/usr/bin/env python3
"""Check the primary ephemeris of every tier-1 and tier-2 object in catalog/master_list.tsv.

  horizons       the ID must be one the Horizons sweep resolved (catalog/sources/horizons.tsv)
  spacetrack_gp  the GP in work/cache/rocketdebris/spacetrack/gp.json is propagated with SGP4/SDP4
                 to 2026-10-21; the radius must lie between perigee and apogee (5 % margin)
  bgtle          the Bill Gray file must exist in ~/tles
  neocpsat_fit   the fit page / elements file must exist
  pluto_mpec     the pseudo-MPEC must be cached under work/cache/neocp/mpecs/ or ../neocpsat/mpecs/
  SATCAT         every real NORAD number is looked up once on Space-Track (space-track skill CLI);
                 a DECAY date before now on a non-terminal object is an error

Writes catalog/verify_report.tsv and prints a summary.
Usage:  ~/.venvs/astro313/bin/python -I scripts/verify_master.py [--no-satcat]
"""
from __future__ import annotations

import csv
import json
import math
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from sgp4.api import Satrec, jday

ROOT = Path(__file__).resolve().parents[1]
NEOCPSAT = ROOT.parent / "neocpsat"
TLES = Path.home() / "tles"
ST = Path.home() / ".claude/skills/space-track/st.py"
PY = Path.home() / ".venvs/astro313/bin/python"
NOW = datetime(2026, 10, 21, tzinfo=timezone.utc)
TERMINAL = {"decayed", "lunar_impact", "landed"}


def tsv(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE))


def fnum(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def satcat(norads):
    """One Space-Track SATCAT query for all NORAD numbers; {norad: row}."""
    out = {}
    ids = sorted(norads, key=int)
    for k in range(0, len(ids), 100):
        q = f"class/satcat/NORAD_CAT_ID/{','.join(ids[k:k + 100])}"
        dump = ROOT / "work/cache/verify" / f"satcat_{k}.json"
        dump.parent.mkdir(parents=True, exist_ok=True)
        res = subprocess.run([str(PY), str(ST), "raw", q, "--json", str(dump)], capture_output=True, text=True)
        try:
            for r in json.loads(dump.read_text()):
                out[r["NORAD_CAT_ID"]] = r
        except (OSError, json.JSONDecodeError):
            print("satcat query failed:", res.stderr[-300:], file=sys.stderr)
    return out


def main():
    master = [r for r in tsv(ROOT / "catalog/master_list.tsv") if r["tier"] in ("1", "2")]
    hz_ok = {r["horizons_id"] for r in tsv(ROOT / "catalog/sources/horizons.tsv")}
    gp = json.load(open(ROOT / "work/cache/rocketdebris/spacetrack/gp.json"))
    jd, fr = jday(NOW.year, NOW.month, NOW.day, 0, 0, 0)
    sc = {} if "--no-satcat" in sys.argv else satcat({r["norad"] for r in master
                                                       if r["norad"].isdigit() and not 79000 <= int(r["norad"]) <= 99999})
    report = []
    for r in master:
        p = r["primary_ephem"].removeprefix("STALE ")
        kind, ref = (p.split(":", 1) + [""])[:2]
        ref = re.sub(r"\[.*$", "", ref)
        verdict, detail = "ok", ""
        if kind == "horizons":
            if ref not in hz_ok:
                verdict, detail = "FAIL", f"Horizons ID {ref} not resolved by the sweep"
        elif kind == "spacetrack_gp":
            g = gp.get(ref) or {}
            if not g.get("TLE_LINE1"):
                verdict, detail = "FAIL", "no GP lines cached"
            else:
                s = Satrec.twoline2rv(g["TLE_LINE1"], g["TLE_LINE2"])
                err, pos, _ = s.sgp4(jd, fr)
                rad = math.dist(pos, (0, 0, 0)) if err == 0 else None
                lo, hi = fnum(r["perigee_km"]), fnum(r["apogee_km"])
                if rad is None:
                    verdict, detail = "FAIL", f"sgp4 error {err}"
                elif lo and hi and not (0.95 * lo <= rad <= 1.05 * hi):
                    verdict, detail = "WARN", f"r={rad:.0f} km outside {lo:.0f}..{hi:.0f}"
                else:
                    detail = f"r(now)={rad:.0f} km, GP {g['EPOCH'][:10]}"
        elif kind == "bgtle":
            if not (TLES / ref).exists():
                verdict, detail = "FAIL", f"~/tles/{ref} missing"
        elif kind == "neocpsat_fit":
            path = ref.split("@")[0]
            cands = [ROOT / path, NEOCPSAT / path.removeprefix("../neocpsat/"), (ROOT / path).resolve()]
            if not any(c.exists() for c in cands):
                verdict, detail = "FAIL", f"fit file {path} missing"
        elif kind == "pluto_mpec":
            desig = Path(ref).stem
            if not ((ROOT / f"work/cache/neocp/mpecs/{desig}.htm").exists() or (NEOCPSAT / f"mpecs/{desig}.html").exists()
                    or (NEOCPSAT / f"mpecs/{desig}.htm").exists()):
                verdict, detail = "WARN", "pseudo-MPEC not cached locally"
        elif not p and r.get("draw") == "N":
            verdict, detail = "ok", "listed, not drawn (no ephemeris, by decision)"
        elif not p:
            verdict, detail = "FAIL", "no primary ephemeris"
        s = sc.get(r["norad"])
        if s:
            decay = s.get("DECAY") or ""
            if decay and r["status"] not in TERMINAL and decay < NOW.date().isoformat():
                if r["regime"] in ("SE_L1", "SE_L2", "EARTH_NEAR_HELIO", "TRANSLUNAR"):
                    detail += f"; SATCAT DECAY {decay} = left Earth orbit (Space-Track convention), not a reentry"
                else:
                    verdict, detail = "FAIL", f"SATCAT DECAY {decay} but status {r['status']}; " + detail
            elif r["cospar"] and s.get("INTLDES") and s["INTLDES"] != r["cospar"]:
                verdict = "WARN" if verdict == "ok" else verdict
                detail += f"; SATCAT INTLDES {s['INTLDES']} ≠ {r['cospar']} ({s.get('SATNAME')})"
        elif sc and r["norad"].isdigit() and not 79000 <= int(r["norad"]) <= 99999:
            detail += "; NORAD not in SATCAT reply"
        report.append({"cid": r["cid"], "tier": r["tier"], "name": r["name"], "kind": kind, "verdict": verdict,
                       "detail": detail.strip("; ")})
    with open(ROOT / "catalog/verify_report.tsv", "w", newline="") as f:
        w = csv.DictWriter(f, ["cid", "tier", "name", "kind", "verdict", "detail"], delimiter="\t",
                           quoting=csv.QUOTE_NONE, escapechar="\\", lineterminator="\n")
        w.writeheader()
        w.writerows(report)
    bad = [x for x in report if x["verdict"] != "ok"]
    print(f"checked {len(report)}: ok={len(report) - len(bad)} warn/fail={len(bad)}; SATCAT rows {len(sc)}")
    for x in bad:
        print(f"  {x['verdict']:4} {x['tier']} {x['cid']:34} {x['kind']:13} {x['detail']}")


if __name__ == "__main__":
    main()
