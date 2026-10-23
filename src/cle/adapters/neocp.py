"""Live NEOCP adapter (ARCHITECTURE.md §5.6) and build/neocp.json (§11 neocp shape, §7 tracklets).

build(): the cached 80-column astrometry work/cache/neocp/obs/<desig>.txt (written by scripts/neocp_live.py) is
refitted with local fo as in cle.adapters.fo (no -c; -h 3 for a geocentric pseudo-MPEC, -h 0 for a heliocentric
one; -g 17 when the page fitted an AMR, or for arcs >= 7 d without a page); the vector table is checked against
fo's one-leg propagation (fo.Fit.verify). The track spans [first obs - 2 d, now + 7 d] inside the window and is cut where
fo's 1-sigma sky uncertainty exceeds 1 deg; 10-minute samples over [first obs - 1 d, last obs + 1 d], the regime
step elsewhere; quality=fit over [first - 2 d, last + 2 d], extrap beyond. The fit is checked against the
pseudo-MPEC in work/cache/neocp/parsed.json (q, e, i in page sigmas; state vector at the page epoch; the 48-h
geocentric table in units of its sigma). If fo fails, or disagrees with the page, the page table is used as
positions (quality=extrap).

write_json(win, ctx) writes build/neocp.json for the data-core export, after the states exist:
  {"schema": "cle/neocp@1", "built_utc", "neocp_snapshot_utc", "window",
   "objects":  {<desig>: {"cid", "catalog": <catalog.js entry; flybys (e > 1) subgroup "flyby"; hidden when no data
                                      at now; trail from first obs - 0.5 d to now + 1 d (§9.4)>,
                          "track": <Track, as targets-*.js>, "fit": {...}, "caveat", "tracklets": [...], "rms_arcsec"}},
   "attached": {<cid>: {"tracklets": [...], "obs_files": [...], "rms_arcsec"}},  # identified targets' astrometry
   "removed":  [{"desig", "removed_utc", "last_listed", "reason"}]}   # left the NEOCP (parsed.json "removed":
                                                                      # set membership across neocp_live runs) and not in objects
  tracklets: {"stn", "n", "t_ms" (light-time epochs), "ra_deg", "dec_deg", "mag", "range_km",
              "stn_km", "dot_km" (base64 little-endian f64 n x 3, geocentric ICRF), "resid_arcsec": [[dra*cos(dec), ddec]]}

    python -I src/cle/adapters/neocp.py <cid> ...      build (and save)
    python -I src/cle/adapters/neocp.py --json         write build/neocp.json from build/states/
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from cle import nbody, tracklets
from cle.adapters import fo, primary
from cle.statetable import Segment, StateTable
from cle.timeutil import iso, parse_iso

ROOT = nbody.ROOT
CACHE = ROOT / "work/cache/neocp"
SIGMA_MAX = 3600.0
NEAR_STEP = 600.0


def desig(target):
    _, ref, _ = primary(target)
    return re.search(r"/([^/]+)\.html?$", ref)[1]


def page(d):
    return json.loads((CACHE / "parsed.json").read_text())["objects"].get(d)


def obs_lines(path):
    return [ln.rstrip("\n")[:80] for ln in Path(path).read_text(errors="replace").splitlines() if len(ln) >= 80]


def args_for(lines, pg):
    """-h 0 for a heliocentric pseudo-MPEC, else -h 3; -g 17 when the page fitted an AMR (so the page check
    compares like with like), or, without a page, when the arc is >= 7 d."""
    first, last = fo.obs_span(lines)
    el = pg["mpec"]["elements"] if pg else {}
    helio = el.get("peri_word") == "Perihelion"
    srp = "AMR" in pg["mpec"].get("elements_text", "") if pg else last - first >= 7 * 86400
    return ["-h", "0" if helio else "3"] + (["-g", "17"] if srp else [])


def sigma_span(fit, first, last, a_s, b_s):
    """The span around the obs arc where fo's sky sigma stays below 1 deg."""
    ts, arc = fit.sigma_arcsec
    good = np.isfinite(arc) & (arc < SIGMA_MAX)
    lo, hi = a_s, b_s
    after = (ts > last) & ~good
    before = (ts < first) & ~good
    if after.any():
        hi = min(hi, ts[after][0])
    if before.any():
        lo = max(lo, ts[before][-1])
    return lo, hi


