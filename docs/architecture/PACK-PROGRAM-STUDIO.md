# Pack Program Studio

Pack Program Studio provides read-only analysis and exact diagnostics for pack
programs such as GroovyScript. It treats those programs as source with
lifecycle, dependency, identity, and mutation effects—not as unstructured
configuration.

The implementation is generic under
[`modules/pack-program-studio/`](../../modules/pack-program-studio/README.md).
Platform-specific GroovyScript behavior belongs under
`profiles/platforms/cleanroom/`; Supersymmetry conventions belong under
`profiles/packs/supersymmetry/groovy/`.

## Current commands

`workbench groovy dev` inventories one exact program tree:

```text
source + runConfig + side/debug/packmode
              |
comment-aware lexical model
              |
stage order, symbols, dependencies, effect candidates, collisions
              |
baseline diff and conservative reload/save recommendation
```

`workbench groovy check` adds compiler diagnostics from GroovyScript's own
language service:

```text
source + matching runConfig + exact GroovyScript JAR and cache
              |
bounded JSON-RPC/LSP connection
              |
syntax-error canary, full-document replacement, exact diagnostics
```

`workbench groovy session` creates and owns a disposable compiler session from
a completed Workbench launch receipt. It binds the runtime/program inventory,
loopback ports, Prism process, workspace URI mapping, readiness canary,
restoration behavior, and final receipt. VS Code and IntelliJ consume the same
descriptor; they do not create a second compiler or launch path.

Use `workbench capabilities pack-program` for the installed public actions and
each command's `--help` for its exact options.

## Source model

The analyzer reads bounded regular UTF-8 files, hashes every input, and follows
the selected `runConfig.json` loader order. It recognizes GroovyScript
preprocessors and preserves uncertainty when installed-mod or packmode context
is missing.

Its lexer masks comments and string contents before extracting balanced call
arguments, simple literals, locations, and normalized expressions. Dynamic
Groovy—helpers, loops, event closures, metaclass operations, interpolated
names, and direct Java registry calls—remains a candidate or escape hatch, not
a fabricated exact effect.

Semantic comparison uses a multiset so duplicate operations remain visible.
Exact file changes are retained even when recognized effects match because
ordering and unrecognized code may still matter.

## Diagnostic custody

The language-service canary prevents silence from being reported as a clean
result. For each checked URI, the service must first publish a deliberate
syntax error and then the candidate diagnostic set. Results bind diagnostics
to the exact source path and SHA-256.

The upstream service accepts one client at a time. A managed session exposes
that sequential boundary and releases the readiness client before IDE
handoff. Cross-host WSL/Windows sessions bind distinct relay and JVM ports plus
explicit URI mappings; clients never guess path translation.

An independently supplied endpoint cannot prove its runtime identity because
the protocol has no peer challenge. A Workbench-created disposable session
adds bounded process and byte custody, but the protocol limitation remains
part of the result.

## Evidence and authority

- Source calls, symbols, edges, effects, and collisions are static
  observations or profile classifications, not Atlas runtime observations.
- Compiler diagnostics are exact for the connected endpoint and in-memory
  bytes, but do not prove script execution or effective registry state.
- Logs retain integration or verbatim evidence state and are not silently
  promoted to causality.
- Pack and platform profiles own lifecycle and identity policy.
- Atlas owns accepted observed and derived game knowledge.
- Blueprints owns construction and mutation.
- Manuals may teach the workflow but cannot authorize a patch.
- Workbench Shell and Exact Runtime Explorer present validated results without
  recreating their rules.

## Reload and save boundary

The selected platform profile defines which lifecycle stages can reload. A
reloadable stage alone is insufficient for an unknown direct mutation, event
registration, or compatibility hook. Workbench requires an admitted adapter
and relevant cold/reload/idempotence evidence before recommending live reload
without a restart alternative.

Material or item identity changes also carry pack-profile save review.
Program analysis never opens or mutates a world to settle that question.

## Nonclaims

The current surface can inventory, compare, diagnose, and retain exact program
context. It does not claim that:

- a recognized builder executed;
- a clean compiler result changed a registry;
- a neighboring exception was caused by Groovy;
- reload is idempotent without controlled evidence;
- progression remains reachable;
- a candidate is approved construction; or
- a source path recorded in an old receipt still names unchanged bytes.

Mutation must enter a Blueprint-backed review and consent lifecycle. Runtime
effects require controlled observation. A successful bounded
material-backed-fluid experiment remains evidence for that exact case, not a
general Pack Program effect ledger.
