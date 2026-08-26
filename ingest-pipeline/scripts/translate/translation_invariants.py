"""Preservation-by-verification invariants — extracted from translate.py (pure move)."""
from __future__ import annotations

import re
from pathlib import Path
PERSON_OPEN = "⟦PERSON_"
PERSON_CLOSE = "⟧"
EN_OPEN = "⟦EN_"
EN_CLOSE = "⟧"
HE_MARKER_FMT = "⟦he:{term}⟧"

# Hebrew char range (narrow א-ת) — word runs used for mock/qa sentinel-aware marking
HEBREW_WORD_RE = re.compile(r"[א-ת]{2,}")
# English preservation: Latin-script spans that must survive verbatim (verified, not masked)
_EN_SENTINEL_RE = re.compile(re.escape(EN_OPEN) + r"\d+" + re.escape(EN_CLOSE))
_PERSON_SENTINEL_RE = re.compile(re.escape(PERSON_OPEN) + r"\d+" + re.escape(PERSON_CLOSE))
# Technical English tokens that must survive verbatim — restricted to capitalized/
# code-like forms (not arbitrary Latin runs). Generic prose like "handles it"
# is intentionally excluded so the model can re-flow sentences naturally.
# Matches: acronyms (API), Title Case phrases (API Gateway, Kubernetes is single
# capitalized word via last alternative), hyphen/slash tokens (CI/CD), CamelCase,
# alphanum (OAuth2, S3). Excludes '/' so file-paths are not merged.
# Single common words like "The"/"And" are filtered by _COMMON_ENGLISH set below.
_EN_SPAN_RE = re.compile(
    r"\b(?:"
    r"[A-Z]{2,}(?:\s+[A-Z][a-z]+){0,2}"          # API, API Gateway
    r"|[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3}"       # Two+ Title Case words
    r"|[A-Za-z]+[-_/][A-Za-z0-9\-_./]+"          # hyphen/slash/underscore
    r"|[A-Z][a-z]*[A-Z][a-zA-Z]*"                # CamelCase
    r"|[A-Za-z]+[0-9][A-Za-z0-9]*"               # alphanum S3/OAuth2
    r"|[A-Z][a-z]{2,}"                           # single Capitalized word (Kubernetes)
    r")\b"
)
_COMMON_ENGLISH = frozenset({
    "The", "This", "That", "These", "Those", "They", "There", "Then", "Than",
    "And", "Or", "But", "With", "From", "For", "To", "Of", "In", "On", "At",
    "By", "A", "An", "Is", "Are", "Was", "Were", "Be", "Been", "Being",
    "It", "Its", "It", "As", "If", "So", "Not", "No", "Yes", "We", "You",
    "He", "She", "Will", "Can", "Has", "Have", "Had", "Do", "Does", "Did",
    "Handles", "Handle",  # generic verbs that should not be invariants alone
})
# URLs and file-paths to preserve verbatim
# NOTE: \w is Unicode-aware in Python 3 and would match Hebrew, so file-path
# alternatives use explicit [A-Za-z0-9_] and require at least one Latin letter.
_URL_RE = re.compile(r"https?://[^\s<>\[\]()\"']+|www\.[^\s<>\[\]()\"']+")
_FILEPATH_RE = re.compile(
    r"(?:[A-Za-z]:)?[\\/][A-Za-z0-9_.\-/\\]+|"  # /abs/path or C:\path or \path
    r"\b[A-Za-z0-9_.\-]+\.(?:md|py|json|csv|txt|pdf|docx|xlsx|png|jpg|jpeg|yaml|yml|toml|sh|js|ts)\b|"
    r"\b(?=[A-Za-z0-9_.\-]*[A-Za-z])[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-/]*"
)
# YAML frontmatter and code sections — must be preserved verbatim and in order
_YAML_RE = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
_FENCED_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
_CODE_RE = re.compile(r"```.*?```|`[^`\n]+`", re.DOTALL)


