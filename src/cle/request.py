"""On-request NEOCP objects (scripts/serve.py, GET /api/neocp/<DESIG>): one temporary designation -> one viewer object.

fetch(desig, cache_dir)   Bill Gray's pseudo-MPEC https://www.projectpluto.com/neocp2/mpecs/<D>.htm (browser-like
                          headers; 404 = no page) and MPC get-obs-neocp (GET with a JSON body; '' = not on the NEOCP)
                          in two threads, 30 s timeouts, one retry. Cached per designation under
                          work/cache/request/<case_safe_name>/ (page.htm, mpc_obs80.txt, obs.txt = every observation
                          seen so far, meta.json) for 10 min, and never across Bill's hourly regeneration at about hh:35.
                          When a source fails, its cached copy is used and the request carries a warning; with no copy
                          of the page the request fails (upstream). Nothing is written for a designation with neither
                          a page nor observations (unknown, or MPC failed before Bill Gray's first page).
build(desig, page_html, obs_text, win, ctx, now_ms)
                          -> {cid, catalog, track, fit, caveat, tracklets, rms_arcsec, request}. The page's and MPC's
                          observations are merged (pluto.astrometric_key); while MPC lists none (the object left the
                          NEOCP, or MPC failed) the ones seen by earlier fetches are kept. The trajectory comes from
                          cle.adapters.neocp.build (find_orb in a per-request run directory work/cache/request/fo/<stamp>
                          under the shared work/fo.lock, waiting at most 20 s; else the page state vector; else the
                          page table), clipped to [t0, t1]; tracklets along it (cle.tracklets). catalog = catalog.entry
                          of a request row: id desig.lower(), name / label the exact-case designation, group
                          "NEOCP · requested", tier 1, drawn, default_visible, chunk None (the track travels in the
                          answer), regime EARTH_NEAR_HELIO for a heliocentric page, else NEOCP_GEO unless the track at now
                          classifies as lunar, SE-L1/L2 or near-Earth heliocentric (validate.regime_now), trail from
                          first obs - 0.5 d to now + 1 d, sources pluto_mpec:<url>[first..last] and neocp_obs:mpc-api,
                          links to the Pluto page and MPC showobsorbs. No page and no observations: not_found, or
                          upstream "MPC unreachable (...)" when MPC failed (the object may be too new for a page). request = {desig, fetched_utc, page_created_utc,
                          n_obs_mpc, n_obs_page, n_obs_used, on_neocp, mode, seconds, cached, warnings, find_orb}.
respond(fetched, win, ctx) -> {"ok": true, "schema": "cle/neocp-request@1", "desig", "built_utc",
                          "window": {"start_utc", "stop_utc"}, "object"}
summary()                 -> GET /api/neocp/summary: Bill Gray's summary.htm ("(geo)" = his best fit is geocentric)
                          with Score / NObs / Arc from MPC neocp.json (summary.htm has no such columns), cached 10 min.
RequestError(code, message): HTTP status CODES[code]; message is one plain line (<= 60 characters) for the UI.
"""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import catalog, export, pluto, tracklets
from .adapters import fo, neocp
from .timeutil import Window, iso
from .validate import load_json, regime_now, write_atomic

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "work/cache/request"
RUNS = CACHE / "fo"
SCHEMA = "cle/neocp-request@1"
MPEC_BASE = "https://www.projectpluto.com/neocp2/mpecs"
SUMMARY_URL = "https://www.projectpluto.com/neocp2/summary.htm"
OBS_API = "https://data.minorplanetcenter.net/api/get-obs-neocp"
MPC_LIST = "https://www.minorplanetcenter.net/Extended_Files/neocp.json"
SHOWOBS = "https://cgi.minorplanetcenter.net/cgi-bin/showobsorbs.cgi?Obj={}&obs=y"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "text/html"}        # projectpluto.com answers 415 without them
TIMEOUT_S, TRIES = 30, 2
MAX_AGE_S = 600
REGEN_MIN = 36                       # Bill Gray regenerates summary.htm and the pages hourly at about hh:35
LOCK_TIMEOUT_S = 20.0
MAX_SPAN_S = 45 * 86400
KEEP_RUNS_S = 6 * 3600               # fo run directories of earlier requests are deleted after this
DAY = 86400.0
GROUP = "NEOCP · requested"
OWN_REGIME = ("LUNAR_ORBIT", "SE_L1", "SE_L2", "EARTH_NEAR_HELIO")
CODES = {"bad_designation": 400, "bad_request": 400, "not_found": 404, "no_orbit": 422, "outside_window": 422,
         "upstream": 502, "busy": 503, "internal": 500}
