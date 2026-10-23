"""find_orb adapter for neocpsat fits (ARCHITECTURE.md §5.4).

A neocpsat_fit reference is a page in ../neocpsat/mpecs/ (its Astrometry section names the obs, which are taken
back from ../neocpsat/data/*.txt, i.e. the member designations' files concatenated) or a refit directory
work/cache/neocpsat_fo/<desig>/ (its obs.txt and fo_args.txt). Local fo refits them (-c <page desig>, -h 0 when
the page is heliocentric else -h 3, -g 17 when the page fitted an AMR, -i) in a fresh -O directory under
work/cache/fo/<stamp>/, serialised by work/fo.lock, with a copy of ~/.find_orb/environ.dat that overrides
EPHEM_START (TT for vectors), EPHEM_STEPS, VECTOR_OPTS=0,AU,86400 (J2000 equator km, km/s) and REDACTED.
`-E 0 -C 500` writes geocentric geometric state vectors with TT epochs, which are converted to UTC; a second run
with -E 16 gives the 1-sigma sky uncertainty, sigma_km = sigma" x delta / 206265. Every fit is first run with a
1-row table (the differential correction is the cost; a window table adds 0.1-0.7 s and up to 3 MB): candidates
are accepted or rejected on that, checked against the newest batch's later observations on a short table over
their arc, and only usable ones get the -E 16 run; the chosen fit alone gets the window table (2 min geocentric,
30 min heliocentric), Hermite-resampled on the regime grid: quality=fit over [first obs - 2 d, last obs + 2 d],
extrap elsewhere. The table is checked against fo's own one-leg propagation at window start, now and end
(Fit.verify); where they differ by > 1 km (fo's multi-step tables drift roughly as the square of their length in
some cases) the window is rebuilt from one-day fo runs. The legs and the one-day runs go through a thread pool
(WORKERS) under one hold of fo.lock; their outputs do not depend on the order or the pool size. A candidate fit is
rejected when unbound (geocentric regimes), not accepted, or more than 1 deg off the newest batch's later
observations; refits that miss their page are retried with -y 30. fo's automatic fit stops after four full
improvement steps or a quarter second of wall time (elem_out.cpp), so every run adds STEPS fixed ones (-y) and the
solution no longer depends on machine load: without them the 313-obs Falcon Heavy fit lands in one of several
minima ~300 km apart, with them it repeats to ~0.05 km (bitwise only where the automatic steps finish in time,
i.e. the NEOCP-class fits). Every candidate's verdict is memoised in
work/cache/fo/verdicts.json under verdict_key() (its obs lines, fo arguments and the newest batch's obs keys, so
any new observation changes it): known-rejected candidates are not refitted until new observations arrive, and
accepted ones rerun with their recorded arguments (fo has no ephemeris-only mode: each run fits, then writes -E).
Run labels are kept under fo's 93-character -O path limit by short(); probes, legs, one-day chunks and short tables
are scratch runs whose fo side files are deleted at once (run(scratch=True), KEEP), and an extrapolation's epoch fit
is labelled <cid>_epoch (epoch_state) so pool workers sharing one STAMP never replace each other's run directory.

    python -I src/cle/adapters/fo.py <cid> ...         build (and save)
"""
from __future__ import annotations

import fcntl
import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import numpy as np
from scipy.interpolate import CubicHermiteSpline

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from cle import nbody
from cle.adapters import primary
from cle.statetable import StateTable
from cle.timeutil import iso

ROOT = nbody.ROOT
FO = Path("/Users/mickey/Desktop/billgray/local/bin/fo")
NEO = ROOT.parent / "neocpsat"
CACHE = ROOT / "work/cache/fo"
VERDICTS = CACHE / "verdicts.json"
KEEP_DAYS = 90                   # verdicts not used for this long are dropped
LOCK = ROOT / "work/fo.lock"
ENVIRON = Path.home() / ".find_orb/environ.dat"
AU = 149597870.7
TT_UTC = 69.184                  # TT - UTC, s (TAI - UTC = 37 s since 2017; no leap second in the window)
PAD = 2 * 86400
HELIO = ("SE_L1", "SE_L2", "EARTH_NEAR_HELIO")
STAMP = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
WORKERS = 8                      # concurrent fo runs of one fit inside one lock hold (legs, one-day chunks)
PATH_MAX = 93                    # fo truncates a longer -O path (strlcpy overflow, rc 255)
STEPS = 10                       # -y: full improvement steps after fo's automatic (wall-clock limited) ones


def jd(t_s, tt=False):
    return np.asarray(t_s) / 86400 + 2440587.5 + (TT_UTC / 86400 if tt else 0)


def obs_time(line):
    y, m, d = int(line[15:19]), int(line[20:22]), float(line[23:32])
    return datetime(y, m, 1, tzinfo=timezone.utc).timestamp() + (d - 1) * 86400


