#!/usr/bin/env python3
"""Build catalog/sources/rocketdebris.tsv from the ../rocketdebris beyond-GEO wiki.

Inputs (read-only):
  ../rocketdebris/status_verified.tsv   128 wiki objects: label, SATCAT decay, BG file, fresh source
  ../rocketdebris/horizons_status.tsv   Horizons commands for the deep-space bodies in the wiki
  work/cache/rocketdebris/spacetrack/{satcat,gp}.json
      SATCAT by launch and current GP, fetched by scripts/rocketdebris_fetch_spacetrack.py
      (GP creation date 2026-10-14).

One row per object that is in status_verified.tsv, or belongs to a wiki launch and
whose SATCAT/GP orbit meets the SCHEMA selection rule (P > 2.4 d or apogee >= 100 000 km).
Elements come from the newest Space-Track GP (mean elements; a from mean motion).

Usage:  ~/.venvs/astro313/bin/python -I scripts/extract_rocketdebris.py [--with-horizons-probes]
"""
from __future__ import annotations

import csv
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _now import now  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
WIKI = ROOT.parent / "rocketdebris"
ST = ROOT / "work/cache/rocketdebris/spacetrack"
OUT = ROOT / "catalog/sources/rocketdebris.tsv"

NOW = now("day")              # CLE_NOW floored to the day, else today (scripts/_now.py)
MU = 398600.4418
RE = 6378.137
P_MIN = 2.4 * 1440.0          # minutes
GP_FRESH_DAYS = 60            # a GP this close to now is treated as covering the window

COLS = ["src", "src_ref", "name", "aliases", "norad", "cospar", "horizons_id", "obj_type", "regime",
        "status", "status_date", "period_d", "a_km", "e", "incl_deg", "perigee_km", "apogee_km",
        "elem_epoch", "frame_center", "ephem_sources", "covers_now", "notes"]

OBJ_TYPE = {"PAYLOAD": "payload", "ROCKET BODY": "rocket_body", "DEBRIS": "debris", "UNKNOWN": "unknown"}

# Deep-space wiki bodies from horizons_status.tsv that are Earth-near (Tesla Roadster
# and the planetary missions are far heliocentric and out of scope). Commands that the
# wiki found broken are left for the horizons sweep to resolve.
HORIZONS_KEEP = {"JWST": "SE_L2", "Gaia": "SE_L2", "Cert-2 Vulcan Centaur": "EARTH_NEAR_HELIO",
                 "Spektr-RG": "SE_L2", "J002E3 (Apollo 12 SIVB)": "EARTH_NEAR_HELIO",
                 "2020 SO (Surveyor-2 Centaur)": "EARTH_NEAR_HELIO", "DART F9 S2": "EARTH_NEAR_HELIO",
                 "WMAP": "EARTH_NEAR_HELIO", "New Glenn GS2 (2025-260C)": "EARTH_NEAR_HELIO",
                 "ESCAPADE Blue": "SE_L2", "ESCAPADE Gold": "SE_L2"}


def tsv(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE))


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def fmt(x, nd):
    return "" if x is None else f"{x:.{nd}f}"


def regime_for(period_min, apogee_km):
    if apogee_km is not None and apogee_km >= 100000:
        return "CISLUNAR_HEO"
    if period_min is not None and period_min > P_MIN:
        return "HEO"
    return "OTHER"