def radec(x):
    return np.degrees(np.arctan2(x[:, 1], x[:, 0])) % 360, np.degrees(np.arcsin(x[:, 2] / np.linalg.norm(x, axis=1)))


def page_check(fit, pg, a_s, b_s, ctx):
    """Our fit against the pseudo-MPEC: elements in page sigmas, central state vector, 48-h table / table sigma."""
    el, m = pg["mpec"]["elements"], pg["mpec"]
    out = {}
    for ours, key in (("q", "q"), ("e", "e"), ("i", "incl")):
        if el.get(key) is not None and fit.el[ours][0] is not None:
            sig = el.get(f"{key}_sigma") or fit.el[ours][1]
            out[f"d{ours}_sigma"] = round(abs(fit.el[ours][0] - el[key]) / sig, 2) if sig else None
    sv = m.get("comments", {}).get("sv_central_au")
    t_ep = (el["epoch_jd_tt"] - 2440587.5) * 86400 - fo.TT_UTC if el.get("epoch_jd_tt") else None
    if sv and el.get("peri_word") == "Perigee" and t_ep and a_s <= t_ep <= b_s:
        out["dr_page_sv_km"] = round(float(np.linalg.norm(fit(t_ep)[0, :3] - np.array(sv) * fo.AU)), 1)
    rows = [r for r in m.get("ephemeris", []) if a_s + 60 <= parse_iso(r["t"]) <= b_s]
    if rows:
        t = np.array([parse_iso(r["t"]) for r in rows])
        ra, dec = radec(tracklets.astrometric(fit, ctx, t, np.zeros((len(t), 3)))[0])
        d = np.radians([[r["ra_deg"] for r in rows], [r["dec_deg"] for r in rows]])
        sep = np.degrees(np.arccos(np.clip(np.sin(d[1]) * np.sin(np.radians(dec)) + np.cos(d[1])
                                           * np.cos(np.radians(dec)) * np.cos(d[0] - np.radians(ra)), -1, 1))) * 3600
        sig = np.array([r["sig_arcsec"] or np.nan for r in rows])
        out["table_max_sep_arcsec"] = round(float(sep.max()), 2)
        out["table_max_sigma_ratio"] = round(float(np.nanmax(sep / sig)), 3)
    out["fit_disagrees"] = bool(any((out.get(k) or 0) > 1 for k in ("dq_sigma", "de_sigma", "di_sigma"))
                                or (out.get("table_max_sigma_ratio") or 0) > 1)
    return out


def table_track(target, pg, win, ctx, why):
    """Fallback: the pseudo-MPEC's 48-h geocentric table as positions (spline velocities), quality=extrap."""
    from scipy.interpolate import CubicSpline
    rows = pg["mpec"]["ephemeris"]
    t = np.array([parse_iso(r["t"]) for r in rows])
    x = np.array([r["xyz_km"] for r in rows])
    f = CubicSpline(t, x)
    k = (t >= win.t0_s) & (t <= win.t1_s)
    sig = np.array([r["sig_arcsec"] * r["delta_km"] / 206264.806 for r in rows], dtype=np.float32)
    seg = Segment(t0_s=float(t[k][0]), step_s=float(t[1] - t[0]), r_km=x[k], v_kms=f(t[k], 1), sigma_km=sig[k],
                  source=f"pluto_mpec:{pg['mpec']['desig']}", quality="extrap")
    return StateTable(cle_id=target["cid"], centre="earth", segments=[seg],
                      provenance=[nbody.raw(ROOT / pg["mpec"]["cache_file"])],
                      validation={"fallback": "page_table", "reason": why},
                      caveat=f"pseudo-MPEC ephemeris table ({why}); astrometric, not refitted")