def obs_key(line):
    return line[15:32].strip(), line[32:56].replace(" ", ""), line[77:80]


def is_obs(line):
    return len(line) >= 80 and re.match(r"\d{4} \d\d \d\d\.\d", line[15:27]) is not None and line[14] not in "sv"


def page_text(path):
    return html.unescape(re.sub(r"<[^>]+>", "", Path(path).read_text(errors="replace")))


@lru_cache(maxsize=1)
def data_index():
    """astrometric key -> (file, 80-col line) over ../neocpsat/data/*.txt, plus their satellite/roving 2nd lines."""
    idx = {}
    for f in sorted((NEO / "data").glob("*.txt")):
        lines = f.read_text(errors="replace").splitlines()
        for k, ln in enumerate(lines):
            if is_obs(ln):
                extra = [lines[k + 1]] if k + 1 < len(lines) and lines[k + 1][14:15] in ("s", "v") else []
                idx.setdefault(obs_key(ln), (f.name, [ln[:80]] + [x[:80] for x in extra]))
    return idx


def page_obs(path):
    """80-col lines of a pseudo-MPEC's Astrometry section."""
    text = page_text(path)
    text = text.split("Astrometry:", 1)[1] if "Astrometry:" in text else text
    return [ln[:80] for ln in text.splitlines() if is_obs(ln)]


def elements(text):
    """The find_orb elements block of an elements.txt / pseudo-MPEC: a, e, i with 1-sigma, epoch, obs, fit."""
    b = text[text.find("Orbital elements"):] if "Orbital elements" in text else text
    def num(rx):
        m = re.search(rx, b, re.M)
        return (float(m.group(1)), float(m.group(2)) if m.lastindex > 1 and m.group(2) else None) if m else (None, None)
    out = {"helio": bool(re.search(r"^\s*Perihelion", b, re.M)) or "J2000 ecliptic" in b,
           "a": num(r"^a\s*([\d.]+)(?:\s*\+/-\s*([\d.e+-]+))?"), "e": num(r"^e\s*([\d.]+)(?:\s*\+/-\s*([\d.e+-]+))?"),
           "i": num(r"Incl\.\s+([\d.]+)(?:\s*\+/-\s*([\d.e+-]+))?"),
           "q": num(r"^q\s*([\d.]+)(?:\s*\+/-\s*([\d.e+-]+))?"),
           "amr": num(r"AMR\s+([-\d.e]+)(?:\s*\+/-\s*([\d.e+-]+))?")}
    m = re.search(r"Epoch\s+(\d{4}) (\w{3})\.? +([\d.]+) TT", b)
    if m:
        mon = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"].index(m[2]) + 1
        out["epoch_tt_s"] = datetime(int(m[1]), mon, 1, tzinfo=timezone.utc).timestamp() + (float(m[3]) - 1) * 86400
    m = re.search(r"(?:From (\d+)|(\d+) of (\d+)) observations", b)
    if m:
        out["n_used"], out["n_obs"] = (int(m[1]), int(m[1])) if m[1] else (int(m[2]), int(m[3]))
    m = re.search(r"mean residual (\d*)\"\.(\d+)", b)
    out["rms_arcsec"] = float(f"{m[1] or 0}.{m[2]}") if m else None
    return out


def page_args(el, desig):
    return ["-c", desig, "-h", "0" if el["helio"] else "3"] + (["-g", "17"] if el["amr"][1] else [])


def resolve(ref):
    """(obs lines, fo args, page path, members) for a neocpsat_fit reference '<path>@<date>'."""
    path = (ROOT / ref.split("@")[0]).resolve()
    if path.name == "elements.txt":
        lines = [ln[:80] for ln in (path.parent / "obs.txt").read_text(errors="replace").splitlines() if len(ln) >= 80]
        stamp = path.parent / "fo_args.txt"
        args = stamp.read_text().split() if stamp.exists() else page_args(elements(path.read_text()), path.parent.name)
        return lines, args, path, {path.parent.name: len(lines)}
    idx = data_index()
    lines, members = [], Counter()
    for ln in page_obs(path):
        f, got = idx.get(obs_key(ln), (None, [ln]))
        members[f or "page"] += 1
        lines += got
    desig = Counter(ln[5:12].strip() or ln[:5].strip() for ln in lines if is_obs(ln)).most_common(1)[0][0]
    return lines, page_args(elements(page_text(path)), desig), path, dict(members)


_hold = threading.Lock(), [0, None, 0.0]  # depth, file and summed wait (s) of this process's hold on LOCK


