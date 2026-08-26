"""Judge — reasoning-first closed-choice LLM classifier.

Builds prompt from taxonomy/doc_types + glossary + top-k candidates + chunk text,
calls OpenAI-compatible /v1/chat/completions with guided_json enum enforcement,
emits JSON with reasoning_brief FIRST. Supports subdomain and doc-type tasks.

Temp 0, categorical confidence buckets.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
import sys
import urllib.request

BUCKETS = ["SURE", "NEEDS_HUMAN_VALIDATION", "I_GUESSED"]
RELATIONS = ["none", "comparison", "relationship", "progression"]


try:  # package import (from classify.x import ...)
    from .taxonomy import TAXONOMY_RE as _TAXONOMY_RE, parse_taxonomy_blocks as _parse_blocks, templates_root
    from .taxonomy import parse_doc_types_blocks as _parse_doctype_blocks, load_doc_types as _load_doc_types
    from .taxonomy import effective_doc_type_candidates as _effective_candidates, singleton_pruned_type as _singleton_type
except ImportError:  # direct script run (python scripts/classify/x.py)
    from taxonomy import TAXONOMY_RE as _TAXONOMY_RE, parse_taxonomy_blocks as _parse_blocks, templates_root
    from taxonomy import parse_doc_types_blocks as _parse_doctype_blocks, load_doc_types as _load_doc_types
    from taxonomy import effective_doc_type_candidates as _effective_candidates, singleton_pruned_type as _singleton_type


def load_yaml_simple(path: Path):
    if not path.exists():
        return "", {}
    txt = path.read_text(encoding="utf-8")
    blocks = _parse_blocks(txt)
    subs: dict = {}
    for name, block in blocks.items():
        def_m = re.search(r"definition:\s*\"(.*?)\"", block, flags=re.DOTALL)
        examples = re.findall(r"text:\s*\"(.*?)\"", block)
        subs[name] = {"definition": def_m.group(1) if def_m else "", "examples": examples}
    return txt, subs


def load_doc_types_simple(path: Path):
    if _load_doc_types is None:
        return "", {}
    txt, mapping = _load_doc_types(path)
    # mapping: {name: {definition, examples, block}}
    out: dict = {}
    for name, info in mapping.items():
        out[name] = {"definition": info.get("definition", ""), "examples": info.get("examples", [])}
    return txt, out


def build_prompt(chunk_text, taxonomy_subs, glossary_text, candidates, policy_text, few_shot_per_topk=3, task="subdomain", source_metadata=None):
    """Build system + user prompt. task=subdomain|doctype; source_metadata for doctype."""
    if task == "doctype":
        # Metadata line for doctype
        meta_line = ""
        if source_metadata:
            meta_line = (
                f"Source metadata: filename={source_metadata.get('filename','')} "
                f"ext={source_metadata.get('source_ext','') or source_metadata.get('ext','')} "
                f"original_language={source_metadata.get('original_language','') or source_metadata.get('lang','')}\n"
                "Typical mapping: internal→he, external→en (defaults, editable)\n\n"
            )
        cand_defs = ""
        for c in candidates:
            sub = taxonomy_subs.get(c, {})
            cand_defs += f"- {c}: {sub.get('definition','')}\n"
            for ex in sub.get("examples", [])[:few_shot_per_topk]:
                cand_defs += f"  example: \"{ex[:200]}\"\n"
        system = (
            "You are a document type classifier (single label). Think step by step BEFORE choosing.\n"
            "First write reasoning_brief mapping evidence (content + metadata + headers) to doc_type — be thorough, no length limit.\n"
            "Then pick doc_type from the candidates only.\n"
            + meta_line
            + f"Candidates:\n{cand_defs}\n"
            + "Buckets: SURE=explicit structural signal and metadata consistent; NEEDS_HUMAN_VALIDATION=ambiguous or close runner-up or metadata ambiguous; I_GUESSED=thin/generic.\n"
            + "For Q&A, distinguish Q with answers (has approved A) vs Q without answers (exercise).\n"
        )
        user = f"Headers + body to classify:\n{chunk_text[:6000]}"
        return system, user
    # subdomain (original)
    glossary_header = ""
    if glossary_text:
        glossary_header = "Domain knowledge (surface form → subdomain):\n" + glossary_text[:2000] + "\n\n"
    # Only candidates
    cand_defs = ""
    for c in candidates:
        sub = taxonomy_subs.get(c, {})
        cand_defs += f"- {c}: {sub.get('definition','')}\n"
        for ex in sub.get("examples", [])[:few_shot_per_topk]:
            cand_defs += f"  example: \"{ex[:200]}\"\n"

    system = (
        "You are a subdomain classifier. Think step by step BEFORE choosing.\n"
        "First write reasoning_brief mapping evidence to subdomain via domain_knowledge — be thorough, no length limit.\n"
        "Then pick primary_subdomain from the candidates only.\n"
        + glossary_header
        + f"Candidates:\n{cand_defs}\n"
        + "Buckets: SURE=explicit+primary focus no comparator; NEEDS_HUMAN_VALIDATION=implicit or comparison or close runner-up; I_GUESSED=thin generic.\n"
        + "If the doc compares 2+ subdomains, set primary to main focus and list others in secondary_subdomains with relation_type.\n"
    )
    user = f"Headers + body to classify:\n{chunk_text[:6000]}"
    return system, user


def build_schema(allowed, task="subdomain"):
    # reasoning_brief listed first in properties so vLLM guided_json tends to emit it first; not a JSON Schema guarantee (L2).
    if task == "doctype":
        return {
            "type": "object",
            "properties": {
                "reasoning_brief": {"type": "string", "description": "Thorough reasoning, evidence → doc_type, no length limit"},
                "doc_type": {"type": "string", "enum": allowed},
                "confidence_bucket": {"type": "string", "enum": BUCKETS},
            },
            "required": ["reasoning_brief", "doc_type", "confidence_bucket"],
            "additionalProperties": False,
        }
    return {
        "type": "object",
        "properties": {
            "reasoning_brief": {"type": "string", "description": "Thorough reasoning, evidence → subdomain, no length limit"},
            "primary_subdomain": {"type": "string", "enum": allowed},
            "secondary_subdomains": {"type": "array", "items": {"type": "string", "enum": allowed}},
            "relation_type": {"type": "string", "enum": RELATIONS},
            "confidence_bucket": {"type": "string", "enum": BUCKETS},
        },
        "required": ["reasoning_brief", "primary_subdomain", "confidence_bucket"],
        "additionalProperties": False,
    }


def call_llm(system, user, base_url, api_key, model, schema):
    url = base_url.rstrip("/") + "/v1/chat/completions"
    # vLLM expects extra_body.guided_json, OpenAI expects response_format.json_schema
    body_dict = {
        "model": model,
        "temperature": 0.0,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    if schema:
        body_dict["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "classification", "schema": schema, "strict": True},
        }
        body_dict["extra_body"] = {"guided_json": schema}
    else:
        body_dict["response_format"] = {"type": "json_object"}
    body = json.dumps(body_dict).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read().decode())
        content = data["choices"][0]["message"]["content"]
        try:
            return json.loads(content) if isinstance(content, str) else content
        except json.JSONDecodeError:
            # Unparseable -> I_GUESSED fallback per spec
            if schema and "doc_type" in schema.get("properties", {}):
                return {"reasoning_brief": content if isinstance(content, str) else "parse error", "doc_type": "", "confidence_bucket": "I_GUESSED"}
            return {"reasoning_brief": content if isinstance(content, str) else "parse error", "primary_subdomain": "", "confidence_bucket": "I_GUESSED", "relation_type": "none", "secondary_subdomains": []}


def _resolve_doc_types_path(campaign: Path) -> Path:
    """Return doc_types.yaml path for the campaign (or template fallback)."""
    cand = campaign / "doc_types.yaml"
    if cand.exists():
        return cand
    return templates_root() / "doc_types.yaml"


def _get_source_metadata(doc: Path) -> dict:
    """Load metadata for a store doc (sidecar .meta.json or frontmatter fallback)."""
    # Try sidecar: <hash>.meta.json (stem + .meta.json)
    side = doc.parent / (doc.stem + ".meta.json")
    if side.exists():
        try:
            return json.loads(side.read_text(encoding="utf-8"))
        except Exception:
            pass
    # Also try with_suffix .meta.json
    alt = doc.with_suffix(".meta.json")
    if alt.exists() and alt != side:
        try:
            return json.loads(alt.read_text(encoding="utf-8"))
        except Exception:
            pass
    # Fallback to frontmatter
    try:
        txt = doc.read_text(encoding="utf-8")
        if txt.startswith("---"):
            for line in txt.splitlines()[1:]:
                if line.strip() == "---":
                    break
                m = re.match(r'source_ext:\s*"([^"]*)"', line)
                if m:
                    return {"source_ext": m.group(1), "original_language": ""}
                m2 = re.match(r'original_language:\s*"([^"]*)"', line)
                if m2:
                    # need both; but we return what we have
                    pass
            # Second pass for both
            fm: dict = {}
            for line in txt.splitlines()[1:]:
                if line.strip() == "---":
                    break
                m = re.match(r'([A-Za-z_]+):\s*"([^"]*)"', line)
                if m:
                    fm[m.group(1)] = m.group(2)
            return {"source_ext": fm.get("source_ext", ""), "original_language": fm.get("original_language", ""), "filename": fm.get("source_path", doc.name)}
    except Exception:
        pass
    return {}


def main():
    p = argparse.ArgumentParser(description="Reasoning-first judge")
    p.add_argument("--campaign", default="campaigns/example")
    p.add_argument("--store", default="store")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--taxonomy", default=None)
    p.add_argument("--doctype", action="store_true", help="doctype mode (doc_type vocab + pruning)")
    args = p.parse_args()

    camp = Path(args.campaign)
    task = "doctype" if args.doctype else "subdomain"

    if task == "doctype":
        doc_types_path = Path(args.taxonomy) if args.taxonomy else _resolve_doc_types_path(camp)
        if not doc_types_path.exists():
            # Try ingest template mirror
            doc_types_path = _resolve_doc_types_path(camp)
        if not doc_types_path.exists():
            print(f"judge: no doc_types.yaml at {doc_types_path}", file=sys.stderr)
            return 1
        _, subs = load_doc_types_simple(doc_types_path)
        gloss_text = ""
        allowed = sorted(subs.keys())
        if not allowed:
            print(f"judge: no doc_types in {doc_types_path}", file=sys.stderr)
            return 1
        schema = build_schema(allowed, task="doctype")
        assert "doc_type" in schema["properties"]
        # For doctype we need blocks for singleton check
        raw_txt = doc_types_path.read_text(encoding="utf-8") if doc_types_path.exists() else ""
        try:
            blocks = _parse_doctype_blocks(raw_txt) if _parse_doctype_blocks else {}
        except Exception:
            blocks = {}
        if args.dry_run:
            print(json.dumps(schema, indent=2))
            print("\n--- Example prompt preview (first candidate) ---")
            cand = allowed[:4]
            few_shot = 3
            _pol_path = camp / "policy.yaml"
            if not _pol_path.exists():
                _pol_path = (templates_root() / "policy.yaml")
            try:
                if _pol_path.exists():
                    _m = re.search(r"few_shot_per_topk:\s*(\d+)", _pol_path.read_text(encoding="utf-8"))
                    if _m:
                        few_shot = int(_m.group(1))
            except Exception:
                pass
            sys_p, _ = build_prompt("Example Q: What is X? A: Y", subs, "", cand, "", few_shot, task="doctype", source_metadata={"filename": "train.md", "source_ext": "md", "original_language": "he"})
            print(sys_p[:1000])
            return 0
        base_url = os.environ.get("CLASSIFY_LLM_BASE_URL") or os.environ.get("QMD_OPENAI_BASE_URL") or ""
        api_key = os.environ.get("CLASSIFY_LLM_API_KEY") or os.environ.get("QMD_OPENAI_API_KEY") or ""
        model = os.environ.get("CLASSIFY_LLM_MODEL") or "minimax-m2.7"
        if not base_url:
            # Allow singleton-pruned docs to succeed even without endpoint (tests)
            # We will handle singleton below and return 0 if all docs were singleton
            pass
        # few_shot
        few_shot_per_topk = 3
        _policy_path_for_fewshot = camp / "policy.yaml"
        if not _policy_path_for_fewshot.exists():
            _policy_path_for_fewshot = (templates_root() / "policy.yaml")
        try:
            if _policy_path_for_fewshot.exists():
                _mf = re.search(r"few_shot_per_topk:\s*(\d+)", _policy_path_for_fewshot.read_text(encoding="utf-8"))
                if _mf:
                    few_shot_per_topk = int(_mf.group(1))
        except Exception:
            pass
        store_root = Path(args.store)
        docs = list(store_root.rglob("*.md"))
        if not docs and args.dry_run:
            return 0
        any_llm_needed = False
        for doc in docs[:2000]:
            meta = _get_source_metadata(doc)
            # Check singleton before anything
            singleton = None
            if blocks and _singleton_type:
                try:
                    singleton = _singleton_type(blocks, original_language=str(meta.get("original_language", "")), extension=str(meta.get("source_ext", "")))
                except Exception:
                    singleton = None
            if singleton:
                out = {"reasoning_brief": f"singleton_constraint: pruned to {singleton} via language/ext (lang={meta.get('original_language','')} ext={meta.get('source_ext','')})", "doc_type": singleton, "confidence_bucket": "SURE", "singleton_constraint": True}
                out_path = doc.parent / (doc.stem + ".judge.json")
                tmp = out_path.with_suffix(out_path.suffix + ".tmp")
                tmp.write_text(json.dumps(out, indent=2), encoding="utf-8")
                tmp.rename(out_path)
                print(f"judge: {doc.name} -> {singleton} [SURE singleton]")
                continue
            # Need LLM for non-singleton
            any_llm_needed = True
            # Load candidates from retrieval sidecar if exists
            sidecar = doc.parent / (doc.stem + ".retrieval.json")
            if not sidecar.exists():
                sidecar = doc.with_suffix(".retrieval.json")
            if sidecar.exists():
                try:
                    data = json.loads(sidecar.read_text(encoding="utf-8"))
                    cands = [c.get("doc_type") or c.get("subdomain") for c in data.get("candidates", [])]
                    cands = [c for c in cands if c]
                except Exception:
                    cands = allowed[:4]
            else:
                # Fallback: pruned candidates from taxonomy pruning
                if blocks and _effective_candidates:
                    try:
                        cands = _effective_candidates(blocks, original_language=str(meta.get("original_language", "")), extension=str(meta.get("source_ext", "")))[:4]
                        if not cands:
                            cands = allowed[:4]
                    except Exception:
                        cands = allowed[:4]
                else:
                    cands = allowed[:4]
            if not base_url:
                print(f"judge: no CLASSIFY_LLM_BASE_URL / QMD_OPENAI_BASE_URL and doc {doc.name} not singleton — failing closed", file=sys.stderr)
                return 1
            text = doc.read_text(encoding="utf-8")
            if text.startswith("---"):
                parts = text.split("---", 2)
                body = parts[2] if len(parts) > 2 else text
            else:
                body = text
            system, user = build_prompt(body, subs, gloss_text, cands, "", few_shot_per_topk, task="doctype", source_metadata=meta)
            try:
                out = call_llm(system, user, base_url, api_key, model, schema)
            except Exception as e:
                print(f"judge: {doc.name} failed: {e}", file=sys.stderr)
                out = {"reasoning_brief": f"LLM error: {e}", "doc_type": cands[0] if cands else "", "confidence_bucket": "I_GUESSED"}
            if out.get("doc_type") not in allowed:
                # Allow empty after pruned singleton but otherwise mark I_GUESSED
                if out.get("doc_type"):
                    print(f"judge: {doc.name} doc_type not in vocab: {out.get('doc_type')}", file=sys.stderr)
                out["confidence_bucket"] = "I_GUESSED"
            out_path = doc.parent / (doc.stem + ".judge.json")
            tmp = out_path.with_suffix(out_path.suffix + ".tmp")
            tmp.write_text(json.dumps(out, indent=2), encoding="utf-8")
            tmp.rename(out_path)
            print(f"judge: {doc.name} -> {out.get('doc_type')} [{out.get('confidence_bucket')}]")
        if any_llm_needed and not base_url:
            return 1
        return 0

    # --- subdomain path (original) ---
    tax_path = Path(args.taxonomy) if args.taxonomy else camp / "taxonomy.yaml"
    if not tax_path.exists():
        tax_path = (templates_root() / "taxonomy.yaml")
    gloss_path = camp / "glossary.yaml"
    if not gloss_path.exists():
        gloss_path = (templates_root() / "glossary.yaml")

    _, subs = load_yaml_simple(tax_path)
    gloss_text = gloss_path.read_text(encoding="utf-8") if gloss_path.exists() else ""
    allowed = sorted(subs.keys())
    if not allowed:
        print(f"judge: no subdomains in {tax_path}", file=sys.stderr)
        return 1

    schema = build_schema(allowed, task="subdomain")
    # Validate schema invariant: numeric score must be impossible
    assert "confidence_bucket" in schema["properties"]
    assert schema["properties"]["confidence_bucket"]["enum"] == BUCKETS

    if args.dry_run:
        print(json.dumps(schema, indent=2))
        print("\n--- Example prompt preview (first candidate) ---")
        cand = allowed[:4]
        few_shot = 3
        _pol_path = Path(args.campaign) / "policy.yaml" if not Path(args.campaign).is_absolute() else Path(args.campaign) / "policy.yaml"
        if not _pol_path.exists():
            _pol_path = (templates_root() / "policy.yaml")
        try:
            if _pol_path.exists():
                _m = re.search(r"few_shot_per_topk:\s*(\d+)", _pol_path.read_text(encoding="utf-8"))
                if _m:
                    few_shot = int(_m.group(1))
        except Exception:
            pass
        sys_p, usr_p = build_prompt("Example doc body about HbA1c...", subs, gloss_text, cand, "", few_shot, task="subdomain")
        print(sys_p[:800])
        return 0

    base_url = os.environ.get("CLASSIFY_LLM_BASE_URL") or os.environ.get("QMD_OPENAI_BASE_URL") or ""
    api_key = os.environ.get("CLASSIFY_LLM_API_KEY") or os.environ.get("QMD_OPENAI_API_KEY") or ""
    model = os.environ.get("CLASSIFY_LLM_MODEL") or "minimax-m2.7"

    if not base_url:
        print("judge: no CLASSIFY_LLM_BASE_URL / QMD_OPENAI_BASE_URL — failing closed (no LLM endpoint configured)", file=sys.stderr)
        return 1

    # few_shot_per_topk from policy.yaml (default 3)
    few_shot_per_topk = 3
    _policy_path_for_fewshot = Path(args.campaign) / "policy.yaml"
    if not _policy_path_for_fewshot.exists():
        _policy_path_for_fewshot = (templates_root() / "policy.yaml")
    try:
        if _policy_path_for_fewshot.exists():
            _mf = re.search(r"few_shot_per_topk:\s*(\d+)", _policy_path_for_fewshot.read_text(encoding="utf-8"))
            if _mf:
                few_shot_per_topk = int(_mf.group(1))
    except Exception:
        pass

    store_root = Path(args.store)
    docs = list(store_root.rglob("*.md"))
    for doc in docs[:2000]:
        # load candidates from sidecar if exists
        # handle stem-based sidecar naming (store/ab/<hash>.md -> <hash>.retrieval.json)
        sidecar = doc.parent / (doc.stem + ".retrieval.json")
        if not sidecar.exists():
            sidecar = doc.with_suffix(".retrieval.json")
        if sidecar.exists():
            try:
                cands = [c["subdomain"] for c in json.loads(sidecar.read_text(encoding="utf-8"))["candidates"]]
            except Exception:
                cands = allowed[:4]
        else:
            cands = allowed[:4]
        text = doc.read_text(encoding="utf-8")
        if text.startswith("---"):
            parts = text.split("---", 2)
            body = parts[2] if len(parts) > 2 else text
        else:
            body = text
        system, user = build_prompt(body, subs, gloss_text, cands, "", few_shot_per_topk, task="subdomain")
        try:
            out = call_llm(system, user, base_url, api_key, model, schema)
        except Exception as e:
            print(f"judge: {doc.name} failed: {e}", file=sys.stderr)
            out = {"reasoning_brief": f"LLM error: {e}", "primary_subdomain": cands[0], "confidence_bucket": "I_GUESSED", "relation_type": "none", "secondary_subdomains": []}
        # validate closed vocab
        if out.get("primary_subdomain") not in allowed:
            print(f"judge: {doc.name} primary not in taxonomy: {out.get('primary_subdomain')}", file=sys.stderr)
            out["confidence_bucket"] = "I_GUESSED"
        out_path = doc.parent / (doc.stem + ".judge.json")
        tmp = out_path.with_suffix(out_path.suffix + ".tmp")
        tmp.write_text(json.dumps(out, indent=2), encoding="utf-8")
        tmp.rename(out_path)
        print(f"judge: {doc.name} -> {out.get('primary_subdomain')} [{out.get('confidence_bucket')}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