def main():
    verified = {r["NORAD"]: r for r in tsv(WIKI / "status_verified.tsv")}
    gp = {k: v for k, v in json.load(open(ST / "gp.json")).items() if isinstance(v, dict) and v.get("EPOCH")}
    satcat = {}
    for launch, rows in json.load(open(ST / "satcat.json")).items():
        for r in rows:
            satcat[r["NORAD_CAT_ID"]] = r

    keep = set(verified)
    for norad, s in satcat.items():
        g = gp.get(norad, {})
        per = fnum(g.get("PERIOD")) or fnum(s.get("PERIOD"))
        apo = fnum(g.get("APOAPSIS")) or fnum(s.get("APOGEE"))
        if (per and per > P_MIN) or (apo and apo >= 100000):
            keep.add(norad)

    out = []
    for norad in sorted(keep, key=lambda n: int(n)):
        v, s, g = verified.get(norad, {}), satcat.get(norad, {}), gp.get(norad, {})
        name = s.get("SATNAME") or v.get("OBJ_NAME") or v.get("Label", "")
        label = v.get("Label", "")
        aliases = [a for a in (label, s.get("OBJECT_NAME")) if a and a != name]
        decay = s.get("DECAY") or v.get("DECAY") or ""
        epoch = g.get("EPOCH", "")
        n = fnum(g.get("MEAN_MOTION"))
        e = fnum(g.get("ECCENTRICITY"))
        a = (MU / (n * 2 * math.pi / 86400) ** 2) ** (1 / 3) if n else None
        per = fnum(g.get("PERIOD")) or fnum(s.get("PERIOD")) or fnum(v.get("PERIOD_min"))
        apo = fnum(g.get("APOAPSIS")) or fnum(s.get("APOGEE")) or fnum(v.get("APOG_km"))
        peri = fnum(g.get("PERIAPSIS")) or fnum(s.get("PERIGEE")) or fnum(v.get("PERIG_km"))
        inc = fnum(g.get("INCLINATION")) or fnum(s.get("INCLINATION")) or fnum(v.get("INCL_deg"))
        status = "decayed" if decay else "unknown"
        sources, notes = [], []
        covers = "N"
        if epoch:
            age = (NOW - datetime.fromisoformat(epoch).replace(tzinfo=timezone.utc)).days
            sources.append(f"spacetrack_gp:{norad}[{epoch[:10]}..{epoch[:10]}]")
            if not decay and abs(age) <= GP_FRESH_DAYS:
                covers = "Y"
            notes.append(f"GP epoch {epoch[:10]} (age {age} d)")
        bg = v.get("BG_FILE")
        if bg:
            sources.append(f"bgtle:{bg}[..{v.get('BG_YR_TAG_EPOCH', '')}]")
        if v.get("FRESH_SOURCE"):
            notes.append(f"wiki fresh source {v['FRESH_SOURCE']}")
        if v.get("NOTES"):
            notes.append(v["NOTES"])
        if not v:
            notes.append("SATCAT/GP member of a wiki launch, not itself in status_verified.tsv")
        out.append({
            "src": "rocketdebris", "src_ref": f"status_verified.tsv:{norad}" if v else f"spacetrack:{norad}",
            "name": name, "aliases": ";".join(aliases), "norad": norad,
            "cospar": s.get("INTLDES") or v.get("INTLDES", ""), "horizons_id": "",
            "obj_type": OBJ_TYPE.get(s.get("OBJECT_TYPE") or v.get("OBJ_TYPE", ""), "unknown"),
            "regime": regime_for(per, apo), "status": status, "status_date": decay,
            "period_d": fmt(per / 1440 if per else None, 4), "a_km": fmt(a, 1), "e": fmt(e, 6),
            "incl_deg": fmt(inc, 3), "perigee_km": fmt(peri + RE if peri is not None else None, 1),
            "apogee_km": fmt(apo + RE if apo is not None else None, 1), "elem_epoch": epoch[:19] + "Z" if epoch else "",
            "frame_center": "earth" if any(x is not None for x in (a, e, per, peri, apo, inc)) else "",
            "ephem_sources": ";".join(sources), "covers_now": covers,
            "notes": "; ".join(notes) + "; perigee/apogee converted from Space-Track altitudes to radii",
        })

    # The wiki's Horizons probes used name commands ("Cert-2", "DART", "2020 SO") that do not match the
    # IDs the Horizons sweep verified, so they only create duplicates; the sweep (horizons.tsv) is used instead.
    for h in tsv(WIKI / "horizons_status.tsv") if "--with-horizons-probes" in sys.argv else []:
        regime = HORIZONS_KEEP.get(h["Label"])
        if not regime:
            continue
        ok = "NOT FOUND" not in h["Horizons_resolved"] and "NO STATE" not in h["Horizons_resolved"]
        ok = ok and h["Label"] != "ESCAPADE Blue"      # wiki probe resolved it to Europa Clipper (-159)
        out.append({c: "" for c in COLS} | {
            "src": "rocketdebris", "src_ref": f"horizons_status.tsv:{h['Label']}", "name": h["Label"],
            "aliases": h["Horizons_resolved"] if ok else "", "horizons_id": h["Command"] if ok else "",
            "obj_type": "payload" if h["Label"] in ("JWST", "Gaia", "Spektr-RG", "WMAP") or "ESCAPADE" in h["Label"] else "rocket_body",
            "regime": regime, "status": "unknown", "frame_center": "sun" if ok else "",
            "ephem_sources": f"horizons:{h['Command']}[..]" if ok else "", "covers_now": "?",
            "notes": f"wiki Horizons probe 2026-04-30: {h['Horizons_resolved']} {h['Summary']}".strip()
                     + ("; that probe hit the wrong body, so no Horizons ID is carried" if h["Label"] == "ESCAPADE Blue" else ""),
        })

    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, COLS, delimiter="\t", quoting=csv.QUOTE_NONE, escapechar="\\", lineterminator="\n")
        w.writeheader()
        for r in out:
            w.writerow({k: str(r.get(k, "")).replace("\t", " ").replace("\n", " ") for k in COLS})
    print(f"wrote {OUT} rows={len(out)}")


if __name__ == "__main__":
    main()
