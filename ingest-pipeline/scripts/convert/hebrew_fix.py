#!/usr/bin/env python3
"""Detect and fix Hebrew text reversed by OCR (docling).

Two reversal symptoms are handled:

- Reversed words: characters of a single word flipped ("םולש" -> "שלום").
- Reversed word order: a 2-3 word phrase in reverse order.

Detection scores each token/phrase as-is vs reversed using two sources:
the persistent corpus dictionary (words + 2/3-word phrase counts) and
wordfreq's Hebrew baseline. A clear win (>= margin x better) is auto-fixed;
a close call is left alone and flagged in the report.

The final-form invariant is a cheap pre-filter: ם ן ף ץ ך may only appear at
the END of a Hebrew word, so a token starting with one is char-reversed.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from wordfreq import word_frequency

HEBREW_TOKEN_RE = re.compile(r"[֐-׿]+")
# A run of 2+ Hebrew tokens separated by single spaces.
HEBREW_SEQ_RE = re.compile(r"[֐-׿]+(?: [֐-׿]+)+")

FINAL_FORMS = "םןךףץ"

DEFAULT_MARGIN = 2.0
# wordfreq noise on rare/pseudo words sits around 1e-7..1e-6; require the
# reversed candidate to beat this floor so noise alone never triggers a fix.
# Real words score far higher (שלום ~= 4e-4) and dictionary frequencies from
# a real corpus are typically 1e-3+, so both signals clear it easily.
DEFAULT_MIN_SCORE = 1e-5


class HebrewDictionary:
    """Persistent word + phrase counts built from trusted (non-OCR) text."""

    def __init__(self):
        self.words: Counter[str] = Counter()
        self.phrases: Counter[str] = Counter()
        self._words_total: int | None = None
        self._phrases_total: int | None = None

    def _invalidate_cache(self) -> None:
        self._words_total = None
        self._phrases_total = None

    def update_from_text(self, text: str) -> None:
        tokens = HEBREW_TOKEN_RE.findall(text)
        if tokens:
            self.words.update(tokens)
            self._words_total = None
        for n in (2, 3):
            for i in range(len(tokens) - n + 1):
                self.phrases[" ".join(tokens[i : i + n])] += 1
                self._phrases_total = None

    def word_freq(self, word: str) -> float:
        if self._words_total is None:
            self._words_total = sum(self.words.values())
        total = self._words_total
        return self.words[word] / total if total else 0.0

    def phrase_freq(self, phrase: str) -> float:
        if self._phrases_total is None:
            self._phrases_total = sum(self.phrases.values())
        total = self._phrases_total
        return self.phrases[phrase] / total if total else 0.0

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"words": dict(self.words), "phrases": dict(self.phrases)},
                       ensure_ascii=False, indent=1),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> "HebrewDictionary":
        d = cls()
        path = Path(path)
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            d.words.update(data.get("words", {}))
            d.phrases.update(data.get("phrases", {}))
            d._invalidate_cache()
        return d


def build_dictionary(texts, dict_path: Path) -> HebrewDictionary:
    """Load the persistent dictionary, update it from texts, save it back."""
    d = HebrewDictionary.load(dict_path)
    for text in texts:
        d.update_from_text(text)
    d.save(dict_path)
    return d


def _word_score(word: str, d: HebrewDictionary) -> float:
    return d.word_freq(word) + word_frequency(word, "he")


def _fix_word(word: str, d: HebrewDictionary, margin: float, min_score: float,
              report: dict, ocr: bool = True) -> str:
    if len(word) < 2:
        return word
    is_final_form = word[0] in FINAL_FORMS
    cur = _word_score(word, d)
    if is_final_form:
        cur = 0.0  # final-form letter at word start: certain char-reversal
    elif not ocr:
        # H2: dictionary-based reversal fixing is OCR-only. For non-OCR text
        # (txt/html/msg/eml/vsdx/onenote) only the final-form invariant is safe;
        # margin-based flips (e.g. שמח->חמש) would silently corrupt correct text.
        return word
    rev = word[::-1]
    rev_score = _word_score(rev, d)
    if rev_score < min_score or rev_score <= cur:
        return word
    if cur == 0 or rev_score >= cur * margin:
        report["fixed_words"].append(word)
        return rev
    report["ambiguous"].append(word)
    return word


def _fix_phrases(seq: str, d: HebrewDictionary, margin: float,
                 min_score: float, report: dict, ocr: bool = True) -> str:
    if not ocr:
        return seq
    words = seq.split(" ")
    i = 0
    out = []
    while i < len(words):
        fixed = False
        for n in (3, 2):
            window = words[i : i + n]
            if len(window) < n:
                continue
            phrase = " ".join(window)
            rev_phrase = " ".join(reversed(window))
            cur = d.phrase_freq(phrase)
            rev = d.phrase_freq(rev_phrase)
            if rev < min_score or rev <= cur:
                continue
            if cur == 0 or rev >= cur * margin:
                report["fixed_phrases"].append(phrase)
                out.extend(reversed(window))
                i += n
                fixed = True
                break
            # Close call: same ambiguous reporting as word-level
            report["ambiguous"].append(phrase)
            break
        if not fixed:
            out.append(words[i])
            i += 1
    return " ".join(out)


def fix_text(text: str, d: HebrewDictionary, margin: float = DEFAULT_MARGIN,
             min_score: float = DEFAULT_MIN_SCORE, ocr: bool = True):
    """Return (fixed_text, report). report keys: fixed_words, fixed_phrases,
    ambiguous, hebrew_fixed."""
    report = {"fixed_words": [], "fixed_phrases": [], "ambiguous": [],
              "hebrew_fixed": False}
    if not HEBREW_TOKEN_RE.search(text):
        return text, report

    text = HEBREW_TOKEN_RE.sub(
        lambda m: _fix_word(m.group(0), d, margin, min_score, report, ocr=ocr), text)
    text = HEBREW_SEQ_RE.sub(
        lambda m: _fix_phrases(m.group(0), d, margin, min_score, report, ocr=ocr), text)

    report["hebrew_fixed"] = bool(report["fixed_words"] or report["fixed_phrases"])
    return text, report
