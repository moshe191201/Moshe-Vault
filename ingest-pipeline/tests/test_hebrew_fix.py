"""Tests for scripts/hebrew_fix.py — Hebrew OCR-reversal detection and fixing.

Crafted fixtures use pseudo-Hebrew tokens (e.g. "אבג") so the persistent
dictionary fully controls the scoring, plus real words (שלום) where wordfreq
is the intended signal.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from convert.hebrew_fix import HebrewDictionary, build_dictionary, fix_text


def make_dict(words=None, phrases=None):
    d = HebrewDictionary()
    for w, c in (words or {}).items():
        d.words[w] = c
    for p, c in (phrases or {}).items():
        d.phrases[p] = c
    return d


class TestWordFix(unittest.TestCase):
    def test_correct_word_untouched(self):
        fixed, report = fix_text("שלום", make_dict())
        self.assertEqual(fixed, "שלום")
        self.assertFalse(report["hebrew_fixed"])

    def test_english_text_untouched(self):
        fixed, report = fix_text("hello world", make_dict())
        self.assertEqual(fixed, "hello world")
        self.assertFalse(report["hebrew_fixed"])
        self.assertEqual(report["ambiguous"], [])

    def test_reversed_real_word_fixed(self):
        # םולש is שלום backwards; it also starts with a final-form letter (ם),
        # which is only legal at the END of a Hebrew word.
        fixed, report = fix_text("םולש", make_dict())
        self.assertEqual(fixed, "שלום")
        self.assertTrue(report["hebrew_fixed"])
        self.assertIn("םולש", report["fixed_words"])

    def test_clear_dictionary_win_fixed(self):
        d = make_dict(words={"גבא": 100, "אבג": 1})
        fixed, report = fix_text("אבג", d, margin=2.0)
        self.assertEqual(fixed, "גבא")
        self.assertTrue(report["hebrew_fixed"])

    def test_close_call_left_and_flagged(self):
        # Reversed scores better (100 vs 60) but within margin 2.0 -> no touch.
        d = make_dict(words={"גבא": 100, "אבג": 60})
        fixed, report = fix_text("אבג", d, margin=2.0)
        self.assertEqual(fixed, "אבג")
        self.assertFalse(report["hebrew_fixed"])
        self.assertEqual(report["ambiguous"], ["אבג"])

    def test_surrounding_text_preserved(self):
        fixed, _ = fix_text("abc םולש, 123!", make_dict())
        self.assertEqual(fixed, "abc שלום, 123!")


class TestPhraseFix(unittest.TestCase):
    def test_reversed_pair_fixed(self):
        d = make_dict(phrases={"גבא דאה": 50})
        fixed, report = fix_text("דאה גבא", d, margin=2.0)
        self.assertEqual(fixed, "גבא דאה")
        self.assertIn("דאה גבא", report["fixed_phrases"])

    def test_correct_pair_untouched(self):
        d = make_dict(phrases={"גבא דאה": 50})
        fixed, report = fix_text("גבא דאה", d, margin=2.0)
        self.assertEqual(fixed, "גבא דאה")
        self.assertEqual(report["fixed_phrases"], [])

    def test_unknown_phrase_untouched(self):
        fixed, report = fix_text("אבג דאה", make_dict(), margin=2.0)
        self.assertEqual(fixed, "אבג דאה")


class TestDictionary(unittest.TestCase):
    def test_update_from_text_counts_words_and_phrases(self):
        d = HebrewDictionary()
        d.update_from_text("אבג דאה אבג")
        self.assertEqual(d.words["אבג"], 2)
        self.assertEqual(d.words["דאה"], 1)
        self.assertEqual(d.phrases["אבג דאה"], 1)
        self.assertEqual(d.phrases["דאה אבג"], 1)

    def test_save_load_roundtrip(self):
        import tempfile
        d = make_dict(words={"אבג": 3}, phrases={"אבג דאה": 2})
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "hebrew_dict.json"
            d.save(p)
            loaded = HebrewDictionary.load(p)
        self.assertEqual(loaded.words["אבג"], 3)
        self.assertEqual(loaded.phrases["אבג דאה"], 2)

    def test_load_missing_file_gives_empty(self):
        d = HebrewDictionary.load(Path("/nonexistent/hebrew_dict.json"))
        self.assertEqual(len(d.words), 0)

    def test_build_dictionary_accumulates_across_runs(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "hebrew_dict.json"
            build_dictionary(["אבג דאה"], p)
            d = build_dictionary(["אבג"], p)
        self.assertEqual(d.words["אבג"], 2)
        self.assertEqual(d.words["דאה"], 1)

    def test_english_not_counted(self):
        d = HebrewDictionary()
        d.update_from_text("hello שלום world")
        self.assertNotIn("hello", d.words)
        self.assertEqual(d.words["שלום"], 1)
        self.assertEqual(len(d.phrases), 0)


if __name__ == "__main__":
    unittest.main()
