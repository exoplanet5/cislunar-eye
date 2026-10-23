"""Earth orientation quaternions for the globe (ARCHITECTURE.md §5.9; MMX src/ephemeris.py:115-128).

Skyfield's itrs.rotation_at(t) is ICRF -> ITRS; it is transposed to ITRS -> ICRF, conjugated into the
Y-up scene basis with MAPPING (X,Y,Z) -> (X,Z,-Y), converted to xyzw and sign-aligned so consecutive
samples have a positive dot product (the browser slerps between them). Builtin timescale, no polar
motion. scripts/check_earth_rotation_sampling.py: slerp error 0.0002" at 1 h, 0.0062" at 6 h.
"""
from __future__ import annotations

import numpy as np
from scipy.spatial.transform import Rotation

MAPPING = np.array([[1., 0., 0.], [0., 0., 1.], [0., -1., 0.]])
STEP_S = 3600


def scene_matrices(ts, t_utc_s):
    from skyfield.framelib import itrs
    days, secs = np.divmod(np.atleast_1d(np.asarray(t_utc_s, dtype=float)), 86400)
    t = ts.utc(1970, 1, 1 + days, 0, 0, secs)        # POSIX seconds: no leap seconds (as Context.times)
    fixed_to_icrf = np.moveaxis(itrs.rotation_at(t), 2, 0).transpose(0, 2, 1)
    return MAPPING @ fixed_to_icrf @ MAPPING.T


def quaternions(ts, t_utc_s) -> np.ndarray:
    q = Rotation.from_matrix(scene_matrices(ts, t_utc_s)).as_quat()
    for i in range(1, len(q)):
        if np.dot(q[i - 1], q[i]) < 0:
            q[i] *= -1
    return q


def track(win, ts, step_s: float = STEP_S) -> dict:
    """{t0_s, step_s, xyzw (n,4)} on the window grid."""
    t = win.grid(step_s)
    return {"t0_s": float(t[0]), "step_s": float(step_s), "xyzw": quaternions(ts, t)}


def fixtures(ts, t_utc_s) -> list[dict]:
    """Greenwich, 90E and the pole in scene coordinates at the given epochs (MMX orientation fixtures)."""
    out = []
    for t, m in zip(np.atleast_1d(t_utc_s), scene_matrices(ts, t_utc_s)):
        for name, ecef in (("Greenwich", [1, 0, 0]), ("90E", [0, 1, 0]), ("north", [0, 0, 1])):
            local = MAPPING @ np.asarray(ecef, float)
            out.append({"t_s": float(t), "name": name, "local_scene": local.tolist(), "inertial_scene": (m @ local).tolist()})
    return out