@contextmanager
def locked():
    """work/fo.lock, taken once per process: nested uses and the pool threads of one hold share it, so runs stay
    serialised against other processes (the hourly against the daily build) but one fit's pool runs together."""
    mutex, st = _hold
    with mutex:
        if st[0] == 0:
            LOCK.parent.mkdir(parents=True, exist_ok=True)
            st[1] = open(LOCK, "w")
            t = time.time()
            fcntl.flock(st[1], fcntl.LOCK_EX)
            st[2] += time.time() - t
        st[0] += 1
    try:
        yield
    finally:
        with mutex:
            st[0] -= 1
            if st[0] == 0:
                fcntl.flock(st[1], fcntl.LOCK_UN)
                st[1].close()


def lock_wait():
    """Seconds this process has spent blocked on LOCK behind another process's run; cli.build_one reports it apart
    from a target's own work (states' pool workers queue on it)."""
    return _hold[1][2]


def short(label, room=7):
    """`label`, or its head + 8 sha chars when a run path (label + a suffix of up to `room` chars, e.g. _d00_E0)
    under CACHE/STAMP would exceed PATH_MAX; left alone where no label fits (long scratch paths)."""
    budget = PATH_MAX - len(str(CACHE / STAMP)) - 1 - room
    if len(label) <= budget or budget < 10:
        return label
    return label[:budget - 9].rstrip("_-") + "_" + hashlib.sha256(label.encode()).hexdigest()[:8]


def environ(path, start_jd, n, step):
    over = {"EPHEM_START": f"{start_jd:.9f}", "EPHEM_STEPS": f"{n} {step}", "EPHEM_STEP_SIZE": step.rstrip("dhms"),
            "VECTOR_OPTS": f"0,{AU},86400", "REDACTED": ""}
    out, seen = [], set()
    for ln in ENVIRON.read_text().splitlines():
        key = ln.split("=", 1)[0] if "=" in ln and not ln.startswith(" ") else None
        if key in over:
            out.append(f"{key}={over[key]}")
            seen.add(key)
        else:
            out.append(ln)
    path.write_text("\n".join(out + [f"{k}={v}" for k, v in over.items() if k not in seen]) + "\n")


KEEP = {"obs.txt", "elements.txt", "command.json", "fo_stdout.txt", "ephem.txt"}   # all a scratch run leaves


def run(label, lines, args, t0_s, n, step_s, option="0", scratch=False):
    """One fo run in a fresh -O directory: fit + ephemeris (-E option, -C 500) from UTC t0 over n steps; STEPS extra
    full improvement steps (-y) unless `args` carry their own. scratch (probes, legs, one-day chunks, short tables):
    fo's side files (total.json, elements.json, covar.txt, ... ~160 KB a run, nothing reads them back) are deleted
    at once, as prune does later for every unreferenced run; the window and sigma tables keep them."""
    d = CACHE / STAMP / f"{label}_E{option}"
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    (d / "obs.txt").write_text("\n".join(lines) + "\n")
    step = f"{step_s / 60:g}m" if step_s < 3600 else f"{step_s / 3600:g}h"
    environ(d / "fo_env.dat", float(jd(t0_s, tt=option == "0")), n, step)
    cmd = [str(FO), str(d / "obs.txt"), *args, *([] if "-y" in args else ["-y", str(STEPS)]), "-i", "-O", str(d),
           "-C", "500", "-E", option, "-e", str(d / "ephem.txt"), "-D", str(d / "fo_env.dat")]
    with locked():
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, cwd=d)    # fo writes alt_eph.txt to cwd
    (d / "fo_stdout.txt").write_text(r.stdout + r.stderr)
    (d / "command.json").write_text(json.dumps({"cmd": cmd, "returncode": r.returncode, "t0_utc": iso(t0_s),
                                                "n": n, "step_s": step_s}, indent=1))
    if scratch:
        for f in d.iterdir():
            if f.is_file() and f.name not in KEEP:
                f.unlink()
    return d


def vectors(d, t0_s, n, step_s):
    """(t_utc_s, (n, 6)) from an -E 0 run; epochs rebuilt from the header (rows print JD to 1e-5 d only)."""
    rows = (d / "ephem.txt").read_text().splitlines()
    head = rows[0].split()
    if abs(float(head[0]) - float(jd(t0_s, tt=True))) > 2e-5 or "Geocentric" not in rows[0]:
        raise ValueError(f"unexpected fo vector header in {d}: {rows[0]}")
    x = np.array([[float(v) for v in r.split()[1:7]] for r in rows[1:] if r.strip() and not r.startswith("#")])
    if len(x) != n:
        raise ValueError(f"fo wrote {len(x)} of {n} vector rows in {d}")
    return t0_s + step_s * np.arange(n), x


