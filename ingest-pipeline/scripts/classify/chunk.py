"""Chunker for Stage 6 classification — first-token priority.

Modes:
  first_window: frontmatter+title+first N tokens + header outline (default, 1500)
  header_aware: frontmatter+title+header outline+first N tokens (for heading-dense docs)
  full:        frontmatter+full body (escape hatch)

Writes content-addressed md to store/ab/abcdef....md with frontmatter
source_doc_id, source_hash, chunk_policy_version.

Atomic: temp file → rename.
Pure stdlib except for token estimate (~4 chars/token, same as qmd spec).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
import sys

import importlib.util as _ilu_chunk
_cc_chunk_path = Path(__file__).resolve().parent / "classify_common.py"
_spec_chunk = _ilu_chunk.spec_from_file_location("_classify_common_chunk", _cc_chunk_path)
_mod_chunk = _ilu_chunk.module_from_spec(_spec_chunk)
_spec_chunk.loader.exec_module(_mod_chunk)  # type: ignore
parse_frontmatter = _mod_chunk.parse_frontmatter
estimate_tokens = _mod_chunk.estimate_tokens
extract_headers = _mod_chunk.extract_headers
atomic_write = _mod_chunk.atomic_write


def chunk_first_window(body: str, window: int, include_outline: bool) -> str:
    # Window in tokens; convert to chars
    char_budget = window * 4
    outline = extract_headers(body) if include_outline else ""
    # Reserve 10% for outline
    outline_budget = min(len(outline), char_budget // 10) if outline else 0
    body_budget = char_budget - outline_budget
    chunk_body = body[:body_budget]
    # Never cut mid-sentence if we can avoid: trim to last period/newline within last 10%
    if len(body) > body_budget:
        tail = chunk_body[-400:]
        cut = max(tail.rfind(". "), tail.rfind("\n"))
        if cut > 0:
            chunk_body = chunk_body[: len(chunk_body) - 400 + cut + 1]
    if outline and outline_budget:
        # Outline at top after title, deduplicate
        return chunk_body + "\n\n## Outline\n" + outline[:outline_budget]
    return chunk_body


def chunk_header_aware(body: str, window: int) -> str:
    headers = extract_headers(body)
    # Prioritize headers, then first window of prose (headers stripped outside fences)
    char_budget = window * 4
    header_part = headers[: char_budget // 3]
    remaining = char_budget - len(header_part)
    # Strip headers outside fences
    prose_lines = []
    in_fence = False
    for line in body.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            prose_lines.append(line)
            continue
        if not in_fence and line.lstrip().startswith("#"):
            continue
        prose_lines.append(line)
    prose = "\n".join(prose_lines)
    return header_part + "\n\n" + prose[:remaining]


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main():
    p = argparse.ArgumentParser(description="Chunk md for classification (first-token priority)")
    p.add_argument("inputs", nargs="*", help="md files or directories")
    p.add_argument("--campaign", default="campaigns/example", help="campaign dir containing policy.yaml")
    p.add_argument("--store", default="store", help="content-addressed store root")
    p.add_argument("--mode", choices=["first_window", "header_aware", "full"], default=None)
    p.add_argument("--window", type=int, default=None)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    mode = args.mode or "first_window"
    window = args.window or 1500
    if window < 200 or window > 8000:
        print(f"window {window} out of range 200-8000", file=sys.stderr)
        return 1

    include_outline = True
    # Try to load policy.yaml for overrides — parse chunk: block robustly
    policy_path = Path(args.campaign) / "policy.yaml"
    if policy_path.exists():
        try:
            txt = policy_path.read_text(encoding="utf-8")
            # Find chunk: block then mode/window inside it
            chunk_block = re.search(r"chunk:\s*\n((?:[ \t]+.*\n)*)", txt)
            block = chunk_block.group(1) if chunk_block else txt
            m = re.search(r"window:\s*(\d+)", block)
            if m and args.window is None:
                window = int(m.group(1))
            m2 = re.search(r"mode:\s*(\w+)", block)
            if m2 and args.mode is None:
                mode = m2.group(1)
            m3 = re.search(r"header_outline:\s*(true|false)", block, flags=re.IGNORECASE)
            if m3:
                include_outline = m3.group(1).lower() == "true"
        except Exception:
            pass

    store_root = Path(args.store)
    inputs: list[Path] = []
    for inp in args.inputs:
        pp = Path(inp)
        if pp.is_dir():
            inputs.extend(pp.rglob("*.md"))
        elif pp.exists():
            inputs.append(pp)
        else:
            print(f"chunk: not found: {inp}", file=sys.stderr)

    if not inputs:
        print("chunk: no inputs", file=sys.stderr)
        return 1

    for src in inputs:
        text = src.read_text(encoding="utf-8")
        fm, body = parse_frontmatter(text)
        title = fm.get("title", src.stem)
        # Choose chunker
        if mode == "first_window":
            chunk_body = chunk_first_window(body, window, include_outline)
        elif mode == "header_aware":
            chunk_body = chunk_header_aware(body, window)
        else:
            chunk_body = body

        h = content_hash(text)
        doc_id = f"{src.stem}__{h[:8]}"
        # Derive source metadata for doc-type pruning (extension + original language)
        # Extension from path; language from frontmatter if present, else empty (pruner treats empty as 'any' when no hard gate)
        source_ext = src.suffix.lstrip(".").lower()
        # Try to detect original language: frontmatter original_language or language, else empty
        original_language = str(fm.get("original_language", fm.get("language", ""))).strip()
        # Also check for 'lang' shorthand
        if not original_language:
            original_language = str(fm.get("lang", "")).strip()
        out_text = (
            f"---\n"
            f'source_doc_id: "{doc_id}"\n'
            f'source_hash: "{h}"\n'
            f'source_path: "{src.as_posix()}"\n'
            f'source_ext: "{source_ext}"\n'
            f'original_language: "{original_language}"\n'
            f'chunk_policy_version: "1"\n'
            f'chunk_mode: "{mode}"\n'
            f'chunk_window: {window}\n'
            f'title: "{title}"\n'
            f"---\n\n"
            f"{chunk_body}\n"
        )
        # Content-addressed path: store/ab/abcdef...md
        out_path = store_root / h[:2] / f"{h}.md"
        meta_path = out_path.with_suffix(".meta.json")
        if args.dry_run:
            print(f"[dry-run] {src} -> {out_path} ({len(chunk_body)} chars, ~{estimate_tokens(chunk_body)} tokens, mode={mode})")
        else:
            atomic_write(out_path, out_text)
            # Sidecar for doc-type pruner
            meta = {
                "source_path": src.as_posix(),
                "source_ext": source_ext,
                "original_language": original_language,
                "title": title,
                "source_hash": h,
            }
            atomic_write(meta_path, json.dumps(meta, indent=2))
            print(f"chunk: {src} -> {out_path} (mode={mode}, window={window})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