def build(target, win, ctx):
    d = desig(target)
    pg = page(d)
    path = CACHE / "obs" / f"{d}.txt"
    if not path.exists() or path.name not in {p.name for p in path.parent.iterdir()}:
        return table_track(target, pg, win, ctx, "no cached astrometry")
    lines = obs_lines(path)
    first, last = fo.obs_span(lines)
    base = nbody.step_for(target["regime"])
    a = win.grid(base, max(win.t0_s, first - fo.PAD - base))[0]
    b = win.grid(base, a, min(win.t1_s, win.now_s + 7 * 86400))[-1]
    src = (lines, args_for(lines, pg), ROOT / pg["mpec"]["cache_file"], {path.name: len(lines)})
    try:
        fit = fo.Fit(target["cid"], src, a, b, 120.0, sigma_step=3600.0)
    except (RuntimeError, ValueError) as err:
        return table_track(target, pg, win, ctx, f"fo failed: {err}")
    fit.verify(ctx, a, b, win.now_s)
    lo, hi = sigma_span(fit, first, last, a, b)
    snap = lambda s, up: win.grid(base, s if up else s - base, s + base if up else s)[0 if up else -1]
    lo, hi = snap(lo, True), snap(hi, False)
    chk = {**fit.check(), **page_check(fit, pg, a, b, ctx), "args": fit.args}
    if chk["fit_disagrees"] or hi - lo < base:
        st = table_track(target, pg, win, ctx, "fo fit disagrees with the page" if chk["fit_disagrees"]
                         else "sigma > 1 deg over the whole window")
        st.validation["fit"] = chk
        return st
    cuts = sorted({lo, hi} | {snap(s, s < (first + last) / 2) for s in (first - fo.PAD, last + fo.PAD,
                                                                        first - 86400, last + 86400) if lo < s < hi})
    segs, mid = [], 0.0
    for c0, c1 in zip(cuts[:-1], cuts[1:]):
        if c1 <= c0:
            continue
        near = first - 86400 - base < c0 and c1 < last + 86400 + base
        q = "fit" if first - fo.PAD - base < c0 and c1 < last + fo.PAD + base else "extrap"
        out, m = nbody.segments(fit, win, NEAR_STEP if near else base, c0, c1, f"fo:neocp:{d}", q,
                                "start" if not segs else "continuous", ctx, sigma=fit.sigma)
        segs += out
        mid = max(mid, m)
    trk = tracklets.build(tracklets.parse(lines), fit, ctx, a, b)
    chk["tracklet_rms_arcsec"] = tracklets.rms(trk)
    cut = f"; sigma > 1 deg after {iso(hi)[:16]}Z" if hi < b - base else ""
    return StateTable(cle_id=target["cid"], centre="earth", segments=segs,
                      provenance=[nbody.raw(path), nbody.raw(ROOT / pg["mpec"]["cache_file"]),
                                  nbody.raw(fit.dir / "elements.txt",
                                            command=json.loads((fit.dir / "command.json").read_text())["cmd"])],
                      validation={"midpoint_max_km": mid, "fo_dir": str(fit.dir.relative_to(ROOT)), "fit": chk,
                                  "fo_consistency": fit.consistency,
                                  "obs_first": iso(first), "obs_last": iso(last), "sigma_limit_utc": iso(hi),
                                  "sigma_km_now": float(fit.sigma(win.now_s)) if fit.sigma and lo <= win.now_s <= hi else None},
                      caveat=f"find_orb fit of {len(lines)} NEOCP obs {iso(first)[:16]}..{iso(last)[:16]}Z "
                             f"(rms {chk['rms_arcsec']}\"); extrapolated beyond {iso(last + fo.PAD)[:10]}{cut}")


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


def fit_block(t, st, pg, lines):
    """The §11 "fit" readout of a live object from its StateTable validation and the pseudo-MPEC."""
    v, m = st.validation, pg.get("mpec", {})
    el, chk = m.get("elements", {}), st.validation.get("fit", {})
    first, last = fo.obs_span(lines) if lines else (None, None)
    ep = el.get("epoch_jd_tt")
    return {"n_obs": chk.get("n_obs"), "n_used": chk.get("n_used"), "rms_arcsec": chk.get("rms_arcsec"),
            "arc_h": round((last - first) / 3600, 3) if lines else None,
            "obs_first": iso(first) if lines else None, "obs_last": iso(last) if lines else None,
            "epoch": iso((ep - 2440587.5) * 86400 - fo.TT_UTC) if ep else None, "epoch_jd_tt": ep,
            "elements": el, "page": {"n_obs": el.get("n_obs"), "n_used": el.get("n_used"),
                                     "rms_arcsec": el.get("mean_resid_arcsec"), "created": m.get("created")},
            "geo_score": m.get("comments", {}).get("geo_score"), "geo": bool((pg.get("summary") or {}).get("geo")),
            "flyby": (el.get("e") or 0) > 1, "srp": "-g" in chk.get("args", []), "pluto_url": primary(t)[1],
            "find_orb_ver": m.get("comments", {}).get("find_orb_ver"), "fo_args": chk.get("args"),
            "page_check": {k: chk.get(k) for k in ("fit_disagrees", "dr_page_sv_km", "table_max_sigma_ratio",
                                                    "table_max_sep_arcsec", "dq_sigma", "de_sigma", "di_sigma")},
            "fo_consistency": v.get("fo_consistency"), "sigma_km_now": v.get("sigma_km_now"),
            "sigma_limit_utc": v.get("sigma_limit_utc"), "fallback": v.get("fallback")}


