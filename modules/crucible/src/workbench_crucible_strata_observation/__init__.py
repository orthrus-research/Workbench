"""Crucible adapter primitives for exact Strata physical observations."""

from .observation import (
    ADAPTER_ID,
    RECEIPT_FORMAT,
    RECEIPT_PREFIX,
    STRATA_INTERFACE_FILES,
    STRATA_INTERFACE_ID,
    StrataObservationValidationError,
    build_strata_observation_receipt,
    canonical_json_bytes,
    parse_strata_observation_receipt,
    write_strata_observation_receipt,
)

__all__ = [
    "ADAPTER_ID",
    "RECEIPT_FORMAT",
    "RECEIPT_PREFIX",
    "STRATA_INTERFACE_FILES",
    "STRATA_INTERFACE_ID",
    "StrataObservationValidationError",
    "build_strata_observation_receipt",
    "canonical_json_bytes",
    "parse_strata_observation_receipt",
    "write_strata_observation_receipt",
]
