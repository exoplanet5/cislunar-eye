"""Ephemeris adapters. Each module exposes

    build(target: dict, win: cle.timeutil.Window, ctx: cle.context.Context) -> cle.statetable.StateTable

where `target` is one object of catalog/master_list.json (keys as in master_list.tsv plus
"sources"). `dispatch` picks the adapter from target["primary_ephem"] (ARCHITECTURE.md AM8):

    horizons:<id>[..]                 -> horizons      (Horizons vectors, MMX request/validation)
    bgtle:<file>[..]                  -> bgtle         (Bill Gray hex TLE inside its Range)
    spacetrack_gp:<norad>[..]         -> gp            (SDP4 -> TEME -> GCRS; EM_* regimes: n-body from GP state, AM6)
    neocpsat_fit:<path>[..]           -> fo            (local find_orb on the neocpsat obs, geocentric vectors)
    pluto_mpec:<url>[..]              -> neocp         (live NEOCP: find_orb on cached obs + tracklets)
    STALE <any of the above>          -> extrap        (latest state, Earth+Moon+Sun integration, AM5)
"""
from __future__ import annotations

import importlib
import re

KIND_TO_MODULE = {
    "horizons": "horizons",
    "bgtle": "bgtle",
    "spacetrack_gp": "gp",
    "neocpsat_fit": "fo",
    "pluto_mpec": "neocp",
    "neocp_obs": "neocp",
}


def primary(target: dict) -> tuple[str, str, bool]:
    """(kind, ref, stale) parsed from target["primary_ephem"]."""
    text = target.get("primary_ephem", "").strip()
    stale = text.startswith("STALE ")
    text = text.removeprefix("STALE ").strip()
    m = re.match(r"^(\w+):(.*?)(\[[^\]]*\])?(\s*\(.*\))?$", text)
    if not m:
        return "", "", stale
    return m[1], m[2], stale


def dispatch(target: dict):
    """Adapter module for a target, or None when it is not drawn."""
    if target.get("draw") != "Y":
        return None
    kind, _, stale = primary(target)
    name = "extrap" if stale else KIND_TO_MODULE.get(kind)
    return importlib.import_module(f"cle.adapters.{name}") if name else None