MSG = {"bad_designation": "not a temporary designation (5–8 letters/digits)",
       "bad_request": "t0 < t1 required (ms), at most 45 d apart",
       "not_found": "not on the NEOCP and no neocp2 page",
       "no_page": "neocp2 page not generated yet (≈ hh:35)",
       "no_elements": "neocp2 page has no orbit",
       "no_track": "no usable orbit on the neocp2 page",
       "outside_window": "no data in the page's ±15 d window",
       "busy": "helper busy with another request"}


class RequestError(Exception):
    """A request that cannot be answered: code (CODES key) and a short message for the UI."""

    def __init__(self, code: str, message: str | None = None):
        super().__init__(message or MSG.get(code, code))
        self.code, self.message, self.status = code, message or MSG.get(code, code), CODES[code]

    def doc(self) -> dict:
        return {"ok": False, "error": self.code, "message": self.message}


@dataclass
class Fetched:
    desig: str
    cache_dir: Path
    page_html: str | None            # None: no pseudo-MPEC (HTTP 404, never cached)
    obs_text: str                    # MPC OBS80 now ('' when not on the NEOCP, or MPC failed)
    kept: list = field(default_factory=list)       # every observation seen so far (cache obs.txt)
    fetched_utc: str = ""
    cached: bool = False             # answered from the 10-min cache
    warnings: list = field(default_factory=list)
    mpc_error: str | None = None     # why MPC's get-obs-neocp failed ('timeout', 'HTTP 503'), None when it answered


# ------------------------------------------------------------------ network (requests is imported on use)

class Down(Exception):
    """An upstream source that did not answer usefully; str() is the short reason ('timeout', 'HTTP 503')."""


def http_get(url, **kw):
    import requests
    last = None
    for k in range(TRIES):
        try:
            r = requests.get(url, timeout=TIMEOUT_S, **kw)
            if r.status_code < 500:
                return r
            last = Down(f"HTTP {r.status_code}")
        except requests.Timeout:
            last = Down("timeout")
        except requests.RequestException as err:
            last = Down("no connection" if isinstance(err, requests.ConnectionError) else type(err).__name__)
        if k + 1 < TRIES:
            time.sleep(1.0)
    raise last


def get_page(desig):
    """The pseudo-MPEC's HTML, or None when Bill Gray has none (404)."""
    r = http_get(f"{MPEC_BASE}/{desig}.htm", headers=HEADERS)
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        raise Down(f"HTTP {r.status_code}")
    return r.content.decode("utf-8", errors="replace")


def get_obs(desig):
    """MPC's OBS80 text for a designation on the NEOCP ('' when it is not, or no longer, listed)."""
    r = http_get(OBS_API, json={"trksubs": [desig], "output_format": ["OBS80"]})
    if r.status_code != 200:
        raise Down(f"HTTP {r.status_code}")
    try:
        js = r.json()
    except ValueError:
        raise Down("bad JSON") from None
    return (js[0].get("OBS80") or "") if isinstance(js, list) and js and isinstance(js[0], dict) else ""


def get_summary():
    r = http_get(SUMMARY_URL, headers=HEADERS)
    if r.status_code != 200:
        raise Down(f"HTTP {r.status_code}")
    return r.content.decode("utf-8", errors="replace")


def get_mpc_list():
    r = http_get(MPC_LIST)
    if r.status_code != 200:
        raise Down(f"HTTP {r.status_code}")
    try:
        js = r.json()
    except ValueError:
        raise Down("bad JSON") from None
    return js if isinstance(js, list) else []


def outcome(future):
    """(value, None) or (None, short reason)."""
    try:
        return future.result(), None
    except Down as err:
        return None, str(err)
    except Exception as err:                                        # noqa: BLE001 - a stubbed or broken getter
        return None, type(err).__name__


