#!/usr/bin/env python3
"""Pack the MMX Hipparcos catalogue for the page (ARCHITECTURE.md §11, review EFF-8).

The MMX file carries 9,827 [ra_deg, dec_deg, Vmag] triples (372 KB of JSON numbers, 312 KB inlined in the page) for
1-3 px sprites. Here `stars` becomes three base64 little-endian arrays: RA uint16 and Dec int16 in units of
360/65536 deg (19.8 arcsec, 0.13 px at the sky camera's 38 deg over 900 px) and V int16 in millimag (79 KB).
`meta` and `hip_ids` are kept. web/sky.js decodes it with loader.decode; scripts/import_mmx.py calls pack_file.

Usage:  ~/.venvs/astro313/bin/python -I scripts/pack_stars.py [--src ~/solarsystem/MMX/web/assets/hipparcos.json]
                                                               [--out web/assets/hipparcos.json]
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
MMX_SRC = Path.home() / "solarsystem/MMX/web/assets/hipparcos.json"
OUT = ROOT / "web/assets/hipparcos.json"
LSB_DEG = 360 / 65536
MAG_UNIT = 0.001
NOTE = ("stars: base64 little-endian arrays ra (uint16) and dec (int16) in units.deg degrees, mag (int16) in units.mag "
        "magnitudes; order as hip_ids")


def b64(a: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(a).tobytes()).decode()


def pack(doc: dict) -> dict:
    """The packed document; a packed input is returned as is."""
    if isinstance(doc["stars"], dict):
        return doc
    a = np.asarray(doc["stars"], dtype=float).reshape(-1, 3)
    ra = (np.rint(a[:, 0] / LSB_DEG).astype(np.int64) % 65536).astype("<u2")
    dec, mag = np.rint(a[:, 1] / LSB_DEG).astype("<i2"), np.rint(a[:, 2] / MAG_UNIT).astype("<i2")
    stars = {"n": len(a), "ra": b64(ra), "dec": b64(dec), "mag": b64(mag), "units": {"deg": LSB_DEG, "mag": MAG_UNIT}}
    return {"meta": doc["meta"] | {"packed": NOTE}, "hip_ids": doc.get("hip_ids"), "stars": stars}


def unpack(stars: dict) -> np.ndarray:
    """(n, 3) float [ra_deg, dec_deg, Vmag] from a packed `stars` block (what web/sky.js computes)."""
    raw = lambda k, dt: np.frombuffer(base64.b64decode(stars[k]), dtype=dt).astype(float)
    u = stars["units"]
    return np.column_stack([raw("ra", "<u2") * u["deg"], raw("dec", "<i2") * u["deg"], raw("mag", "<i2") * u["mag"]])


def pack_file(src: Path, out: Path) -> dict:
    """Write the packed catalogue to `out`; returns an IMPORT.json record (scripts/import_mmx.py)."""
    doc = pack(json.loads(src.read_text()))
    text = json.dumps(doc, separators=(",", ":"), ensure_ascii=False) + "\n"
    tmp = out.with_name(f".{out.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(out)
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    return {"dst": str(out.relative_to(ROOT)) if out.is_relative_to(ROOT) else str(out), "src": str(src),
            "src_sha256": digest(src), "sha256": digest(out), "mode": "packed",
            "method": f"scripts/pack_stars.py: ra uint16 / dec int16 at {LSB_DEG:.6g} deg, mag int16 millimag, base64 LE"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--src", type=Path, default=MMX_SRC if MMX_SRC.exists() else OUT)
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args()
    src = json.loads(a.src.read_text())["stars"]
    rec = pack_file(a.src, a.out)
    stars = json.loads(a.out.read_text())["stars"]
    msg = f"packed {stars['n']} stars -> {rec['dst']} ({a.out.stat().st_size} B)"
    if not isinstance(src, dict):
        err = np.abs(unpack(stars) - np.asarray(src, float))
        err[:, 0] = np.minimum(err[:, 0], 360 - err[:, 0])
        msg += f"; max error ra {err[:, 0].max() * 3600:.1f}\" dec {err[:, 1].max() * 3600:.1f}\" mag {err[:, 2].max():.4f}"
    print(msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
