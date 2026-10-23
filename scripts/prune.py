#!/usr/bin/env python3
"""make prune / prune-apply: retention for the find_orb and Horizons raw caches (REVIEW EFF-9, DW-05).

    ~/.venvs/astro313/bin/python -I scripts/prune.py [--apply] [--root DIR] [--min-age-h 1]

work/cache/fo/<stamp>/      keep every stamp that build/*.json or build/states/*.json names (fo_dir, provenance) and,
                            per referenced run directory, the newest older stamp holding a run of the same label (the
                            previous fit of that target); in kept stamps the other run directories (one-day chunks
                            _d<nn>, legs _l<k>, probes _f/_fy/_s, rejected candidates) are trimmed to obs.txt,
                            elements.txt, command.json, fo_stdout.txt and a gzipped ephem.txt (fo's side files -
                            total.json, elements.json, fo_env.dat, covar.txt, ... - are ~80 % of a stamp and nothing
                            reads them back); delete every other stamp.
work/cache/horizons_eph/    keep the stamps build/ references plus the two newest; delete the rest. Daily stamps hold
                            the solution probes and window-edge pieces; the 10-day tiles build/states references
                            live in older stamps and are kept by the reference.
Nothing else under work/cache is touched (horizons = the sweep, neocp, gp, billgray, news, ...); inputs.sha256 and
verdicts.json stay. Stamps younger than --min-age-h are left alone (a build may still be writing them). Refuses to
run while work/make.lock is held by a live ops/run.sh that is not this process's ancestor, or while work/fo.lock is
held (a find_orb run), or when build/ references nothing. Dry run by default: prints what --apply would do and the
bytes it would free; exit 0 either way, 2 when refused.
"""
from __future__ import annotations

import argparse
import fcntl
import gzip
import os
import re
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAMP = re.compile(r"^\d{8}T\d{6}Z")
FO_REF = re.compile(r"work/cache/fo/(\d{8}T\d{6}Z)/([^/\"\\\s]+)")
HZ_REF = re.compile(r"work/cache/horizons_eph/([^/\"\\\s]+)/")
KEEP_FILES = {"obs.txt", "elements.txt", "command.json", "fo_stdout.txt", "ephem.txt", "ephem.txt.gz"}
MB = 1e6


def du(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) if path.is_dir() else path.stat().st_size


def referenced(build: Path):
    """({fo stamp: {run labels}}, {horizons_eph stamps}) named anywhere in build/*.json and build/states/*.json."""
    fo, hz = defaultdict(set), set()
    for p in sorted(build.glob("*.json")) + sorted((build / "states").glob("*.json")):
        text = p.read_text(errors="replace")
        for stamp, label in FO_REF.findall(text):
            fo[stamp].add(label)
        hz.update(HZ_REF.findall(text))
    return dict(fo), hz


def stamps(cache: Path):
    return sorted((d for d in cache.iterdir() if d.is_dir() and STAMP.match(d.name)), key=lambda d: d.name) \
        if cache.is_dir() else []


def plan_fo(cache: Path, refs: dict, young: set):
    """(keep {stamp: {plain labels}}, delete [stamps], gzip [ephem.txt], trim [side files]) for work/cache/fo."""
    found = stamps(cache)
    keep = {s: set(labels) for s, labels in refs.items() if (cache / s).is_dir()}
    for stamp, labels in refs.items():
        for label in labels:                                      # the previous run of the same label
            prev = next((d for d in reversed(found) if d.name < stamp and (d / label).is_dir()), None)
            if prev is not None:
                keep.setdefault(prev.name, set()).add(label)
    delete = [d for d in found if d.name not in keep and d.name not in young]
    other = [r for d in found if d.name in keep and d.name not in young
             for r in sorted(d.iterdir()) if r.is_dir() and r.name not in keep[d.name]]
    gz = [r / "ephem.txt" for r in other if (r / "ephem.txt").is_file()]
    trim = [f for r in other for f in sorted(r.iterdir()) if f.is_file() and f.name not in KEEP_FILES]
    return keep, delete, gz, trim


def plan_hz(cache: Path, refs: set, young: set, newest: int = 2):
    found = stamps(cache)
    keep = refs | {d.name for d in found[-newest:]}
    return keep, [d for d in found if d.name not in keep and d.name not in young]


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


