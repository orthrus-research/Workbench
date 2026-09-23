# Crucible GTCEu subsurface trace V1

Status: experimental controlled-observation receipt

Machine-readable form:
[GTCEu subsurface trace schema V1](../schemas/gtceu-subsurface-trace-v1.schema.json).

## Purpose

This receipt carries bounded GTCEu 2.8.10 ore-selection, deposit, placement,
and exact-position decision observations. It closes the gap between a static
ore definition and Strata's exact final state without making Workbench Shell or
Subsurface Studio a runtime authority.

The trace does not contain final chunk state. Subsurface Studio may correlate
one trace decision with an independently validated Strata coordinate, while
Atlas remains responsible for causal interpretation across admitted runtime
actors and writes.

## Scope and completion

Every trace binds one exact GTCEu inventory ID, impact inventory ID, runtime
artifact-set digest, run ID, seed, dimension, and bounded chunk window. Capture
state is `complete`, `partial`, or `incomplete`. Coverage independently states
whether selected-definition observations and per-position decisions are
complete for the declared selector.

Absence is meaningful only when capture state is `complete`, the applicable
coverage flag is true, and `truncated` is false. Otherwise a missing decision
is unavailable, never “not attempted.”

## Deposit and decision identity

One deposit record retains:

- exact definition path, GTCEu grid, selection ordinal and effective weight;
- priority, counted-vein state, center and inclusive bounds;
- RNG algorithm plus a digest of retained seed material;
- cache hit/epoch when observed; and
- candidate, density-rejected, host-rejected, other-rejected, and successful
  write counts.

Per-position decisions link to one deposit and classify the observed outcome
as `density-rejected`, `host-rejected`, `write-failed`, or `written`. Before
and after block states and an Atlas-compatible write-chain ID remain optional
because not every cooperative probe observes the lower write channel.

The parser rejects duplicate deposit, decision, or exact deposit-position
identities; decisions outside their deposit bounds or capture scope; summary
count contradictions; unknown deposit references; and content-address drift.

## Assembly input and custody

`tools/assemble_gtceu_subsurface_trace.py` accepts one closed JSON object with
format `workbench-crucible-gtceu-subsurface-trace-input-v1` and exactly these
additional fields: `adapter_profile`, `capture`, `coverage`, `deposits`, and
`decisions`. It reads one bounded regular non-symlink file with duplicate-key
and replacement detection, builds and validates the content address, and
atomically creates a fresh output. It never replaces an existing trace.

The input consists of already observed normalized records. The assembler does
not launch or instrument GTCEu, infer missing events, close partial coverage,
read final chunks, or admit evidence into Atlas. A runtime adapter must name
its selector and truthfully set capture and coverage state before assembly.

## Bounds and authority

A V1 receipt contains at most 10,000 deposits and 250,000 decisions. It is
profile-specific to Minecraft 1.12.2 and GTCEu 2.8.10-beta. It does not prove
determinism, final-state survival, visual quality, or behavior outside its
selector. Bedrock fluids use a separate virtual-cell observation surface.
