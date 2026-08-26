#!/usr/bin/env python3
"""Shared taxonomy parsing and template lookup for stage 6.

These lived in second_brain_vault_framework.core, with judge.py and retrieve.py
importing them behind a try/except that silently fell back to a local copy. Now
that the pipeline is decoupled from the framework that import can never succeed,
so the fallback was the only live path and the duplication was pure noise.

One definition, imported by validate.py, judge.py, retrieve.py and
export_label_studio.py. Pure stdlib.
"""
from __future__ import annotations

import re
from pathlib import Path

TAXONOMY_RE = re.compile(r"^\s{2}([\w-]+):\s*(?:\n|$)", re.MULTILINE)

# Keys that appear at subdomain indentation but are not subdomains.
_NON_SUBDOMAIN_KEYS = ("subdomains", "version", "campaign")


def parse_taxonomy_blocks(txt: str) -> dict[str, str]:
    """Map subdomain name -> its raw YAML block."""
    matches = list(TAXONOMY_RE.finditer(txt))
    blocks: dict[str, str] = {}
    for i, m in enumerate(matches):
        name = m.group(1)
        if name in _NON_SUBDOMAIN_KEYS:
            continue
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(txt)
        blocks[name] = txt[start:end]
    return blocks


# --- Document-type helpers (dual classification) ---

_DOC_TYPE_NON_KEYS = ("doc_types", "version", "campaign", "routing_defaults")

_EXCLUDED_DOC_TYPE_SHADOW_KEYS = ("chunk", "retrieval", "judge", "confidence", "relation", "review")


def parse_doc_types_blocks(txt: str) -> dict[str, str]:
    """Map doc-type name -> its raw YAML block.

    Same indent regex as subdomains; filters to blocks that look like a doc-type
    (contains definition: or ephemeral:) to avoid policy/routing false positives.
    """
    matches = list(TAXONOMY_RE.finditer(txt))
    blocks: dict[str, str] = {}
    for i, m in enumerate(matches):
        name = m.group(1)
        if name in _DOC_TYPE_NON_KEYS or name in _EXCLUDED_DOC_TYPE_SHADOW_KEYS:
            continue
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(txt)
        block = txt[start:end]
        if "definition:" in block or "ephemeral:" in block:
            blocks[name] = block
    return blocks


def _parse_allowed(block: str, key: str) -> list[str] | None:
    """Parse e.g. allowed_original_languages: [he] -> ['he']; [] -> []; missing -> None."""
    m = re.search(rf"{key}:\s*\[([^\]]*)\]", block)
    if not m:
        return None
    inner = m.group(1).strip()
    if not inner:
        return []
    return [s.strip().strip('"').strip("'") for s in inner.split(",") if s.strip()]


def effective_doc_type_candidates(
    blocks: dict[str, str], *, original_language: str, extension: str
) -> list[str]:
    """Prune doc-types by hard gates. Empty allowed list means no restriction."""
    ext = extension.lower().lstrip(".")
    lang = original_language.lower()
    out: list[str] = []
    for name, block in blocks.items():
        langs = _parse_allowed(block, "allowed_original_languages")
        exts = _parse_allowed(block, "allowed_extensions")
        if langs is not None and langs != [] and lang not in [lng.lower() for lng in langs]:
            continue
        if exts is not None and exts != [] and ext not in [e.lower().lstrip(".") for e in exts]:
            continue
        out.append(name)
    return sorted(out)


def singleton_pruned_type(
    blocks: dict[str, str], *, original_language: str, extension: str
) -> str | None:
    cands = effective_doc_type_candidates(blocks, original_language=original_language, extension=extension)
    return cands[0] if len(cands) == 1 else None


def load_doc_types(path: Path | str) -> tuple[str, dict]:
    """Return (raw_txt, {name: {definition, examples, block}})."""
    p = Path(path)
    if not p.exists():
        return "", {}
    txt = p.read_text(encoding="utf-8")
    blocks = parse_doc_types_blocks(txt)
    out: dict[str, dict] = {}
    for name, block in blocks.items():
        dm = re.search(r"definition:\s*\"(.*?)\"", block, flags=re.DOTALL)
        examples = re.findall(r"text:\s*\"(.*?)\"", block)
        out[name] = {"definition": dm.group(1) if dm else "", "examples": examples, "block": block}
    return txt, out


def templates_root() -> Path:
    """Pipeline classification templates.

    Was ``payload_root()/templates/classification`` in the framework, and in the
    scripts a path relative to the CWD that only resolved when the process
    happened to start at the repo root. Resolved from this file instead.
    """
    return Path(__file__).resolve().parents[2] / "templates" / "classification"