def removed_list(parsed, shown):
    """§11 "removed": objects neocp_live found absent from the NEOCP listings (parsed.json "removed", with the
    listing stamp at which they were first missing) that are not among the live objects written."""
    return [{"desig": d, "removed_utc": r["removed_utc"], "last_listed": r.get("last_listed", ""),
             "reason": "no longer on the NEOCP"}
            for d, r in sorted((parsed.get("removed") or {}).items()) if d not in shown]


def write_json(win, ctx, out=ROOT / "build/neocp.json"):
    """build/neocp.json: live NEOCP objects (§11 neocp shape: catalog entry, Track, fit, tracklets) plus the
    NEOCP / neocpsat tracklets of identified drawn targets ("attached"), from build/states/."""
    from cle import catalog, export
    from cle.validate import regime_now
    parsed = json.loads((CACHE / "parsed.json").read_text())
    states = ROOT / "build/states"
    doc = {"schema": "cle/neocp@1", "built_utc": iso(datetime.now(timezone.utc).timestamp()),
           "neocp_snapshot_utc": parsed["stats"].get("neocp_txt_mtime"), "window": [iso(win.t0_s), iso(win.t1_s)],
           "objects": {}, "attached": {}, "removed": []}
    for t in catalog.targets():
        if t["draw"] != "Y" or not (states / f"{t['cid']}.json").exists():
            continue
        st = StateTable.load(states, t["cid"])
        segs = [x for x in (export.clip(x, win.t0_s, win.t1_s) for x in st.segments) if x is not None]
        if not segs:
            continue
        at = tracklets.track_at(st, ctx)
        kind, ref, stale = primary(t)
        files, lines = neocp_obs(t, win)
        trk = tracklets.build(tracklets.parse(lines), at, ctx, *at.span)
        if kind == "pluto_mpec" and not stale:
            d = desig(t)
            pg = parsed["objects"].get(d, {})
            st.segments = segs
            fit = fit_block(t, st, pg, lines)
            entry = catalog.entry(t, regime_now(st, ctx, win, t["regime"])["regime_now"], True)
            if fit["flyby"]:
                entry["subgroup"] = "flyby"
            if not any(x.t0_s <= win.now_s <= x.t_s[-1] for x in segs):       # no data at now: hidden by default
                entry["default_visible"] = False
            if fit["flyby"]:                    # hyperbolic: a first-obs trail is a straight line across the view
                entry["trail"] = {"past_s": 86400, "future_s": 43200}
            elif fit["obs_first"]:                                            # §9.4: first obs -> now, 1 d ahead
                entry["trail"] = {"past_s": round(win.now_s - parse_iso(fit["obs_first"]) + 0.5 * 86400), "future_s": 86400}
            doc["objects"][d] = {"cid": t["cid"], "catalog": entry, "track": export.track(segs, st.centre),
                                 "fit": fit, "caveat": st.caveat, "tracklets": trk,
                                 "rms_arcsec": tracklets.rms(trk)}
        elif trk:
            doc["attached"][t["cid"]] = {"tracklets": trk, "obs_files": files, "rms_arcsec": tracklets.rms(trk)}
    doc["removed"] = removed_list(parsed, doc["objects"])
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=1, allow_nan=False))
    tmp.replace(out)
    return doc


if __name__ == "__main__":
    if "--json" in sys.argv:
        from cle.context import Context
        from cle.timeutil import window
        doc = write_json(window(), Context())
        print(f"build/neocp.json: {len(doc['objects'])} live objects, {len(doc['attached'])} attached, "
              f"{sum(len(o['tracklets']) for o in doc['objects'].values())} + "
              f"{sum(len(o['tracklets']) for o in doc['attached'].values())} tracklets")
    else:
        nbody.run(build)
