# Atlas runtime material classification contract v1

Status: active bounded runtime-graph projection

This contract defines `workbench-atlas-runtime-material-classification-v1`.
It makes a material an independently classifiable Atlas subject and keeps
item, block, prefix, and fluid realization outside the material core.

## Question

For one exact runtime-graph profile and physical side:

1. which material identities exist in the frozen runtime registry;
2. which are persistent materials and which are non-persistent markers;
3. what composition, verified properties, flags, presentation values, and
   GregTech-derived values belong to each material;
4. which prefix/material pairs are eligible for generation;
5. which item, fluid, or other forms were actually observed; and
6. which identity or closure contradictions remain unresolved.

## Authority split

The projection engine under `modules/atlas` owns generic graph reading,
bounded traversal, canonicalization, and conflict retention. It does not own
GregTech property names or dependency rules.

Each platform or pack profile supplies an immutable
`MaterialClassificationPolicy`. The policy binds:

- the exact platform profile and admitted material adapters;
- base and capability property keys;
- all-of and any-of property dependencies;
- deterministic fallback dependencies used when verification finds no
  admitted any-of alternative;
- incompatible property pairs;
- lossless flag-to-semantic-category mappings;
- required flag and property closure for each known flag;
- flags implied by verified properties; and
- the optional generation-constraint kind used for the eligibility lens.

The complete canonical policy and its SHA-256 are embedded in the result.
Historical GTCEu evidence therefore remains valid only for the exact policy
that interpreted it.

## Material core

Every material row contains a `core` and a `form_lens`. The `core` contains:

- registry role: `persistent` or `marker`;
- registry name, namespace, local name, numeric ID, storage-registry identity,
  and every observed typed identity key;
- composition basis, chemical formula, ordered quantified components, and
  direct element evidence;
- exact verified property nodes and their encoded values, partitioned into
  base, capability, and profile-extension keys;
- exact flags plus policy-defined semantic categories;
- color and icon-set presentation; and
- explicitly named GregTech-derived values.

The core receives its own SHA-256. No item or fluid association contributes to
that digest. A material with no realized form remains a complete material row.

Numeric IDs are authoritative only for persistent materials and only together
with storage-registry identity. Registry resource location remains separately
available. Marker materials retain their observed numeric value but it is not
promoted to persistent identity.

Composition classification is an evidence summary, not a chemistry guess:

- `elemental`: direct element evidence only;
- `composite`: ordered component evidence only;
- `elemental_composite`: both evidence types are present; and
- `unspecified`: neither is present.

`Material.isSolid()` is retained only as `is_solid_api_value`; it is not a
physical-phase classification.

## Property and flag semantics

Verified properties are a set-valued classification. The projection never
collapses them into one material type. Profile policies may report missing
dependency closure and incompatible property pairs as frontier issues.

Known flags are checked against their profile-bound required flags and
properties. This mirrors the frozen closure without claiming that all
platforms verify flags in the same way.

Unknown property keys and flags are retained as profile extensions. They are
not discarded, voted away, or treated as universal GregTech behavior. Exact
property implementation class and encoded value remain available for later
comparison.

## Form lenses

The realized-form lens reports every outgoing `has_form` relationship and
partitions its target as `fluid`, `item_variant`, or `other`. These rows do not
alter core classification.

Where a profile admits a generation-constraint kind, the prefix-generation
lens separately reports the captured `do_generate_item` decision and whether
an item form using the same prefix was observed. Its states are:

- `eligible_with_realized_form`;
- `eligible_without_realized_form`;
- `ineligible_with_unified_form`; and
- `ineligible_without_realized_form`.

Eligibility does not prove realization. Realization does not prove that
GregTech generated the form; an external item may participate in unification.
A fluid property indicates material capability, while a material-to-fluid form
relationship supplies realization evidence.

## Frontier and conflicts

Structural count mismatches, ambiguous material identity, duplicate registry
identity, missing property closure, incompatible properties, malformed
composition, and fluid capability/realization disagreement are retained as
material-local issue codes. A row is `exact` only when it has no issue codes;
otherwise it is `frontier`.

Bounds cover material count, identity keys, properties, components, elements,
forms, and generation decisions. Exceeding a bound fails closed. SQL batching
is operational and does not affect canonical bytes.

## Groovy correctness boundary

This runtime classification is the future comparison target for Groovy
material registration checks, but it does not parse or authorize Groovy code.
A correctness checker must retain three distinct artifacts:

1. **declared intent** — the exact builder calls and source spans;
2. **expected verified closure** — profile rules applied to that declaration;
3. **observed frozen classification** — this contract's runtime result.

The checker may compare stable identity, expected property closure, flags,
composition, and requested form eligibility. It must not require every eligible
form to exist, infer a declaration from a fluid relationship, or erase
runtime-added dependencies. Source/runtime differences remain evidence to
explain, not values to repair silently.

## Non-claims

Passing this projection does not establish source declaration parity,
scientific chemical accuracy, current Cleanroom behavior, complete external
unification, recipe correctness, localization, or stable release support. A
new platform/profile requires its own admitted policy and runtime evidence.
