"""NEOCP adapter (ARCHITECTURE.md §5.6): a trajectory for one NEOCP designation from its 80-column astrometry and Bill
Gray's pseudo-MPEC, and its §11 "fit" readout. The viewer requests NEOCP objects one designation at a time (cle.request,
scripts/serve.py); the scheduled live-NEOCP flow is retired (the viewer's catalog leaves the NEOCP groups out,
catalog.VIEWER_EXCLUDED_GROUPS, and the astrometry of identified catalog targets moved to cle.astrometry).

build(target, win, ctx, pg=None, lines=None, fo_cache=None, lock_timeout=None, use_fo=True) takes the page
({"mpec": cle.pluto.parse_mpec(...) minus obs_lines, plus "cache_file": the saved page}) and the obs lines from the
caller, or, for a master-list row, from work/cache/neocp/parsed.json and obs/<desig>.txt. It tries, in order, and
records the path in validation["mode"] (validation["fallback"]: why the earlier paths were not used):

  fo          local find_orb refit as in cle.adapters.fo (no -c; -h 3 for a geocentric page, -h 0 for a heliocentric
              one; -g 17 when the page fitted an AMR, or for arcs >= 7 d without a page), checked against fo's one-leg
              propagation (fo.Fit.verify; mode "fo_daily" when the table was rebuilt from one-day runs). The track spans
              [first obs - 2 d, max(now, last obs) + 7 d] inside the window and is cut where fo's 1-sigma sky
              uncertainty exceeds 1 deg; 10-minute samples over [first obs - 1 d, last obs + 1 d], the regime step
              elsewhere; quality=fit over [first - 2 d, last + 2 d], extrap beyond. The fit is checked against the page
              (q, e, i in page sigmas; state vector at the page epoch; the 48-h table in units of its sigma). All its
              runs go under one hold of work/fo.lock (TimeoutError after lock_timeout s) and, with fo_cache, into
              <fo_cache>/<label>_E<opt> instead of work/cache/fo/<STAMP>/.
  page_sv     the page's state vector at its elements epoch (TT): relative to the central body for a geocentric fit,
              else heliocentric + the DE440s Sun, integrated with cle.nbody.propagate (Earth + Moon + Sun + J2) over
              [first obs - 1 d, max(now, last obs) + 3 d] inside the window; 10-minute samples and quality=fit over the
              obs arc +/- 1 d, extrap beyond. No covariance: the track ends where the page's 48-h table sigma passes
              1 deg (linear in time from the last obs to the first table row past it). Used when find_orb is missing,
              busy, fails, disagrees with the page, or its sigma exceeds 1 deg everywhere. Scratch checks of 2026-11-01:
              it reproduces Bill Gray's tables to < 1" and the fo tracks to ~ 20 km.
  page_table  last resort: the page's 48-h geocentric astrometric table as positions (spline velocities), extrap.

    python -I src/cle/adapters/neocp.py <cid> ...      build (and save) master-list rows
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from cle import nbody, pluto, tracklets
from cle.adapters import fo, primary
from cle.statetable import Segment, StateTable
from cle.timeutil import iso, parse_iso

ROOT = nbody.ROOT
CACHE = ROOT / "work/cache/neocp"
SIGMA_MAX = 3600.0
NEAR_STEP = 600.0
DAY = 86400.0
FO_AHEAD, SV_AHEAD, SV_PAD = 7 * DAY, 3 * DAY, DAY
MODE = {"fo": "fo", "fo_daily": "fo_daily", "nbody": "fo"}        # fo.Fit.mode -> validation["mode"]
FO_ERRORS = (RuntimeError, ValueError, OSError, subprocess.SubprocessError, IndexError, KeyError)


class Fallback(Exception):
    """find_orb ran but its fit is not used (it disagrees with the page, or its sigma exceeds 1 deg everywhere)."""

    def __init__(self, why, check):
        super().__init__(why)
        self.check = check


def fo_reason(err) -> str:
    """A short, path-free reason for a failed find_orb run: the fallback text reaches the helper's JSON and the UI's
    status line (fo's own messages name run directories under the home directory; they go to stderr instead)."""
    if isinstance(err, subprocess.TimeoutExpired):
        return "timeout"
    if isinstance(err, (OSError, subprocess.SubprocessError)):
        return "did not run"
    return "no fit" if isinstance(err, RuntimeError) else "bad output"


def desig(target):
    _, ref, _ = primary(target)
    return re.search(r"/([^/]+)\.html?$", ref)[1]


def page(d):
    return json.loads((CACHE / "parsed.json").read_text())["objects"].get(d)


def page_file(pg):
    return ROOT / pg["mpec"]["cache_file"]                 # an absolute cache_file stays absolute


def obs_lines(path):
    return [ln.rstrip("\n")[:80] for ln in Path(path).read_text(errors="replace").splitlines() if len(ln) >= 80]


def rel(path):
    path = Path(path)
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


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


def table_check(rv_at, pg, a_s, b_s, ctx):
    """A geocentric trajectory against the pseudo-MPEC's 48-h table (astrometric RA/Dec): max separation in arcsec
    and in units of the table's sigma, over the rows inside [a_s, b_s]."""
    rows = [r for r in pg["mpec"].get("ephemeris", []) if a_s + 60 <= parse_iso(r["t"]) <= b_s]
    if not rows:
        return {}
    t = np.array([parse_iso(r["t"]) for r in rows])
    ra, dec = radec(tracklets.astrometric(rv_at, ctx, t, np.zeros((len(t), 3)))[0])
    d = np.radians([[r["ra_deg"] for r in rows], [r["dec_deg"] for r in rows]])
    sep = np.degrees(np.arccos(np.clip(np.sin(d[1]) * np.sin(np.radians(dec)) + np.cos(d[1])
                                       * np.cos(np.radians(dec)) * np.cos(d[0] - np.radians(ra)), -1, 1))) * 3600
    sig = np.array([r["sig_arcsec"] or np.nan for r in rows])
    ratio = sep / sig
    return {"table_max_sep_arcsec": round(float(sep.max()), 2),
            "table_max_sigma_ratio": round(float(np.nanmax(ratio)), 3) if np.isfinite(ratio).any() else None}


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
    out |= table_check(fit, pg, a_s, b_s, ctx)
    out["fit_disagrees"] = bool(any((out.get(k) or 0) > 1 for k in ("dq_sigma", "de_sigma", "di_sigma"))
                                or (out.get("table_max_sigma_ratio") or 0) > 1)
    return out


def ahead(win, last, span):
    """Track end: `span` past now, or past the last obs when that is later (a page clock behind the obs), in the window."""
    return min(win.t1_s, max(win.now_s, last) + span)


def snapper(win, base):
    """snap(s, up): the first base-grid epoch >= s (up) or the last one <= s."""
    return lambda s, up: win.grid(base, s if up else s - base, s + base if up else s)[0 if up else -1]


def sampled(rv_at, win, ctx, base, lo, hi, first, last, source, pad, sigma=None):
    """Segments of rv_at on [lo, hi] (base-grid epochs): NEAR_STEP over [first - 1 d, last + 1 d], the base step
    elsewhere; quality fit over [first - pad, last + pad], extrap beyond. Returns (segments, midpoint_max_km)."""
    snap = snapper(win, base)
    cuts = sorted({lo, hi} | {snap(s, s < (first + last) / 2) for s in (first - pad, last + pad, first - DAY, last + DAY)
                              if lo < s < hi})
    segs, mid = [], 0.0
    for c0, c1 in zip(cuts[:-1], cuts[1:]):
        if c1 <= c0:
            continue
        near = first - DAY - base < c0 and c1 < last + DAY + base
        q = "fit" if first - pad - base < c0 and c1 < last + pad + base else "extrap"
        out, m = nbody.segments(rv_at, win, NEAR_STEP if near else base, c0, c1, source, q,
                                "start" if not segs else "continuous", ctx, sigma=sigma)
        segs += out
        mid = max(mid, m)
    return segs, mid


@contextmanager
def fo_dirs(run_dir):
    """fo runs go to run_dir (fo.CACHE = its parent, fo.STAMP = its name) instead of work/cache/fo/<STAMP>."""
    if run_dir is None:
        yield
        return
    old = fo.CACHE, fo.STAMP
    fo.CACHE, fo.STAMP = Path(run_dir).parent, Path(run_dir).name
    try:
        yield
    finally:
        fo.CACHE, fo.STAMP = old


def fo_track(target, pg, lines, win, ctx):
    """The find_orb path (module docstring); raises Fallback when its fit is not to be used."""
    d = desig(target)
    first, last = fo.obs_span(lines)
    base = nbody.step_for(target["regime"])
    grid = win.grid(base, max(win.t0_s, first - fo.PAD - base), ahead(win, last, FO_AHEAD))
    if len(grid) < 2:
        raise ValueError("observations outside the window")
    a, b = grid[0], grid[-1]
    src = (lines, args_for(lines, pg), page_file(pg), {"obs": len(lines)})
    fit = fo.Fit(target["cid"], src, a, b, 120.0, sigma_step=3600.0)
    fit.verify(ctx, a, b, win.now_s)
    lo, hi = sigma_span(fit, first, last, a, b)
    snap = snapper(win, base)
    lo, hi = snap(lo, True), snap(hi, False)
    chk = {**fit.check(), **page_check(fit, pg, a, b, ctx), "args": fit.args}
    if chk["fit_disagrees"] or hi - lo < base:
        raise Fallback("find_orb fit disagrees with the page" if chk["fit_disagrees"]
                       else "find_orb sigma > 1 deg over the whole window", chk)
    segs, mid = sampled(fit, win, ctx, base, lo, hi, first, last, f"fo:neocp:{d}", fo.PAD, sigma=fit.sigma)
    trk = tracklets.build(tracklets.parse(lines), fit, ctx, a, b)
    chk["tracklet_rms_arcsec"] = tracklets.rms(trk)
    # what the segments say: quality fit to last + PAD (sampled), extrap beyond, the track cut at hi when sigma > 1 deg;
    # a track cut before last + PAD has no extrapolated part, so the caveat says where it ends instead
    extrap = hi > last + fo.PAD
    tail = (f"; extrapolated beyond {iso(last + fo.PAD)[:10]}" if extrap else "") + (
        "" if hi >= b - base else f"; sigma > 1° after {iso(hi)[:16]}Z" if extrap else f"; track ends {iso(hi)[:16]}Z (sigma > 1°)")
    return StateTable(cle_id=target["cid"], centre="earth", segments=segs,
                      provenance=[nbody.raw(fit.dir / "obs.txt"), nbody.raw(page_file(pg)),
                                  nbody.raw(fit.dir / "elements.txt",
                                            command=json.loads((fit.dir / "command.json").read_text())["cmd"])],
                      validation={"mode": MODE.get(fit.mode, "fo"), "fallback": None, "midpoint_max_km": mid,
                                  "fo_dir": rel(fit.dir), "fit": chk, "fo_consistency": fit.consistency,
                                  "obs_first": iso(first), "obs_last": iso(last), "sigma_limit_utc": iso(hi),
                                  "sigma_km_now": float(fit.sigma(win.now_s))
                                  if fit.sigma and lo <= win.now_s <= hi else None},
                      caveat=f"find_orb fit of {len(lines)} NEOCP obs {iso(first)[:16]}..{iso(last)[:16]}Z "
                             f"(rms {chk['rms_arcsec']}″){tail}")


def page_state(pg, ctx):
    """(t_utc_s, geocentric ICRF state (6,) in km and km/s, 'central' | 'heliocentric') of the pseudo-MPEC's state
    vector at its elements epoch, or None when the page has none. mAU/day -> km/s is v * 1e-3 * AU / 86400."""
    m = pg["mpec"]
    el, cm = m.get("elements", {}), m.get("comments", {})
    if not el.get("epoch_jd_tt"):
        return None
    t = (el["epoch_jd_tt"] - 2440587.5) * DAY - fo.TT_UTC
    kms = 1e-3 * fo.AU / DAY
    centre = pluto.page_center(el)
    if centre in ("earth", "moon") and cm.get("sv_central_au") and cm.get("sv_central_mau_d"):
        rv = np.r_[np.array(cm["sv_central_au"]) * fo.AU, np.array(cm["sv_central_mau_d"]) * kms]
        return t, rv + (ctx.moon_rv([t])[0] if centre == "moon" else 0.0), "central"
    if cm.get("sv_helio_au") and cm.get("sv_helio_mau_d"):
        rv = np.r_[np.array(cm["sv_helio_au"]) * fo.AU, np.array(cm["sv_helio_mau_d"]) * kms]
        return t, rv + ctx.sun_rv([t])[0], "heliocentric"           # object - Earth = (object - Sun) + (Sun - Earth)
    return None


def table_sigma_end(pg, last):
    """When the page's 48-h table sigma passes 1 deg after the last obs: linear between rows, and from (last obs, 0)
    to the first row when that one is already past it; None while it stays below."""
    prev = (last, 0.0)
    for r in pg["mpec"].get("ephemeris", []):
        t, s = parse_iso(r["t"]), r.get("sig_arcsec")
        if s is None or t <= last:
            continue
        if s > SIGMA_MAX:
            return prev[0] + (t - prev[0]) * (SIGMA_MAX - prev[1]) / (s - prev[1])
        prev = (t, s)
    return None


def sv_track(target, pg, lines, win, ctx, why):
    """The page state-vector path (module docstring)."""
    d = desig(target)
    state = page_state(pg, ctx)
    if state is None:
        raise ValueError("no state vector on the pseudo-MPEC")
    t_ep, rv, kind = state
    obs = [ln for ln in lines if fo.is_obs(ln)]
    first, last = fo.obs_span(obs) if obs else (t_ep, t_ep)
    base = nbody.step_for(target["regime"])
    grid = win.grid(base, max(win.t0_s, first - SV_PAD - base), ahead(win, last, SV_AHEAD))
    if len(grid) < 2:
        raise ValueError("observations outside the window")
    a, b = grid[0], grid[-1]
    snap = snapper(win, base)
    hi, end = b, table_sigma_end(pg, last)
    if end is not None and end < b:                           # cut there, but keep the obs arc and one step
        keep = snap(min(max(last + 3600, a + base), b), True)
        hi = min(b, max(snap(max(end, a), False), keep))
    traj = nbody.propagate(ctx, t_ep, rv, a, hi)
    segs, mid = sampled(traj, win, ctx, base, a, hi, first, last, f"pluto_sv:{d}", SV_PAD)
    if not segs:
        raise ValueError("no samples inside the window")
    el = pg["mpec"].get("elements", {})
    chk = {"n_obs": el.get("n_obs"), "n_used": el.get("n_used"), "rms_arcsec": el.get("mean_resid_arcsec"), "args": None,
           **table_check(traj, pg, a, hi, ctx)}
    cut = f"; sigma > 1 deg after {iso(hi)[:16]}Z (neocp2 table)" if hi < b else ""
    return StateTable(cle_id=target["cid"], centre="earth", segments=segs, provenance=[nbody.raw(page_file(pg))],
                      validation={"mode": "page_sv", "fallback": why, "midpoint_max_km": mid, "fit": chk,
                                  "state": {"t_utc": iso(t_ep), "from": kind, "rv": [round(float(x), 6) for x in rv]},
                                  "obs_first": iso(first) if obs else None, "obs_last": iso(last) if obs else None,
                                  "sigma_limit_utc": iso(hi) if hi < b else None, "sigma_km_now": None},
                      caveat=f"propagated from Bill Gray's find_orb state vector of {iso(t_ep)[:16]}Z without "
                             f"covariance ({why}); {len(obs)} obs to {iso(last)[:16]}Z{cut}")


def table_track(target, pg, win, ctx, why):
    """Last resort: the pseudo-MPEC's 48-h geocentric table as positions (spline velocities), quality=extrap."""
    from scipy.interpolate import CubicSpline
    rows = pg["mpec"].get("ephemeris") or []
    t = np.array([parse_iso(r["t"]) for r in rows])
    k = (t >= win.t0_s) & (t <= win.t1_s)
    if k.sum() < 2:
        raise ValueError("the 48-h table is outside the window")
    x = np.array([r["xyz_km"] for r in rows])
    f = CubicSpline(t, x)
    sig = np.array([(r["sig_arcsec"] or np.nan) * r["delta_km"] / 206264.806 for r in rows], dtype=np.float32)
    seg = Segment(t0_s=float(t[k][0]), step_s=float(t[1] - t[0]), r_km=x[k], v_kms=f(t[k], 1), sigma_km=sig[k],
                  source=f"pluto_mpec:{pg['mpec']['desig']}", quality="extrap")
    el = pg["mpec"].get("elements", {})
    return StateTable(cle_id=target["cid"], centre="earth", segments=[seg], provenance=[nbody.raw(page_file(pg))],
                      validation={"mode": "page_table", "fallback": why, "midpoint_max_km": None,
                                  "fit": {"n_obs": el.get("n_obs"), "n_used": el.get("n_used"),
                                          "rms_arcsec": el.get("mean_resid_arcsec"), "args": None},
                                  "sigma_limit_utc": None, "sigma_km_now": None},
                      caveat=f"pseudo-MPEC ephemeris table ({why}); astrometric, not refitted")


def fallback(target, pg, lines, win, ctx, why):
    """page_sv, else page_table; ValueError when neither gives a track."""
    try:
        return sv_track(target, pg, lines, win, ctx, why)
    except (ValueError, RuntimeError) as err:
        sv_err = str(err)
    try:
        return table_track(target, pg, win, ctx, f"{why}; page state vector: {sv_err}")
    except (ValueError, KeyError, IndexError) as err:
        raise ValueError(f"{desig(target)}: no trajectory ({why}; page state vector: {sv_err}; page table: {err})") from None


def build(target, win, ctx, pg=None, lines=None, fo_cache=None, lock_timeout=None, use_fo=True):
    d = desig(target)
    pg = page(d) if pg is None else pg
    if not pg or not pg.get("mpec"):
        raise ValueError(f"{d}: no pseudo-MPEC")
    if lines is None:
        path = CACHE / "obs" / f"{d}.txt"
        lines = obs_lines(path) if path.exists() and path.name in {p.name for p in path.parent.iterdir()} else []
    rejected = None
    if not any(fo.is_obs(ln) for ln in lines):
        why = "no astrometry"
    elif not use_fo:
        why = "find_orb not used"
    elif not fo.FO.exists():
        why = "find_orb not installed"
    else:
        try:
            with fo_dirs(fo_cache), fo.locked(timeout=lock_timeout):
                return fo_track(target, pg, lines, win, ctx)
        except TimeoutError:
            why = f"find_orb busy: fo.lock held > {lock_timeout:g} s"
        except Fallback as err:
            why, rejected = str(err), err.check
        except FO_ERRORS as err:
            why = f"find_orb failed ({fo_reason(err)})"
            print(f"{d}: find_orb failed: {type(err).__name__}: {err}", file=sys.stderr, flush=True)
    st = fallback(target, pg, lines, win, ctx, why)
    if rejected:
        st.validation["fo_rejected"] = rejected
    return st


def fit_block(t, st, pg, lines):
    """The §11 "fit" readout of a NEOCP object: the path (mode) and why (fallback), the fit's numbers (fo's, or the
    page's own for the page paths), the observations drawn (arc, stations), the page, its check, the sigma."""
    v, m = st.validation, pg.get("mpec", {})
    el, chk = m.get("elements", {}), v.get("fit", {})
    obs = [ln for ln in lines or () if fo.is_obs(ln)]
    first, last = fo.obs_span(obs) if obs else (None, None)
    ep = el.get("epoch_jd_tt")
    return {"mode": v.get("mode", "fo"), "n_obs": chk.get("n_obs"), "n_used": chk.get("n_used"),
            "rms_arcsec": chk.get("rms_arcsec"),
            "arc_h": round((last - first) / 3600, 3) if obs else None,
            "arc_d": round((last - first) / DAY, 4) if obs else None,
            "stations": sorted({ln[77:80] for ln in obs}),
            "obs_first": iso(first) if obs else None, "obs_last": iso(last) if obs else None,
            "epoch": iso((ep - 2440587.5) * 86400 - fo.TT_UTC) if ep else None, "epoch_jd_tt": ep,
            "elements": el, "page": {"n_obs": el.get("n_obs"), "n_used": el.get("n_used"),
                                     "rms_arcsec": el.get("mean_resid_arcsec"), "created": m.get("created")},
            "geo_score": m.get("comments", {}).get("geo_score"), "geo": pluto.page_center(el) == "earth",
            "flyby": (el.get("e") or 0) > 1, "srp": "-g" in (chk.get("args") or []), "pluto_url": primary(t)[1],
            "find_orb_ver": m.get("comments", {}).get("find_orb_ver"), "fo_args": chk.get("args"),
            "page_check": {k: chk.get(k) for k in ("fit_disagrees", "dr_page_sv_km", "table_max_sigma_ratio",
                                                    "table_max_sep_arcsec", "dq_sigma", "de_sigma", "di_sigma")},
            "fo_consistency": v.get("fo_consistency"), "sigma_km_now": v.get("sigma_km_now"),
            "sigma_limit_utc": v.get("sigma_limit_utc"), "fallback": v.get("fallback")}


if __name__ == "__main__":
    nbody.run(build)
