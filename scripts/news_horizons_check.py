#!/usr/bin/env python3
"""Verify JPL Horizons IDs / coverage for objects found by current-events research.

Used by the `news` source extractor of cislunar-eye (see
catalog/sources/SCHEMA.md).  For every row of an input TSV
(`key<TAB>command<TAB>center`) the script sends ONE Horizons API request
(EPHEM_TYPE=VECTORS, REF_PLANE=FRAME i.e. ICRF equatorial, geometric) for the
reference epoch 2026-10-21T00:00 UTC.  If the target resolves and the
ephemeris covers the epoch it sends a second request (EPHEM_TYPE=ELEMENTS,
REF_PLANE='BODY EQUATOR') to get osculating elements about the requested
centre (Earth 500@399 or Moon 500@301).

* `command` may be a numeric spacecraft ID (e.g. -170) or a name string; a
  name that matches several bodies is reported as `multiple` with the match
  list, nothing is guessed.
* Raw responses are cached under work/cache/news/horizons/<key>_{vec,elm}.json
  and reused on re-runs (idempotent); pass --refresh to re-download.
* Output: TSV on stdout (or --out) with columns
  key, command, center, status, target_name, horizons_id, covers_now,
  r_km, a_km, e, incl_deg, perigee_km, apogee_km, period_d, coverage_note.

* --scan START STOP STEP: minimum geocentric distance over a window (used
  for Earth flyby / return timing, e.g. JUICE, Clipper, ESCAPADE, Cert-2).
* --objdata: fetch only the Horizons object-data header (OBJ_DATA=YES,
  MAKE_EPHEM=NO) for each numeric ID and cache it as
  work/cache/news/horizons_objdata/obj_<id>.txt (mission notes, launch dates,
  trajectory-file coverage tables).

Usage:
  ~/.venvs/astro313/bin/python -I scripts/news_horizons_check.py \
      work/cache/news/horizons_targets.tsv --out work/cache/news/horizons_summary.tsv
  ~/.venvs/astro313/bin/python -I scripts/news_horizons_check.py \
      targets.tsv --scan 2026-09-01 2027-01-15 1h
  ~/.venvs/astro313/bin/python -I scripts/news_horizons_check.py \
      targets.tsv --objdata
"""
import argparse
import json
import math
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://ssd.jpl.nasa.gov/api/horizons.api"
EPOCH = "2026-10-21 00:00"
ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "work" / "cache" / "news" / "horizons"


def fetch(params, path, refresh=False):
    if path.exists() and not refresh:
        return json.loads(path.read_text())
    q = {"format": "json", "MAKE_EPHEM": "YES", "OBJ_DATA": "NO"}
    q.update(params)
    url = API + "?" + urllib.parse.urlencode(q)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=90) as r:
                data = json.loads(r.read().decode())
            data["_url"] = url
            path.write_text(json.dumps(data))
            time.sleep(0.5)
            return data
        except Exception as exc:  # network hiccup
            err = str(exc)
            time.sleep(3 * (attempt + 1))
    return {"error": err, "_url": url}


def parse_name(text):
    m = re.search(r"Target body name:\s*(.+?)(?:\s{2,}|\n)", text)
    name = m.group(1).strip() if m else ""
    mid = re.search(r"\((-?\d+)\)", name)
    return name, (mid.group(1) if mid else "")


def soe_block(text):
    m = re.search(r"\$\$SOE(.*?)\$\$EOE", text, re.S)
    return m.group(1) if m else None


