"""Compatibility entry point for the native Supersymmetry pack_mutations module."""

from workbench_profile_supersymmetry import pack_mutations as _implementation
from workbench_profile_supersymmetry.pack_mutations import *


def __getattr__(name):
    return getattr(_implementation, name)


if __name__ == "__main__":
    raise SystemExit(_implementation.main())
