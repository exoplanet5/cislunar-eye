"""Measure how coarsely the baked Earth-orientation quaternions can be sampled.

Reproduces the MMX viewer's globe-rotation logic (/Users/mickey/solarsystem/MMX/src/ephemeris.py:115-128):
Skyfield ``itrs.rotation_at(t)`` (ICRF -> ITRS) is transposed to ITRS -> ICRF, conjugated into the
Y-up scene basis with MAPPING (X,Y,Z) -> (X,Z,-Y), converted to xyzw quaternions and sign-aligned so
consecutive samples have a positive dot product. The browser then slerps between neighbours
(MMX web/app.js:333). This script samples the quaternions at several steps over the cislunar-eye
default display window, slerps to the midpoints and compares with the exact rotation there.

Reported error is the angle of the residual rotation and the corresponding surface displacement on a
6378.137 km sphere. Pure computation: no network, no downloads, no kernel (Earth rotation only).

Usage:
    ~/.venvs/astro313/bin/python -I scripts/check_earth_rotation_sampling.py [--start ISO] [--days N]
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

import numpy as np
from scipy.spatial.transform import Rotation, Slerp
from skyfield.api import load
from skyfield.framelib import itrs

MAPPING = np.array([[1., 0., 0.], [0., 0., 1.], [0., -1., 0.]])
RE_KM = 6378.137


def scene_rotations(ts, epochs):
    t = ts.from_datetimes(epochs)
    fixed_to_icrf = np.moveaxis(itrs.rotation_at(t), 2, 0).transpose(0, 2, 1)
    return Rotation.from_matrix(MAPPING @ fixed_to_icrf @ MAPPING.T)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", default="2026-09-21T00:00:00Z")
    parser.add_argument("--days", type=float, default=60.0)
    args = parser.parse_args()
    start = datetime.fromisoformat(args.start.replace("Z", "+00:00")).astimezone(timezone.utc)
    ts = load.timescale(builtin=True)
    total = args.days * 86400
    print(f"window {start.isoformat()} + {args.days} d; builtin Skyfield timescale, polar motion omitted")
    for step in (60, 600, 1800, 3600, 10800, 21600):
        n = int(total // step) + 1
        knots = [start + timedelta(seconds=step * i) for i in range(n)]
        mids = [start + timedelta(seconds=step * (i + 0.5)) for i in range(n - 1)]
        # Midpoint subset keeps the run fast for fine steps.
        pick = np.unique(np.linspace(0, n - 2, min(n - 1, 4000)).astype(int))
        rot = scene_rotations(ts, knots)
        times = np.arange(n) * step
        interp = Slerp(times, rot)(times[pick] + step / 2)
        exact = scene_rotations(ts, [mids[i] for i in pick])
        err = (exact * interp.inv()).magnitude()
        print(f"step {step:6d} s  quats {n:6d}  max slerp error {np.degrees(err.max()) * 3600:9.4f} arcsec"
              f"  = {err.max() * RE_KM * 1000:8.2f} m at the surface")


if __name__ == "__main__":
    main()
