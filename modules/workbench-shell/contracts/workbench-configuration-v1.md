# Workbench configuration V1

Status: current executable configuration contract

## Purpose and authority

`workbench.toml` selects the pack document, one variant within that pack, and
the platform document bound by that variant. It also declares the only
host-path import understood by V1.

The manifest is an authority router, not another profile. Minecraft,
Cleanroom, Java and dependency versions, artifact locks, recognition rules,
catalogs, permissions, and pack policy remain in the selected profile
documents.

Normal execution supports exactly `workbench/config/v1`. Missing, older,
newer, and otherwise named schemas fail closed. When another configuration
schema becomes current, the normal loader is changed to accept only that
schema. Historical records may remain readable but are not active
configuration.

## Document

The canonical suite manifest is:

```toml
schema = "workbench/config/v1"

[selection]
pack_document = "profiles/packs/supersymmetry/profile.yaml"
pack_variant = "cleanroom-provisional"
platform_document = "profiles/platforms/cleanroom/provisional.yaml"

[bindings]
java_candidate_home = { env = "WORKBENCH_JAVA_HOME" }
```

The root has exactly `schema`, `selection`, and `bindings`. `selection` has
exactly the three fields shown above. `bindings` may contain only
`java_candidate_home`, which may be absent.
The [parsed-object schema](../schemas/workbench-configuration-v1.schema.json)
describes the transport shape. The executable loader owns the path, profile,
and cross-document semantics.

## Selection rules

- Profile paths use canonical suite-relative POSIX spelling, remain beneath
  `profiles/`, end in `.yaml` or `.yml`, and traverse no symlinks.
- The manifest and selected documents are bounded regular non-symlink files.
- Both profiles declare the current `schema_version: 1` and a canonical
  embedded Workbench profile ID.
- `pack_variant` names a real entry in the pack document's `profiles` table.
- That entry declares `platform_profile_id`, which exactly equals the selected
  platform document's embedded `profile_id`.
- Selection never falls back to a pack default or inferred workspace name.

The loader retains each document's exact immutable bytes and SHA-256. The
`selection_digest` is `sha256:` plus the SHA-256 of newline-terminated
canonical JSON containing the configuration schema, selected paths, variant,
embedded IDs, and exact pack and platform document hashes. TOML comments and
key ordering affect manifest provenance but do not affect selection identity.

## Binding rules

A binding is either an absolute literal path or a one-field import:

```toml
java_candidate_home = "/opt/workbench/jdk"
```

Environment names match `[A-Z][A-Z0-9_]*`. There is no variable interpolation,
environment wildcard, `.env` import, default, required flag, inheritance,
include, command execution, remote document, or automatic child-process
export. A present environment value must itself be an absolute path. A missing
variable resolves to an explicit unbound value; the operation that consumes a
binding decides whether that is an error.

Loading a declaration does not prove that a path exists or is the correct
runtime. The Java binding nominates a candidate only. Its consumer must
validate it against the selected platform profile and operation.

Resolution snapshots the supplied environment once. Configuration inspection
may resolve every supported binding; an operation resolves only the binding
names it consumes, so an unrelated ambient value cannot fail the operation or
enter its identity. Each returned binding has immutable provenance:
`literal`, `environment`, or `undeclared`, plus the manifest and selection
identities that produced the snapshot. A consumer rejects a snapshot from a
different configuration before network access or mutation. Secret values are
outside V1 and must not be placed in this manifest.

The resolver can calculate an `operation_binding_digest` over a canonical
command ID and only the binding names that command consumes. Names are sorted
before canonicalization, so request order has no effect. Every digest entry
contains the binding name, resolved value, and source; an environment source
also names the declared variable. Undeclared and declared-but-missing imports
remain different inputs. The digest contains neither manifest bytes nor
unconsumed bindings.

This digest is available for configuration review and for future
identity-bearing operation formats. Existing runtime V1 plans and receipts do
not claim to bind configuration provenance and remain byte-compatible. No
configuration-bound mutation plan is exposed until the corresponding
materialization and receipt formats are versioned together.

## Current integration boundary

The current configuration-aware surface comprises configuration validation and
resolution, workspace inspection, active-instance initialization and
registration, the generic `runtime-*` CLI operations, and one stdio protocol
session. Those boundaries load the manifest and selected profile bytes once
and pass the same immutable object to their consumers. Generic Doctor remains
profile-free.

Explicit Supersymmetry developer lanes retain their existing pack-specific
contracts and default-suite invocation rules. They are not generalized into
alternate-pack consumers by configuration V1. Their remaining pack-specific
markers and routing fields must move into a future profile schema before those
lanes can claim generic configuration support. Workspace Home's optional
Supersymmetry exact-context enrichment is part of that same transitional pack
lane; its generic Doctor observation remains profile-free.
