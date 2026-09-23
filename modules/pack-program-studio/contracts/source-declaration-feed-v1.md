# Pack Program Studio Source Declaration Feed V1

This contract projects an exact Pack Program Studio static program into a
versioned list of source declaration candidates for Atlas. It does not claim
that Groovy executed, that a registry contains the declared identity, or that a
player can obtain it.

Each declaration carries a semantic descriptor, lifecycle placement, exact
source provenance, and a content identity. The descriptor is an input to
Atlas's canonical semantic identity; Pack Program Studio does not decide
cross-layer equality. Atlas must preserve the original declaration identity and
provenance when it normalizes the feed.

Authority is deliberately narrow:

- Pack Program Studio owns the static source observation.
- Crucible owns observed runtime registry and effect state.
- Atlas owns the evidence-bounded projection and derived interpretation.
- Blueprints remains the construction authority.

Schema: `../schemas/workbench-pack-source-declarations-v1.schema.json`.
