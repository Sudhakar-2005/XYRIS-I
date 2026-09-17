"""
Tests for the viseme system — text-to-mouth-shape conversion.

Verifies Unicode reduction, language independence, coverage detection,
and the VisemeStream fusion engine.
"""
import unittest
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.viseme import (
    to_latin, coverage, text_to_visemes, VisemeStream,
    VISEMES, _LETTER, _CYRILLIC, _GREEK, _DIGRAPH, _MIN_COVERAGE,
)


class TestToLatin(unittest.TestCase):

    def test_basic_ascii(self):
        self.assertEqual(to_latin("a"), "a")
        self.assertEqual(to_latin("Z"), "z")
        self.assertEqual(to_latin("m"), "m")

    def test_accented_characters(self):
        self.assertEqual(to_latin("é"), "e")
        self.assertEqual(to_latin("ü"), "u")
        self.assertEqual(to_latin("ş"), "s")
        self.assertEqual(to_latin("ğ"), "g")
        self.assertEqual(to_latin("ñ"), "n")
        self.assertEqual(to_latin("å"), "a")

    def test_undecomposed_letters(self):
        self.assertEqual(to_latin("ı"), "i")
        self.assertEqual(to_latin("ø"), "o")
        self.assertEqual(to_latin("ß"), "s")
        self.assertEqual(to_latin("æ"), "a")
        self.assertEqual(to_latin("ł"), "l")

    def test_cyrillic(self):
        self.assertEqual(to_latin("а"), "a")
        self.assertEqual(to_latin("б"), "b")
        self.assertEqual(to_latin("м"), "m")
        self.assertEqual(to_latin("п"), "p")
        self.assertEqual(to_latin("я"), "a")

    def test_greek(self):
        self.assertEqual(to_latin("α"), "a")
        self.assertEqual(to_latin("β"), "v")
        self.assertEqual(to_latin("μ"), "m")
        self.assertEqual(to_latin("π"), "p")
        self.assertEqual(to_latin("ω"), "o")

    def test_non_latin_returns_empty(self):
        self.assertEqual(to_latin("漢"), "")
        self.assertEqual(to_latin("ア"), "")

    def test_digit_returns_empty(self):
        self.assertEqual(to_latin("1"), "")

    def test_special_char_returns_empty(self):
        self.assertEqual(to_latin("@"), "")
        self.assertEqual(to_latin("!"), "")


class TestCoverage(unittest.TestCase):

    def test_empty_string(self):
        self.assertEqual(coverage(""), 0.0)

    def test_pure_english(self):
        self.assertGreater(coverage("hello world"), 0.9)

    def test_pure_cyrillic(self):
        self.assertGreater(coverage("привет"), 0.9)

    def test_pure_greek(self):
        self.assertGreater(coverage("γεια σας"), 0.9)

    def test_cjk_low_coverage(self):
        self.assertLess(coverage("日本語"), _MIN_COVERAGE)

    def test_mixed_coverage(self):
        c = coverage("hello 你好")
        self.assertGreater(c, 0.3)
        self.assertLess(c, 0.8)


class TestTextToVisemes(unittest.TestCase):

    def test_empty_text(self):
        result = text_to_visemes("")
        self.assertEqual(result, [])

    def test_none_text(self):
        result = text_to_visemes(None)
        self.assertEqual(result, [])

    def test_english_word(self):
        result = text_to_visemes("hello")
        self.assertGreater(len(result), 0)
        viseme_names = [v[0] for v in result]
        # "hello" = h(K) + e(E) + l(L) + o(O)
        self.assertIn("K", viseme_names)
        self.assertIn("E", viseme_names)
        self.assertIn("L", viseme_names)
        self.assertIn("O", viseme_names)

    def test_lips_closed_on_mbp(self):
        result = text_to_visemes("mama")
        viseme_names = [v[0] for v in result]
        self.assertIn("MBP", viseme_names)

    def test_digraph_sh(self):
        result = text_to_visemes("sh")
        viseme_names = [v[0] for v in result]
        self.assertIn("S", viseme_names)

    def test_digraph_th(self):
        result = text_to_visemes("think")
        viseme_names = [v[0] for v in result]
        self.assertIn("TD", viseme_names)

    def test_accented_english(self):
        result = text_to_visemes("café")
        self.assertGreater(len(result), 0)

    def test_turkish(self):
        result = text_to_visemes("merhaba")
        self.assertGreater(len(result), 0)

    def test_russian(self):
        result = text_to_visemes("привет")
        self.assertGreater(len(result), 0)

    def test_greek(self):
        result = text_to_visemes("γεια")
        self.assertGreater(len(result), 0)

    def test_cjk_returns_empty(self):
        result = text_to_visemes("日本語")
        self.assertEqual(result, [])

    def test_all_visemes_are_valid(self):
        result = text_to_visemes("the quick brown fox jumps over the lazy dog")
        for viseme, duration in result:
            self.assertIn(viseme, VISEMES, f"Unknown viseme: {viseme}")
            self.assertGreater(duration, 0)

    def test_pauses_inserted_for_punctuation(self):
        result = text_to_visemes("hello. world")
        viseme_names = [v[0] for v in result]
        self.assertIn("REST", viseme_names)

    def test_doubled_letters_collapsed(self):
        # "mm" should produce one MBP, not two
        result = text_to_visemes("mm")
        mbp_count = sum(1 for v, _ in result if v == "MBP")
        self.assertEqual(mbp_count, 1)


class TestVisemeStream(unittest.TestCase):

    def test_initial_state(self):
        stream = VisemeStream()
        self.assertEqual(stream.pending, 0)

    def test_feed_text_increases_pending(self):
        stream = VisemeStream()
        stream.feed_text("hello world")
        self.assertGreater(stream.pending, 0)

    def test_reset_clears_queue(self):
        stream = VisemeStream()
        stream.feed_text("hello world")
        stream.reset()
        self.assertEqual(stream.pending, 0)

    def test_frames_with_silence(self):
        stream = VisemeStream()
        stream.feed_text("hello")
        # All-zero audio (silence)
        audio = [(0.0, 0.0, 0.0)] * 10
        result = stream.frames(audio, 0.02)
        self.assertEqual(len(result), 10)
        for level, openness, width in result:
            self.assertEqual(level, 0.0)

    def test_frames_with_audio(self):
        stream = VisemeStream()
        stream.feed_text("hello")
        # Non-zero audio
        audio = [(0.5, 0.3, 0.1)] * 20
        result = stream.frames(audio, 0.02)
        self.assertEqual(len(result), 20)
        # At least some frames should have non-zero output
        has_nonzero = any(l > 0 for l, _, _ in result)
        self.assertTrue(has_nonzero)

    def test_queue_bounded_at_600(self):
        stream = VisemeStream()
        # Feed a lot of text
        stream.feed_text("hello " * 200)
        self.assertLessEqual(stream.pending, 600)


if __name__ == "__main__":
    unittest.main()