# ------------------------------------------------------------------ cache

def fresh(fetched_s, now_s, max_age_s=MAX_AGE_S) -> bool:
    """Younger than max_age_s and from the same hourly regeneration slot (hh:36 .. hh+1:35)."""
    if fetched_s is None or not 0 <= now_s - fetched_s < max_age_s:
        return False
    return (fetched_s - REGEN_MIN * 60) // 3600 == (now_s - REGEN_MIN * 60) // 3600


def cache_dir_for(desig, root: Path = CACHE) -> Path:
    """work/cache/request/<desig>, or <desig>~<case mask> when a different-case designation got there first."""
    root.mkdir(parents=True, exist_ok=True)
    return root / pluto.case_safe_name(desig, root)


def read_text(path: Path):
    try:
        return path.read_text(errors="replace")
    except OSError:
        return None


def fetch(desig, cache_dir=None, *, max_age_s=MAX_AGE_S, now_s=None, getters=None) -> Fetched:
    """The page and MPC's observations of one designation (module docstring); getters = (page, obs) for tests."""
    if not pluto.valid_desig(desig):
        raise RequestError("bad_designation")
    cdir = Path(cache_dir) if cache_dir else cache_dir_for(desig)
    now_s = time.time() if now_s is None else now_s
    meta = load_json(cdir / "meta.json", {})
    meta = meta if isinstance(meta, dict) and meta.get("desig") == desig else {}
    old_page = read_text(cdir / "page.htm") if meta else None
    kept = pluto.obs80_lines(read_text(cdir / "obs.txt") or "") if meta else []
    if meta and fresh(meta.get("fetched_s"), now_s, max_age_s) and not meta.get("warnings"):
        return Fetched(desig, cdir, old_page if meta.get("page") else None, read_text(cdir / "mpc_obs80.txt") or "",
                       kept, meta.get("fetched_utc", ""), cached=True, mpc_error=meta.get("mpc_error"))
    page_getter, obs_getter = getters or (get_page, get_obs)
    with ThreadPoolExecutor(2) as ex:
        fp, fm = ex.submit(page_getter, desig), ex.submit(obs_getter, desig)
    page_html, page_err = outcome(fp)
    obs_text, mpc_err = outcome(fm)
    warnings = []
    if page_err:
        if old_page is None:
            raise RequestError("upstream", f"MPC and neocp2 unreachable ({page_err})" if mpc_err
                               else f"neocp2 unreachable ({page_err})")
        page_html = old_page
        warnings.append(f"neocp2 unreachable ({page_err}): page of {meta.get('fetched_utc', '')[:16]}Z")
    elif page_html is None and old_page is not None:
        page_html = old_page
        warnings.append("neocp2 page gone: cached copy")
    if mpc_err:
        obs_text = ""
        warnings.append(f"MPC unreachable ({mpc_err})")
    obs_text = obs_text or ""
    page_lines = pluto.parse_mpec(page_html, desig)["obs_lines"] if page_html else []
    kept = pluto.merge_obs(page_lines, pluto.obs80_lines(obs_text), kept)
    if page_html is None and not kept:             # nothing to remember: no cache directory for unknown designations
        return Fetched(desig, cdir, None, obs_text, [], iso(now_s), warnings=warnings, mpc_error=mpc_err)
    if page_html is not None and not page_err:
        write_atomic(cdir / "page.htm", page_html)
    if not mpc_err:
        write_atomic(cdir / "mpc_obs80.txt", obs_text)
    if kept:
        write_atomic(cdir / "obs.txt", "\n".join(kept) + "\n")
    meta = {"desig": desig, "fetched_utc": iso(now_s), "fetched_s": now_s, "page": page_html is not None,
            "page_error": page_err, "mpc_error": mpc_err, "warnings": warnings}
    write_atomic(cdir / "meta.json", json.dumps(meta, indent=1))
    return Fetched(desig, cdir, page_html, obs_text, kept, iso(now_s), warnings=warnings, mpc_error=mpc_err)


# ------------------------------------------------------------------ window, result cache key

