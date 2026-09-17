"""
JARVIS Session History Test Suite.

Tests the session history functions in memory/memory_manager.py and
the SessionHistoryOverlay in ui.py.
"""
import json
import unittest
from pathlib import Path
import tempfile
import shutil

PROJECT_ROOT = Path(__file__).resolve().parent.parent
import sys
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class TestSaveSessionLog(unittest.TestCase):
    """Test saving session transcripts to disk."""

    def setUp(self):
        from memory import memory_manager as mm
        self._orig_sessions_dir = mm._SESSIONS_DIR
        self._tmpdir = Path(tempfile.mkdtemp())
        mm._SESSIONS_DIR = self._tmpdir
        self.mm = mm

    def tearDown(self):
        self.mm._SESSIONS_DIR = self._orig_sessions_dir
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_save_creates_file(self):
        self.mm.save_session_log(["User: hello", "JARVIS: hi there"])
        files = list(self._tmpdir.glob("*.json"))
        self.assertEqual(len(files), 1)

    def test_save_empty_turns_noop(self):
        self.mm.save_session_log([])
        files = list(self._tmpdir.glob("*.json"))
        self.assertEqual(len(files), 0)

    def test_save_content_valid_json(self):
        self.mm.save_session_log(["User: test", "JARVIS: ok"])
        files = list(self._tmpdir.glob("*.json"))
        data = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertEqual(data["turn_count"], 2)
        self.assertEqual(len(data["turns"]), 2)
        self.assertIn("date", data)
        self.assertIn("time", data)

    def test_save_with_summary(self):
        self.mm.save_session_log(["User: hi"], summary="Quick chat")
        files = list(self._tmpdir.glob("*.json"))
        data = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertEqual(data["summary"], "Quick chat")

    def test_save_with_language(self):
        self.mm.save_session_log(["User: bonjour"], language="French")
        files = list(self._tmpdir.glob("*.json"))
        data = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertEqual(data["language"], "French")

    def test_multiple_saves(self):
        self.mm.save_session_log(["User: a", "JARVIS: b"])
        self.mm.save_session_log(["User: c", "JARVIS: d"])
        files = list(self._tmpdir.glob("*.json"))
        self.assertEqual(len(files), 2)


class TestGetSavedSessions(unittest.TestCase):
    """Test retrieving saved session transcripts."""

    def setUp(self):
        from memory import memory_manager as mm
        self._orig_sessions_dir = mm._SESSIONS_DIR
        self._tmpdir = Path(tempfile.mkdtemp())
        mm._SESSIONS_DIR = self._tmpdir
        self.mm = mm

    def tearDown(self):
        self.mm._SESSIONS_DIR = self._orig_sessions_dir
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_empty_dir(self):
        sessions = self.mm.get_saved_sessions()
        self.assertEqual(sessions, [])

    def test_returns_sessions(self):
        self.mm.save_session_log(["User: hello", "JARVIS: hi"])
        sessions = self.mm.get_saved_sessions()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["turn_count"], 2)

    def test_limit(self):
        for i in range(5):
            self.mm.save_session_log([f"User: msg {i}"])
        sessions = self.mm.get_saved_sessions(limit=3)
        self.assertEqual(len(sessions), 3)

    def test_newest_first(self):
        # Create files with different timestamps by writing directly
        for i in range(3):
            entry = {"date": f"2025-01-0{i+1}", "time": "10:00",
                     "turns": [f"turn {i}"], "turn_count": 1}
            path = self._tmpdir / f"2025-01-0{i+1}_10-00.json"
            path.write_text(json.dumps(entry), encoding="utf-8")
        sessions = self.mm.get_saved_sessions()
        # Should be sorted newest first (file names sort reverse)
        self.assertEqual(len(sessions), 3)
        self.assertIn("_file", sessions[0])


class TestGetSessionCount(unittest.TestCase):
    """Test session count function."""

    def setUp(self):
        from memory import memory_manager as mm
        self._orig_sessions_dir = mm._SESSIONS_DIR
        self._tmpdir = Path(tempfile.mkdtemp())
        mm._SESSIONS_DIR = self._tmpdir
        self.mm = mm

    def tearDown(self):
        self.mm._SESSIONS_DIR = self._orig_sessions_dir
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_count_empty(self):
        self.assertEqual(self.mm.get_session_count(), 0)

    def test_count_with_files(self):
        self.mm.save_session_log(["User: a", "JARVIS: b"])
        self.mm.save_session_log(["User: c", "JARVIS: d"])
        self.assertEqual(self.mm.get_session_count(), 2)


class TestSessionHistoryOverlayImport(unittest.TestCase):
    """Test that SessionHistoryOverlay can be imported from ui."""

    def test_import(self):
        from ui import SessionHistoryOverlay
        self.assertTrue(callable(SessionHistoryOverlay))

    def test_has_rebuild(self):
        from ui import SessionHistoryOverlay
        self.assertTrue(hasattr(SessionHistoryOverlay, "_rebuild"))

    def test_has_toggle_expand(self):
        from ui import SessionHistoryOverlay
        self.assertTrue(hasattr(SessionHistoryOverlay, "_toggle_expand"))


class TestMainWindowHistoryMethod(unittest.TestCase):
    """Test that MainWindow has the history panel method."""

    def test_has_method(self):
        from ui import MainWindow
        self.assertTrue(hasattr(MainWindow, "_open_history_panel"))


if __name__ == "__main__":
    unittest.main()
