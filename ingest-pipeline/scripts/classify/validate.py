#!/usr/bin/env python3
"""Stage 6 closed-vocabulary classification validator.

Extracted from second_brain_vault_framework.core.cmd_classify. It lived in the
framework for historical reasons only: its sole input is store/*.judge.json,
produced by scripts/classify/judge.py, and the templates it reads are pipeline
templates. The framework now carries no knowledge of the pipeline.

Deliberately does NOT import second_brain_vault_framework -- that import is the
coupling this move exists to remove. parse_frontmatter is copied below rather than
shared: it is ~25 lines of pure stdlib, and duplicating it is cheaper than a
dependency pointing from the pipeline back at the framework package.

Pure stdlib. Reads store/*.judge.json, validates primary/secondary against
taxonomy.yaml, patches frontmatter and appends a ledger event.

CLI:
  python scripts/classify/validate.py <vault_root> --campaign DIR --store DIR [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import datetime  # module, not the class: the moved body calls datetime.datetime.now(...)
from pathlib import Path


try:  # package import (from classify.x import ...)
    from .taxonomy import TAXONOMY_RE as _TAXONOMY_RE, parse_taxonomy_blocks as _parse_taxonomy_blocks, templates_root as _templates_root
    from .taxonomy import parse_doc_types_blocks as _parse_doctype_blocks
except ImportError:  # direct script run (python scripts/classify/x.py)
    from taxonomy import TAXONOMY_RE as _TAXONOMY_RE, parse_taxonomy_blocks as _parse_taxonomy_blocks, templates_root as _templates_root
    from taxonomy import parse_doc_types_blocks as _parse_doctype_blocks

import importlib.util as _ilu_v2
_cc_v_path = Path(__file__).resolve().parent / "classify_common.py"
_spec_v2 = _ilu_v2.spec_from_file_location("_cc_v", _cc_v_path)
_mod_v2 = _ilu_v2.module_from_spec(_spec_v2)
_spec_v2.loader.exec_module(_mod_v2)  # type: ignore
parse_frontmatter = _mod_v2.parse_frontmatter


def _load_taxonomy_subdomains(campaign: Path, root: Path) -> set[str]:
    """Load allowed subdomains — strict, no payload fallback (H2 fail-closed)."""
    cand: Path | None = None
    if campaign.is_absolute():
        if campaign.is_dir():
            cand = campaign / "taxonomy.yaml"
        elif campaign.suffix in (".yaml", ".yml"):
            cand = campaign
        else:
            cand = campaign / "taxonomy.yaml"
        if cand is not None and not cand.exists() and (campaign / "taxonomy.yaml").exists():
            cand = campaign / "taxonomy.yaml"
    else:
        for q in [root / campaign / "taxonomy.yaml", root / campaign]:
            if q.is_dir():
                pp = q / "taxonomy.yaml"
                if pp.exists():
                    cand = pp
                    break
            elif q.exists() and q.suffix in (".yaml", ".yml"):
                cand = q
                break
        if cand is None:
            if campaign.exists() and campaign.suffix in (".yaml", ".yml"):
                cand = campaign
            elif (campaign / "taxonomy.yaml").exists():
                cand = campaign / "taxonomy.yaml"
    if cand is None or not cand.exists():
        return set()
    try:
        txt = cand.read_text(encoding="utf-8")
    except Exception:
        return set()
    return set(_parse_taxonomy_blocks(txt).keys())


def _load_doc_types_names(campaign: Path, root: Path) -> set[str]:
    """Load allowed doc_types — strict, no payload fallback for campaign, but template fallback for tests."""
    if _parse_doctype_blocks is None:
        return set()
    cand: Path | None = None
    if campaign.is_absolute():
        if campaign.is_dir():
            cand = campaign / "doc_types.yaml"
        elif campaign.suffix in (".yaml", ".yml"):
            cand = campaign
        else:
            cand = campaign / "doc_types.yaml"
        if cand is not None and not cand.exists() and (campaign / "doc_types.yaml").exists():
            cand = campaign / "doc_types.yaml"
    else:
        for q in [root / campaign / "doc_types.yaml", root / campaign]:
            if q.is_dir():
                pp = q / "doc_types.yaml"
                if pp.exists():
                    cand = pp
                    break
            elif q.exists() and q.suffix in (".yaml", ".yml"):
                cand = q
                break
        if cand is None:
            if campaign.exists() and campaign.suffix in (".yaml", ".yml"):
                cand = campaign
            elif (campaign / "doc_types.yaml").exists():
                cand = campaign / "doc_types.yaml"
    # Fallback to template for tests where campaign is dummy
    if cand is None or not cand.exists():
        # Try template
        try:
            tmpl = _templates_root() / "doc_types.yaml"
            if tmpl.exists():
                cand = tmpl
            else:
                return set()
        except Exception:
            return set()
    try:
        txt = cand.read_text(encoding="utf-8")
    except Exception:
        return set()
    return set(_parse_doctype_blocks(txt).keys())



def _yaml_escape_str(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")


def _yaml_quote_str(s: str) -> str:
    return f'"{_yaml_escape_str(s)}"'


def _yaml_dump_scalar(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return _yaml_quote_str(v)
    return _yaml_quote_str(str(v))


def _classification_file_version(path: Path) -> str:
    """Extract ``version:`` from a YAML file, fallback to ``1``."""
    try:
        t = path.read_text(encoding="utf-8")
        m = re.search(r"^version:\s*(\S+)", t, flags=re.MULTILINE)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    except Exception:
        pass
    return "1"


def _resolve_campaign_file(campaign_resolved: Path, filename: str) -> Path:
    """Resolve ``filename`` (taxonomy.yaml etc.) preferring the campaign dir."""
    if campaign_resolved.is_dir():
        pp = campaign_resolved / filename
        if pp.exists():
            return pp
    else:
        pp = campaign_resolved.parent / filename
        if pp.exists():
            return pp
    return _templates_root() / filename


def _classification_policy_values(campaign_resolved: Path) -> tuple[int, str]:
    """Read ``top_k`` and ``model_id`` from policy.yaml (campaign preferred)."""
    policy_path = _resolve_campaign_file(campaign_resolved, "policy.yaml")
    top_k = 4
    model_id = "minimax-m2.7"
    try:
        if policy_path.exists():
            t = policy_path.read_text(encoding="utf-8")
            m = re.search(r"top_k:\s*(\d+)", t)
            if m:
                top_k = max(1, min(10, int(m.group(1))))
            jm = re.search(r"judge:\s*\n((?:[ \t]+.*\n)*)", t)
            block = jm.group(1) if jm else t
            mm = re.search(r"model:\s*([^\s#\n]+)", block)
            if mm:
                model_id = mm.group(1).strip().strip('"').strip("'").rstrip(",")
    except Exception:
        pass
    return top_k, model_id

def cmd_classify(root: Path, *, campaign: Path, store: Path, dry_run: bool = False, doctype: bool = False) -> int:
    """Closed-vocabulary validator: rejects primary/secondary or doc_type not in vocab, patches frontmatter/ledger.

    Reads store/*.judge.json produced by scripts/classify/judge.py, validates against
    taxonomy.yaml (subdomain) or doc_types.yaml (doctype), and on success patches the store md frontmatter
    and appends a ledger event. Pure stdlib — no LLM call.
    """
    # Resolve campaign before load — strict, no payload fallback (H2) for subdomain; doctype allows template fallback for tests
    campaign_resolved = (root / campaign) if not campaign.is_absolute() else campaign
    if doctype:
        # Doctype: allow template fallback (payload/doc_types.yaml) when campaign missing
        allowed = _load_doc_types_names(campaign_resolved if campaign_resolved.exists() or campaign_resolved.is_absolute() else campaign, root)
        if not allowed:
            print(f"classify: no doc_types found (checked {campaign_resolved / 'doc_types.yaml'} and template) — run questionnaire and freeze doc_types first", file=sys.stderr)
            return 1
        allowed_buckets = {"SURE", "NEEDS_HUMAN_VALIDATION", "I_GUESSED"}
        store_path = (root / store) if not store.is_absolute() else store
        if not store_path.exists():
            print(f"classify: store not found: {store_path}", file=sys.stderr)
            return 1
        judge_files = list(store_path.rglob("*.judge.json"))
        if not judge_files:
            print(f"classify: no judge outputs in {store_path} (run scripts/classify/classify_doctype.py first)", file=sys.stderr)
            return 1
        ledger_path = campaign_resolved / "ledger.jsonl" if campaign_resolved.is_dir() else campaign_resolved.parent / "ledger.jsonl"
        # For template-fallback case where campaign doesn't exist, use store-adjacent ledger
        if not campaign_resolved.exists() and not campaign_resolved.is_absolute():
            # try root/campaign, else fallback to store parent
            if not (root / campaign).exists():
                ledger_path = store_path / "ledger.jsonl"
        existing: set[tuple] = set()
        if ledger_path.exists():
            for line in ledger_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    ev = json.loads(line)
                    existing.add((ev.get("doc_id"), ev.get("doc_type")))
                except Exception:
                    pass
        if dry_run:
            print(f"classify: dry-run — {len(judge_files)} judge files, allowed doc_types={sorted(allowed)}")
            return 0
        failures = 0
        classified = 0
        skipped = 0
        for jf in judge_files:
            try:
                data = json.loads(jf.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                print(f"classify: bad JSON {jf}: {e}", file=sys.stderr)
                failures += 1
                continue
            if "confidence" in data and isinstance(data.get("confidence"), (int, float)):
                print(f"classify: {jf.name}: numeric confidence {data['confidence']} rejected — use confidence_bucket", file=sys.stderr)
                failures += 1
                continue
            doc_type = data.get("doc_type", "")
            bucket = data.get("confidence_bucket", "")
            reasoning = data.get("reasoning_brief", "")
            if bucket not in allowed_buckets:
                print(f"classify: {jf.name}: confidence_bucket '{bucket}' not in {sorted(allowed_buckets)} — rejected", file=sys.stderr)
                failures += 1
                continue
            if not reasoning or not reasoning.strip():
                print(f"classify: {jf.name}: reasoning_brief missing or empty — rejected", file=sys.stderr)
                failures += 1
                continue
            if doc_type not in allowed:
                print(f"classify: {jf.name}: doc_type '{doc_type}' not in doc_types {sorted(allowed)} — rejected", file=sys.stderr)
                failures += 1
                continue
            base = jf.name[:-len(".judge.json")]
            sibling = jf.parent / f"{base}.md"
            doc_id = sibling.stem if sibling.exists() else jf.stem.replace(".judge", "")
            if (doc_id, doc_type) in existing:
                print(f"classify: {jf.name} -> {doc_type} [{bucket}] (already ledgered, skipping)")
                skipped += 1
                continue
            if sibling.exists() and not dry_run:
                txt = sibling.read_text(encoding="utf-8")
                fm, body = parse_frontmatter(txt)
                fm["doc_type"] = doc_type
                fm["doc_decision"] = doc_type
                fm["decided_by"] = "model"
                _trust_map = {"SURE": "high", "NEEDS_HUMAN_VALIDATION": "needs_review", "I_GUESSED": "low"}
                fm["trust"] = _trust_map.get(bucket, "needs_review")
                fm["level"] = "1"
                fm_lines = ["---"]
                for k, v in fm.items():
                    if isinstance(v, list):
                        fm_lines.append(f"{k}:")
                        for item in v:
                            fm_lines.append(f"  - {_yaml_dump_scalar(item)}")
                    else:
                        fm_lines.append(f"{k}: {_yaml_dump_scalar(v)}")
                fm_lines.append("---")
                new_text = "\n".join(fm_lines) + "\n\n" + body
                tmp = sibling.with_suffix(sibling.suffix + ".tmp")
                tmp.write_text(new_text, encoding="utf-8")
                tmp.replace(sibling)
            if not dry_run:
                ledger_path.parent.mkdir(parents=True, exist_ok=True)
                doc_types_version = _classification_file_version(_resolve_campaign_file(campaign_resolved, "doc_types.yaml"))
                policy_version = _classification_file_version(_resolve_campaign_file(campaign_resolved, "policy.yaml"))
                top_k_val, model_val = _classification_policy_values(campaign_resolved)
                event = {
                    "doc_id": doc_id,
                    "task": "doctype",
                    "doc_type": doc_type,
                    "confidence_bucket": bucket,
                    "method": "model",
                    "reasoning_brief": reasoning,
                    "doc_types_version": doc_types_version,
                    "policy_version": policy_version,
                    "model_id": model_val,
                    "top_k": top_k_val,
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                }
                if data.get("singleton_constraint"):
                    event["singleton_constraint"] = True
                with ledger_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(event) + "\n")
                existing.add((doc_id, doc_type))
            print(f"classify: {jf.name} -> {doc_type} [{bucket}]")
            classified += 1
        if failures:
            print(f"classify: {failures} file(s) rejected (closed vocabulary)", file=sys.stderr)
            return 1
        print(f"classify: {classified} classified, {skipped} skipped, ledger {ledger_path}")
        return 0
    # --- subdomain path (original) ---
    campaign_resolved = (root / campaign) if not campaign.is_absolute() else campaign
    campaign_tax = campaign_resolved / "taxonomy.yaml" if campaign_resolved.is_dir() else campaign_resolved
    if campaign_resolved.is_dir() and not campaign_tax.exists():
        print(f"classify: no taxonomy found at {campaign_tax} — run questionnaire and freeze taxonomy first", file=sys.stderr)
        return 1
    if not campaign_resolved.is_dir() and campaign_resolved.suffix not in (".yaml", ".yml") and not campaign_resolved.exists():
        print(f"classify: campaign not found: {campaign_resolved}", file=sys.stderr)
        return 1
    allowed = _load_taxonomy_subdomains(campaign_resolved if campaign_resolved.exists() else campaign, root)
    if not allowed:
        print(f"classify: no taxonomy found at {campaign_tax} (no valid subdomains)", file=sys.stderr)
        return 1
    allowed_buckets = {"SURE", "NEEDS_HUMAN_VALIDATION", "I_GUESSED"}
    allowed_relations = {"none", "comparison", "relationship", "progression"}
    # Resolve store path relative to vault root if needed
    store_path = (root / store) if not store.is_absolute() else store
    if not store_path.exists():
        print(f"classify: store not found: {store_path}", file=sys.stderr)
        return 1
    judge_files = list(store_path.rglob("*.judge.json"))
    if not judge_files:
        print(f"classify: no judge outputs in {store_path} (run scripts/classify/judge.py first)", file=sys.stderr)
        return 1

    # Ledger path per backbone: campaigns/<campaign>/ledger.jsonl (or store-adjacent)
    ledger_path = campaign_resolved / "ledger.jsonl" if campaign_resolved.is_dir() else campaign_resolved.parent / "ledger.jsonl"
    # Load existing ledger for idempotency (I2)
    existing: set[tuple] = set()
    if ledger_path.exists():
        for line in ledger_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
                existing.add((ev.get("doc_id"), ev.get("primary")))
            except Exception:
                pass
    if dry_run:
        print(f"classify: dry-run — {len(judge_files)} judge files, allowed={sorted(allowed)}")
    failures = 0
    classified = 0
    skipped = 0
    for jf in judge_files:
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"classify: bad JSON {jf}: {e}", file=sys.stderr)
            failures += 1
            continue
        # Reject numeric confidence contamination (C4)
        if "confidence" in data and isinstance(data.get("confidence"), (int, float)):
            print(f"classify: {jf.name}: numeric confidence {data['confidence']} rejected — use confidence_bucket", file=sys.stderr)
            failures += 1
            continue
        primary = data.get("primary_subdomain", "")
        secondary = data.get("secondary_subdomains", [])
        bucket = data.get("confidence_bucket", "")
        relation = data.get("relation_type", "none")
        reasoning = data.get("reasoning_brief", "")
        # Strict bucket/relation/reasoning validation (C4)
        if bucket not in allowed_buckets:
            print(f"classify: {jf.name}: confidence_bucket '{bucket}' not in {sorted(allowed_buckets)} — rejected", file=sys.stderr)
            failures += 1
            continue
        if relation not in allowed_relations:
            print(f"classify: {jf.name}: relation_type '{relation}' not in {sorted(allowed_relations)} — rejected", file=sys.stderr)
            failures += 1
            continue
        if not reasoning or not reasoning.strip():
            print(f"classify: {jf.name}: reasoning_brief missing or empty — rejected", file=sys.stderr)
            failures += 1
            continue
        if primary not in allowed:
            print(f"classify: {jf.name}: primary '{primary}' not in taxonomy {sorted(allowed)} — rejected", file=sys.stderr)
            failures += 1
            continue
        for s in secondary:
            if s not in allowed:
                print(f"classify: {jf.name}: secondary '{s}' not in taxonomy", file=sys.stderr)
                failures += 1
                break
        else:
            base = jf.name[:-len(".judge.json")]
            sibling = jf.parent / f"{base}.md"
            # Idempotency: skip if already ledgered with same primary (I2)
            doc_id = sibling.stem if sibling.exists() else jf.stem.replace(".judge", "")
            if (doc_id, primary) in existing:
                print(f"classify: {jf.name} -> {primary} [{bucket}] (already ledgered, skipping)")
                skipped += 1
                continue
            if sibling.exists() and not dry_run:
                txt = sibling.read_text(encoding="utf-8")
                fm, body = parse_frontmatter(txt)
                fm["domains"] = [primary] + secondary
                fm["doc_decision"] = primary
                fm["decided_by"] = "model"
                fm["doc_type"] = "classified"
                _trust_map = {"SURE": "high", "NEEDS_HUMAN_VALIDATION": "needs_review", "I_GUESSED": "low"}
                fm["trust"] = _trust_map.get(bucket, "needs_review")
                fm["level"] = "1"
                fm_lines = ["---"]
                for k, v in fm.items():
                    if isinstance(v, list):
                        fm_lines.append(f"{k}:")
                        for item in v:
                            fm_lines.append(f"  - {_yaml_dump_scalar(item)}")
                    else:
                        fm_lines.append(f"{k}: {_yaml_dump_scalar(v)}")
                fm_lines.append("---")
                new_text = "\n".join(fm_lines) + "\n\n" + body
                tmp = sibling.with_suffix(sibling.suffix + ".tmp")
                tmp.write_text(new_text, encoding="utf-8")
                tmp.replace(sibling)
            if not dry_run:
                ledger_path.parent.mkdir(parents=True, exist_ok=True)
                # Audit trail: versions and model/top-k (SKILL.md promise)
                tax_version = _classification_file_version(_resolve_campaign_file(campaign_resolved, "taxonomy.yaml"))
                gloss_version = _classification_file_version(_resolve_campaign_file(campaign_resolved, "glossary.yaml"))
                policy_version = _classification_file_version(_resolve_campaign_file(campaign_resolved, "policy.yaml"))
                top_k_val, model_val = _classification_policy_values(campaign_resolved)
                event = {
                    "doc_id": doc_id,
                    "stage": "classify",
                    "status": "classified",
                    "primary": primary,
                    "secondary": secondary,
                    "relation_type": relation,
                    "confidence_bucket": bucket,
                    "method": "model",
                    "reasoning_brief": reasoning,
                    "taxonomy_version": tax_version,
                    "glossary_version": gloss_version,
                    "policy_version": policy_version,
                    "model_id": model_val,
                    "top_k": top_k_val,
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                }
                with ledger_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(event) + "\n")
                existing.add((doc_id, primary))
            print(f"classify: {jf.name} -> {primary} [{bucket}]")
            classified += 1
    if failures:
        print(f"classify: {failures} file(s) rejected (closed vocabulary)", file=sys.stderr)
        return 1
    print(f"classify: {classified} classified, {skipped} skipped, ledger {ledger_path}")
    return 0

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="validate closed-vocabulary classification")
    ap.add_argument("vault_root", nargs="?", default=".", help="vault root")
    ap.add_argument("--campaign", required=True, help="campaign dir holding taxonomy.yaml / doc_types.yaml")
    ap.add_argument("--store", required=True, help="dir holding *.judge.json")
    ap.add_argument("--dry-run", action="store_true", help="report without patching")
    ap.add_argument("--doctype", action="store_true", help="validate doc_type (doctype mode)")
    args = ap.parse_args(argv)
    return cmd_classify(Path(args.vault_root).resolve(), campaign=Path(args.campaign),
                        store=Path(args.store), dry_run=args.dry_run, doctype=args.doctype)


if __name__ == "__main__":
    sys.exit(main())
