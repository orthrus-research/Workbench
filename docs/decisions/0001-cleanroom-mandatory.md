# Decision 0001: CleanroomMC is mandatory

Status: accepted
Date: 2026-07-30

## Decision

Workbench supports Minecraft 1.12.2 development through declared CleanroomMC
platform profiles. Active construction targets Cleanroom. Experimental work
may use a provisional profile; stable releases use a tested supported profile.

Legacy Forge environments remain valid evidence sources for their exact
historical profiles and may support observation, comparison, and migration.
They are not equivalent release targets.

## Consequences

- Platform identity is present where it changes implementation or test meaning.
- Existing Forge captures retain their original labels.
- Legacy Java 8 collectors are not activated as Workbench core.
- Modern Java capabilities follow the exact selected Cleanroom profile rather
  than a hard-coded eternal Java version.
- The initial Supersymmetry profile can drive experimental work as soon as a
  runnable provisional Cleanroom setup exists.
