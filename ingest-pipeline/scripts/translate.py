#!/usr/bin/env python3
"""Shim — canonical is scripts/translate/translate.py."""
import pathlib as _p
import importlib.util as _ilu
import sys as _sys

_canon = _p.Path(__file__).with_name("translate") / "translate.py"
_spec = _ilu.spec_from_file_location("translate.translate", _canon)
_mod = _ilu.module_from_spec(_spec)
_sys.modules["translate.translate"] = _mod
_spec.loader.exec_module(_mod)
if __name__ == "__main__":
    _sys.exit(_mod.main() if hasattr(_mod, "main") else 0)
