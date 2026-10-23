"""Integration rules between build/states, build/neocp.json, the export and the page (unittest, -I).

    ~/.venvs/astro313/bin/python -I -m unittest discover -s tests -p 'test_*.py'
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from cle import export  # noqa: E402


class NotDrawnTest(unittest.TestCase):
    def test_failed_build_is_not_drawn_even_with_old_states(self):
        """A target whose adapter failed this build is listed with the reason, never drawn from stale states."""
        with tempfile.TemporaryDirectory() as d, mock.patch.object(export, "STATES", Path(d)):
            (Path(d) / "x.json").write_text("{}")
            row = {"cid": "x"}
            self.assertEqual(export.not_drawn(row, {"status": "ok"}), "")
            why = export.not_drawn(row, {"status": "failed", "error": "RuntimeError: x: no coherent fit (a.html: e=1.19e+03, 4/4 obs)"})
            self.assertEqual(why, "build failed: no coherent fit (details: build/validation_report.md)")
            self.assertIn("no states built", export.not_drawn({"cid": "y"}, {}))

    def test_neocp_js_payload(self):
        """neocp.js is build/neocp.json over the §11 defaults; without it, a valid empty layer."""
        with tempfile.TemporaryDirectory() as d, mock.patch.object(export, "BUILD", Path(d)):
            empty = export.neocp("2026-10-21T13:00:00Z")
            self.assertEqual((empty["objects"], empty["removed"], empty["schema"]), ({}, [], "cle/neocp@1"))
            (Path(d) / "neocp.json").write_text('{"neocp_snapshot_utc": "2026-10-21T07:02:26Z", "objects": {"A": {}},'
                                                ' "attached": {"tiandu-1": {"tracklets": []}}}')
            full = export.neocp("2026-10-21T13:00:00Z")
            self.assertEqual(full["neocp_snapshot_utc"], "2026-10-21T07:02:26Z")
            self.assertEqual(set(full["objects"]), {"A"})
            self.assertIn("tiandu-1", full["attached"])


if __name__ == "__main__":
    unittest.main()
