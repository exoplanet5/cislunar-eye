"""Bill Gray TLE adapter (ARCHITECTURE.md §5.5): hex/classical TLEs from ~/tles, strictly inside their # Range.

The TLE used at t is the one whose '# MJD m' header (TT) has m <= t < m + step ('# Ephem range: a b step');
files without MJD headers use the TLE epoch. States come from Bill Gray's get_vect at the TT instant: SDP4 output
precessed back to J2000 (precession only, the inverse of eph2tle), taken as ICRF; skyfield TEME->GCRS is never
applied. Segments break at each TLE switch and share the boundary epoch (join 'continuous', or 'splice' when the
switch jumps > 50 km); jump_km is kept either way. quality=fit.

    python -I src/cle/adapters/bgtle.py <cid> ...        build (and save)
    python -I src/cle/adapters/bgtle.py --check          SMILE / Artemis II / Gaia vs Horizons, plus TT and frame controls
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from cle import nbody
from cle.adapters import primary
from cle.statetable import StateTable
from cle.timeutil import iso, parse_iso

ROOT = nbody.ROOT
TLES = Path.home() / "tles"
GET_VECT = Path.home() / "Desktop/billgray/sat_code/get_vect"
TMP = ROOT / "work/cache/bgtle/tmp"
SPLICE_KM = 50.0
MJD0 = 2400000.5


@lru_cache(maxsize=1)
def extract():
    """scripts/extract_billgray.py (its tle_list.txt parser), imported read-only."""
    spec = importlib.util.spec_from_file_location("extract_billgray", ROOT / "scripts/extract_billgray.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["extract_billgray"] = mod
    spec.loader.exec_module(mod)
    return mod


def entry(name):
    """The (last) tle_list.txt Include of `name`: Range applies to the next Include only (sat_id semantics)."""
    hits = [e for e in extract().parse_tle_list(TLES / "tle_list.txt").entries if e.include == name]
    if not hits:
        raise LookupError(f"{name} is not included in ~/tles/tle_list.txt")
    e = hits[-1]
    return e, (e.range_start.timestamp() if e.range_start else None), (e.range_end.timestamp() if e.range_end else None)


def tle_epoch_mjd_utc(l1):
    yy, doy = int(l1[18:20]), float(l1[20:32])
    year = 2000 + yy if yy < 57 else 1900 + yy
    return parse_iso(f"{year}-01-01T00:00:00Z") / 86400 + 40587 + doy - 1


@lru_cache(maxsize=64)
def read(name):
    """(TLE records, step_d, ephem range) of one file; each record has mjd (TT), name, l1, l2, worst_km."""
    lines = (TLES / name).read_text(errors="replace").splitlines()
    step, span, recs = None, None, []
    mjd = worst = None
    for k, ln in enumerate(lines):
        if ln.startswith("# Ephem range:") and span is None:
            a, b, step = map(float, ln[14:].split()[:3])
            span = (a, b)
        elif ln.startswith("# MJD "):
            mjd = float(ln.split()[2])
        elif ln.startswith("# Worst residual:"):
            worst = float(re.findall(r"[\d.]+", ln)[0])
        elif ln.startswith("1 ") and k + 1 < len(lines) and lines[k + 1].startswith("2 "):
            label = lines[k - 1].strip() if k and not lines[k - 1].startswith("#") else name
            m = mjd if mjd is not None else tle_epoch_mjd_utc(ln) + 69.184 / 86400
            recs.append({"mjd": m, "name": label, "l1": ln.rstrip(), "l2": lines[k + 1].rstrip(), "worst_km": worst})
            mjd = worst = None
    recs.sort(key=lambda r: r["mjd"])
    if step is None:
        gaps = np.diff([r["mjd"] for r in recs])
        step = float(np.median(gaps)) if len(gaps) else 1.0
    if span is None:
        span = (recs[0]["mjd"], recs[-1]["mjd"] + step)
    return recs, step, span


def tle_file(rec):
    import hashlib
    fn = TMP / (hashlib.sha1((rec["l1"] + rec["l2"]).encode()).hexdigest()[:16] + ".tle")
    if not fn.exists():
        TMP.mkdir(parents=True, exist_ok=True)
        tmp = fn.with_suffix(f".{threading.get_ident()}")
        tmp.write_text(f"{rec['name']}\n{rec['l1']}\n{rec['l2']}\n")
        tmp.replace(fn)
    return fn


def get_vect(rec, jd_tt, of_date=False):
    """(6,) J2000 equatorial state, km and km/s, from get_vect at JD (TT)."""
    fn = tle_file(rec)
    args = [str(GET_VECT), str(fn), "-t", f"{jd_tt:.9f}"] + (["-d"] if of_date else [])
    out = subprocess.run(args, capture_output=True, text=True, timeout=30, cwd=str(TMP)).stdout.splitlines()
    for i, ln in enumerate(out):
        if "# Ctr 3 km sec" in ln:
            return np.array([float(x) for x in ln.split()[:3] + out[i + 1].split()[:3]])
    raise RuntimeError(f"get_vect gave no state for {rec['name']} at JD {jd_tt}")


class Track:
    """rv(t_utc_s) from the TLE of each instant (MJD rule), evaluated with get_vect in parallel."""

    def __init__(self, name, ctx, of_date=False, force=None):
        self.name, self.ctx, self.of_date, self.force = name, ctx, of_date, force
        self.recs, self.step, self.span = read(name)
        self.mjd = np.array([r["mjd"] for r in self.recs])
        self.memo = {}

    def mjd_tt(self, t):
        return self.ctx.times(t).tt - MJD0

    def index(self, t):
        m = self.mjd_tt(t)
        k = np.searchsorted(self.mjd, m, side="right") - 1
        ok = (k >= 0) & (m < self.mjd[np.maximum(k, 0)] + self.step + 1e-9)
        if not ok.all():
            raise ValueError(f"{self.name}: no TLE covers some requested epochs")
        return k

    def __call__(self, t, k=None):
        t = np.atleast_1d(np.asarray(t, dtype=float))
        k = self.index(t) if k is None else np.broadcast_to(k, t.shape)
        jd = self.ctx.times(t).tt if self.force is None else self.force(t)
        todo = [(int(i), float(j)) for i, j in zip(k, jd) if (int(i), float(j)) not in self.memo]
        for i in {i for i, _ in todo}:
            tle_file(self.recs[i])
        with ThreadPoolExecutor(max_workers=8) as ex:
            for key, rv in zip(todo, ex.map(lambda kj: get_vect(self.recs[kj[0]], kj[1], self.of_date), todo)):
                self.memo[key] = rv
        return np.array([self.memo[(int(i), float(j))] for i, j in zip(k, jd)])

    def covered(self, a_s=None, b_s=None, range_=(None, None)):
        """[a, b] in UTC seconds inside the file's ephem range, the include Range and [a_s, b_s]."""
        lo = (self.span[0] - 40587) * 86400 - 69.184
        hi = (self.span[1] - 40587) * 86400 - 69.184
        vals_lo = [v for v in (lo, range_[0], a_s) if v is not None]
        vals_hi = [v for v in (hi, range_[1], b_s) if v is not None]
        return max(vals_lo), min(vals_hi)


