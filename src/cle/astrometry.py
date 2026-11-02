"""Astrometry tracklets of identified catalog targets (ARCHITECTURE.md §7): build/tracklets.json -> targets-<chunk>.js.

The NEOCP / neocpsat observations a drawn catalog target lists as `neocp_obs:<file>` sources (../neocpsat/data,
work/cache/neocp/obs) that fall in the window are placed along the target's saved track (build/states/<cid>, cubic
Hermite) by cle.tracklets.build. `cle states` writes, after the states:

  build/tracklets.json  {"schema": "cle/tracklets@1", "built_utc", "window": [t0, t1],
                         "targets": {<cid>: {"tracklets": [...], "obs_files": [...], "rms_arcsec"}}}

and `cle export` copies each non-empty list into that target's targets-<chunk>.js payload as `tracklets` when the
file was built for the export's window. Tracklet items keep the cle.tracklets.build shape: {"stn", "n", "t_ms",
"ra_deg", "dec_deg", "mag", "range_km", "stn_km", "dot_km", "resid_arcsec"}. Requested NEOCP objects carry their own.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import catalog, tracklets
from .adapters import fo, primary
from .statetable import StateTable
from .timeutil import iso
from .validate import BUILD, STATES, load_json, write_atomic

ROOT = Path(__file__).resolve().parents[2]
OUT = BUILD / "tracklets.json"
SCHEMA = "cle/tracklets@1"


def obs_lines(path):
    return [ln.rstrip("\n")[:80] for ln in Path(path).read_text(errors="replace").splitlines() if len(ln) >= 80]


def neocp_obs(target, win):
    """80-column lines of the target's neocp_obs sources that fall in the window (deduplicated)."""
    files, lines, seen = [], [], set()
    for src in target.get("sources", []):
        for item in src.get("ephem_sources", "").split(";"):
            kind, ref, _ = primary({"primary_ephem": item})
            path = ROOT / ref.split("#")[0]
            if kind != "neocp_obs" or not path.exists() or path.name not in {p.name for p in path.parent.iterdir()}:
                continue
            files.append(str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path.resolve()))
            for ln in obs_lines(path):
                k = fo.obs_key(ln) if fo.is_obs(ln) else ln
                if k not in seen and (not fo.is_obs(ln) or win.t0_s <= fo.obs_time(ln) <= win.t1_s):
                    seen.add(k)
                    lines.append(ln)
    return sorted(set(files)), lines


def catalog_tracklets(win, ctx, rows=None, states: Path = STATES) -> dict:
    """{cid: {"tracklets", "obs_files", "rms_arcsec"}} for the drawn viewer targets (catalog.targets()) whose saved
    states reach into the window and whose observations give at least one tracklet."""
    from .export import clip
    out = {}
    for t in catalog.targets() if rows is None else rows:
        if t["draw"] != "Y" or not (states / f"{t['cid']}.json").exists():
            continue
        st = StateTable.load(states, t["cid"])
        if all(clip(s, win.t0_s, win.t1_s) is None for s in st.segments):
            continue
        files, lines = neocp_obs(t, win)
        at = tracklets.track_at(st, ctx)
        trk = tracklets.build(tracklets.parse(lines), at, ctx, *at.span)
        if trk:
            out[t["cid"]] = {"tracklets": trk, "obs_files": files, "rms_arcsec": tracklets.rms(trk)}
    return out


def write_json(win, ctx, out: Path = OUT) -> dict:
    """build/tracklets.json for this window (atomic)."""
    doc = {"schema": SCHEMA, "built_utc": iso(time.time()), "window": [iso(win.t0_s), iso(win.t1_s)],
           "targets": catalog_tracklets(win, ctx)}
    write_atomic(out, json.dumps(doc, indent=1, allow_nan=False))
    return doc


def load(win, path: Path = OUT) -> dict:
    """{cid: [tracklets]} from build/tracklets.json when it was built for this window; {} otherwise (a missing or
    other-window file adds nothing: its dots would not match this export's tracks)."""
    doc = load_json(path, None)
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA or doc.get("window") != [iso(win.t0_s), iso(win.t1_s)]:
        return {}
    return {cid: v["tracklets"] for cid, v in (doc.get("targets") or {}).items() if v.get("tracklets")}
