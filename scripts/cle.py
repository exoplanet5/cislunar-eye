#!/usr/bin/env python3
"""Entry point for the cislunar-eye pipeline that works under `python -I` (which ignores PYTHONPATH).

Usage:  ~/.venvs/astro313/bin/python -I scripts/cle.py <command> [options]   (see cle/cli.py)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cle.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
