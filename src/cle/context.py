"""Context bodies: geocentric geometric ICRF states of the Moon and Sun from DE440s (~/JPLDE).

Every adapter that needs the Moon or Sun (Moon-centred lunar tracks, n-body integration, regime
checks) takes them from one Context so the whole build uses one kernel and one timescale.
"""
from __future__ import annotations

from functools import cached_property
from pathlib import Path

import numpy as np

KERNEL = Path.home() / "JPLDE/de440s.bsp"
# DE440 GM values, km^3/s^2
GM_EARTH = 398600.435507
GM_MOON = 4902.800118
GM_SUN = 132712440041.279419
MU_EM = GM_MOON / (GM_EARTH + GM_MOON)                       # 0.0121506
MU_SE = (GM_EARTH + GM_MOON) / (GM_SUN + GM_EARTH + GM_MOON)  # 3.0404e-6
RE_KM, RM_KM = 6378.137, 1737.4


class Context:
    def __init__(self, kernel: Path = KERNEL):
        from skyfield.api import load, load_file
        self.kernel_path = Path(kernel)
        self.ts = load.timescale(builtin=True)
        self.eph = load_file(str(self.kernel_path))
        self.earth, self.moon, self.sun = self.eph["earth"], self.eph["moon"], self.eph["sun"]

    def times(self, t_utc_s):
        """Skyfield Time for UTC seconds since 1970."""
        t = np.atleast_1d(np.asarray(t_utc_s, dtype=float))
        days, secs = np.divmod(t, 86400)        # day + seconds-of-day: Skyfield takes leap seconds from the day
        return self.ts.utc(1970, 1, 1 + days, 0, 0, secs)

    def _rv(self, body, t_utc_s) -> np.ndarray:
        p = (body - self.earth).at(self.times(t_utc_s))
        return np.hstack([p.position.km.T, p.velocity.km_per_s.T])

    def moon_rv(self, t_utc_s) -> np.ndarray:
        """(n, 6) geocentric Moon state, km and km/s, ICRF, geometric."""
        return self._rv(self.moon, t_utc_s)

    def sun_rv(self, t_utc_s) -> np.ndarray:
        """(n, 6) geocentric Sun state, km and km/s, ICRF, geometric."""
        return self._rv(self.sun, t_utc_s)

    @cached_property
    def kernel_sha256(self) -> str:
        import hashlib
        h = hashlib.sha256()
        with open(self.kernel_path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