def build(target, win, ctx):
    _, name, _ = primary(target)
    e, r0, r1 = entry(name)
    trk = Track(name, ctx)
    step = nbody.step_for(target["regime"])
    grid = win.grid(step, *trk.covered(win.t0_s, win.t1_s, (r0, r1)))
    if len(grid) < 2:
        raise ValueError(f"{target['cid']}: {name} Range does not cover the window")
    k0, k1 = trk.index(grid[[0, -1]])
    starts = [(trk.mjd[k] - 40587) * 86400 - 69.184 for k in range(k0, k1 + 1)]
    segs, switches, mid, prev = [], [], 0.0, None
    for j, a, b in nbody.pieces(starts, grid):
        k = k0 + j
        at = lambda t, k=k: trk(t, k)
        jump = float(np.linalg.norm(at(a)[0, :3] - prev(a)[0, :3])) if prev else 0.0
        join = "start" if prev is None else "splice" if jump > SPLICE_KM else "continuous"
        out, m = nbody.segments(at, win, step, a, b, f"bgtle:{name}@MJD{trk.mjd[k]:.1f}", "fit", join, ctx)
        out[0].jump_km = jump
        if prev:
            switches.append({"t_utc": iso(a), "mjd": trk.mjd[k], "jump_km": round(jump, 3)})
        segs += out
        mid, prev = max(mid, m), at
    worst = [r["worst_km"] for r in trk.recs if r["worst_km"] is not None]
    return StateTable(cle_id=target["cid"], centre="earth", segments=segs,
                      provenance=[nbody.raw(TLES / name, range=e.range_raw, tool=str(GET_VECT),
                                            command="get_vect <tle> -t <JD TT>")],
                      validation={"midpoint_max_km": mid, "tle_switches": len(switches),
                                  "switch_jump_max_km": max((s["jump_km"] for s in switches), default=0.0),
                                  "splices": [s for s in switches if s["jump_km"] > SPLICE_KM],
                                  "file_worst_residual_km": max(worst, default=None)},
                      caveat=f"Bill Gray TLE {name} inside its Range {e.range_raw}")


