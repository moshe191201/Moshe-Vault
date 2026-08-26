"""Export Label Studio tasks + render view.xml.

Reads store/*.md + sidecars (.retrieval.json, .judge.json),
renders markdown to HTML (minimal), writes tasks JSON.
"""
from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path
import sys


def md_to_html(md: str) -> str:
    # Minimal: headers, bold, code fences, paragraphs — enough for review
    # Strip frontmatter
    if md.startswith("---"):
        parts = md.split("---", 2)
        if len(parts) > 2:
            md = parts[2]
    # Escape then restore markdown markers
    esc = html.escape(md)
    esc = re.sub(r"^### (.*)", r"<h3>\1</h3>", esc, flags=re.MULTILINE)
    esc = re.sub(r"^## (.*)", r"<h2>\1</h2>", esc, flags=re.MULTILINE)
    esc = re.sub(r"^# (.*)", r"<h1>\1</h1>", esc, flags=re.MULTILINE)
    esc = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", esc)
    esc = re.sub(r"```(.*?)```", r"<pre>\1</pre>", esc, flags=re.DOTALL)
    esc = re.sub(r"\n\n+", r"</p><p>", esc)
    return f"<p>{esc}</p>"



try:  # package import (from classify.x import ...)
    from .taxonomy import templates_root as _templates_root
except ImportError:  # direct script run (python scripts/classify/x.py)
    from taxonomy import templates_root as _templates_root

