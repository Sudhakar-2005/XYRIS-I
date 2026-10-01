"""HandTracker cleanup guard — the mediapipe env fixes (audit §3.12).

Two log errors drove these tests:

1. ``'HandTracker' object has no attribute '_hands'`` — __init__ raised
   (solutions-less mediapipe build) and __del__ then raised a SECOND error
   on the half-built object. close() must tolerate missing state.
2. ``module 'mediapipe' has no attribute 'solutions'`` — mediapipe 1.0.x /
   0.10.30+ removed the legacy solutions API the spatial engine is built
   on. The venv is pinned to 0.10.21; the construction test fails loudly
   if a future upgrade silently drops it again.
"""
from __future__ import annotations

import unittest
from unittest import mock


class TestCleanupGuard(unittest.TestCase):

    def test_close_tolerates_a_half_built_object(self):
        from core.spatial.hand_tracker import HandTracker
        half = HandTracker.__new__(HandTracker)   # __init__ never ran
        half.close()          # must not raise
        half.__del__()        # the exact call that used to log an error
        half.close()          # idempotent

    def test_del_on_half_built_object_does_not_raise(self):
        import gc
        from core.spatial.hand_tracker import HandTracker
        half = HandTracker.__new__(HandTracker)
        del half
        gc.collect()


class TestRealConstruction(unittest.TestCase):

    def test_mediapipe_solutions_api_is_present(self):
        try:
            import mediapipe as mp
        except ImportError:
            self.skipTest("mediapipe not installed")
        self.assertTrue(hasattr(mp, "solutions"),
                        "mediapipe lost the legacy solutions API — the "
                        "spatial engine needs 0.10.21 (pip install "
                        "mediapipe==0.10.21)")
        self.assertTrue(hasattr(mp.solutions.hands, "Hands"))

    def test_handtracker_constructs_and_closes(self):
        try:
            import mediapipe  # noqa: F401
        except ImportError:
            self.skipTest("mediapipe not installed")
        from core.spatial.hand_tracker import HandTracker
        tracker = HandTracker()
        try:
            self.assertIsNotNone(tracker._hands)
        finally:
            tracker.close()      # real resources released, no exception
        tracker.close()          # idempotent

    def test_missing_mediapipe_raises_a_clear_import_error(self):
        from core.spatial import hand_tracker
        with mock.patch.object(hand_tracker, "HAS_MEDIAPIPE", False), \
                mock.patch.object(hand_tracker, "mp", None):
            with self.assertRaises(ImportError):
                hand_tracker.HandTracker()


if __name__ == "__main__":
    unittest.main()
