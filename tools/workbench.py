#!/usr/bin/env python3
"""Contributor entry point for the native Core dispatcher."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
for source in (ROOT / "api/src", ROOT / "core/src"):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from workbench_core.development import enable_source_checkout
from workbench_core.host_services import install_local_host_services

enable_source_checkout(ROOT)
install_local_host_services()

from workbench_core.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