def mask_person_names(text: str, first: set[str], last: set[str]) -> tuple[str, list[str]]:
    """Exact-match scan for person names (unigram + bigram), mask to sentinels.

    Token-boundary safe: replaces whole Hebrew tokens only, never substrings
    inside a longer word (e.g. 'דן' inside 'דניאל').
    """
    tokens = re.findall(r"[א-ת]{2,}", text)
    token_set = set(tokens)

    words = re.findall(r"[א-ת]+", text)
    bigram_candidates: set[str] = set()
    for i in range(len(words) - 1):
        bg = f"{words[i]} {words[i+1]}"
        bigram_candidates.add(bg)

    single_names: set[str] = {t for t in token_set if t in first or t in last}
    bigram_names: set[str] = set()
    for bg in bigram_candidates:
        parts = bg.split()
        if len(parts) == 2 and parts[0] in first and parts[1] in last:
            bigram_names.add(bg)

    # If a bigram was matched, its component tokens must not also be listed as singles
    if bigram_names:
        bigram_tokens: set[str] = set()
        for bg in bigram_names:
            bigram_tokens.update(bg.split())
        single_names -= bigram_tokens

    all_names = sorted(single_names | bigram_names, key=len, reverse=True)
    if not all_names:
        return text, []

    name_to_sentinel: dict[str, str] = {}
    mapping: list[str] = []
    for name in all_names:
        sentinel = f"{PERSON_OPEN}{len(mapping)}{PERSON_CLOSE}"
        name_to_sentinel[name] = sentinel
        mapping.append(name)

    masked = _mask_via_tokens(text, name_to_sentinel)
    return masked, mapping


def _mask_via_tokens(text: str, name_to_sentinel: dict[str, str]) -> str:
    """Replace names at token boundaries by scanning Hebrew word spans."""
    bigram_set = {k for k in name_to_sentinel if " " in k}
    single_set = {k for k in name_to_sentinel if " " not in k}

    he_spans = [(m.start(), m.end(), m.group(0)) for m in re.finditer(r"[א-ת]+", text)]
    if not he_spans:
        return text

    skip: set[int] = set()
    replacements: dict[int, str] = {}

    # Bigram pass first (longer matches win)
    for i in range(len(he_spans) - 1):
        if i in skip:
            continue
        bg = f"{he_spans[i][2]} {he_spans[i + 1][2]}"
        if bg in bigram_set:
            sep = text[he_spans[i][1]: he_spans[i + 1][0]]
            if sep in (" ", "\t", "\n", "־", "-", "–", "—"):
                replacements[i] = name_to_sentinel[bg]
                skip.add(i + 1)

    # Single pass for remaining tokens
    for i, (_, _, tok) in enumerate(he_spans):
        if i in skip or i in replacements:
            continue
        if tok in single_set:
            replacements[i] = name_to_sentinel[tok]

    if not replacements:
        return text

    # Rebuild via offset scan
    result: list[str] = []
    cur = 0
    i = 0
    while i < len(he_spans):
        s, e, _tok = he_spans[i]
        if i in replacements:
            result.append(text[cur:s])
            result.append(replacements[i])
            if (i + 1) in skip:
                cur = he_spans[i + 1][1]
                i += 2
            else:
                cur = e
                i += 1
        elif i in skip:
            i += 1
        else:
            i += 1
    result.append(text[cur:])
    return "".join(result)


def unmask_person_names(text: str, mapping: list[str]) -> str:
    for i, name in enumerate(mapping):
        text = text.replace(f"{PERSON_OPEN}{i}{PERSON_CLOSE}", name)
    return text


def is_english_only_doc(text: str, he_threshold: int = 10, ratio_threshold: float = 0.02) -> bool:
    """Return True for docs that are entirely (or effectively) English.

    Strips frontmatter + code fences before counting so English docs with
    YAML/code aren't misclassified. Heuristic: Hebrew char count < he_threshold
    AND Hebrew/(Hebrew+Latin) < ratio_threshold, with at least some Latin.
    This matches "entirely English" while tolerating stray Hebrew characters.
    """
    body = text
    if body.startswith("---\n"):
        end = body.find("\n---\n", 4)
        if end != -1:
            body = body[end + 5:]
    # Strip code fences — they may contain Hebrew-like chars in comments
    body = re.sub(r"```.*?```", "", body, flags=re.DOTALL)
    body = re.sub(r"`[^`]*`", "", body)
    he_chars = len(re.findall(r"[א-ת]", body))
    latin_chars = len(re.findall(r"[A-Za-z]", body))
    if latin_chars < 20:
        return False
    if he_chars == 0:
        return True
    if he_chars < he_threshold and (he_chars / max(he_chars + latin_chars, 1)) < ratio_threshold:
        return True
    return False


