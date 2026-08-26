"""Shared helpers for subdomain + doc-type pipelines. Pure stdlib."""
from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def hash_embed(text: str, dim=64):
    vec = [0.0] * dim
    for w in re.findall(r"\w+", text.lower())[:200]:
        h = int(hashlib.sha256(w.encode()).hexdigest(), 16)
        vec[h % dim] += 1.0
    n = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / n for x in vec]


def build_centroids(examples_by_label: dict[str, list[str]], dim=64, embed_fn=None):
    """Mean embedding per label, L2-normalized. embed_fn(text)->vec for DI/tests."""
    cents: dict[str, list[float]] = {}
    for label, exs in examples_by_label.items():
        if not exs:
            continue
        vecs = [embed_fn(t) if embed_fn else hash_embed(t, dim=dim) for t in exs]
        d = len(vecs[0])
        assert all(len(v) == d for v in vecs), f"dim mismatch for {label}"
        mean = [sum(v[i] for v in vecs) / len(vecs) for i in range(d)]
        n = math.sqrt(sum(x * x for x in mean)) or 1.0
        cents[label] = [x / n for x in mean]
    return cents


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def atomic_write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.rename(path)


def parse_frontmatter(text: str):
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return {}, text
    fm: dict = {}
    cur = None
    for raw in lines[1:end]:
        if re.match(r"^\s*-\s+", raw) and cur is not None:
            fm.setdefault(cur, [])
            if isinstance(fm[cur], list):
                fm[cur].append(raw.strip()[2:].strip('"'))
            continue
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", raw)
        if m:
            k, v = m.group(1), m.group(2).strip()
            cur = k
            if v == "":
                fm[k] = []
            else:
                fm[k] = v.strip('"')
    body = "\n".join(lines[end + 1 :])
    return fm, body


def extract_headers(body: str) -> str:
    out: list[str] = []
    fence = False
    for line in body.splitlines():
        if line.strip().startswith("```"):
            fence = not fence
            continue
        if not fence and line.lstrip().startswith("#"):
            out.append(line)
    return "\n".join(out)
