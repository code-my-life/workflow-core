"""Adapters to the outside world: the only code that knows each external system.

The agent runtime's adapter is the one subpackage here. The rest of the package never names it (ADR 0001): it finds
it with `runtime_adapter()` and only sees the ports of `workflow_core.model`.
"""

from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType


def runtime_adapter() -> ModuleType:
    """The agent runtime's adapter: the single subpackage of this package."""
    found = [module.name for module in pkgutil.iter_modules(__path__) if module.ispkg]
    if len(found) != 1:
        raise RuntimeError(f"expected exactly one runtime adapter under {__name__}, found {found}")
    return importlib.import_module(f"{__name__}.{found[0]}")
