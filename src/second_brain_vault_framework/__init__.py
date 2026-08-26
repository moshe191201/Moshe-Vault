"""second-brain-vault-framework — the tool + payload that builds and updates vaults.

A *vault* is a user-owned folder of content. This package is the framework that
lays framework-owned files into it and upgrades them in place.
"""

__version__ = "0.2.1"

from . import core  # noqa: E402,F401  (re-exported for `from ... import core`)

__all__ = ["__version__", "core"]
