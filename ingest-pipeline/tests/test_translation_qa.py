"""QA sentinel count+order checks — Task 4 TDD."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def test_glossary_sentinel_pass():
    from translate.translation_qa import check_glossary_sentinel
    term_map = [{"id": 0, "term_he": "אבטחת מידע", "english": "Information Security", "keep_source": False, "occurrences": 2}]
    body = "We use ⟦EN:0:Information Security⟧ and again ⟦EN:0:Information Security⟧."
    res = check_glossary_sentinel(body, term_map)
    assert res["status"] == "pass"


def test_glossary_sentinel_missing_one():
    from translate.translation_qa import check_glossary_sentinel
    term_map = [{"id": 0, "term_he": "אבטחת מידע", "english": "Information Security", "keep_source": False, "occurrences": 2}]
    body = "We use ⟦EN:0:Information Security⟧ once."
    res = check_glossary_sentinel(body, term_map)
    assert res["status"] == "fail"
    assert "expected 2" in res["violations"][0]


def test_glossary_sentinel_wrong_english():
    from translate.translation_qa import check_glossary_sentinel
    term_map = [{"id": 0, "term_he": "מערכת", "english": "system", "keep_source": False, "occurrences": 1}]
    body = "We use ⟦EN:0:System⟧."  # wrong case
    res = check_glossary_sentinel(body, term_map)
    assert res["status"] == "fail"


def test_glossary_sentinel_order():
    from translate.translation_qa import check_glossary_sentinel
    term_map = [
        {"id": 0, "term_he": "מערכת", "english": "system", "keep_source": False, "occurrences": 1, "src_order": 0},
        {"id": 1, "term_he": "מידע", "english": "information", "keep_source": False, "occurrences": 1, "src_order": 1},
    ]
    body = "⟦EN:1:information⟧ before ⟦EN:0:system⟧"  # swapped
    res = check_glossary_sentinel(body, term_map)
    assert res["status"] == "fail"


def test_glossary_sentinel_keep_source():
    from translate.translation_qa import check_glossary_sentinel
    term_map = [{"id": 0, "term_he": "שבת", "english": "", "keep_source": True, "occurrences": 1}]
    body = "Keep ⟦KEEP:שבת⟧ as is."
    res = check_glossary_sentinel(body, term_map)
    assert res["status"] == "pass"


def test_glossary_sentinel_hDBim():
    from translate.translation_qa import check_glossary_sentinel
    term_map = [{"id": 0, "term_he": "DB", "english": "DB", "keep_source": False, "occurrences": 3}]
    body = "ה ⟦EN:0:DB⟧ ים " * 3
    res = check_glossary_sentinel(body.strip(), term_map)
    assert res["status"] == "pass"
