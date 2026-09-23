# Crucible canonical JSON V2 conformance

Status: executable canonical-JSON conformance vectors. These vectors do not by
themselves admit any Crucible V2 runtime capability.

This directory fixes the language-neutral vectors for the
`workbench-canonical-json-v2` byte domain reserved by the
[Crucible graph model](../../../../docs/architecture/CRUCIBLE-GRAPH-MODEL.md#shared-v2-canonical-byte-domain).
The vectors exercise canonical bytes and semantic record content IDs without
changing or redirecting any V1 identity path.

## Run

From the repository root, with Node.js 18 or newer:

```sh
node modules/crucible/conformance/canonical-v2/verify.mjs
```

The verifier has no package dependencies and never rewrites the fixtures. A
successful run reports the exact valid and invalid vector counts.

## Files

- `valid-vectors.json` contains full identity-bearing input records, expected
  canonical body UTF-8 as lowercase hexadecimal, the SHA-256 of the bare body,
  and the expected domain-separated content ID.
- `invalid-vectors.json` contains raw JSON text or exact input-byte hex plus the
  stable rejection code expected from the verifier. Raw inputs are necessary
  for duplicate keys, malformed UTF-8, unescaped controls, and other cases that
  a host JSON parser could erase or replace.
- `verify.mjs` supplies the strict JSON-domain parser, canonical encoder,
  canonical-byte validator, content-ID calculation, and fixture runner.

Both vector manifests are ordinary UTF-8 JSON. They are directly consumable by
Python's `json` module and other host parsers because identity-bearing numbers
and malicious byte sequences remain inside `input_json` strings or
`input_utf8_hex` strings. A consumer must pass those inner bytes to its own
strict implementation; parsing an `input_json` value with a default host JSON
decoder would lose duplicate-key and signed-64-bit guarantees.

## Vector semantics

For each valid vector, `input_json` is a transport representation of a complete
record, including its `id`. It is intentionally allowed to contain whitespace,
noncanonical member order, JSON Unicode escapes, short control escapes, or
negative zero. The verifier:

1. performs fatal UTF-8 decoding and strict JSON-domain parsing;
2. rejects duplicate keys, floats, integers outside the signed 64-bit domain,
   malformed Unicode, and values deeper than 256 nested containers;
3. omits exactly the top-level `id` field;
4. encodes the complete remaining body canonically;
5. checks `expected_canonical_body_utf8_hex`; and
6. checks the record and expected IDs against:

```text
SHA-256(
  UTF8("workbench-content-v2\n") ||
  UTF8(kind) || 0x0a ||
  canonical_json(body_without_id)
)
```

The manifest fixes the content-domain prefix in both text and hex. Every valid
case also fixes `expected_canonical_body_sha256`; the verifier confirms that
this undomained digest is not substituted for the content-ID digest.

The Unicode-order vector contains U+E000 and U+10000 keys. Their order catches
implementations that sort JavaScript, Java, or similar UTF-16 strings by code
unit instead of comparing unsigned UTF-8 bytes. The same vector keeps
precomposed U+00E9 distinct from `e` plus U+0301; normalization is forbidden.

Invalid vectors default to the `parse-and-identify-record` operation. Vectors
using `validate-canonical-bytes` are syntactically valid inputs that a transport
normalizer may read, but an immutable-object reader must reject because their
bytes are not already the canonical encoding. This distinction covers wrong
member order, short control escapes, escaped non-ASCII scalars, insignificant
whitespace, and negative zero.

## JavaScript API rules

The parser returns every JSON integer as `bigint`. The exported `canonicalize`
function deliberately rejects every JavaScript `number`, including apparently
safe integers, so a caller cannot accidentally round a signed 64-bit value
before identity calculation. Callers that start with bytes should use:

```js
import {
  canonicalize,
  computeContentIdentity,
  parseJsonDomainBytes,
  validateCanonicalJsonBytes,
} from "./modules/crucible/conformance/canonical-v2/verify.mjs";
```

`parseJsonDomainBytes` accepts noncanonical transport spellings and returns the
validated value domain. `validateCanonicalJsonBytes` additionally requires the
input bytes to equal their canonical re-encoding exactly.

## Boundary

This package validates the shared canonical value domain and common semantic
identity header. It cannot decide whether an otherwise well-formed field is
unknown: each future closed record schema must perform that check before
identity is accepted. It also does not implement object storage, shards,
evidence admission, recipes, graph revisions, references, or any V1 adapter.
