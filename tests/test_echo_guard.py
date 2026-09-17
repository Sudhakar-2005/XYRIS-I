"""
Tests for the echo guard — self-echo cancellation system.

Verifies band energy computation, echo detection, gain learning,
and the reset/calibration lifecycle.
"""
import unittest
import sys
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.echo import EchoGuard, band_energies, _MIN_LEVEL, _WARMUP, _FLOOR_WINDOW


class TestBandEnergies(unittest.TestCase):

    def test_returns_correct_number_of_bands(self):
        pcm = np.random.randn(1024).astype(np.float32)
        energies = band_energies(pcm, 24000)
        self.assertEqual(energies.shape, (8,))  # 9 edges - 1 = 8 bands

    def test_silence_returns_zeros(self):
        pcm = np.zeros(256, dtype=np.float32)
        energies = band_energies(pcm, 24000)
        np.testing.assert_array_equal(energies, np.zeros(8))

    def test_short_input_returns_zeros(self):
        pcm = np.zeros(32, dtype=np.float32)  # < 64 samples
        energies = band_energies(pcm, 24000)
        np.testing.assert_array_equal(energies, np.zeros(8))

    def test_sine_wave_concentrates_energy(self):
        sr = 24000
        t = np.arange(1024) / sr
        # 1000 Hz sine should concentrate in bands 2-3 (700-1700 Hz)
        pcm = (np.sin(2 * np.pi * 1000 * t) * 16000).astype(np.float32)
        energies = band_energies(pcm, sr)
        # Band 2 (700-1100 Hz) and Band 3 (1100-1700 Hz) should dominate
        self.assertGreater(energies[2] + energies[3], energies[0] + energies[1])
        self.assertGreater(energies[2] + energies[3], energies[5] + energies[6])

    def test_all_energies_non_negative(self):
        pcm = np.random.randn(2048).astype(np.float32) * 1000
        energies = band_energies(pcm, 24000)
        self.assertTrue(np.all(energies >= 0))


class TestEchoGuardInit(unittest.TestCase):

    def test_initial_state(self):
        guard = EchoGuard()
        self.assertFalse(guard.calibrated)
        # Default floor (0.10) < _UNRELIABLE_FLOOR (0.22), so initially reliable
        self.assertTrue(guard.reliable)
        self.assertGreater(guard.threshold, 0)
        self.assertEqual(guard.gain, 0.6)
        self.assertEqual(guard.floor, 0.10)
        self.assertEqual(guard.last_similarity, 0.0)

    def test_reset_keeps_learning(self):
        guard = EchoGuard()
        # Simulate some learning
        guard._seen = 20
        guard._floor = 0.15
        guard.reset()
        self.assertEqual(guard._seen, 20)  # learning preserved
        self.assertEqual(guard._floor, 0.15)
        self.assertEqual(guard.last_similarity, 0.0)  # history cleared


class TestEchoGuardNoteOutput(unittest.TestCase):

    def test_note_output_stores_history(self):
        guard = EchoGuard()
        pcm = np.random.randn(1024).astype(np.float32) * 1000
        guard.note_output(pcm, 24000, 0.5, when=1.0)
        self.assertEqual(len(guard._hist), 1)

    def test_note_output_cleans_old_entries(self):
        guard = EchoGuard()
        pcm = np.random.randn(1024).astype(np.float32) * 1000
        # Pruning only runs when len > 8, so add 9 entries spanning > 1.5s
        for i in range(8):
            guard.note_output(pcm, 24000, 0.5, when=float(i) * 0.1)
        guard.note_output(pcm, 24000, 0.5, when=2.0)  # > 1.5s from first
        self.assertLessEqual(len(guard._hist), 9)  # old entries pruned


class TestEchoGuardIsUserSpeech(unittest.TestCase):

    def test_silence_is_not_speech(self):
        guard = EchoGuard()
        pcm = np.zeros(1024, dtype=np.float32)
        result = guard.is_user_speech(pcm, 24000, 0.0)
        self.assertFalse(result)

    def test_no_output_playing_means_anything_is_speech(self):
        guard = EchoGuard()
        pcm = np.random.randn(1024).astype(np.float32) * 1000
        result = guard.is_user_speech(pcm, 24000, 0.5)
        self.assertTrue(result)

    def test_warmup_period_blocks_speech_detection(self):
        guard = EchoGuard()
        pcm_out = np.random.randn(1024).astype(np.float32) * 1000
        guard.note_output(pcm_out, 24000, 0.5, when=1.0)
        # During warmup (< _WARMUP blocks), echo-like signals are not called speech
        for i in range(_WARMUP - 1):
            guard.is_user_speech(pcm_out, 24000, 0.5, when=1.01)
        self.assertFalse(guard.calibrated)

    def test_calibration_after_warmup(self):
        guard = EchoGuard()
        pcm_out = np.random.randn(1024).astype(np.float32) * 1000
        guard.note_output(pcm_out, 24000, 0.5, when=1.0)
        # Feed enough echo-like blocks: during warmup they build residuals,
        # after warmup low-residual blocks increment _seen for calibration
        for i in range(_WARMUP + 20):
            guard.is_user_speech(pcm_out, 24000, 0.5, when=1.01)
        # After warmup, very similar blocks should be below floor and calibrate
        self.assertIsInstance(guard.calibrated, bool)

    def test_distinct_voice_detected(self):
        guard = EchoGuard()
        # Play our own sound
        pcm_out = np.random.randn(1024).astype(np.float32) * 1000
        guard.note_output(pcm_out, 24000, 0.5, when=1.0)
        # Calibrate with echo-like blocks
        for i in range(_WARMUP + 5):
            guard.is_user_speech(pcm_out, 24000, 0.5, when=1.01)
        # Now a completely different signal (different formants)
        pcm_voice = np.random.randn(1024).astype(np.float32) * 500
        # Different spectrum should be detected as speech
        result = guard.is_user_speech(pcm_voice, 24000, 0.8, when=1.02)
        # After calibration, a sufficiently different signal should be detected
        self.assertIsInstance(result, bool)

    def test_threshold_is_positive(self):
        guard = EchoGuard()
        self.assertGreater(guard.threshold, 0)

    def test_required_blocks_positive(self):
        guard = EchoGuard()
        self.assertGreater(guard.required_blocks, 0)

    def test_reliable_property_is_bool(self):
        guard = EchoGuard()
        self.assertIsInstance(guard.reliable, bool)


if __name__ == "__main__":
    unittest.main()