def mask_english_spans(text: str) -> tuple[str, list[str]]:
    """Legacy: mask contiguous Latin-script spans to EN sentinels.

    Kept for tests/back-compat. New flow uses extract_english_spans() +
    verify_preserved() — LLM sees raw English with preservation context
    instead of sentinels.
    """
    # Split by PERSON sentinels so we don't capture the word "PERSON"
    p_parts = _PERSON_SENTINEL_RE.split(text)
    p_sents = _PERSON_SENTINEL_RE.findall(text)
    en_mapping: list[str] = []
    out_parts: list[str] = []
    for idx, seg in enumerate(p_parts):
        # Within each non-PERSON segment, mask English spans
        def _repl(m: re.Match) -> str:
            span = m.group(0).strip()
            # Require at least 2 letters and at least one word with 2+ letters
            if len(re.findall(r"[A-Za-z]", span)) < 2:
                return m.group(0)
            # Skip very short fragments that are mostly punctuation
            if len(span) < 2:
                return m.group(0)
            sentinel = f"{EN_OPEN}{len(en_mapping)}{EN_CLOSE}"
            en_mapping.append(span)
            return sentinel
        # Only mask spans that look like real English (at least 2 consecutive letters)
        masked_seg = _EN_SPAN_RE.sub(_repl, seg)
        out_parts.append(masked_seg)
        if idx < len(p_sents):
            out_parts.append(p_sents[idx])
    return "".join(out_parts), en_mapping


def unmask_english_spans(text: str, mapping: list[str]) -> str:
    for i, span in enumerate(mapping):
        text = text.replace(f"{EN_OPEN}{i}{EN_CLOSE}", span)
    return text


# ── Preservation-by-verification (LLM sees raw text + invariants as context) ──

def extract_english_spans(text: str) -> list[str]:
    """Extract technical English spans that must be preserved verbatim.

    Restricted to capitalized/code-like tokens (see _EN_SPAN_RE) so generic
    prose does not become an invariant. URLs/paths are excluded.
    Sentence-internal spans are found via technical token regex; single common
    words like "The" alone are filtered.
    """
    # Mask URLs/paths first so they don't pollute English spans
    masked = _URL_RE.sub("\n", text)
    masked = _FILEPATH_RE.sub("\n", masked)
    spans: list[str] = []
    for m in _EN_SPAN_RE.finditer(masked):
        span = m.group(0).strip().strip(".,;:\"'()")
        if len(span) < 2 or len(re.findall(r"[A-Za-z]", span)) < 2:
            continue
        if not re.search(r"[A-Za-z]{2,}", span):
            continue
        # Filter single common English words that are not technical
        if span in _COMMON_ENGLISH:
            continue
        # For multi-word spans starting with a common word, strip it
        # e.g. "The API Gateway" -> the regex already prefers "API Gateway",
        # but guard anyway
        first_word = span.split()[0] if span else ""
        if first_word in _COMMON_ENGLISH and len(span.split()) > 1:
            # If the technical signal is only after the common word, keep the tail
            tail = span[len(first_word):].strip()
            if tail and tail not in spans and tail not in _COMMON_ENGLISH:
                # Re-validate tail is still technical
                if _EN_SPAN_RE.search(tail):
                    span = tail
                else:
                    continue
            else:
                continue
        if span not in spans:
            spans.append(span)
    return spans


