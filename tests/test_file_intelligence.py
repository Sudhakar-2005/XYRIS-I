"""
JARVIS File Intelligence Test Suite.

Tests the file_intelligence.py action tool using temporary directories
to avoid touching real user files.
"""
import shutil
import tempfile
import time
import unittest
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from actions.file_intelligence import (
    file_intelligence, _file_hash, _match_pattern, _format_size,
)


class TestHelpers(unittest.TestCase):
    """Test internal helper functions."""

    def test_format_size_bytes(self):
        self.assertEqual(_format_size(0), "0.0 B")
        self.assertEqual(_format_size(500), "500.0 B")

    def test_format_size_kb(self):
        self.assertIn("KB", _format_size(1500))

    def test_format_size_mb(self):
        self.assertIn("MB", _format_size(1_500_000))

    def test_format_size_gb(self):
        self.assertIn("GB", _format_size(1_500_000_000))

    def test_file_hash_returns_string(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
            f.write(b"hello world")
            f.flush()
            path = Path(f.name)
        try:
            h = _file_hash(path)
            self.assertIsInstance(h, str)
            self.assertEqual(len(h), 64)  # SHA-256 hex
        finally:
            path.unlink()

    def test_file_hash_nonexistent(self):
        h = _file_hash(Path("/nonexistent/file.txt"))
        self.assertEqual(h, "")

    def test_match_pattern_exact(self):
        self.assertTrue(_match_pattern("test.txt", "test.txt"))

    def test_match_pattern_star(self):
        self.assertTrue(_match_pattern("test.txt", "*.txt"))
        self.assertFalse(_match_pattern("test.py", "*.txt"))

    def test_match_pattern_question(self):
        self.assertTrue(_match_pattern("test.ab", "test.??"))
        self.assertFalse(_match_pattern("test.txt", "test.??"))
        self.assertTrue(_match_pattern("test.abc", "test.???"))


class TestFindDuplicates(unittest.TestCase):
    """Test duplicate file detection."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="jarvis_test_"))
        (self.tmpdir / "a.txt").write_text("same content")
        (self.tmpdir / "b.txt").write_text("same content")
        (self.tmpdir / "c.txt").write_text("different content")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_no_duplicates(self):
        d = self.tmpdir / "nodups"
        d.mkdir()
        (d / "a.txt").write_text("aaa")
        (d / "b.txt").write_text("bbb")
        result = file_intelligence({
            "action": "find_duplicates", "path": str(d),
        })
        self.assertIn("no duplicate", result.lower())

    def test_finds_duplicates(self):
        result = file_intelligence({
            "action": "find_duplicates", "path": str(self.tmpdir),
        })
        self.assertIn("duplicate group", result.lower())

    def test_max_results(self):
        result = file_intelligence({
            "action": "find_duplicates", "path": str(self.tmpdir),
            "max_results": "1",
        })
        self.assertIsInstance(result, str)

    def test_nonexistent_path(self):
        result = file_intelligence({
            "action": "find_duplicates",
            "path": str(Path.home() / "nonexistent_jarvis_test_path"),
        })
        self.assertIn("does not exist", result)


class TestSearchContent(unittest.TestCase):
    """Test content search (grep-like)."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="jarvis_test_"))
        (self.tmpdir / "a.txt").write_text("Hello World\nSecond line")
        (self.tmpdir / "b.txt").write_text("Another file\nHello Again")
        (self.tmpdir / "c.py").write_text("def hello():\n    pass")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_finds_content(self):
        result = file_intelligence({
            "action": "search_content", "path": str(self.tmpdir),
            "query": "Hello",
        })
        self.assertIn("match", result.lower())

    def test_no_match(self):
        result = file_intelligence({
            "action": "search_content", "path": str(self.tmpdir),
            "query": "zzzznotfound",
        })
        self.assertIn("no match", result.lower())

    def test_empty_query(self):
        result = file_intelligence({
            "action": "search_content", "path": str(self.tmpdir),
            "query": "",
        })
        self.assertIn("provide a search query", result.lower())

    def test_extension_filter(self):
        result = file_intelligence({
            "action": "search_content", "path": str(self.tmpdir),
            "query": "hello", "extension": "py",
        })
        self.assertIn("match", result.lower())

    def test_max_results(self):
        result = file_intelligence({
            "action": "search_content", "path": str(self.tmpdir),
            "query": "Hello", "max_results": "1",
        })
        self.assertIn("1 match", result)


class TestRecentFiles(unittest.TestCase):
    """Test recent files listing."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="jarvis_test_"))
        (self.tmpdir / "old.txt").write_text("old")
        (self.tmpdir / "new.txt").write_text("new")
        # Make one file clearly newer
        new_time = time.time() + 10
        (self.tmpdir / "new.txt").touch()
        import os
        os.utime(str(self.tmpdir / "new.txt"), (new_time, new_time))

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_lists_recent(self):
        result = file_intelligence({
            "action": "recent_files", "path": str(self.tmpdir),
        })
        self.assertIn("new.txt", result)

    def test_max_results(self):
        result = file_intelligence({
            "action": "recent_files", "path": str(self.tmpdir),
            "max_results": "1",
        })
        self.assertIn("Most recently", result)

    def test_empty_dir(self):
        d = self.tmpdir / "empty"
        d.mkdir()
        result = file_intelligence({
            "action": "recent_files", "path": str(d),
        })
        self.assertIn("no files", result.lower())


class TestDirSize(unittest.TestCase):
    """Test directory size analysis."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="jarvis_test_"))
        sub = self.tmpdir / "subdir"
        sub.mkdir()
        (self.tmpdir / "root.txt").write_text("a" * 1000)
        (sub / "child.txt").write_text("b" * 2000)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_shows_sizes(self):
        result = file_intelligence({
            "action": "dir_size", "path": str(self.tmpdir),
        })
        self.assertIn("Size analysis", result)
        self.assertIn("subdir", result)

    def test_empty_dir(self):
        d = self.tmpdir / "empty"
        d.mkdir()
        result = file_intelligence({
            "action": "dir_size", "path": str(d),
        })
        self.assertIn("empty", result.lower())

    def test_nonexistent_path(self):
        result = file_intelligence({
            "action": "dir_size",
            "path": str(Path.home() / "nonexistent_jarvis_test_path"),
        })
        self.assertIn("does not exist", result)


class TestFileTypeStats(unittest.TestCase):
    """Test file type statistics."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="jarvis_test_"))
        (self.tmpdir / "a.txt").write_text("a")
        (self.tmpdir / "b.txt").write_text("b")
        (self.tmpdir / "c.py").write_text("c")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_shows_distribution(self):
        result = file_intelligence({
            "action": "file_type_stats", "path": str(self.tmpdir),
        })
        self.assertIn(".txt", result)
        self.assertIn(".py", result)

    def test_empty_dir(self):
        d = self.tmpdir / "empty"
        d.mkdir()
        result = file_intelligence({
            "action": "file_type_stats", "path": str(d),
        })
        self.assertIn("no files", result.lower())


class TestBatchRename(unittest.TestCase):
    """Test batch rename functionality."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="jarvis_test_"))
        (self.tmpdir / "photo_001.jpg").write_bytes(b"\xff\xd8")
        (self.tmpdir / "photo_002.jpg").write_bytes(b"\xff\xd8")
        (self.tmpdir / "other.txt").write_text("x")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_dry_run(self):
        result = file_intelligence({
            "action": "batch_rename", "path": str(self.tmpdir),
            "find": "photo_", "replace": "img_", "extension": "jpg",
        })
        self.assertIn("dry run", result.lower())

    def test_apply_rename(self):
        result = file_intelligence({
            "action": "batch_rename", "path": str(self.tmpdir),
            "find": "photo_", "replace": "img_", "extension": "jpg",
            "dry_run": "false",
        })
        self.assertIn("renamed", result.lower())
        self.assertTrue((self.tmpdir / "img_001.jpg").exists())
        self.assertTrue((self.tmpdir / "img_002.jpg").exists())

    def test_no_matches(self):
        result = file_intelligence({
            "action": "batch_rename", "path": str(self.tmpdir),
            "find": "zzz_", "replace": "yyy_",
        })
        self.assertIn("no files matched", result.lower())


class TestTempCleanup(unittest.TestCase):
    """Test temp file cleanup."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="jarvis_test_"))
        (self.tmpdir / "old.tmp").write_text("old")
        (self.tmpdir / "recent.txt").write_text("recent")
        # Make the .tmp file old
        old_time = time.time() - (60 * 86400)
        import os
        os.utime(str(self.tmpdir / "old.tmp"), (old_time, old_time))

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_dry_run(self):
        result = file_intelligence({
            "action": "temp_cleanup", "path": str(self.tmpdir),
        })
        self.assertIn("dry run", result.lower())

    def test_apply_cleanup(self):
        result = file_intelligence({
            "action": "temp_cleanup", "path": str(self.tmpdir),
            "dry_run": "false",
        })
        self.assertIn("cleaned up", result.lower())

    def test_no_old_files(self):
        d = self.tmpdir / "fresh"
        d.mkdir()
        (d / "file.txt").write_text("x")
        result = file_intelligence({
            "action": "temp_cleanup", "path": str(d),
        })
        self.assertIn("no old temp", result.lower())


class TestUnknownAction(unittest.TestCase):
    """Test unknown action handling."""

    def test_unknown_action(self):
        result = file_intelligence({"action": "bogus"})
        self.assertIn("unknown file intelligence action", result.lower())

    def test_empty_action(self):
        result = file_intelligence({"action": ""})
        self.assertIn("unknown file intelligence action", result.lower())


class TestToolDeclaration(unittest.TestCase):
    """Verify the tool declaration meets the loader contract."""

    def test_tool_dict_exists(self):
        from actions.file_intelligence import TOOL
        self.assertIsInstance(TOOL, dict)

    def test_tool_name(self):
        from actions.file_intelligence import TOOL
        self.assertEqual(TOOL["name"], "file_intelligence")

    def test_tool_has_handler(self):
        from actions.file_intelligence import TOOL
        self.assertTrue(callable(TOOL["handler"]))

    def test_tool_parameters_are_object(self):
        from actions.file_intelligence import TOOL
        self.assertEqual(TOOL["parameters"]["type"], "OBJECT")

    def test_tool_description_nonempty(self):
        from actions.file_intelligence import TOOL
        self.assertTrue(len(TOOL["description"]) > 20)


if __name__ == "__main__":
    unittest.main()