def hermite(t, x):
    """A (t, (n, 6)) vector table as a callable: (m, 6) at UTC epochs, cubic Hermite in position and velocity."""
    pos = CubicHermiteSpline(t, x[:, :3], x[:, 3:])
    return lambda tt: np.hstack([pos(tt), pos(tt, 1)])


def sky_sigma(text):
    m = re.match(r"^([\d.]+)([d'\"]?)$", text)
    return float(m[1]) * {"": 1, '"': 1, "'": 60, "d": 3600}[m[2]] if m else None


def sigmas(d, t0_s, n, step_s):
    """(t_utc_s, sigma_km, sigma_arcsec) from an -E 16 (observables + sigmas) run."""
    out = []
    for r in (d / "ephem.txt").read_text().splitlines():
        tok = r.split()
        if len(tok) > 12 and re.match(r"\d{4}$", tok[0]):
            s, delta = sky_sigma(tok[-2]), tok[10]
            dk = float(delta) * AU if "." in delta else float(delta.rstrip("k"))      # AU, or whole km when close
            out.append((s * dk / 206264.806, s) if s is not None else (np.nan, np.nan))
    out = np.array(out).reshape(-1, 2)
    return t0_s + step_s * np.arange(len(out)), out[:, 0], out[:, 1]


def obs_span(lines):
    t = sorted(obs_time(ln) for ln in lines if is_obs(ln))
    return t[0], t[-1]


