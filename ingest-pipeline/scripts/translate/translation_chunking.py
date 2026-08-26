"""Markdown chunking + glossary filtering — extracted from translate.py (pure move)."""
from __future__ import annotations

import re


def chunk_markdown(md_text: str, max_chars: int = 6000) -> list[dict]:
    """Split at heading boundaries, then paragraph boundaries if chunk exceeds budget.
    Never mid-code-block / mid-frontmatter / mid-table (table handled as paragraph).
    Returns [{section_path, chunk_text}].

    Note: kept heading→paragraph aligned with how qmd chunks markdown for
    embedding (qmd's chunking is internal: AST for code + heading-aware for
    markdown, not exposed as a library). If qmd exposes a chunk API later,
    wire it here — same boundaries keep translation chunks = retrieval chunks.
    """
    # Separate frontmatter
    body = md_text
    frontmatter = ""
    if md_text.startswith("---\n"):
        end = md_text.find("\n---\n", 4)
        if end != -1:
            frontmatter = md_text[: end + 5]
            body = md_text[end + 5:]

    # Track code fence state so we never split inside one
    lines = body.split("\n")
    sections: list[dict] = []
    cur_lines: list[str] = []
    cur_heading = ""
    in_fence = False

    def flush():
        nonlocal cur_lines, cur_heading
        if cur_lines:
            # Trim
            text = "\n".join(cur_lines).strip()
            if text:
                sections.append({"section_path": cur_heading, "chunk_text": text})
            cur_lines = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            cur_lines.append(line)
            continue
        if not in_fence and re.match(r"^#{1,6}\s+", line):
            # Heading boundary — flush previous section
            flush()
            cur_heading = line.strip()
            cur_lines.append(line)
        else:
            cur_lines.append(line)
            # Check if current buffer exceeds budget and we're at a paragraph boundary
            if not in_fence and sum(len(l) for l in cur_lines) > max_chars:
                # Look back for blank line (paragraph boundary)
                # Find last empty line in cur_lines
                last_blank = -1
                for i in range(len(cur_lines) - 1, -1, -1):
                    if cur_lines[i].strip() == "":
                        last_blank = i
                        break
                if last_blank > 0:
                    head = cur_lines[: last_blank + 1]
                    tail = cur_lines[last_blank + 1 :]
                    text = "\n".join(head).strip()
                    if text:
                        sections.append({"section_path": cur_heading, "chunk_text": text})
                    cur_lines = tail
                # else: no paragraph boundary found — keep accumulating (rare, very long paragraph)

    flush()
    # Re-attach frontmatter to first chunk
    if frontmatter and sections:
        sections[0]["chunk_text"] = frontmatter + "\n" + sections[0]["chunk_text"]
    elif frontmatter and not sections:
        sections.append({"section_path": "", "chunk_text": frontmatter})

    if not sections and body.strip():
        sections.append({"section_path": "", "chunk_text": body.strip()})
    return sections


def glossary_for_chunk(chunk_text: str, glossary: list[dict]) -> list[dict]:
    """Filter glossary to entries whose term_he occurs in chunk at word boundaries.

    Only terms with status 'approved' or 'keep_source' are injected — 'proposed'
    rows must not leak into prompts. Uses token-boundary matching to avoid
    injecting מודל when chunk contains only מודלים.
    """
    relevant = []
    he_tokens = set(re.findall(r"[א-ת]+", chunk_text))
    has_hebrew = re.compile(r"[א-ת]")
    for row in glossary:
        term = (row.get("term_he") or "").strip()
        if not term:
            continue
        status = (row.get("status") or "approved").strip()
        if status not in ("approved", "keep_source"):
            continue
        # Fast path: exact token match for single-token Hebrew terms
        if " " not in term and term in he_tokens:
            relevant.append(row)
            continue
        # Boundary-aware regex: require word boundaries so substring inside longer word doesn't match
        if has_hebrew.search(term):
            pat = r"(?<![א-ת])" + re.escape(term) + r"(?![א-ת])"
        else:
            pat = r"(?<![A-Za-z0-9_])" + re.escape(term) + r"(?![A-Za-z0-9_])"
        if re.search(pat, chunk_text):
            relevant.append(row)
    return relevant