def window_from(t0_ms, t1_ms, now_ms=None, wall_s=None) -> Window:
    """The page's data window (ms since 1970); now: the given one, else the wall clock, clamped into it."""
    try:
        t0, t1 = float(t0_ms) / 1000, float(t1_ms) / 1000
        now = None if now_ms in (None, "") else float(now_ms) / 1000
    except (TypeError, ValueError):
        raise RequestError("bad_request") from None
    if not all(math.isfinite(x) and 0 < x < 4.1e9 for x in (t0, t1) + (() if now is None else (now,))) \
            or t1 <= t0 or t1 - t0 > MAX_SPAN_S:
        raise RequestError("bad_request")
    now = (time.time() if wall_s is None else wall_s) if now is None else now
    return Window(now_s=min(max(now, t0), t1), t0_s=t0, t1_s=t1)


def observations(page_lines, mpc_lines, kept):
    """The lines a build uses: page + MPC, plus the earlier ones while MPC lists none."""
    return pluto.merge_obs(page_lines, mpc_lines, () if pluto.optical(mpc_lines) else kept)


def cache_key(fetched: Fetched, win: Window) -> tuple:
    """(desig, t0, t1, the page's Created stamp, sha256 of the observations used)."""
    p = pluto.parse_mpec(fetched.page_html, fetched.desig) if fetched.page_html else {}
    lines = observations(p.get("obs_lines", []), pluto.obs80_lines(fetched.obs_text), fetched.kept)
    sha = hashlib.sha256("\n".join(lines).encode()).hexdigest()
    return fetched.desig, export.ms(win.t0_s), export.ms(win.t1_s), p.get("created"), sha


# ------------------------------------------------------------------ build

def fmt(x, nd):
    return "" if x is None or not math.isfinite(x) else f"{x:.{nd}f}"


def target_row(desig, p, regime, first, last) -> dict:
    """A master-list-like row for catalog.entry and the neocp adapter."""
    el = p["elements"]
    centre = pluto.page_center(el)
    geo = pluto.derive_geocentric(el) if centre == "earth" else {}
    if centre == "sun":
        geo = {k: el[s] * pluto.AU_KM for k, s in (("a_km", "a"), ("perigee_km", "q"), ("apogee_km", "Q")) if el.get(s)}
    url = f"{MPEC_BASE}/{desig}.htm"
    span = f"[{iso(first)[:10]}..{iso(last)[:10]}]"
    ep = el.get("epoch_jd_tt")
    return {"cid": desig.lower(), "name": desig, "label": desig, "aliases": "", "norad": "", "cospar": "",
            "horizons_id": "", "group": GROUP, "obj_type": "OTHER", "regime": regime, "status": "unknown",
            "status_date": iso(last)[:10], "tier": 1, "tier_reason": "requested from the NEOCP", "draw": "Y",
            "default_visible": True, "caveat": "", "chunk": catalog.CHUNK.get(regime, "helio"), "centre": "earth",
            "staleness": {"flag": None, "age_d": None}, "left_neocp": None,
            "primary_ephem": f"pluto_mpec:{url}{span}",
            "sources": [{"ephem_sources": f"pluto_mpec:{url}{span};neocp_obs:mpc-api"}],
            "elem_epoch": iso((ep - 2440587.5) * DAY - fo.TT_UTC) if ep else "", "frame_center": centre,
            "a_km": fmt(geo.get("a_km"), 1), "e": fmt(el.get("e"), 6), "incl_deg": fmt(el.get("incl"), 4),
            "perigee_km": fmt(geo.get("perigee_km"), 1), "apogee_km": fmt(geo.get("apogee_km"), 1),
            "period_d": fmt(geo.get("period_d"), 4) if centre == "earth" else "",
            "notes": " | ".join(p.get("header_text") or [])}


