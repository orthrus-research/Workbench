"""Supersymmetry admission of original static provenance primitives."""

from .source_span_index import SourceResolver, validate_index
from .pack_mutations import validate_extraction

PROFILE_API_VERSION = 1
PROVENANCE_PRIMITIVES_API_VERSION = 1


def source_resolver(*, source_root=None, pack_root=None):
    """Select exact locked source bytes for historical or user-state inputs."""
    return SourceResolver(source_root=source_root, pack_root=pack_root)


def validate_static_primitives(source_index, extraction, *, resolver=None):
    """Reopen pinned source and validate both original record families."""
    if resolver is None:
        resolver = SourceResolver()
    validate_index(source_index, resolver)
    validate_extraction(extraction, source_index, resolver)