class Fit:
    """A reproduced fit: fo elements from a 1-row run, then (by default, or on demand) the -E 0 vector table on
    [a_s, b_s] as a Hermite callable (table) and sigma_km(t) from an -E 16 run (sigma_table). When the refit
    misses its page (not accepted, or a, e, i off by > 1 page sigma) it is rerun with 3 x STEPS full improvement
    steps (-y 30), which recovers fits that fo's default iteration leaves unconverged; with retry=False the
    arguments are taken as final (a memoised verdict's). Run directories under CACHE/STAMP: <label>_f / _fy (the
    fit probes), <label>_E0 / _E16 (window table, sigmas; self.dir is the table's once it exists), _s (short
    table), _l<k> (one-leg propagations), _d<nn> (one-day chunks)."""

    def __init__(self, label, ref, a_s, b_s, table_step, with_sigma=True, sigma_step=21600.0, retry=True, table=True):
        self.lines, args, self.page, self.members = resolve(ref) if isinstance(ref, str) else ref
        self.first, self.last = obs_span(self.lines)
        self.page_el = (elements(page_text(self.page)) if self.page.suffix in (".html", ".htm")
                        else elements(self.page.read_text()) if self.page.name == "elements.txt" else None)
        self.label, self.a_s, self.b_s, self.step = short(label), a_s, b_s, table_step
        best = None
        for extra in ([], ["-y", str(3 * STEPS)]):
            if extra and (not retry or best and best[0] >= (1, -1)):
                break
            d = run(self.label + ("_fy" if extra else "_f"), self.lines, args + extra, a_s, 1, table_step, scratch=True)
            if not (d / "elements.txt").exists() or not (d / "ephem.txt").exists():
                continue
            self.el, self.dir, self.args = elements((d / "elements.txt").read_text()), d, args + extra
            c = self.check()
            score = (int(c["accepted"]), -max([c.get(f"d{k}_sigma") or 0 for k in "aei"]) if self.page_el else 0)
            if best is None or score > best[0]:
                best = (score, d, self.args, self.el)
        if best is None:
            raise RuntimeError(f"fo produced no fit/vectors for {self.page.name} ({d})")
        _, self.dir, self.args, self.el = best
        self.reproduced = best[0] >= (1, -1)
        self.rv, self.t, self.x, self.mode, self.consistency = None, None, None, "fo", {}
        self.sigma, self.sigma_arcsec = None, None
        if table:
            self.table()
        if with_sigma:
            self.sigma_table(sigma_step)

    def __call__(self, t):
        if self.rv is None:
            raise RuntimeError(f"{self.label}: no vector table yet (Fit.table())")
        return self.rv(np.atleast_1d(np.asarray(t, dtype=float)))

    def table(self):
        """The -E 0 vector table over [a_s, b_s] at the table step; its directory (same elements as the fit probe,
        plus the vectors) becomes self.dir."""
        n = int(np.ceil((self.b_s - self.a_s) / self.step - 1e-9)) + 1
        d = run(self.label, self.lines, self.args, self.a_s, n, self.step)
        self.t, self.x = vectors(d, self.a_s, n, self.step)
        self.rv, self.dir = hermite(self.t, self.x), d
        return self

    def sigma_table(self, step=21600.0):
        """sigma_km(t) over [a_s, b_s] from an -E 16 run, log-interpolated between its steps."""
        m = max(2, int((self.b_s - self.a_s) // step) + 1)
        sd = run(self.label, self.lines, self.args, self.a_s, m, step, option="16")
        ts, s, arc = sigmas(sd, self.a_s, m, step)
        ok = np.isfinite(s) & (s > 0)
        if ok.sum() >= 2:
            self.sigma = lambda tt, ts=ts[ok], ls=np.log(s[ok]): np.exp(np.interp(tt, ts, ls))
            self.sigma_dir = sd
        self.sigma_arcsec = ts, arc
        return self

    def single(self, t_s, k=0):
        """fo's own one-leg propagation (epoch -> t) of this fit: geocentric (6,) at UTC t_s (run <label>_l<k>)."""
        d = run(f"{self.label}_l{k}", self.lines, self.args, t_s, 1, 3600.0, scratch=True)
        return vectors(d, t_s, 1, 3600.0)[1][0]

    def daily(self, a_s, b_s, chunk_s=86400.0):
        """The vector table on [a_s, b_s] rebuilt from one-day fo runs (WORKERS at a time under one lock hold),
        each started by fo's own one-leg propagation from the epoch. A run drifts from the one-leg solution about
        as the square of the time since its start, so each is corrected by (tau / T)^2 times its mismatch with
        the one-leg state at its end. Returns (t, x, largest such mismatch in km)."""
        step = self.step
        c0s = np.arange(a_s, b_s - 1e-6, chunk_s)
        ns = [int(round((min(c0 + chunk_s, b_s) - c0) / step)) + 1 for c0 in c0s]
        chunk = lambda i: vectors(run(f"{self.label}_d{i:02d}", self.lines, self.args, c0s[i], ns[i], step, scratch=True),
                                  c0s[i], ns[i], step)
        with locked(), ThreadPoolExecutor(WORKERS) as ex:
            last = ex.submit(self.single, c0s[-1] + step * (ns[-1] - 1), 3)
            parts = list(ex.map(chunk, range(len(c0s))))
        ts, xs = [p[0] for p in parts], [p[1] for p in parts]
        ends = [x[0] for x in xs[1:]] + [last.result()]
        jumps = []
        for t, x, end in zip(ts, xs, ends):
            w = ((t - t[0]) / (t[-1] - t[0]))[:, None] ** 2
            jumps.append(float(np.linalg.norm(end[:3] - x[-1, :3])))
            x += w * (end - x[-1])
        return (np.concatenate([t[:-1] for t in ts[:-1]] + [ts[-1]]),
                np.concatenate([x[:-1] for x in xs[:-1]] + [xs[-1]]), max(jumps))

    def verify(self, ctx, a_s, b_s, mid_s, tol=1.0):
        """fo's multi-step vector table drifts from fo's own one-leg propagation in some cases (P22qGC1: 818 km
        after 46 d of 2-min steps; fast flybys), roughly as the square of its length, while matching it to < 1 m
        in others. Compare the two at a_s, mid_s (now) and b_s (legs _l0.._l2, in the pool); beyond tol km rebuild
        [a_s, b_s] from one-day runs (drift < 1 km per day), all under one lock hold. Should fo give no vectors,
        integrate its epoch state with cle.nbody."""
        times = (a_s, min(max(mid_s, a_s), b_s), b_s)
        with locked():
            with ThreadPoolExecutor(WORKERS) as ex:
                one = list(ex.map(self.single, times, range(3)))
            legs = {iso(t): round(float(np.linalg.norm(x[:3] - self(t)[0, :3])), 3) for t, x in zip(times, one)}
            self.consistency = {"fo_table_vs_one_leg_km": legs, "tol_km": tol}
            if max(legs.values()) > tol:
                try:
                    t, x, jump = self.daily(a_s, b_s)
                    self.rv, self.t, self.x, self.mode = hermite(t, x), t, x, "fo_daily"
                    self.consistency["daily_chunk_jump_max_km"] = round(jump, 3)
                except (RuntimeError, ValueError, OSError) as err:
                    t_e = self.el["epoch_tt_s"] - TT_UTC
                    self.rv, self.mode = nbody.propagate(ctx, t_e, self.single(t_e, 4), a_s, b_s), "nbody"
                    self.consistency["daily_error"] = str(err)[:200]
        self.consistency["mode"] = self.mode
        return self

    def check(self):
        """Acceptance (>= 70 % used, rms < 5") and the a, e, i agreement with the page, in units of the page 1-sigma."""
        el, pg = self.el, self.page_el or {}
        out = {"n_obs": el.get("n_obs"), "n_used": el.get("n_used"), "rms_arcsec": el.get("rms_arcsec"),
               "accepted": bool(el.get("n_obs") and el["n_used"] >= 0.7 * el["n_obs"] and (el["rms_arcsec"] or 0) < 5)}
        for k in ("a", "e", "i"):
            if pg.get(k, (None,))[0] is not None and el[k][0] is not None:
                sig = pg[k][1] or el[k][1]
                out[f"d{k}_sigma"] = round(abs(el[k][0] - pg[k][0]) / sig, 2) if sig else None
                out[f"{k}_ours_page"] = [el[k][0], pg[k][0]]
        return out

    def later_oc(self, lines, ctx):
        """Max O-C (arcsec) of observations in `lines` taken after this fit's arc (inside [a_s, b_s]), or None;
        without the window table they are checked on a short -E 0 table (<label>_s) over their own arc."""
        from cle import tracklets
        obs = [o for o in tracklets.parse(lines) if o["t_s"] > self.last + 3600 and self.a_s < o["t_s"] < self.b_s]
        if not obs:
            return None
        rv = self.rv
        if rv is None:
            t0, n = obs[0]["t_s"] - self.step, int(np.ceil((obs[-1]["t_s"] - obs[0]["t_s"]) / self.step)) + 3
            rv = hermite(*vectors(run(self.label + "_s", self.lines, self.args, t0, n, self.step, scratch=True), t0, n, self.step))
        r = np.array([x for tr in tracklets.build(obs, rv, ctx) for x in tr["resid_arcsec"]]).reshape(-1, 2)
        return round(float(np.hypot(r[:, 0], r[:, 1]).max()), 1) if len(r) else None


def candidates(target, days=14):
    """neocpsat_fit references of the target whose arc ends within `days` of the primary's, newest first."""
    _, ref, _ = primary(target)
    end = lambda text: re.search(r"\.\.(\d{4}-\d\d-\d\d)\]", text)
    items = {ref: end(target["primary_ephem"])[1]}
    for src in target.get("sources", []):
        for item in src.get("ephem_sources", "").split(";"):
            kind, r, _ = primary({"primary_ephem": item})
            if kind == "neocpsat_fit" and end(item) and (ROOT / r.split("@")[0]).exists() \
                    and all(r.split("@")[0] != x.split("@")[0] for x in items):
                items[r] = end(item)[1]
    newest = items[ref]
    near = [r for r, e in items.items() if (parse_day(newest) - parse_day(e)) <= days * 86400]
    rest = [r for r in sorted(items, key=items.get, reverse=True) if r not in near]
    return sorted(near, key=lambda r: (items[r], r == ref), reverse=True), rest


def parse_day(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp()


LATER_MAX = 3600.0              # a fit that misses later astrometry of the object by > 1 deg is contradicted


def usable(fit, target, later=None):
    return (fit.check()["accepted"] and (target["regime"] in HELIO or (fit.el["e"][0] or 0) < 1)
            and (later is None or later < LATER_MAX))


def verdict_key(lines, args, newest):
    """sha256 of a candidate's obs lines (sorted, unique), its fo arguments and the newest batch's obs keys: any
    new observation of the object, in the candidate or in the batch it is checked against, changes it."""
    h = hashlib.sha256()
    for ln in sorted(set(lines)):
        h.update(ln.encode() + b"\n")
    h.update((" ".join(args) + "\n").encode())
    for k in sorted(obs_key(ln) for ln in newest if is_obs(ln)):
        h.update(("|".join(k) + "\n").encode())
    return h.hexdigest()


def load_verdicts():
    try:
        return json.loads(VERDICTS.read_text())
    except (OSError, ValueError):
        return {}


def save_verdicts(new):
    """Merge `new` into verdicts.json under the fo lock (re-read first: states may run in several processes);
    entries not used for KEEP_DAYS are dropped."""
    if not new:
        return
    with locked():
        cutoff = iso(time.time() - KEEP_DAYS * 86400)
        cur = {k: v for k, v in (load_verdicts() | new).items() if v.get("run_utc", "") >= cutoff}
        VERDICTS.parent.mkdir(parents=True, exist_ok=True)
        tmp = VERDICTS.with_suffix(".tmp")
        tmp.write_text(json.dumps(cur, indent=1))
        tmp.replace(VERDICTS)


def epoch_vector(d):
    """Find_Orb's state vector at the elements epoch from an -O directory's elements.txt (km, km/s, J2000
    equator): the one relative to the central body when present (geocentric fits), else the heliocentric one."""
    try:
        text = (d / "elements.txt").read_text()
    except OSError:
        return None
    rows = re.findall(r"^# State vector ([^:\n]+):\n#\s*(\S+)\s+(\S+)\s+(\S+) AU\n#\s*(\S+)\s+(\S+)\s+(\S+) mAU/day",
                      text, re.M)
    if not rows:
        return None
    centre, *v = rows[-1]
    return {"centre": "central body" if "central" in centre else "sun", "r_km": [float(x) * AU for x in v[:3]],
            "v_km_s": [float(x) * 1e-3 * AU / 86400 for x in v[3:]]}


def verdict(fit, later, ok, cid, run_utc, old=None):
    el = fit.el
    t_e = el["epoch_tt_s"] - TT_UTC if el.get("epoch_tt_s") else None
    return {"cid": cid, "page": fit.page.name, "args": fit.args, "accepted": fit.check()["accepted"], "usable": ok,
            "e": el["e"][0], **{k: el.get(k) for k in ("n_obs", "n_used", "rms_arcsec")}, "later_oc_arcsec": later,
            "reproduced": fit.reproduced, "elements": el,
            "epoch_state": {"t_utc": iso(t_e) if t_e else None, **(epoch_vector(fit.dir) or {})},
            "first_seen": old["first_seen"] if old else run_utc, "run_utc": run_utc}


def reason(t):
    """One short clause on why a tried candidate was not usable."""
    if t.get("error"):
        return t["error"][:60]
    if (t.get("later_oc_arcsec") or 0) >= LATER_MAX:
        return f"{t['later_oc_arcsec'] / 3600:.0f} deg off later obs"
    if (t.get("e") or 0) >= 1:
        return f"unbound (e={t['e']:.3g})"
    return f"not accepted ({t.get('n_used')}/{t.get('n_obs')} obs, rms {t.get('rms_arcsec')}\")"


def choose(target, win, ctx):
    """The latest coherent fit: of the pages ending within 14 d of the primary's, the accepted (and, for
    geocentric regimes, bound) refit with the smallest fo sigma at now; else the newest usable older page.
    Every refit is also checked against the newest batch's observations taken after its own arc; a fit
    that misses them by more than 1 deg is rejected. Pages with identical obs and arguments are fitted once.
    Candidates are fitted with a 1-row table and checked on a short one; usable ones get the -E 16 sigma run
    (the ranking) and the chosen fit alone the window table. Verdicts are memoised (verdicts.json): a candidate
    known to be rejected for these observations is skipped, an accepted one reruns with its recorded arguments
    (no -y 30 probe); fo errors are not memoised."""
    helio = target["regime"] in HELIO
    near, rest = candidates(target)
    newest = resolve(near[0])[0]
    t_new = obs_span(newest)[0]
    memo, fresh, run_utc = load_verdicts(), {}, iso(time.time())
    tried, best, done = [], None, {}
    try:
        for group in (near, rest):
            for ref in group:
                label = target["cid"] + "_" + re.sub(r"[^A-Za-z0-9_-]", "_", Path(ref.split("@")[0]).stem)
                try:
                    src = resolve(ref)
                    vk = verdict_key(src[0], src[1], newest)
                    known = fresh.get(vk) or memo.get(vk)
                    if known and not known["usable"]:
                        fresh[vk] = known | {"run_utc": run_utc}
                        tried.append({"ref": ref, "usable": False, "known_since": known["first_seen"],
                                      **{k: known.get(k) for k in ("e", "n_obs", "n_used", "rms_arcsec",
                                                                   "later_oc_arcsec", "args")}})
                        continue
                    key = (tuple(src[0]), tuple(src[1]))
                    if key not in done:
                        fit = Fit(label, (src[0], known["args"] if known else src[1], src[2], src[3]),
                                  min(win.t0_s, t_new - 86400), win.t1_s, 1800.0 if helio else 120.0,
                                  with_sigma=False, retry=known is None, table=False)
                        done[key] = fit, fit.later_oc(newest, ctx)
                    fit, later = done[key]
                    ok = usable(fit, target, later)
                    if ok and fit.sigma_arcsec is None:
                        fit.sigma_table()
                except (RuntimeError, ValueError, OSError) as err:
                    tried.append({"ref": ref, "error": str(err)[:200]})
                    continue
                fresh[vk] = verdict(fit, later, ok, target["cid"], run_utc, known)
                sig = float(fit.sigma(win.now_s)) if fit.sigma else np.inf
                tried.append({"ref": ref, "usable": ok, "sigma_km_now": None if np.isinf(sig) else round(sig, 1),
                              "e": fit.el["e"][0], **{k: fit.el.get(k) for k in ("n_obs", "n_used", "rms_arcsec")},
                              "later_oc_arcsec": later, "args": fit.args})
                if ok and (best is None or sig < best[1]):
                    best = (fit, sig, later)
                if group is rest and best:
                    break
            if best:
                break
    finally:
        save_verdicts(fresh)
    if best is None:
        why = "; ".join(f"{Path(t['ref'].split('@')[0]).stem}: {reason(t)}" for t in tried)
        if any(t.get("error") for t in tried):
            raise RuntimeError(f"{target['cid']}: no coherent find_orb fit covers the window ({why})")
        since = min(t.get("known_since") or run_utc for t in tried)[:10]
        raise RuntimeError(f"{target['cid']}: known-unfittable since {since}, waiting for new observations "
                           f"({len(tried)} candidates rejected, newest obs {iso(obs_span(newest)[1])[:10]}: {why})")
    return best[0].table(), best[2], tried, t_new


def build(target, win, ctx):
    _, ref, _ = primary(target)
    fit, later, tried, t_new = choose(target, win, ctx)
    fit.verify(ctx, win.t0_s, win.t1_s, win.now_s)
    step = nbody.step_for(target["regime"])
    fa, fb = fit.first - PAD, fit.last + PAD
    starts = [win.t0_s] + [s for s in (fa, fb) if win.t0_s < s < win.t1_s]
    qual = ["fit" if fa <= s < fb or (s == win.t0_s and fa <= s <= fb) else "extrap" for s in starts]
    segs, mid = [], 0.0
    src = f"fo{'+nbody' if fit.mode == 'nbody' else ''}:{fit.page.name}"
    for k, a, b in nbody.pieces(starts, win.grid(step)):
        out, m = nbody.segments(fit, win, step, a, b, src, qual[k], "start" if not segs else "continuous", ctx,
                                sigma=fit.sigma)
        segs += out
        mid = max(mid, m)
    chk = fit.check() | {"reproduced": fit.reproduced, "args": fit.args}
    dext = (win.t1_s - fit.last) / 86400
    swapped = fit.page != (ROOT / ref.split("@")[0]).resolve()
    prov = [nbody.raw(fit.page, members=fit.members), nbody.raw(fit.dir / "obs.txt"),
            nbody.raw(fit.dir / "elements.txt", command=json.loads((fit.dir / "command.json").read_text())["cmd"])]
    return StateTable(cle_id=target["cid"], centre="earth", segments=segs, provenance=prov,
                      validation={"midpoint_max_km": mid, "fo_dir": str(fit.dir.relative_to(ROOT)), "fit": chk,
                                  "obs_first": iso(fit.first), "obs_last": iso(fit.last), "candidates": tried,
                                  "primary_replaced": swapped, "later_oc_arcsec": later,
                                  "fo_consistency": fit.consistency,
                                  "sigma_km_now": float(fit.sigma(win.now_s)) if fit.sigma else None},
                      caveat=f"find_orb refit of {fit.page.name} ({chk['n_used']}/{chk['n_obs']} obs "
                             f"{iso(fit.first)[:10]}..{iso(fit.last)[:10]}, rms {chk['rms_arcsec']}\"); "
                             f"extrapolated beyond {iso(fb)[:10]}" + (f" ({dext:.0f} d to window end)" if dext > 2 else "")
                             + (f"; misses the {iso(t_new)[:10]} astrometry by {later:.0f}\"" if later else "")
                             + (f"; {Path(ref.split('@')[0]).name} not used (weaker or unbound fit)" if swapped else "")
                             + ("; fo's epoch state integrated with Earth + Moon + Sun (fo gave no vectors)"
                                if fit.mode == "nbody" else ""))


def epoch_state(ref, ctx, step_s=21600.0, until_s=None, cid=None):
    """For extrapolation (AM5): (t_utc_s, geocentric (6,) state at the refit's elements epoch, info). info["fit"]
    is the Fit, whose sigma (fo -E 16 from the last obs to until_s) and .single(t) (fo's own one-leg
    propagation) the caller can use. Runs are labelled <cid>_epoch (the page's name without a cid): states' pool
    workers share one STAMP and run() replaces an existing run directory, so two targets extrapolated from one
    page must not share a label."""
    lines, args, page, members = resolve(ref)
    first, last = obs_span(lines)
    label = cid or re.sub(r"[^A-Za-z0-9_-]", "_", page.parent.name if page.name == "elements.txt" else page.stem)
    b = max(until_s or last, last) + 2 * step_s
    fit = Fit(label + "_epoch", (lines, args, page, members), last, b, step_s, sigma_step=step_s)
    t_e = fit.el["epoch_tt_s"] - TT_UTC
    rv = fit.single(t_e)
    return t_e, rv, {"page": str(page.relative_to(ROOT.parent)) if page.is_relative_to(ROOT.parent) else str(page),
                     "members": members, "fo_dir": str(fit.dir.relative_to(ROOT)), "check": fit.check(),
                     "args": fit.args, "obs_first": iso(first), "obs_last": iso(last), "fit": fit,
                     "provenance": [nbody.raw(page), nbody.raw(fit.dir / "obs.txt"),
                                    nbody.raw(fit.dir / "elements.txt",
                                              command=json.loads((fit.dir / "command.json").read_text())["cmd"])]}


if __name__ == "__main__":
    nbody.run(build)