def extract_urls_and_paths(text: str) -> list[str]:
    """Extract URLs and file-paths that must be preserved verbatim."""
    urls: list[str] = []
    for m in _URL_RE.finditer(text):
        s = m.group(0).strip().rstrip(".,;:)]}'\"")
        if len(s) >= 8 and s not in urls:
            urls.append(s)
    # Remove URLs before path scan so we don't capture fragments like 's://'
    masked = _URL_RE.sub(" ", text)
    paths: list[str] = []
    for m in _FILEPATH_RE.finditer(masked):
        s = m.group(0).strip().rstrip(".,;:)]}'\"")
        if len(s) < 3:
            continue
        if "/" not in s and "\\" not in s and "." not in s:
            continue
        # File-paths must contain at least one Latin letter (avoid date false-positives like 12/2024)
        if not re.search(r"[A-Za-z]", s):
            continue
        # Avoid tiny fragments and duplicates of URLs
        if s in urls or s in paths:
            continue
        paths.append(s)
    return urls + paths


def extract_person_names(text: str, first: set[str], last: set[str]) -> list[str]:
    """Extract person names from text using the same allowlist logic as masking."""
    _, mapping = mask_person_names(text, first, last)
    return mapping


def extract_yaml_frontmatter(text: str) -> list[str]:
    m = _YAML_RE.search(text)
    return [m.group(0)] if m else []


def extract_code_sections(text: str) -> list[str]:
    """Extract fenced + inline code sections in source order."""
    return [m.group(0) for m in _CODE_RE.finditer(text)]


def extract_preservation_invariants(text: str, first: set[str], last: set[str]) -> dict:
    """Collect all invariants that must survive translation verbatim.

    Returns dict with keys: person_names, english_spans, urls_and_paths,
    yaml_frontmatter, code_sections
    Each is a deduplicated list in order of appearance (except yaml which is 0/1).
    Code/YAML are extracted first and masked out before english/url extraction
    to avoid double-counting text inside code.
    """
    yaml_blocks = extract_yaml_frontmatter(text)
    code_blocks = extract_code_sections(text)
    # Mask yaml + code so english/url extraction ignores their interior
    masked = _YAML_RE.sub("\n", text)
    masked = _CODE_RE.sub("\n", masked)
    return {
        "yaml_frontmatter": yaml_blocks,
        "code_sections": code_blocks,
        "person_names": extract_person_names(masked, first, last),
        "english_spans": extract_english_spans(masked),
        "urls_and_paths": extract_urls_and_paths(masked),
    }


def verify_preserved(source_invariants: list[str], translation: str) -> list[str]:
    """Return subset of source_invariants missing verbatim in translation."""
    return [s for s in source_invariants if s not in translation]


def verify_all_preserved(invariants: dict, translation: str) -> dict:
    """Verify all categories; returns {category: [missing,...]} for failures."""
    missing: dict[str, list[str]] = {}
    for cat, items in invariants.items():
        bad = verify_preserved(items, translation)
        if bad:
            missing[cat] = bad
    return missing


def verify_ordered(source_items: list[str], translation: str) -> list[str]:
    """Return items that are out of order (present but monotonic violation)."""
    positions: list[int | None] = []
    for item in source_items:
        try:
            positions.append(translation.index(item))
        except ValueError:
            positions.append(None)
    present = [(i, p) for i, p in enumerate(positions) if p is not None]
    out: list[str] = []
    for k in range(1, len(present)):
        if present[k][1] < present[k - 1][1]:
            out.append(source_items[present[k][0]])
    return out


def verify_all_ordered(invariants: dict, translation: str) -> dict:
    """Check order per category; returns {category: [out_of_order,...]}."""
    bad: dict[str, list[str]] = {}
    for cat, items in invariants.items():
        if len(items) <= 1:
            continue
        oo = verify_ordered(items, translation)
        if oo:
            bad[cat] = oo
    return bad


def verify_global_order(source_text: str, invariants: dict, translation: str) -> list[str]:
    """Check that all preserved pieces appear in same relative order as in source."""
    all_occurrences: list[tuple[int, str]] = []
    for items in invariants.values():
        for val in items:
            idx = source_text.find(val)
            if idx != -1:
                all_occurrences.append((idx, val))
    all_occurrences.sort(key=lambda x: x[0])
    ordered_vals = [v for _, v in all_occurrences]
    if len(ordered_vals) <= 1:
        return []
    return verify_ordered(ordered_vals, translation)


# Person-name helpers — single source in translation_common.py
from .translation_common import load_codenames, load_person_names  # re-export for tmod import
