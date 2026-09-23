# Atlas composed material semantics policy v2

Status: active profile-policy model

`workbench-atlas-material-semantics-policy-v2` is the common profile authority
from which source normalization and runtime material classification obtain
flag semantics. It composes version-bound semantic layers instead of treating
all flags as universal GregTech behavior.

Each layer binds its owner, source-lock identity, revision, tree, available
file digests, atomic flag definitions, and finite flag presets. Atomic flags
retain their runtime name, declaring source type and symbol, semantic
categories, required flags, and required properties. Presets expand to an
ordered, duplicate-free list of atomic runtime flag names.

The policy also binds typed constants and declarative conditional flag rules.
These rules are interpreted by a bounded source evaluator; their presence is
not permission to execute Java or Groovy.

The runtime view compiles to the existing immutable
`MaterialClassificationPolicy`. A new runtime policy ID is mandatory whenever
composed layers change. The prior GTCEu-only V1 policy remains available for
historical results and is not reinterpreted in place.
