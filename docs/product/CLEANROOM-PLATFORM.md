# Cleanroom platform boundary

Workbench targets CleanroomMC for Minecraft 1.12.2 development. A supported
construction target names an exact Cleanroom profile; "Minecraft 1.12.2" or
"Forge compatible" is not precise enough.

Useful upstream references are the [Cleanroom repository], [platform
introduction], [mod template], and [porting guide]. They describe a moving
platform. Workbench profiles, not an unversioned web page, define which exact
combination has been tested.

[Cleanroom repository]: https://github.com/CleanroomMC/Cleanroom
[platform introduction]: https://cleanroommc.com/wiki/end-user-guide/introduction
[mod template]: https://github.com/CleanroomMC/CleanroomModTemplate
[porting guide]: https://cleanroommc.com/wiki/cleanroom-mod-development/porting

## What the platform requirement means

- Every supported workspace declares its Cleanroom and Minecraft identities.
- Construction targets Cleanroom. Legacy Forge is evidence or migration
  input, not an equivalent release target.
- Stable Blueprints target a tested supported profile. Experimental
  Blueprints name a provisional profile and their limited evidence scope.
- Atlas observations retain the platform, side, source, and runtime identities
  that give them meaning.
- Manuals teach the Cleanroom path and label historical differences.
- Runtime validation uses an exact Cleanroom environment.
- CLI and IDE clients preserve the same platform requirement.

The current profile authority is under
[`profiles/platforms/cleanroom/`](../../profiles/platforms/cleanroom/README.md).
Product code may consume that authority but must not duplicate it.

## Exact profile fields

A platform profile binds at least:

- Minecraft and Cleanroom versions;
- Java runtime, source language, and emitted bytecode expectations;
- mappings and namespace;
- build-provider family;
- Mixin and transformation environment;
- client, server, launcher, and native-runtime boundaries;
- exact bootstrap and tool artifacts; and
- known compatibility limits.

Results from different profiles are not interchangeable merely because both
use Minecraft 1.12.2.

## Java and build identity

Workbench distinguishes the Java used to run Workbench or an IDE, the Java
used by the build, the authored language level, emitted bytecode, and the JVM
that runs the game. A downgrade pipeline is not equivalent to a native build
on the selected Cleanroom runtime.

The selected profile determines the supported Java and build-provider
combination. Workbench does not freeze a present upstream template, provider,
or Java version into permanent product policy.

## Compatibility claims

Cleanroom's broad Forge compatibility is a starting hypothesis, not a
pack-specific guarantee. A claim about a mod, script, coremod, Mixin,
resource, world, or behavior applies only to the profile and evidence scope
that were tested.

Workbench fails closed when an operation needs an authority or artifact that
the selected profile does not supply. Experimental work may use a declared
provisional profile; a stable release requires a tested supported profile.

## Historical Forge evidence

Exact legacy Forge evidence remains valid for the environment that produced
it. Workbench preserves its source, build, runtime, side, and world identities
and may use it for migration or comparison. It never relabels that evidence as
Cleanroom behavior and never lets it authorize a Cleanroom release.

The convention order is therefore:

1. exact Cleanroom platform invariants;
2. exact dependency and framework requirements;
3. approved pack standards;
4. observed repository patterns; and
5. developer choices allowed by the higher authorities.

A common historical Forge pattern does not outrank a current Cleanroom
requirement. A modern language technique does not outrank an exact dependency
or pack constraint.

## Support test

A Workbench release may call a platform path supported only when a developer
can identify the exact profile, construct and compile against it, run the
required focused validation, distinguish historical evidence from current
behavior, and retain enough runtime identity to reproduce a failure.

Anything less can be useful experimental or migration support, but must be
labeled accordingly.
