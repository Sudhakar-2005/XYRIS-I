"""
Stage G — XYRIS I app icon (replaces the legacy arc-reactor jarvis.ico).

Verifies the renamed generator renders a multi-resolution .ico and that
the JARVIS-era generator name is gone.
"""
import sys
import unittest
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ui import MainWindow


class TestAppIcon(unittest.TestCase):
    def test_build_app_icon_writes_multires_ico(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "xyris.ico"
            self.assertTrue(MainWindow._build_app_icon(out))
            self.assertGreater(out.stat().st_size, 0)
            import PIL.Image
            with PIL.Image.open(out) as im:
                sizes = {s[0] for s in im.info.get("sizes", [])}
            self.assertIn(256, sizes)
            self.assertIn(16, sizes)

    def test_generator_renamed_from_jarvis(self):
        self.assertTrue(hasattr(MainWindow, "_build_app_icon"))
        self.assertFalse(hasattr(MainWindow, "_build_jarvis_icon"))


if __name__ == "__main__":
    unittest.main()
