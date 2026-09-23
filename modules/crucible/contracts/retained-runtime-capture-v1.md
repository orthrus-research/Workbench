# Retained runtime capture reader V1

`workbench_crucible_runtime_snapshot.capture.read_runtime_capture` admits
retained `workbench-runtime-graph-raw-bundle-v1` captures for read-only consumers.
It launches no game, changes no capture or input bytes, and makes no claim that
historical evidence represents the current workspace or game state.

The caller supplies a capture directory, explicit adapter IDs in `categories`,
the original `input_manifest` file, and an optional positive `max_source_bytes`.
The bound covers the manifest, input manifest and every declared payload,
including unselected categories. A valid empty completion marker, canonical
UTF-8 manifest, manifest seal, safe ordered payload table, regular files, exact
payload byte counts and SHA-256 hashes are required. Root and member symlinks
are refused. The input's raw SHA-256, capture ID, launch ID and physical side
must agree with the capture binding.

Requested category results must be canonical, sealed V1 documents whose
bindings match the manifest. Admission requires complete status, exact boolean
stability, zero integer unsupported counts, empty diagnostics, canonically
ordered object records, matching record counts and hashes, and two matching
ordered sample observations. Booleans do not qualify as integers. Unselected
categories receive byte-integrity checks only; a partial category is never
promoted to complete.

Crucible owns these generic framing and custody checks. Canonical hashing
preserves the producer's original JSON numeric tokens, orders keys by UTF-8
bytes and escapes control characters with lowercase Unicode escapes. Parsed
domain values use Python numeric values; numeric overflow or underflow to zero
is refused. The returned per-record hashes retain original canonical identity.
`record_digest_fields` optionally maps requested adapter IDs to tuples of
top-level record field names. The reader hashes each field's complete value,
including nested content, without interpreting it. Missing fields fail closed.

## Auxiliary payloads

The same owner module exposes a generic reader for declared auxiliary JSON
object payloads after capture admission:

```python
from workbench_crucible_runtime_snapshot.capture import read_capture_payload

payload = read_capture_payload(
    capture,
    "preparation.json",
    record_arrays={"before": "/preparation/records_before"},
    record_digest_fields={"before": ("recipe",)},
)
```

`capture` is the `RetainedRuntimeCapture` returned by `read_runtime_capture`.
`filename` must be a safe single filename declared in that capture's sealed
payload table. The reader reopens the canonical manifest, verifies its document
seal and exact retained file hash, and uses its current on-disk descriptor.
It does not trust the mutable `capture.manifest` dictionary. The completion
marker must still be an empty regular file. Root/member symlinks, changed
authority, replaced files, size/hash mismatches and files changing during
verification are refused. The manifest remains limited to 16 MiB and the
payload read is limited to its declared byte count. This operation verifies the
requested payload and current authority; it does not reread every other capture
payload or the original input manifest.

`record_arrays` optionally maps caller-chosen labels to RFC 6901 JSON pointers
resolving to arrays of objects. Escapes `~0` and `~1`, object keys including the
empty string, and existing zero-based array indices are supported. URI fragment
pointers, malformed escapes, missing paths, invalid indices and nonobject
records are refused. Arrays retain their original order; auxiliary arrays have
no generic sorting requirement. `record_digest_fields` maps those same labels
to tuples of unique top-level field names present in every selected record.
Each field's whole value is hashed. Empty arrays produce empty digest tuples.

The returned `RetainedCapturePayload` contains:

| Field | Meaning |
|---|---|
| `value` | The complete canonical object decoded to ordinary Python values. |
| `file_sha256` | SHA-256 of the exact retained payload bytes. |
| `record_sha256` | Label to tuple of original canonical record hashes, preserving array order. |
| `record_field_sha256` | Label to tuple of field-name/hash dictionaries in the same order. |

Record and field hashes are computed before converting numeric tokens to Python
numbers, preserving spellings such as `1.0E-7` and `-0.0`. The payload must be a
canonical UTF-8 object; duplicate keys, nonfinite/unrepresentable numbers and
other noncanonical content are refused. No domain-specific format, relationship,
preparation status or transition semantics are interpreted. Reading auxiliary
bytes does not promote a partial category, qualify matching, or establish
execution success; consumers must validate their own versioned contracts.

## Interpretation boundary

Profiles own category/checkpoint support, domain schema checks, semantic-field
digest verification and any graph projection. Their adapter API versions and
supported projection formats are separate from this retained V1 format. A
valid reader result establishes content integrity and bounded capture scope;
it establishes neither trusted execution, current freshness, profile support,
domain correctness nor gameplay viability. The public reader requires
Workbench Crucible 0.1.1 or later.
