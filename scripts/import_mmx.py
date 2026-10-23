#!/usr/bin/env python3
"""Import the MMX viewer assets (read-only source) and build the textures (ARCHITECTURE.md §12, AM7).

Copies three.js r170 + addons, the vendor licences and the d3-celestial overlay catalogue from MMX verbatim, packs the
Hipparcos catalogue (scripts/pack_stars.py, EFF-8), records the MMX commit and sha256 of every copied file (and of
the MMX files that web/ derives from) in web/vendor/three/IMPORT.json, downsamples the Blue Marble basemap to
web/assets/earth_4096.jpg and copies the LROC WAC Moon mosaic to web/assets/moon_4096.jpg. The MMX tree is never
written.

Usage:  ~/.venvs/astro313/bin/python -I scripts/import_mmx.py [--mmx DIR] [--force]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pack_stars import pack_file  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MMX = Path.home() / "solarsystem/MMX"
COPY = ["web/vendor/three/three.module.min.js", "web/vendor/three/LICENSE",
        "web/vendor/three/addons/controls/OrbitControls.js",
        "web/vendor/three/addons/lines/Line2.js", "web/vendor/three/addons/lines/LineGeometry.js",
        "web/vendor/three/addons/lines/LineMaterial.js", "web/vendor/three/addons/lines/LineSegments2.js",
        "web/vendor/three/addons/lines/LineSegmentsGeometry.js", "web/vendor/three/addons/renderers/CSS2DRenderer.js",
        "web/vendor/STARSHIP_LICENSE", "web/vendor/CELESTIAL_LICENSE", "web/assets/stars.json"]
STARS = "web/assets/hipparcos.json"
DERIVED = {"web/states.js": "web/states.js", "web/sky.js": "web/sky.js", "web/sky-style.js": "web/sky-style.js",
           "web/index.template.html": "web/index.template.html", "scripts/build_site.mjs": "scripts/build_site.mjs",
           "web/scene/trailMaterial.js": "web/vendor/three/addons/lines/LineMaterial.js", "tests/browser.mjs": "tests/browser.mjs"}
EARTH = [Path.home() / "sda/basemaps/world.topo.bathy.200409.3x21600x10800.jpg",
         Path.home() / "sda/satobserver/app/assets/earth_day.jpg"]
MOON = ROOT.parent / "lunarmission/moon_wac_z3.jpg"
SIZE = (4096, 2048)


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def earth_texture(out: Path, force: bool) -> dict:
    from PIL import Image
    src = next(p for p in EARTH if p.exists())
    if force or not out.exists():
        Image.MAX_IMAGE_PIXELS = None
        with Image.open(src) as im:
            im.draft("RGB", (SIZE[0] * 2, SIZE[1] * 2))          # JPEG DCT pre-scale, still >= 2x the target
            im.convert("RGB").resize(SIZE, Image.LANCZOS).save(out, "JPEG", quality=85, optimize=True)
    return {"dst": str(out.relative_to(ROOT)), "src": str(src), "src_sha256": digest(src), "sha256": digest(out),
            "mode": "generated", "method": f"Pillow LANCZOS {SIZE[0]}x{SIZE[1]}, JPEG q85"}


def moon_texture(out: Path) -> dict:
    from PIL import Image
    with Image.open(MOON) as im:
        if im.size != SIZE:
            raise SystemExit(f"{MOON} is {im.size}, expected {SIZE}")
    shutil.copyfile(MOON, out)
    return {"dst": str(out.relative_to(ROOT)), "src": str(MOON), "sha256": digest(out), "mode": "copy",
            "method": "LROC WAC global mosaic, verbatim"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mmx", type=Path, default=MMX)
    ap.add_argument("--force", action="store_true", help="rebuild the Earth texture")
    a = ap.parse_args()
    commit = subprocess.run(["git", "-C", str(a.mmx), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    files = []
    for rel in COPY:
        src, dst = a.mmx / rel, ROOT / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        if digest(src) != digest(dst):
            raise SystemExit(f"copy mismatch {rel}")
        files.append({"dst": rel, "src": rel, "sha256": digest(dst), "mode": "copy"})
    for dst, src in DERIVED.items():
        files.append({"dst": dst, "src": src, "src_sha256": digest(a.mmx / src), "mode": "derived"})
    (ROOT / "web/assets").mkdir(parents=True, exist_ok=True)
    files.append(pack_file(a.mmx / STARS, ROOT / STARS) | {"src": STARS})
    files.append(earth_texture(ROOT / "web/assets/earth_4096.jpg", a.force))
    files.append(moon_texture(ROOT / "web/assets/moon_4096.jpg"))
    record = {"mmx": str(a.mmx), "mmx_commit": commit, "imported_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "three": "r170", "files": files}
    out = ROOT / "web/vendor/three/IMPORT.json"
    out.write_text(json.dumps(record, indent=1) + "\n")
    print(f"imported {len(COPY)} files from MMX {commit[:7]}; textures {SIZE[0]}x{SIZE[1]}; wrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
