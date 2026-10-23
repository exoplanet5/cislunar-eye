#!/usr/bin/env python3
"""make status: ages of the pipeline's inputs and outputs against the wall clock (REVIEW DW-13; read-only).

    ~/.venvs/astro313/bin/python -I scripts/status.py [--root DIR] [--now ISO]

One line per item: stamp, age in hours. Exit 1 when the NEOCP snapshot is older than 3 h (the hourly job has
stopped, ARCHITECTURE.md §6.4), when the last daily run (newest work/logs/daily-*.log, else dist/data/manifest.js
built_utc) is older than 36 h, or when that daily log ends in FAILED.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NEOCP_MAX_H, DAILY_MAX_H = 3.0, 36.0


def parse(text) -> float | None:
    """Epoch seconds of an ISO stamp, a bare date or a 20261022T021603Z folder name; None otherwise."""
    text = str(text or "").strip()
    try:
        return datetime.strptime(text, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        pass
    try:
        t = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).timestamp()
    except ValueError:
        return None


def load(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def mtime(path) -> float | None:
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def newest(root: Path, pattern: str) -> Path | None:
    files = sorted(root.glob(pattern), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def git_head(repo: Path) -> str:
    try:
        return subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%cI %h %s"], capture_output=True,
                              text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def collect(root: Path) -> list[dict]:
    """[{what, stamp, t}]: t is the epoch (s) the age is measured from, None when the item is missing."""
    rows = []
    add = lambda what, stamp="", t=None: rows.append({"what": what, "stamp": str(stamp or ""), "t": t})
    stats = (load(root / "work/cache/neocp/parsed.json") or {}).get("stats") or {}
    snap = stats.get("neocp_txt_mtime")
    add("neocp snapshot (neocp.txt)", snap, parse(snap) if snap else mtime(root / "work/cache/neocp/neocp.txt"))
    for what, pattern in (("horizons stamp", "work/cache/horizons_eph/*/manifest.json"), ("gp stamp", "work/cache/gp/2*/manifest.json")):
        p = newest(root, pattern)
        got = (load(p) or {}).get("retrieved_utc") if p else None
        add(what, got, parse(got))
    p = newest(root, "work/cache/fo/2*")
    add("fo stamp", p.name if p else "", parse(p.name) if p else None)
    add("master generated_for", (load(root / "catalog/master_list.json") or {}).get("generated_for"), mtime(root / "catalog/master_list.json"))
    r = load(root / "build/states_report.json") or {}
    add("states_report now_utc", f"{r.get('now_utc', '')} ({' .. '.join(w[:10] for w in r.get('window') or [])})".strip(" ()"), mtime(root / "build/states_report.json"))
    add("neocp.json built", (load(root / "build/neocp.json") or {}).get("built_utc"), mtime(root / "build/neocp.json"))
    try:
        built = re.search(r'"built_utc":"([^"]+)"', (root / "dist/data/manifest.js").read_text())[1]
    except (OSError, TypeError):
        built = None
    add("manifest built_utc", built, parse(built))
    head = git_head(Path.home() / "tles")
    add("~/tles HEAD", head, parse(head.split(" ")[0]) if head else None)
    for name in ("daily", "update-neocp"):
        p = newest(root, f"work/logs/{name}-*.log")
        last = p.read_text().strip().splitlines()[-1:] if p else []
        add(f"last {name} log", f"{p.name}: {last[0][20:100] if last else ''}" if p else "", mtime(p) if p else None)
    return rows


def problems(rows: list[dict], now_s: float) -> list[str]:
    by = {r["what"]: r for r in rows}
    age = lambda r: None if r["t"] is None else (now_s - r["t"]) / 3600
    out = []
    a = age(by["neocp snapshot (neocp.txt)"])
    if a is None or a > NEOCP_MAX_H:
        out.append(f"NEOCP snapshot {'missing' if a is None else f'{a:.1f} h old'} (> {NEOCP_MAX_H:g} h): the hourly update-neocp is not running")
    daily = by["last daily log"] if by["last daily log"]["t"] is not None else by["manifest built_utc"]
    a = age(daily)
    if a is None or a > DAILY_MAX_H:
        out.append(f"last daily {'missing' if a is None else f'{a:.1f} h old'} (> {DAILY_MAX_H:g} h): run make daily")
    elif "FAILED" in by["last daily log"]["stamp"]:
        out.append(f"last daily failed: {by['last daily log']['stamp']}")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--now", help="ISO UTC instead of the wall clock (tests)")
    args = ap.parse_args(argv)
    now_s = parse(args.now) if args.now else time.time()
    rows = collect(Path(args.root))
    bad = problems(rows, now_s)
    for r in rows:
        a = "   missing" if r["t"] is None else f"{(now_s - r['t']) / 3600:8.1f} h"
        print(f"{r['what']:28s} {r['stamp'][:60]:60s} {a}")
    print("status: ok" if not bad else "status: STALE\n  " + "\n  ".join(bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
