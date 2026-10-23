#!/usr/bin/env python3
"""`make extract`: run every catalog extractor in order (ARCHITECTURE.md §13, review DW-08).

    billgray      extract_billgray.py          ~/tles + get_vect cache (offline)
    neocpsat      extract_neocpsat.py          ../neocpsat; local find_orb for uncached groups, one at a time
                                               (work/fo.lock, shared with cle.adapters.fo)
    lunarmission  extract_lunarmission.py      --offline: the cached wiki / Horizons / Space-Track snapshots
    rocketdebris  extract_rocketdebris.py      ../rocketdebris + cached SATCAT/GP (offline)
    spacetrack    extract_spacetrack_gp.py     Space-Track class/gp through the skill CLI; skipped (not failed)
                                               with --offline or without credentials (ST_USERNAME/ST_PASSWORD
                                               or ~/.config/space-track/credentials.json)

Every extractor sees the same CLE_NOW (the environment's, else this run's hour, so a run crossing midnight is
still one date) and writes catalog/sources/<src>.tsv idempotently; stdout/stderr go to work/logs/extract/<name>.log.
Exit status 1 when any extractor fails; the others still run.

Usage:  ~/.venvs/astro313/bin/python -I scripts/extract_all.py [--offline] [--only a,b] [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _now import now_iso  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
LOGS = ROOT / "work/logs/extract"
CRED_FILE = Path.home() / ".config/space-track/credentials.json"

STEPS = [  # name, script, arguments, needs the network
    ("billgray", "extract_billgray.py", [], False),
    ("neocpsat", "extract_neocpsat.py", [], False),
    ("lunarmission", "extract_lunarmission.py", ["--offline"], False),
    ("rocketdebris", "extract_rocketdebris.py", [], False),
    ("spacetrack", "extract_spacetrack_gp.py", [], True),
]


def have_credentials() -> bool:
    return bool(os.environ.get("ST_USERNAME") and os.environ.get("ST_PASSWORD")) or CRED_FILE.exists()


def plan(offline: bool, only=None):
    """[(name, argv, skip reason)] for this run."""
    out = []
    for name, script, args, net in STEPS:
        if only and name not in only:
            continue
        skip = ""
        if net and offline:
            skip = "offline"
        elif net and not have_credentials():
            skip = "no Space-Track credentials"
        out.append((name, [sys.executable, "-I", str(SCRIPTS / script), *args], skip))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--offline", action="store_true", help="skip the extractors that need the network")
    ap.add_argument("--only", default="", help="comma-separated extractor names")
    ap.add_argument("--dry-run", action="store_true", help="list the commands and exit")
    a = ap.parse_args(argv)
    only = set(filter(None, a.only.split(",")))
    env = os.environ | {"CLE_NOW": os.environ.get("CLE_NOW") or now_iso()}
    steps = plan(a.offline, only)
    print(f"extract: CLE_NOW={env['CLE_NOW']}", flush=True)
    failed = []
    for name, cmd, skip in steps:
        if a.dry_run:
            print(f"  {name:13s} {'skip (' + skip + ')' if skip else ' '.join(cmd[1:])}")
            continue
        if skip:
            print(f"  {name:13s} skipped: {skip}", flush=True)
            continue
        LOGS.mkdir(parents=True, exist_ok=True)
        log = LOGS / f"{name}.log"
        t = time.time()
        with open(log, "w") as f:
            f.write(f"$ {' '.join(cmd)}\nCLE_NOW={env['CLE_NOW']}\n\n")
            f.flush()
            r = subprocess.run(cmd, cwd=ROOT, env=env, stdout=f, stderr=subprocess.STDOUT)
        tail = log.read_text().strip().splitlines()[-1:]
        status = "ok" if r.returncode == 0 else f"FAILED exit {r.returncode}"
        print(f"  {name:13s} {status:14s} {time.time() - t:6.1f} s  {tail[0][:80] if tail else ''}  ({log.relative_to(ROOT)})", flush=True)
        if r.returncode:
            failed.append(name)
    if failed:
        print(f"extract: {len(failed)} failed: {', '.join(failed)}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
