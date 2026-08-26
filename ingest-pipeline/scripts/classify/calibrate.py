"""Calibration + ledger helpers.

Calibrate: per-bucket accuracy, confusion matrix, glossary-miss report.
Ledger: append-only JSONL helpers.
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
import sys


def calibrate(gold_path: Path, predictions_dir: Path, doctype: bool = False):
    """Compare gold labels vs judge outputs."""
    if not gold_path.exists():
        print(f"calibrate: gold not found: {gold_path}", file=sys.stderr)
        return 1
    gold = json.loads(gold_path.read_text(encoding="utf-8"))
    # gold: list of {doc_id, true_primary / true_doc_type, ...}
    by_bucket = collections.defaultdict(list)
    confusion = collections.Counter()
    constraint_misses = 0
    pruned_total = 0
    total = 0
    correct = 0
    for row in gold:
        doc_id = row.get("doc_id", "")
        # Support both true_primary (subdomain) and true_doc_type (doctype)
        if doctype:
            true_label = row.get("true_doc_type", row.get("true_primary", ""))
            pred_key = "doc_type"
        else:
            true_label = row.get("true_primary", "")
            pred_key = "primary_subdomain"
        # find prediction sidecar
        pred = None
        for p in predictions_dir.rglob("*.judge.json"):
            if doc_id in p.name or doc_id in p.read_text(encoding="utf-8"):
                try:
                    pred = json.loads(p.read_text(encoding="utf-8"))
                    break
                except Exception:
                    pass
        # If no sidecar, try direct match by doc_id key in gold row's pred field
        if pred is None and ("pred_primary" in row or "pred_doc_type" in row):
            if doctype:
                pred = {"doc_type": row.get("pred_doc_type", ""), "confidence_bucket": row.get("confidence_bucket", "NEEDS_HUMAN_VALIDATION"), "singleton_constraint": row.get("singleton_constraint", False)}
            else:
                pred = {"primary_subdomain": row["pred_primary"], "confidence_bucket": row.get("confidence_bucket", "NEEDS_HUMAN_VALIDATION")}
        if pred is None:
            continue
        total += 1
        bucket = pred.get("confidence_bucket", "NEEDS_HUMAN_VALIDATION")
        is_correct = pred.get(pred_key) == true_label
        if is_correct:
            correct += 1
        by_bucket[bucket].append(is_correct)
        confusion[(true_label, pred.get(pred_key, ""))] += 1
        # constraint miss: singleton pruned but true label was pruned out (would appear as mismatch where pred is singleton)
        if pred.get("singleton_constraint") and not is_correct:
            constraint_misses += 1
        if pred.get("singleton_constraint"):
            pruned_total += 1

    print(f"calibrate: {correct}/{total} overall accuracy {correct/total:.2%}" if total else "calibrate: no matched docs")
    for bucket in ["SURE", "NEEDS_HUMAN_VALIDATION", "I_GUESSED"]:
        vals = by_bucket.get(bucket, [])
        if vals:
            acc = sum(vals)/len(vals)
            print(f"  {bucket}: {sum(vals)}/{len(vals)} = {acc:.2%} (n={len(vals)})")
        else:
            print(f"  {bucket}: n=0")
    if confusion:
        print("  Confusion (true -> pred):")
        for (t,p), c in confusion.most_common(10):
            if t != p:
                print(f"    {t} -> {p}: {c}")
    # Glossary-miss: I_GUESSED where true term likely in glossary but not mapped
    guessed = by_bucket.get("I_GUESSED", [])
    if guessed:
        miss_rate = 1 - (sum(guessed)/len(guessed)) if guessed else 0
        if miss_rate > 0.5:
            print(f"  Glossary-miss signal: I_GUESSED accuracy {sum(guessed)/len(guessed):.2%} — consider adding surface forms for thin docs")
    if pruned_total:
        miss_rate = constraint_misses / pruned_total if pruned_total else 0
        print(f"  Constraint miss: {constraint_misses}/{pruned_total} singleton pruned overridden = {miss_rate:.2%}" + (" — review language/extension gates" if miss_rate > 0.1 else ""))
    return 0


# Ledger helpers (minimal)
def ledger_append(ledger_path: Path, event: dict):
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")


def ledger_project_raw(ledger_path: Path, doc_id: str | None = None) -> dict:
    """Return projection dict keyed by doc_id+task (so subdomain+doctype coexist)."""
    state: dict = {}
    if not ledger_path.exists():
        return state
    for line in ledger_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if doc_id and ev.get("doc_id") != doc_id:
            continue
        key = ev.get("doc_id", "_global")
        task = ev.get("task", "")
        # If task present, keep per-task; else overwrite
        if task:
            key = f"{key}:{task}"
        state[key] = ev
    return state


def ledger_project(ledger_path: Path, doc_id: str | None = None):
    if not ledger_path.exists():
        print(f"ledger: not found: {ledger_path}", file=sys.stderr)
        return 1
    state = ledger_project_raw(ledger_path, doc_id)
    # Also produce simple dict keyed by doc_id for backward compat when task absent
    print(json.dumps(state, indent=2))
    return 0


def main():
    p = argparse.ArgumentParser(description="Calibrate + ledger")
    sub = p.add_subparsers(dest="cmd")
    c1 = sub.add_parser("calibrate")
    c1.add_argument("--gold", required=True, help="gold JSON path")
    c1.add_argument("--store", default="store")
    c1.add_argument("--doctype", action="store_true", help="calibrate doc-type")
    c2 = sub.add_parser("ledger-append")
    c2.add_argument("--ledger", required=True)
    c2.add_argument("--event", required=True, help="JSON event string")
    c3 = sub.add_parser("ledger-project")
    c3.add_argument("--ledger", required=True)
    c3.add_argument("--doc-id", default=None)
    args = p.parse_args()
    if args.cmd == "calibrate":
        return calibrate(Path(args.gold), Path(args.store), doctype=getattr(args, "doctype", False))
    elif args.cmd == "ledger-append":
        ledger_append(Path(args.ledger), json.loads(args.event))
        print(f"ledger: appended to {args.ledger}")
        return 0
    elif args.cmd == "ledger-project":
        return ledger_project(Path(args.ledger), args.doc_id)
    else:
        p.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