def main():
    p = argparse.ArgumentParser(description="Export Label Studio tasks")
    p.add_argument("--campaign", default="campaigns/example")
    p.add_argument("--store", default="store")
    p.add_argument("--out", default="label_studio_tasks.json")
    p.add_argument("--view-out", default=None, help="rendered view.xml path")
    p.add_argument("--doctype", action="store_true", help="export for doc-type")
    p.add_argument("--singleton-audit", action="store_true", help="export singleton audit view")
    args = p.parse_args()

    camp = Path(args.campaign)
    # Resolve vocab for view rendering
    if args.doctype or args.singleton_audit:
        # Doctype: parse doc_types
        dt_path = camp / "doc_types.yaml"
        if not dt_path.exists():
            dt_path = _templates_root() / "doc_types.yaml"
        subs = []
        if dt_path.exists():
            from taxonomy import parse_doc_types_blocks as _pdtb
            subs = list(_pdtb(dt_path.read_text(encoding="utf-8")).keys())
    else:
        tax_path = camp / "taxonomy.yaml"
        if not tax_path.exists():
            tax_path = _templates_root() / "taxonomy.yaml"
        # Parse subdomain list for view rendering
        subs = []
        if tax_path.exists():
            for m in re.finditer(r"^\s{2}([\w-]+):\s*(?:\n|$)", tax_path.read_text(encoding="utf-8"), flags=re.MULTILINE):
                n = m.group(1)
                if n not in ("subdomains", "version", "campaign"):
                    subs.append(n)

    store_root = Path(args.store)
    docs = list(store_root.rglob("*.md")) if store_root.exists() else []
    # Filter out sidecars (already .md but with frontmatter source_doc_id)
    docs = [d for d in docs if d.suffix == ".md" and not d.name.endswith(".retrieval.json")]

    tasks = []
    for doc in docs[:5000]:
        text = doc.read_text(encoding="utf-8")
        html_text = md_to_html(text)
        # sidecars — handle both with_suffix and stem-based naming (store/ab/<hash>.md)
        judge = {}
        # try stem-based first, then with_suffix
        for cand in [doc.parent / (doc.stem + ".judge.json"), doc.with_suffix(".judge.json")]:
            if cand.exists():
                try:
                    judge = json.loads(cand.read_text(encoding="utf-8"))
                    break
                except Exception:
                    pass
        retrieval = {}
        for cand in [doc.parent / (doc.stem + ".retrieval.json"), doc.with_suffix(".retrieval.json")]:
            if cand.exists():
                try:
                    retrieval = json.loads(cand.read_text(encoding="utf-8"))
                    break
                except Exception:
                    pass
        # Meta for pruned note
        meta = {}
        for cand in [doc.parent / (doc.stem + ".meta.json"), doc.with_suffix(".meta.json")]:
            if cand.exists():
                try:
                    meta = json.loads(cand.read_text(encoding="utf-8"))
                    break
                except Exception:
                    pass
        bucket = judge.get("confidence_bucket", "NEEDS_HUMAN_VALIDATION")
        cls_map = {"SURE": "sure", "NEEDS_HUMAN_VALIDATION": "needs", "I_GUESSED": "guessed"}
        if args.doctype or args.singleton_audit:
            # Filter singleton audit: only docs with singleton_constraint
            if args.singleton_audit and not judge.get("singleton_constraint"):
                continue
            if not args.singleton_audit and judge.get("singleton_constraint"):
                # Exclude singleton from main doctype queue (they go to audit)
                continue
            pruned_note = f"pruned: {retrieval.get('pruned', False)}" if retrieval else ""
            meta_line = f"Source: {meta.get('source_path','')} ext={meta.get('source_ext','')} lang={meta.get('original_language','')}" if meta else ""
            # candidates may be under doc_type or subdomain
            cands = retrieval.get("candidates", [])
            cand_str = ", ".join(c.get("doc_type") or c.get("subdomain","") for c in cands)
            tasks.append({
                "data": {
                    "text_html": html_text,
                    "filename": doc.name,
                    "doc_type": judge.get("doc_type", ""),
                    "confidence_bucket": bucket,
                    "confidence_class": cls_map.get(bucket, "needs"),
                    "candidates": cand_str,
                    "reasoning_brief": judge.get("reasoning_brief", ""),
                    "pruned_note": pruned_note,
                    "meta_line": meta_line,
                }
            })
        else:
            tasks.append({
                "data": {
                    "text_html": html_text,
                    "filename": doc.name,
                    "llm_primary": judge.get("primary_subdomain", ""),
                    "llm_secondary": ", ".join(judge.get("secondary_subdomains", [])),
                    "relation_type": judge.get("relation_type", "none"),
                    "confidence_bucket": bucket,
                    "confidence_class": cls_map.get(bucket, "needs"),
                    "candidates": ", ".join(c.get("subdomain","") for c in retrieval.get("candidates", [])),
                    "reasoning_brief": judge.get("reasoning_brief", ""),
                }
            })

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_text(json.dumps(tasks, indent=2), encoding="utf-8")
    tmp.rename(out_path)
    print(f"export: {len(tasks)} tasks -> {out_path}")

    # Render view.xml with N choices if requested
    if args.view_out and subs:
        # Choose template based on task
        if args.doctype:
            tpl_path = _templates_root() / "label_studio" / "view_doctype.xml"
        elif args.singleton_audit:
            tpl_path = _templates_root() / "label_studio" / "view_singleton_audit.xml"
        else:
            tpl_path = _templates_root() / "label_studio" / "view.xml"
        if not tpl_path.exists() and args.doctype:
            # Fallback to generic view.xml and inject doc_type choices
            tpl_path = _templates_root() / "label_studio" / "view.xml"
        if tpl_path.exists():
            tpl = tpl_path.read_text(encoding="utf-8")
            # Render N choices for variable-N (I1): replace placeholder Choice blocks
            rendered = tpl
            # Try doc_type placeholder
            if "<!-- CHOICES_PLACEHOLDER_doctype -->" in rendered:
                rendered = rendered.replace("<!-- CHOICES_PLACEHOLDER_doctype -->", "\n".join(f'      <Choice value="{s}" />' for s in subs))
            for tag in ("primary", "secondary", "doc_type"):
                pat = re.compile(rf'(<Choices name="{tag}".*?>)(.*?)(</Choices>)', re.DOTALL)
                if pat.search(rendered):
                    choices_str = "\n".join(f'        <Choice value="{s}" />' for s in subs)
                    rendered = pat.sub(lambda m: f"{m.group(1)}\n{choices_str}\n      {m.group(3)}", rendered)
            Path(args.view_out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.view_out).write_text(rendered, encoding="utf-8")
            kind = "doctype" if args.doctype else ("singleton_audit" if args.singleton_audit else "subdomain")
            print(f"export: view.xml -> {args.view_out} (N={len(subs)} {kind}: {', '.join(subs)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
