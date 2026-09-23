# Workbench workspace doctor report V1

Status: experimental product-generic read-only discovery contract

Contract ID:
`WORKBENCH-PROJECT-INTELLIGENCE-WORKSPACE-DOCTOR-REPORT-V1`

Machine-readable artifact:
[Workspace doctor report schema V1](../schemas/workspace-doctor-report-v1.schema.json).

## Purpose and authority

This report gives a bounded, read-only account of an inspected development
target and a minimal ordered repair proposal. Its format is
`workbench-project-intelligence-workspace-doctor-report-v1`, its
`schema_version` is `1`, and `read_only` is always `true`. `capability` names
the bounded operation whose readiness was requested; V1 currently supports
general `workspace-context` discovery and the composed `worldgen-dev` adapter.

Project Intelligence reports what it found; it does not create another source
of platform, profile, or runtime truth. Atlas remains the authority for
interpreted knowledge, and Blueprints remains the construction authority.
Cleanroom is the active construction target. Any legacy Forge observation is
valid only for the exact legacy target named by the report.

## Target context

`target` always contains these sections: `workspace`, `repository`, `profile`,
`platform`, `build`, `java_roles`, `surfaces`, `runtime`, and `integrations`.
Every section except `surfaces` has one state and may carry bounded,
producer-specific facts. `surfaces` is an object of named arrays. `java_roles`
is an array whose entries each carry their own state; this keeps
compile host, annotation processor, runtime JVM, tool JVM, and emitted classfile
identities separate. Surfaces may describe client, dedicated-server, and common
source or resource surfaces independently. Facts must not be silently
generalized to other profiles or platforms.

Every state-bearing target section and Java-role entry uses exactly one state:

- `observed`: read or measured from the inspected target;
- `declared`: stated by configuration or metadata but not independently
  observed;
- `known-absent`: a declared bounded search established absence;
- `unresolved`: available evidence cannot determine the value;
- `ambiguous`: multiple supported candidates remain;
- `unavailable`: the evidence source or required tool could not be accessed;
  and
- `bounded`: a derived answer is valid only within stated limits.

`unavailable` is not absence. `known-absent` requires a fact or limitation
describing the completed search boundary. `bounded` requires the applicable
bound in its facts or `limitations`.

## Findings, repairs, and status

Finding severity is `blocker`, `warning`, or `info`. A blocker prevents the
target from being identified or used for its requested development operation;
a warning leaves the target usable with a concrete risk; info records useful
context that needs no intervention. Every finding has `id`, `severity`, `title`,
`detail`, at least one concise evidence string, and a repair object with
`action`, nullable exact `command`, and `mutates: false`.

The repair object is an inert proposal. `mutates: false` records that the doctor
did not apply that repair while producing the report; it does not authorize or
execute the displayed command. `repair_plan` is an ordered, duplicate-free list
of finding IDs. Every listed ID must name a finding in the same report. Order is
execution order, including prerequisites. Each `next_commands` entry has an ID,
purpose, exact command, availability flag, and finding IDs in `blocked_by`.
These entries are also inert previews and are never run by report generation.

Summary counts `blockers`, `warnings`, and `information` must equal the findings
of each severity in the report. Status is derived as follows:

- `blocked` when at least one blocker exists;
- `attention` when no blocker and at least one warning exists; and
- `ready` when neither blockers nor warnings exist.

An info finding does not prevent `ready`. A structurally valid document with
incorrect counts, status, or repair references is not a valid report.

## Read-only boundary and limitations

Producing this report must not download a JDK or dependency, run a build,
rewrite configuration, provision or alter a runtime, change a repository, or
apply a repair. Reading files, metadata, process/tool versions, and existing
runtime inventories is allowed. Report emission may write only to the caller's
chosen output or ignored `.workbench/` evidence storage.

`limitations` names incomplete search boundaries, inaccessible authorities,
and checks intentionally not performed. A report may be `ready` within its
declared bounds; it is not proof that a build, launch, transformation, or game
behavior will succeed.
