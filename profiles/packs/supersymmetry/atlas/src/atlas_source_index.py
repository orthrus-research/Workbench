"""Compatibility entry point for the native Supersymmetry source_span_index module."""

from workbench_profile_supersymmetry import source_span_index as _implementation
from workbench_profile_supersymmetry.source_span_index import *


def __getattr__(name):
    return getattr(_implementation, name)


if __name__ == "__main__":
    raise SystemExit(_implementation.main())
