"""Uniform state table shared by every adapter (ARCHITECTURE.md §5.1).

Each adapter returns a StateTable; `save` writes build/states/<cle_id>.npz (arrays) plus
<cle_id>.json (everything else). Positions are km, velocities km/s, ICRF axes, geometric,
relative to `centre` ("earth", or "moon" for lunar orbiters), at UTC epochs
t0_s + step_s * k (seconds since 1970).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

QUALITIES = ("ref", "pred", "fit", "extrap", "gp", "conic")
JOINS = ("start", "continuous", "splice", "gap", "source")


@dataclass
class Segment:
    t0_s: float
    step_s: float
    r_km: np.ndarray                     # (n, 3) float64
    v_kms: np.ndarray                    # (n, 3) float64
    source: str                          # "<kind>:<ref>", e.g. horizons:-170, gp:59228, fo:Spektr-RG@2026-10-04
    quality: str                         # one of QUALITIES
    join_before: str = "start"           # one of JOINS
    sigma_km: np.ndarray | None = None   # (n,) float32 1-sigma position uncertainty, if known
    jump_km: float = 0.0                 # position jump at the join, if join_before != continuous

    def __post_init__(self):
        self.r_km = np.asarray(self.r_km, dtype=float).reshape(-1, 3)
        self.v_kms = np.asarray(self.v_kms, dtype=float).reshape(-1, 3)
        if len(self.r_km) != len(self.v_kms) or len(self.r_km) < 2:
            raise ValueError("segment needs >= 2 matching r/v rows")
        if self.quality not in QUALITIES or self.join_before not in JOINS:
            raise ValueError(f"bad quality/join {self.quality}/{self.join_before}")
        if not (np.isfinite(self.r_km).all() and np.isfinite(self.v_kms).all()):
            raise ValueError("non-finite state")

    @property
    def n(self) -> int:
        return len(self.r_km)

    @property
    def t_s(self) -> np.ndarray:
        return self.t0_s + self.step_s * np.arange(self.n)


@dataclass
class StateTable:
    cle_id: str
    centre: str                                       # "earth" | "moon"
    segments: list[Segment]
    provenance: list[dict] = field(default_factory=list)   # raw paths, sha256, request/command, retrieved_utc
    validation: dict = field(default_factory=dict)          # midpoint error, splices, regime_now, ...
    caveat: str = ""
    now_utc: str = ""                                       # build window the table was made for (cli.states stamps
    t0_utc: str = ""                                        # them; export draws nothing from another window, DW-02)
    t1_utc: str = ""

    def save(self, directory: Path) -> Path:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        arrays = {}
        meta = {"cle_id": self.cle_id, "centre": self.centre, "provenance": self.provenance,
                "validation": self.validation, "caveat": self.caveat, "now_utc": self.now_utc, "t0_utc": self.t0_utc,
                "t1_utc": self.t1_utc, "segments": []}
        for k, s in enumerate(self.segments):
            arrays[f"r{k}"], arrays[f"v{k}"] = s.r_km, s.v_kms
            if s.sigma_km is not None:
                arrays[f"s{k}"] = np.asarray(s.sigma_km, dtype=np.float32)
            d = {k2: v for k2, v in asdict(s).items() if k2 not in ("r_km", "v_kms", "sigma_km")}
            meta["segments"].append(d | {"n": s.n, "has_sigma": s.sigma_km is not None})
        tmp = directory / f".{self.cle_id}.npz"
        np.savez_compressed(tmp, **arrays)
        tmp.replace(directory / f"{self.cle_id}.npz")
        jt = directory / f".{self.cle_id}.json"
        jt.write_text(json.dumps(meta, indent=1, default=float))
        jt.replace(directory / f"{self.cle_id}.json")
        return directory / f"{self.cle_id}.npz"

    @classmethod
    def load(cls, directory: Path, cle_id: str) -> "StateTable":
        directory = Path(directory)
        meta = json.loads((directory / f"{cle_id}.json").read_text())
        z = np.load(directory / f"{cle_id}.npz")
        segs = []
        for k, d in enumerate(meta["segments"]):
            segs.append(Segment(t0_s=d["t0_s"], step_s=d["step_s"], r_km=z[f"r{k}"], v_kms=z[f"v{k}"],
                                source=d["source"], quality=d["quality"], join_before=d["join_before"],
                                sigma_km=z[f"s{k}"] if d.get("has_sigma") else None, jump_km=d.get("jump_km", 0.0)))
        return cls(cle_id=meta["cle_id"], centre=meta["centre"], segments=segs, provenance=meta["provenance"],
                   validation=meta["validation"], caveat=meta.get("caveat", ""), now_utc=meta.get("now_utc", ""),
                   t0_utc=meta.get("t0_utc", ""), t1_utc=meta.get("t1_utc", ""))
