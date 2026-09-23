"""Crucible V2 deterministic contract and validation primitives.

Use :func:`seal_record`, :func:`validate_record`, and
:func:`load_canonical_record` for identity-bearing records.  ``content_id``
and ``record_content_id`` remain deliberately exported as low-level identity
primitives for conformance and domain-defined identities; they do not perform
closed-schema, semantic, authority, or relation validation.
"""

from .records import (
    AdapterValidatedReference,
    AuthorityScope,
    BlobResolver,
    ExternalReferenceKey,
    ExternalReferenceResolver,
    PolicyValidationRequest,
    PolicyValidationResult,
    PolicyValidator,
    PublicationRecord,
    RecordResolution,
    RecordResolver,
    ReferenceExpectation,
    RelationResolvers,
    SEMANTIC_ROOT_DOMAIN,
    TrustedAuthorityAdapter,
    ValidatedExternalReference,
    ValidatedRecord,
    load_canonical_record,
    seal_record,
    semantic_root_v2,
    validate_publication,
    validate_record,
)
from .schema_registry import (
    RecordContract,
    RecordValidationError,
    SCHEMA_VERSION,
    SchemaRegistryError,
    ValidationDiagnostic,
    ValidationPhase,
    assert_schema_registry_ready,
    registered_contracts,
    registered_schema_bytes,
)

__all__ = [
    "AdapterValidatedReference",
    "AuthorityScope",
    "BlobResolver",
    "PublicationRecord",
    "ExternalReferenceResolver",
    "ExternalReferenceKey",
    "PolicyValidationRequest",
    "PolicyValidationResult",
    "PolicyValidator",
    "RecordContract",
    "RecordResolution",
    "RecordResolver",
    "RecordValidationError",
    "ReferenceExpectation",
    "RelationResolvers",
    "SCHEMA_VERSION",
    "SEMANTIC_ROOT_DOMAIN",
    "SchemaRegistryError",
    "TrustedAuthorityAdapter",
    "ValidatedExternalReference",
    "ValidatedRecord",
    "ValidationDiagnostic",
    "ValidationPhase",
    "assert_schema_registry_ready",
    "load_canonical_record",
    "registered_contracts",
    "registered_schema_bytes",
    "seal_record",
    "semantic_root_v2",
    "validate_publication",
    "validate_record",
]
