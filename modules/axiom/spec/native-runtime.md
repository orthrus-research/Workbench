# Profile-pinned native JVM execution

Status: transition foundation implemented. Axiom builds and runs on the real
selected JVM; the custom bytecode, heap, class-loading and Java bootstrap models
have been removed. Complete upstream recipe construction remains unfinished.

## Selection and ownership

The Cleanroom profile owns `profiles/platforms/cleanroom/jvm-runtime.json`:
Temurin HotSpot 25.0.4+7-LTS, Linux x86_64. This is Workbench's provisional
candidate, not a claim that upstream Supersymmetry universally pins that patch.
The profile's provisional selection, existing toolchain archive lock, and runtime
inventory must agree. A mismatch is a failure, not permission to choose another JDK.

Build with the matching JDK, including its pinned compiler. Execute with that
same selected runtime image. The Gradle bytecode target is now 25; a `--release`
flag alone never establishes runtime identity. The build tool reuses existing
toolchain provisioning and does not give Axiom its own installer.

The native engine embeds the exact profile runtime policy at build time. Its
library and command entry points compare the observed version/vendor/platform
and hash the selected runtime files before evaluating inputs. The standalone
supervisor and isolated child each perform admission. Known agents, alternate
bootstrap inputs and security/runtime path overrides are not admitted.
Runtime identity and observed JVM arguments participate in recipe program IDs,
so cache receipts from a different execution context cannot silently transfer.

This is process-preflight file evidence for a **trusted, unchanged installation**.
It is not loaded-memory attestation, a hostile-executable detector, a complete OS
image identity, or protection against concurrent runtime replacement. The host
must provision trusted bytes before launching Java. Different OS/architecture
images require separately reviewed profile entries; no vendor/version fallback.

## Execution responsibility

The actual JVM supplies verification, linking, initialization, ordinary object
and array behavior, exceptions, reflection, strings, native standard-library
operations and garbage collection. Axiom does not reproduce those mechanisms.

Axiom supplies isolated recipe/registry/machine state, explicit environment facts,
logical time and event inputs, result explanations and source/artifact identity.
The profile supplies the selected upstream dependency and transformation policy;
Core owns provisioning, process cancellation and cleanup. Mod-specific loader
ordering, mixins and construction phases remain necessary semantics, not duties
that the JVM can infer from a folder of JARs.

Use independently executable upstream operations directly. Where game coupling
prevents that, extract the smallest source-bound operation with audited dependency
substitutions. Neither loading arbitrary mod entry points nor supplying a fake
world is an acceptable shortcut. Do not launch Minecraft or use Workbench's game
harness to decide recipe validity.

## What this slice does not claim

`check` and `query` retain the bounded Groovy AST and ordinary MIXER start model.
`target` and `platform` remain non-executing input inspections. The native Groovy
unit fixture is trusted test code only. It proves the selected JVM/compiler path
can execute ordinary Groovy helpers; it does not qualify GroovyScript bootstrap,
material generation, pack callbacks, installed mixins or a complete registry.
`wholePackParity` and `installedCompositionQualified` remain false.

Before accepting general source execution, isolation must encompass compilation
(including transforms), registration and processing. The disposable worker now
installs thread-synchronized Linux syscall and resource restrictions before any
compilation, with controlled negative probes. Existing AST restrictions are not
an arbitrary-Groovy sandbox, and those probes do not qualify a pack registration
environment. Timeouts or denied operations remain incomplete evaluation, not
recipe rejection. See the [construction dependency gate](native-construction.md).

## Retired surface and next gate

The modeled JVM, modeled Foundation/event execution, corresponding conformance
drivers and obsolete implementation specifications have been removed from the
public source/build surface. A private pre-transition snapshot retains them for
recovery and research. Historical results do not transfer to this architecture.
[Registration research](registration-research.md) preserves useful requirements.

The next vertical must construct one real recipe-producing dependency chain on
the native JVM from a profile-owned input package, without a caller-supplied
synthetic registry. Capture a baseline, a developer edit, registration lineage
and a machine decision. Bind every actually used source/artifact/configuration
input and report the boundary of that chain rather than claiming the whole pack.
Choose infrastructure work by the concrete dependency it removes from this gate.