def new_run_dir(root: Path = RUNS) -> Path:
    """work/cache/request/fo/<UTC stamp>[_k]: this request's page copy and find_orb runs (short: fo's -O limit)."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for k in range(1000):
        d = root / (stamp if k == 0 else f"{stamp}_{k}")
        try:
            d.mkdir(parents=True)
            return d
        except FileExistsError:
            continue
    raise RuntimeError(f"no free run directory under {root}")


def prune_runs(root: Path = RUNS, keep: Path | None = None, max_age_s=KEEP_RUNS_S):
    for d in root.iterdir() if root.is_dir() else ():
        try:
            if d != keep and d.is_dir() and time.time() - d.stat().st_mtime > max_age_s:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def build(desig, page_html, obs_text, win, ctx, now_ms=None, *, kept=(), run_dir=None, runs: Path = RUNS, use_fo=True,
          lock_timeout=LOCK_TIMEOUT_S, fetched_utc=None, cached=False, warnings=(), started=None,
          mpc_error=None) -> dict:
    """The viewer object for one designation (module docstring); run_dir: find_orb's, else a new one under runs
    (made only once the page passed its checks). Raises RequestError."""
    t_start = time.time() if started is None else started
    if not pluto.valid_desig(desig):
        raise RequestError("bad_designation")
    if now_ms is not None:
        win = Window(now_s=min(max(float(now_ms) / 1000, win.t0_s), win.t1_s), t0_s=win.t0_s, t1_s=win.t1_s)
    p = pluto.parse_mpec(page_html, desig) if page_html else None
    page_lines = p["obs_lines"] if p else []
    mpc_lines = pluto.obs80_lines(obs_text)
    lines = observations(page_lines, mpc_lines, pluto.obs80_lines("\n".join(kept)))
    obs = [ln for ln in lines if fo.is_obs(ln)]
    if p is None:
        if obs:
            raise RequestError("no_orbit", MSG["no_page"])
        if mpc_error:                        # MPC failed and Bill Gray has no page (yet): not evidence of "unknown"
            raise RequestError("upstream", f"MPC unreachable ({mpc_error})")
        raise RequestError("not_found")
    el = p["elements"]
    if not el.get("epoch_jd_tt") or el.get("e") is None:
        raise RequestError("no_orbit", f"fit failed: {len(obs)} obs, need ≥ 3" if len(obs) < 3 else MSG["no_elements"])
    if obs:
        first, last = fo.obs_span(obs)
        if last < win.t0_s or first > win.t1_s:
            raise RequestError("outside_window")
    else:                                     # astrometry redacted and not (or no longer) on MPC's list: the orbit only
        first = last = (el["epoch_jd_tt"] - 2440587.5) * DAY - fo.TT_UTC
    regime = "EARTH_NEAR_HELIO" if pluto.page_center(el) == "sun" else "NEOCP_GEO"
    row = target_row(desig, p, regime, first, last)
    run_dir = Path(run_dir) if run_dir else new_run_dir(runs)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "page.htm").write_text(page_html)
    pg = {"mpec": {k: v for k, v in p.items() if k != "obs_lines"} | {"cache_file": str(run_dir / "page.htm")},
          "summary": None}
    try:
        st = neocp.build(row, win, ctx, pg=pg, lines=lines, fo_cache=run_dir, lock_timeout=lock_timeout, use_fo=use_fo)
    except ValueError as err:
        raise RequestError("outside_window" if "outside the window" in str(err) else "no_orbit",
                           None if "outside the window" in str(err) else MSG["no_track"]) from None
    segs = [x for x in (export.clip(s, win.t0_s, win.t1_s) for s in st.segments) if x is not None]
    if not segs:
        raise RequestError("outside_window")
    st.segments = segs
    at = tracklets.track_at(st, ctx)
    trk = tracklets.build(tracklets.parse(lines), at, ctx, *at.span)
    fit = neocp.fit_block(row, st, pg, lines)
    rnow = regime_now(st, ctx, win, regime)
    if regime == "NEOCP_GEO" and rnow.get("classified") in OWN_REGIME:
        regime = row["regime"] = rnow["classified"]
        rnow = regime_now(st, ctx, win, regime)
    row["caveat"] = st.caveat
    entry = catalog.entry(row, rnow["regime_now"], True)
    entry |= {"chunk": None, "trail": {"past_s": max(0, round(win.now_s - first + 0.5 * DAY)), "future_s": round(DAY)},
              "links": entry["links"] + [{"label": "MPC NEOCP", "url": SHOWOBS.format(desig)}]}
    n_mpc, n_page = len(pluto.optical(mpc_lines)), len(pluto.optical(page_lines))
    notes = list(warnings) + ([fit["fallback"]] if fit["fallback"] else []) \
        + (["neocp2 astrometry redacted"] if p.get("astrometry_redacted") else [])
    rms = tracklets.rms(trk)
    return {"cid": row["cid"], "catalog": entry, "track": export.track(segs, st.centre), "fit": fit, "caveat": st.caveat,
            "tracklets": trk, "rms_arcsec": None if rms is None else round(rms, 3),
            "request": {"desig": desig, "fetched_utc": fetched_utc or iso(time.time()),
                        "page_created_utc": p.get("created"), "n_obs_mpc": n_mpc, "n_obs_page": n_page,
                        "n_obs_used": len(obs), "on_neocp": n_mpc > 0, "mode": fit["mode"],
                        "seconds": round(time.time() - t_start, 2), "cached": cached, "warnings": notes,
                        "find_orb": bool(use_fo and fo.FO.exists())}}


def respond(fetched: Fetched, win: Window, ctx, *, use_fo=True, lock_timeout=LOCK_TIMEOUT_S, runs: Path = RUNS,
            started=None) -> dict:
    """The 200 document for a fetched designation; the run directory of this request (made by build once the page
    passed its checks) is kept KEEP_RUNS_S, like every other one under runs."""
    try:
        obj = build(fetched.desig, fetched.page_html, fetched.obs_text, win, ctx, kept=fetched.kept, runs=runs,
                    use_fo=use_fo, lock_timeout=lock_timeout, fetched_utc=fetched.fetched_utc, cached=fetched.cached,
                    warnings=fetched.warnings, started=started, mpc_error=fetched.mpc_error)
    finally:
        prune_runs(runs)
    return {"ok": True, "schema": SCHEMA, "desig": fetched.desig, "built_utc": iso(time.time()),
            "window": {"start_utc": iso(win.t0_s), "stop_utc": iso(win.t1_s)}, "object": obj}


# ------------------------------------------------------------------ summary

def num(x, kind=float):
    try:
        v = kind(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def summary(cache_root: Path = CACHE, *, max_age_s=MAX_AGE_S, now_s=None, getters=None) -> dict:
    """GET /api/neocp/summary (module docstring); getters = (summary.htm, MPC neocp.json) for tests."""
    d = cache_root / "_summary"                     # '_' keeps it apart from any designation's directory
    now_s = time.time() if now_s is None else now_s
    meta = load_json(d / "meta.json", {})
    ok = isinstance(meta, dict) and fresh(meta.get("fetched_s"), now_s, max_age_s)
    text = read_text(d / "summary.htm") if ok else None
    if text is not None:
        mpc = load_json(d / "neocp.json", [])
    else:
        sum_getter, list_getter = getters or (get_summary, get_mpc_list)
        with ThreadPoolExecutor(2) as ex:
            fs, fl = ex.submit(sum_getter), ex.submit(list_getter)
        text, err = outcome(fs)
        mpc, _ = outcome(fl)
        if text is None:
            raise RequestError("upstream", f"neocp2 summary unreachable ({err})")
        mpc = mpc if isinstance(mpc, list) else []
        meta = {"fetched_utc": iso(now_s), "fetched_s": now_s}
        write_atomic(d / "summary.htm", text)
        write_atomic(d / "neocp.json", json.dumps(mpc))
        write_atomic(d / "meta.json", json.dumps(meta))
    parsed = pluto.parse_summary(text)
    listed = {r.get("Temp_Desig"): r for r in mpc if isinstance(r, dict)}
    objects = {}
    for desig, r in parsed["rows"].items():
        m = listed.get(desig, {})
        score, n_obs = num(m.get("Score")), num(m.get("NObs"))
        objects[desig] = {"geo": r["geo"], "moid_au": r["moid_au"], "score": None if score is None else int(round(score)),
                          "n_obs": None if n_obs is None else int(round(n_obs)), "arc_d": num(m.get("Arc"))}
    return {"ok": True, "regenerated_utc": parsed["regenerated"], "fetched_utc": meta.get("fetched_utc"), "objects": objects}
