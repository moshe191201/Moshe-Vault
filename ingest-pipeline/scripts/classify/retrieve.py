"""Top-k retriever — embedding filter before LLM judge.

Builds per-label centroid from taxonomy/doc_types examples, calls OpenAI-compatible
/v1/embeddings, ranks by cosine, returns top-k per doc.

Supports --doctype for document-type vocab with language/extension pruning.

Offline-friendly: if no endpoint, falls back to deterministic hashing so tests pass.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
from pathlib import Path
import sys
import urllib.request, urllib.error

from taxonomy import parse_taxonomy_blocks, parse_doc_types_blocks, effective_doc_type_candidates, load_doc_types, templates_root
from classify_common import cosine, hash_embed

TOP_K_MIN, TOP_K_MAX = 1, 10


def call_embeddings(texts, base_url, api_key, model):
    url = base_url.rstrip("/") + "/v1/embeddings"
    body = json.dumps({"model": model, "input": texts}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read().decode())
        # OpenAI shape: data[].embedding
        return [row["embedding"] for row in data["data"]]


def _templates_root_lazy(filename: str):
    return templates_root() / filename


def load_taxonomy(path: Path):
    txt = path.read_text(encoding="utf-8")
    subs = {}
    blocks = parse_taxonomy_blocks(txt)
    for name, block in blocks.items():
        # Support text: "quoted", text: 'single', text: | multiline (first line)
        examples = re.findall(r'text:\s*"(.*?)"', block)
        examples += re.findall(r"text:\s*'(.*?)'", block)
        # multiline literal: text: | then indented line
        for mm in re.finditer(r"text:\s*\|\n\s+(.+)", block):
            examples.append(mm.group(1).strip())
        subs[name] = examples
    return subs


def load_doc_types_tolerant(path: Path):
    """Load doc_types examples dict from a doc_types.yaml path, tolerant of structure."""
    _txt, mapping = load_doc_types(path)
    return {k: v.get("examples", []) for k, v in mapping.items()}, _txt


def main():
    p = argparse.ArgumentParser(description="Top-k retriever")
    p.add_argument("--campaign", default="campaigns/example")
    p.add_argument("--store", default="store")
    p.add_argument("--top-k", type=int, default=None)
    p.add_argument("--doctype", action="store_true", help="retrieve for doc-type (uses doc_types.yaml + pruning)")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    policy_path = Path(args.campaign) / "policy.yaml"
    if args.doctype:
        # Resolve doc_types.yaml: campaign/doc_types.yaml or template fallback
        cand = Path(args.campaign) / "doc_types.yaml"
        if cand.exists():
            vocab_path = cand
        else:
            vocab_path = _templates_root_lazy("doc_types.yaml")
        if not vocab_path.exists():
            print(f"retrieve: no doc_types.yaml at {cand} nor template {vocab_path}", file=sys.stderr)
            return 1
        subs, raw_txt = load_doc_types_tolerant(vocab_path)
        # Keep raw_txt for pruning via blocks parsed separately
        # Parse blocks for pruning (need block strings)
        blocks = parse_doc_types_blocks(raw_txt)
    else:
        tax_path = Path(args.campaign) / "taxonomy.yaml"
        if not tax_path.exists():
            # fallback to payload template
            tax_path = _templates_root_lazy("taxonomy.yaml")
        vocab_path = tax_path
        subs = load_taxonomy(vocab_path)
        blocks = {}
    top_k = args.top_k or 4
    # policy override
    if policy_path.exists():
        m = re.search(r"top_k:\s*(\d+)", policy_path.read_text(encoding="utf-8"))
        if m and args.top_k is None:
            top_k = int(m.group(1))
    top_k = max(TOP_K_MIN, min(TOP_K_MAX, top_k))

    if not subs:
        kind = "doc_types" if args.doctype else "subdomains"
        print(f"retrieve: no {kind} in {vocab_path}", file=sys.stderr)
        return 1

    base_url = os.environ.get("CLASSIFY_EMBED_BASE_URL") or os.environ.get("QMD_OPENAI_BASE_URL") or ""
    api_key = os.environ.get("CLASSIFY_EMBED_API_KEY") or os.environ.get("QMD_OPENAI_API_KEY") or ""
    model = os.environ.get("CLASSIFY_EMBED_MODEL") or "embeddinggemma-300M"

    # Build centroids
    centroids = {}
    centroid_dim = None
    for name, examples in subs.items():
        if not examples:
            continue
        # Embed examples
        if base_url:
            try:
                vecs = call_embeddings(examples, base_url, api_key, model)
            except Exception as e:
                # Fail-closed when endpoint configured (I6): outage must not silently use hash stub in prod
                # Hash fallback only when base_url == "" (tests). With base_url set, exit non-zero.
                print(f"retrieve: embedding API failed ({e}) — failing closed (endpoint configured)", file=sys.stderr)
                return 1
        else:
            vecs = [hash_embed(t) for t in examples]
        # centroid = mean
        dim = len(vecs[0])
        if centroid_dim is None:
            centroid_dim = dim
        elif dim != centroid_dim:
            print(f"retrieve: embedding dim mismatch {dim} vs {centroid_dim} — failing closed", file=sys.stderr)
            return 1
        cent = [sum(v[i] for v in vecs)/len(vecs) for i in range(dim)]
        n = math.sqrt(sum(x*x for x in cent)) or 1.0
        centroids[name] = [x/n for x in cent]

    store_root = Path(args.store)
    docs = list(store_root.rglob("*.md")) if store_root.exists() else []
    if not docs:
        print(f"retrieve: no docs in {store_root}", file=sys.stderr)
        if args.dry_run:
            print(f"[dry-run] would rank {len(subs)} labels top_k={top_k}")
            return 0
        return 1

    # For doctype pruning we need helper
    prune_fn = effective_doc_type_candidates

    for doc in docs[:1000]:  # cap per run
        # Prune before embedding: effective candidates based on metadata
        allowed_centroids = centroids
        pruned = False
        pruned_note = ""
        if args.doctype and prune_fn is not None and blocks:
            # Read metadata sidecar or frontmatter
            meta = {}
            meta_path = doc.with_suffix(".meta.json")
            # content-addressed md has suffix .md, so with_suffix .meta.json => .meta.json (not .md.meta.json) -> need special handling
            # Actually store/ab/abcd.md -> abcd.meta.json would be with_suffix -> abcd.meta.json? Path.with_suffix replaces last suffix, so .md->.meta.json works but double-ext?
            # For .md, with_suffix(".meta.json") -> "abcd.meta.json"
            # Check both
            if not meta_path.exists():
                # try sibling with .meta.json directly
                alt = doc.parent / (doc.stem + ".meta.json")
                if alt.exists():
                    meta_path = alt
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except Exception:
                    meta = {}
            else:
                # fallback to frontmatter
                try:
                    txt = doc.read_text(encoding="utf-8")
                    if txt.startswith("---"):
                        parts = txt.split("---", 2)
                        body_tmp = parts[2] if len(parts) > 2 else txt
                        fm_lines = parts[1] if len(parts) > 1 else ""
                        m_ext = re.search(r'source_ext:\s*"([^"]*)"', fm_lines)
                        m_lang = re.search(r'original_language:\s*"([^"]*)"', fm_lines)
                        if m_ext:
                            meta["source_ext"] = m_ext.group(1)
                        if m_lang:
                            meta["original_language"] = m_lang.group(1)
                except Exception:
                    pass
            ext = str(meta.get("source_ext", ""))
            lang = str(meta.get("original_language", ""))
            # Only prune if at least one gate is present (i.e., lang/ext provided); empty lang/ext means no restriction
            allowed_names = set(prune_fn(blocks, original_language=lang, extension=ext))
            if allowed_names != set(centroids.keys()):
                pruned = True
                pruned_note = f"pruned by lang={lang!r} ext={ext!r}"
            allowed_centroids = {k: v for k, v in centroids.items() if k in allowed_names}
            if not allowed_centroids:
                print(f"retrieve: no candidates after pruning for {doc.name} (lang={lang!r} ext={ext!r}) — skipping", file=sys.stderr)
                continue
        text = doc.read_text(encoding="utf-8")
        # strip frontmatter for embedding
        if text.startswith("---"):
            parts = text.split("---", 2)
            body = parts[2] if len(parts) > 2 else text
        else:
            body = text
        if base_url:
            try:
                qvec = call_embeddings([body[:4000]], base_url, api_key, model)[0]
            except Exception as e:
                print(f"retrieve: embedding API failed for doc {doc.name} ({e}) — failing closed", file=sys.stderr)
                return 1
            if len(qvec) != centroid_dim:
                print(f"retrieve: query dim {len(qvec)} vs centroid dim {centroid_dim} mismatch — failing closed", file=sys.stderr)
                return 1
        else:
            qvec = hash_embed(body[:4000], dim=centroid_dim)
        scores = [(name, cosine(qvec, cent)) for name, cent in allowed_centroids.items()]
        scores.sort(key=lambda x: x[1], reverse=True)
        top = scores[:top_k]
        if args.dry_run:
            flag = f" [{pruned_note}]" if pruned else ""
            print(f"[dry-run] {doc.name}: top-{top_k} = {', '.join(f'{n}({s:.2f})' for n,s in top)}{flag}")
        else:
            # write sidecar .retrieval.json next to doc (handle .md -> .retrieval.json)
            # doc is store/ab/<hash>.md -> sidecar is store/ab/<hash>.retrieval.json
            sidecar = doc.parent / (doc.stem + ".retrieval.json")
            tmp = sidecar.with_suffix(sidecar.suffix + ".tmp")
            # tmp will be .retrieval.json.tmp
            payload = {"doc": doc.name, "top_k": top_k, "candidates": [{"subdomain" if not args.doctype else "doc_type": n, "score": s} for n,s in top]}
            # For doctype, legacy key is subdomain but also add doc_type for new code; keep both for compat
            # Normalize: ensure both keys for readers
            if args.doctype:
                payload["candidates"] = [{"doc_type": n, "subdomain": n, "score": s} for n,s in top]
                payload["pruned"] = pruned
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.rename(sidecar)
            print(f"retrieve: {doc.name} -> {', '.join(n for n,_ in top)}" + (f" [{pruned_note}]" if pruned else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
