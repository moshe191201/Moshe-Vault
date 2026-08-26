#!/usr/bin/env python3
"""Thin wrapper for subdomain classification — delegates to judge.py."""
import sys

if __name__ == "__main__":
    from judge import main
    sys.exit(main())
