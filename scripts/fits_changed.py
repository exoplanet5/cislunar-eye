#!/usr/bin/env python3
"""Gate for `make update-fits` (ARCHITECTURE.md §13): have the find_orb inputs in ../neocpsat changed?

    fits_changed.py            exit 0 when ../neocpsat/{catalog.csv,data/*.txt,mpecs/*.html} differ from the
                               record work/cache/fo/inputs.sha256 (or there is no record), 1 when unchanged
    fits_changed.py --mark     the same, and write the record (after a successful `cle states --adapter fo`)
    fits_changed.py --status   print the recorded and the current digest, then changed / unchanged

The digest is the sha256 of every input file's name and content, so a re-download of identical pages does not
trigger a refit. --neocpsat and --record override the locations (tests).
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _now import now_iso  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
NEO = ROOT.parent / "neocpsat"
RECORD = ROOT / "work/cache/fo/inputs.sha256"


def inputs(neo: Path) -> list[Path]:
    return [p for p in [neo / "catalog.csv"] + sorted((neo / "data").glob("*.txt")) + sorted((neo / "mpecs").glob("*.html"))
            if p.is_file()]


def digest(neo: Path) -> tuple[str, int]:
    h, files = hashlib.sha256(), inputs(neo)
    for p in files:
        h.update(f"{p.relative_to(neo)}\0{hashlib.sha256(p.read_bytes()).hexdigest()}\n".encode())
    return h.hexdigest(), len(files)


def recorded(record: Path) -> list[str]:
    try:
        return record.read_text().split()
    except OSError:
        return []


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mark", action="store_true", help="write the record for the current inputs")
    ap.add_argument("--status", action="store_true", help="print the recorded and current digests")
    ap.add_argument("--neocpsat", type=Path, default=NEO)
    ap.add_argument("--record", type=Path, default=RECORD)
    a = ap.parse_args(argv)
    cur, n = digest(a.neocpsat)
    old = recorded(a.record)
    changed = not old or old[0] != cur
    if a.status:
        print(f"recorded {' '.join(old) if old else '(none)'}")
        print(f"current  {cur} {now_iso()} {n} files")
    if a.mark:
        a.record.parent.mkdir(parents=True, exist_ok=True)
        a.record.write_text(f"{cur} {now_iso()} {n}\n")
    print(f"fits inputs: {'no record' if not old else 'changed' if changed else 'unchanged'}"
          f" ({n} files under {a.neocpsat}{'; recorded' if a.mark else ''})")
    return 0 if changed else 1


if __name__ == "__main__":
    sys.exit(main())