def num(pattern, block):
    m = re.search(pattern + r"\s*=\s*([-+]?\d[\d.]*E[-+]\d+|[-+]?\d[\d.]*)", block)
    return float(m.group(1)) if m else None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("targets")
    ap.add_argument("--out")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--scan", nargs=3, metavar=("START", "STOP", "STEP"),
                    help="instead of the epoch check, report minimum geocentric "
                         "distance of each target over START..STOP (e.g. "
                         "2026-09-01 2027-01-01 1h)")
    ap.add_argument("--objdata", action="store_true",
                    help="fetch object-data headers only (numeric IDs)")
    args = ap.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)
    if args.scan:
        return scan(args)
    if args.objdata:
        return objdata(args)
    rows = []
    for line in Path(args.targets).read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        key, command, center = (line.split("\t") + ["", ""])[:3]
        center = center or "500@399"
        base = {"COMMAND": f"'{command}'", "CENTER": f"'{center}'",
                "TLIST": f"'{EPOCH}'", "TLIST_TYPE": "CAL", "TIME_TYPE": "UT"}
        vec = fetch(dict(base, EPHEM_TYPE="VECTORS", REF_PLANE="FRAME",
                         VEC_TABLE="2", VEC_CORR="NONE", OUT_UNITS="KM-S",
                         CSV_FORMAT="NO"), CACHE / f"{key}_vec.json", args.refresh)
        text = vec.get("result", "") or vec.get("error", "")
        out = dict(key=key, command=command, center=center, status="",
                   target_name="", horizons_id="", covers_now="?", r_km="",
                   a_km="", e="", incl_deg="", perigee_km="", apogee_km="",
                   period_d="", coverage_note="")
        name, hid = parse_name(text)
        out["target_name"], out["horizons_id"] = name, hid
        blk = soe_block(text)
        if "No matches found" in text or "Unknown target" in text:
            out["status"] = "not_found"
        elif "Multiple major-bodies match" in text or "Matching small-bodies" in text:
            out["status"] = "multiple"
            ids = re.findall(r"^\s*(-?\d+)\s+(.+?)\s{2,}", text, re.M)
            out["coverage_note"] = "; ".join(f"{i} {n.strip()}" for i, n in ids[:12])
        elif blk:
            x, y, z = (num(r"\bX", blk), num(r"\bY", blk), num(r"\bZ", blk))
            out["status"] = "ok"
            out["covers_now"] = "Y"
            if None not in (x, y, z):
                out["r_km"] = f"{math.sqrt(x*x+y*y+z*z):.0f}"
            elm = fetch(dict(base, TIME_TYPE="TDB", EPHEM_TYPE="ELEMENTS", REF_PLANE="BODY EQUATOR",
                             OUT_UNITS="KM-S", CSV_FORMAT="NO"),
                        CACHE / f"{key}_elm.json", args.refresh)
            eb = soe_block(elm.get("result", "") or "")
            if eb:
                ec, qr, inc, a = num(r"EC", eb), num(r"QR", eb), num(r"IN", eb), num(r"\bA", eb)
                ad, pr = num(r"AD", eb), num(r"PR", eb)
                if ec is not None:
                    out["e"] = f"{ec:.4f}"
                if inc is not None:
                    out["incl_deg"] = f"{inc:.2f}"
                if ec is not None and ec < 1 and a and a > 0:
                    out["a_km"] = f"{a:.0f}"
                    out["perigee_km"] = f"{qr:.0f}" if qr else ""
                    out["apogee_km"] = f"{ad:.0f}" if ad and ad < 1e20 else ""
                    out["period_d"] = f"{pr/86400:.3f}" if pr and pr < 1e20 else ""
                elif ec is not None:
                    out["coverage_note"] = "unbound about centre (e>=1)"
        else:
            out["status"] = "no_ephem"
            m = re.search(r"(No ephemeris[^\n]*|prior to[^\n]*|after[^\n]*A\.D\.[^\n]*)", text)
            out["coverage_note"] = (m.group(1).strip() if m else text.strip()[:200]).replace("\t", " ")
            out["covers_now"] = "N" if "No ephemeris" in text else "?"
        rows.append(out)
    cols = list(rows[0].keys()) if rows else []
    lines = ["\t".join(cols)] + ["\t".join(str(r[c]).replace("\t", " ").replace("\n", " ") for c in cols) for r in rows]
    txt = "\n".join(lines) + "\n"
    if args.out:
        Path(args.out).write_text(txt)
    sys.stdout.write(txt)


def scan(args):
    """Minimum geocentric distance (km) per target over a time window."""
    start, stop, step = args.scan
    tag = re.sub(r"[^0-9A-Za-z]", "", start + stop + step)
    print("key\thorizons_id\tmin_r_km\tmin_utc\tfirst_utc\tlast_utc\tnote")
    for line in Path(args.targets).read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        key, command = line.split("\t")[:2]
        d = fetch({"COMMAND": f"'{command}'", "CENTER": "'500@399'",
                   "START_TIME": f"'{start}'", "STOP_TIME": f"'{stop}'",
                   "STEP_SIZE": f"'{step}'", "EPHEM_TYPE": "VECTORS",
                   "REF_PLANE": "FRAME", "VEC_TABLE": "3", "VEC_CORR": "NONE",
                   "OUT_UNITS": "KM-S", "CSV_FORMAT": "YES", "TIME_TYPE": "UT"},
                  CACHE / f"{key}_scan_{tag}.json", args.refresh)
        text = d.get("result", "") or d.get("error", "")
        _, hid = parse_name(text)
        blk = soe_block(text)
        if not blk:
            m = re.search(r"(No ephemeris[^\n]*)", text)
            print(f"{key}\t{hid}\t\t\t\t\t{m.group(1) if m else text.strip()[:160]!r}")
            continue
        best, first, last = None, None, None
        for row in blk.strip().splitlines():
            f = [c.strip() for c in row.split(",")]
            if len(f) < 9:
                continue
            t = f[1].replace("A.D. ", "")
            r = math.sqrt(sum(float(v) ** 2 for v in f[2:5]))
            first = first or t
            last = t
            if best is None or r < best[0]:
                best = (r, t)
        print(f"{key}\t{hid}\t{best[0]:.0f}\t{best[1]}\t{first}\t{last}\t")
    return 0


def objdata(args):
    """Cache the Horizons object-data header for each target (one request each)."""
    outdir = ROOT / "work" / "cache" / "news" / "horizons_objdata"
    outdir.mkdir(parents=True, exist_ok=True)
    for line in Path(args.targets).read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        key, command = line.split("\t")[:2]
        path = outdir / f"obj_{command}.txt"
        if path.exists() and not args.refresh:
            print(f"{key}\t{command}\tcached\t{path}")
            continue
        d = fetch({"COMMAND": f"'{command}'", "OBJ_DATA": "YES", "MAKE_EPHEM": "NO"},
                  outdir / f"obj_{command}.json", args.refresh)
        text = d.get("result", "") or d.get("error", "")
        path.write_text(text)
        first = next((l.strip() for l in text.splitlines() if "Revised" in l), "")
        print(f"{key}\t{command}\tfetched\t{first}")
    return 0


if __name__ == "__main__":
    main()
