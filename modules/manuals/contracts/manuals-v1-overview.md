# Manuals V1 overview

Manuals turns implementation knowledge into practical, evidence-backed
teaching. It explains what to change, where it belongs, why a step exists, and
how to recognize common failure modes. It never authorizes executable work.

Atlas supplies bounded facts, Blueprints supplies validated construction
records, and Crucible supplies runtime observations. Manuals may teach from
those records without changing their scope or authority.

## Contract

The [implementation-guide V1 contract](implementation-guide-v1.md) and
[requirement policy](requirement-policy-v1.json) define:

- content-addressed guide identity;
- ordered instructional steps;
- evidence and authority thresholds;
- eight requirement classes;
- non-normative examples;
- explicit omission claims; and
- bounded unknowns and validation expectations.

The [contract examples](../examples/contract-examples-v1.json) contain valid
and fail-closed cases used by the module tests.

## Authority boundary

Manuals may consume approved standard identities, validation summaries,
shareable proof records, and their referenced Atlas evidence. Manuals may not
approve a standard, select a construction variant, authorize a generated
file, waive a check, or turn an example into a requirement.

Canonical structured records are authoritative when a guide is generated
from them. Markdown is the human-facing projection. Large source trees,
runtime captures, worlds, class dumps, and build products remain outside the
repository under their normal ignored evidence storage.
