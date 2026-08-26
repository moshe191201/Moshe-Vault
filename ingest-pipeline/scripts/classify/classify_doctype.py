#!/usr/bin/env python3
"""Thin wrapper for doc-type classification — delegates to judge.py with task=doctype."""
import sys
from pathlib import Path

if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--doctype" not in argv:
        argv = ["--doctype"] + argv
    sys.argv = [sys.argv[0]] + argv
    from judge import main
    sys.exit(main())
