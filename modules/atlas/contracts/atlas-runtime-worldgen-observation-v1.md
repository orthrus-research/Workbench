# Atlas retained runtime worldgen observation V1

Status: implemented read-only normalization and derivation slice.

Atlas owns normalization and conservative derivation from one explicit
runtime-root selection. V1 consumes a pack-owned audit profile plus retained
BiomeTweaker scripts/reports, logs, selected Forge configuration, exact mod
artifacts, and selected source/runtime alignment inputs. It emits exact input
identities, normalized positive-weight climate membership, explicit profile
expectation states, artifact binding, log assertions, derived findings, and
limitations.

The observation is non-normative and is not an Atlas publication. Input files
from a mutable root are not assumed contemporaneous, and the root is not
assumed to belong to the project that selected the profile. Profile guidance
is suppressed unless every finding-specific required artifact matches its
pinned SHA-256. Zero-weight entries are structural but not selectable;
negative weights, duplicate biome identities, and duplicate selector
definitions fail closed.

Atlas does not start Minecraft, write a world, infer a terminal process result
from log severity, or attribute a visual chunk symptom. Workbench Shell owns
profile selection, project-context composition, transport identity, CLI
rendering, and error presentation. The Shell transport and result schema are
defined by the
[runtime world-generation audit V1 contract](../../workbench-shell/contracts/runtime-worldgen-audit-v1.md).
