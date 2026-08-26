#!/usr/bin/env python3
"""Convert legacy glossary english:'A / B' -> translations:['A','B'].

Usage:
  python scripts/convert_glossary_translations.py experiments/mevaker-100/glossary_3223_translated.json
  python scripts/convert_glossary_translations.py experiments/mevaker-100/glossary_3223_translated.json --out glossary_new.json
  python scripts/convert_glossary_translations.py --check  # dry-run, print stats

Backcompat: re-running on already-converted files is a no-op (english absent -> skip).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


def convert_row(row: dict) -> bool:
    """Pop english -> translations; return True if converted."""
    if "translations" in row:
        return False
    eng = row.pop("english", None)
    if eng is None:
        return False
    if not isinstance(eng, str) or not eng.strip():
        row["translations"] = []
        return True
    parts = [s.strip() for s in eng.split(" / ")]
    # dedupe preserving order
    seen, out = set(), []
    for t in parts:
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    row["translations"] = out
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description="Split english:'A / B' into translations:['A','B']")
    ap.add_argument("glossary", nargs="?", type=Path, help="glossary .json (legacy english field)")
    ap.add_argument("--out", type=Path, default=None, help="output path (default: in-place)")
    ap.add_argument("--check", action="store_true", help="dry-run: print stats without writing")
    args = ap.parse_args(argv)

    if args.glossary is None:
        # check mode without file: just print help
        ap.print_help()
        sys.exit(2)

    p: Path = args.glossary
    if not p.exists():
        print(f"not found: {p}", file=sys.stderr)
        sys.exit(1)

    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        print("glossary must be a JSON array", file=sys.stderr)
        sys.exit(1)

    n_converted = sum(convert_row(r) for r in data)
    lens = Counter(len(r.get("translations", [])) for r in data)

    out = args.out or p
    if not args.check:
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        assert all("english" not in r for r in data), "stale english keys remain"
        print(f"converted {n_converted} rows -> {out}  lens={dict(lens)}")
    else:
        print(f"would convert {n_converted} rows  lens={dict(lens)}  (dry-run)")
        for r in data[:5]:
            print(f"  {r.get('term_he')} -> {r.get('translations')}")


if __name__ == "__main__":
    main()