def ancestors() -> set:
    out, pid = set(), os.getppid()
    while pid > 1 and pid not in out:
        out.add(pid)
        try:
            pid = int(subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True,
                                     timeout=5).stdout.strip() or 0)
        except (OSError, ValueError, subprocess.SubprocessError):
            break
    return out


def held(work: Path) -> str:
    """Why the caches must be left alone now, or ''."""
    lock = work / "make.lock"
    if lock.is_dir():
        try:
            pid = int((lock / "pid").read_text())
        except (OSError, ValueError):
            pid = None
        if pid is None or (alive(pid) and pid not in ancestors()):
            return f"{lock} held by pid {pid}"
    fl = work / "fo.lock"
    if fl.exists():
        with open(fl) as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return f"{fl} held (find_orb running)"
            fcntl.flock(f, fcntl.LOCK_UN)
    return ""


def gzip_file(f: Path) -> int:
    """ephem.txt -> ephem.txt.gz (atomic), returns the bytes freed."""
    out, tmp = f.with_name(f.name + ".gz"), f.with_name("." + f.name + ".gz.tmp")
    with open(f, "rb") as src, gzip.open(tmp, "wb", compresslevel=6) as dst:
        shutil.copyfileobj(src, dst)
    tmp.replace(out)
    freed = f.stat().st_size - out.stat().st_size
    f.unlink()
    return freed


def run(root: Path, apply: bool, min_age_h: float, out=sys.stdout) -> int:
    work, build, now = root / "work", root / "build", time.time()
    why = held(work)
    if why:
        print(f"prune: refused, {why}", file=out)
        return 2
    fo_refs, hz_refs = referenced(build)
    if not fo_refs and not hz_refs:
        print(f"prune: refused, {build} references no cache stamp (run states first)", file=out)
        return 2
    young = lambda cache: {d.name for d in stamps(cache) if now - d.stat().st_mtime < min_age_h * 3600}
    fo_cache, hz_cache = work / "cache/fo", work / "cache/horizons_eph"
    keep, delete, gz, trim = plan_fo(fo_cache, fo_refs, young(fo_cache))
    hz_keep, hz_delete = plan_hz(hz_cache, hz_refs, young(hz_cache))
    freed = 0
    verb = "" if apply else "would "
    n_fo, size_fo = len(stamps(fo_cache)), du(fo_cache) if fo_cache.is_dir() else 0
    print(f"fo: {n_fo} stamps, {size_fo / MB:.1f} MB; keep {len(keep)}: "
          + ", ".join(f"{s} ({'referenced' if s in fo_refs else 'previous'}, {len(l)} runs)" for s, l in sorted(keep.items())),
          file=out)
    for d in delete:
        size = du(d)
        freed += size
        if apply:
            shutil.rmtree(d)
    print(f"fo: {verb}delete {len(delete)} stamps, {freed / MB:.1f} MB", file=out)
    plain = sum(f.stat().st_size for f in gz)
    got = sum(gzip_file(f) if apply else len(b := f.read_bytes()) - len(gzip.compress(b, 6)) for f in gz)
    freed += got
    print(f"fo: {verb}gzip {len(gz)} ephem.txt in kept stamps, {plain / MB:.1f} -> {(plain - got) / MB:.1f} MB", file=out)
    side = sum(f.stat().st_size for f in trim)
    if apply:
        for f in trim:
            f.unlink()
    freed += side
    print(f"fo: {verb}delete {len(trim)} side files of unreferenced runs in kept stamps, {side / MB:.1f} MB", file=out)
    n_hz, hz_freed = len(stamps(hz_cache)), sum(du(d) for d in hz_delete)
    if apply:
        for d in hz_delete:
            shutil.rmtree(d)
    freed += hz_freed
    print(f"horizons_eph: {n_hz} stamps; keep {len(hz_keep)} ({len(hz_refs)} referenced + newest 2); "
          f"{verb}delete {len(hz_delete)} stamps, {hz_freed / MB:.1f} MB", file=out)
    print(f"{'freed' if apply else 'would free'} {freed / MB:.1f} MB" + ("" if apply else " (dry run; --apply to do it)"), file=out)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="delete and gzip (default: dry run)")
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--min-age-h", type=float, default=1.0, help="leave stamps younger than this alone (hours)")
    a = ap.parse_args(argv)
    return run(Path(a.root), a.apply, a.min_age_h)


if __name__ == "__main__":
    sys.exit(main())