def end_state(name, ctx):
    """(t_utc_s, rv) at the end of the file's Range (or its ephem range), for extrapolation (AM5)."""
    e, r0, r1 = entry(name)
    trk = Track(name, ctx)
    _, b = trk.covered(range_=(r0, r1))
    b = np.floor(b / 60) * 60
    k = trk.index(b - 1.0)
    return b, trk(b, k[0])[0], {"file": name, "range": e.range_raw, "mjd": trk.recs[int(k[0])]["mjd"],
                                "worst_km": trk.recs[int(k[0])]["worst_km"]}


CHECKS = (("SMILE", "26109a.tle", -463, "2026-06-22 06:00", "2026-07-06 00:00"),
          ("Artemis II", "26069a.tle", -1024, "2026-04-03 06:00", "2026-04-10 18:00"),
          ("Gaia", "13074a25.tle", -139479, "2025-01-05 00:00", "2025-01-19 00:00"))


def check(ctx):
    """Bill Gray TLEs against Horizons inside their Range, before the file was made: get_vect J2000 (the
    convention), UTC passed as TT (time-scale control, 69 s) and get_vect of-date through skyfield TEME->GCRS
    (frame control: adds the nutation rotation)."""
    from skyfield.sgp4lib import TEME
    report = {}
    for label, name, hz, a, b in CHECKS:
        t, ref = nbody.hz_vectors(hz, a, b, "1 h", ROOT / "work/cache/bgtle/hz")
        trk = Track(name, ctx)
        k = trk.index(t)
        good = trk(t, k)
        bad_t = Track(name, ctx, force=lambda s: s / 86400 + 2440587.5)(t, k)
        od = Track(name, ctx, of_date=True)(t, k)
        bad_f = np.einsum("jik,kj->ki", TEME.rotation_at(ctx.times(t)), od[:, :3])
        r = {"file": name, "horizons": hz, "span": [a, b],
             "range_km": [round(float(x)) for x in np.linalg.norm(ref[:, :3], axis=1)[[0, -1]]],
             "file_worst_residual_km": max(x["worst_km"] or 0 for x in trk.recs)}
        for key, x in (("get_vect_j2000", good[:, :3]), ("utc_as_tt", bad_t[:, :3]), ("teme_to_gcrs", bad_f)):
            dr = np.linalg.norm(x - ref[:, :3], axis=1)
            r[key] = {"max_km": round(float(dr.max()), 2), "median_km": round(float(np.median(dr)), 2)}
        cos = np.sum(bad_f * good[:, :3], 1) / np.linalg.norm(bad_f, axis=1) / np.linalg.norm(good[:, :3], axis=1)
        r["teme_to_gcrs"]["rotation_arcsec"] = round(float(np.median(np.degrees(np.arccos(np.clip(cos, -1, 1))))) * 3600, 2)
        report[label] = r
    return report


if __name__ == "__main__":
    if "--check" in sys.argv:
        import json
        from cle.context import Context
        from cle.timeutil import window
        print(json.dumps(check(Context()), indent=1))
    else:
        nbody.run(build)
