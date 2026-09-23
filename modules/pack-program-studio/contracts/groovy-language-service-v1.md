# Groovy language-service V1 contract

Status: implemented experimental exact-diagnostics vertical slice

## Developer outcome

Given exact candidate source, an explicit pack/platform profile, an existing
client runtime, and a reachable GroovyScript 1.4.3 language server, a developer
can ask whether selected scripts produce canonicalization diagnostics without
leaving the terminal. Every diagnostic is bound to the exact source hash and
the result inventories the local `runConfig`, mod JARs, Groovy class cache,
optional Java executable, and optional launch receipt.

The operation is available directly as `workbench groovy check` and through
the high-signal console as `pack-program.groovy-check`.

## Preconditions and inputs

V1 requires:

1. one explicit named pack adapter or pack-program profile;
2. one exact source/Groovy root and either repeatable `--file` selections or
   explicit `--all`;
3. one non-symlink client runtime root containing the same `runConfig.json`, a
   bounded `mods/` inventory, and the exact profile-locked GroovyScript JAR;
4. client-side preprocessor context; and
5. a running GroovyScript 1.4.3 language server, normally started in the
   physical client with `-Dgroovyscript.run_ls=true` or `/grs runLS`.

The profile defaults to loopback TCP port `25564`. `--server-workspace-uri`
maps source URIs when Workbench and the game see different paths. `--java` and
`--runtime-receipt` add exact caller context, but cannot authenticate the TCP
peer.

## Exact input custody

Before any source is sent, Workbench:

- runs the static program inventory and re-reads every selected regular,
  non-symlink UTF-8 file to reject changes during analysis;
- requires the runtime and candidate `runConfig.json` hashes to match;
- hashes every bounded regular JAR in `mods/` and requires the exact
  GroovyScript filename, size, and SHA-256 locked by the language profile;
- hashes the bounded Groovy class-cache tree and records that upstream cache
  V4 uses modification time and Java version rather than content addressing;
- optionally hashes and runs the explicitly supplied Java executable with only
  `-version` under a 15-second timeout (the caller must trust that executable);
  and
- optionally retains a launch-receipt hash as explicitly unauthenticated
  context.

The runtime ID binds the run-config hash, mod-graph ID, cache-tree hash, Java
hash, and receipt hash. It is a local inventory identity, not proof that the
server process owns those bytes.

## Protocol and diagnostic canary

The broker is a bounded JSON-RPC/LSP client. It performs `initialize`, full
document synchronization, and a `textDocument/documentSymbol` request, which
is the upstream compile trigger. For every selected URI it first opens an
in-memory source containing a deterministic syntax-error prefix and requires
an explicit error diagnostic. It then replaces the document with the exact
candidate bytes and waits for both an explicit diagnostic publication and the
compile-trigger response.

This canary cycle matters because silence is not a clean compile. A file is
`no-diagnostics` only after the same connection has demonstrated diagnostic
publication for that URI and then explicitly published the candidate result.
Diagnostics bind path, source SHA-256, range, severity, code, source, and
message in their own content IDs.

GroovyScript 1.4.3's embedded server compiles through Groovy canonicalization
and its publisher covers upstream syntax-error messages. Therefore the result
is exact canonicalization evidence, not execution, loader completion, registry
acceptance, reload safety, or save compatibility.

## Endpoint and disclosure boundary

Source bytes are sensitive project content. Without `--allow-remote`, V1
accepts only numeric loopback addresses; `localhost` is pinned to
`127.0.0.1` before connecting rather than trusted through mutable name
resolution. A non-loopback host requires explicit disclosure consent.

The upstream protocol has no nonce, process, artifact, runtime, or launch
receipt challenge. The result must therefore retain endpoint identity as
`unavailable-upstream-protocol`. A successful canary proves the behavior of
the connected language service, but not that it belongs to the inventoried
runtime. The additive
[`managed language-session V1`](groovy-managed-language-session-v1.md) now
strengthens local custody for a receipt-bound disposable Prism launch while
retaining this protocol limitation; it does not retroactively authenticate an
independently supplied V1 check endpoint.

## Bounds and failure behavior

The versioned language profile caps selected files, per-file and aggregate
source bytes, header bytes, message bytes, transcript messages, diagnostics,
runtime artifacts, and artifact bytes. The client rejects duplicate headers
or JSON keys, non-finite JSON values, malformed JSON-RPC envelopes, invalid
ranges/severities, oversized buffers, excessive transcript traffic, and
timeouts. Peer-controlled notification bodies are not retained; the receipt
keeps only ordered message metadata, sizes, and hashes.

Partial progress is retained honestly:

- `completed` means every selected file finished its canary and exact check;
- `partial` means at least one file completed before a protocol, peer, or
  shutdown failure; and
- `blocked` means no file produced an accepted exact check.

The semantic validator recomputes outer, runtime, mod-graph, transcript, and
diagnostic identities; verifies counts, ordering, selected/checked bindings,
state transitions, authority claims, and profile/runtime relationships; and
rejects rebound outer identities with stale inner evidence.

## Result and surfaces

The serialized format is
`workbench-groovy-language-service-result-v1`. `--output` writes only to a
fresh explicit path. Exact Runtime Explorer semantically validates a saved
result, then projects file checks and diagnostics as `verbatim-evidence` with
`runtime_form.state = not-observed`. Recorded source paths remain declarations;
navigation targets the retained receipt until a current workspace revalidates
them.

Terminal and console surfaces call the same broker and validator. Neither
reimplements compiler semantics or upgrades compiler evidence into Atlas
runtime truth.

## Exit codes

- `0`: all selected files completed; diagnostics may still be present unless
  `--strict` is used;
- `1`: `--strict` and the result contains diagnostics or is inconclusive; and
- `2`: invalid/changed input, unsafe disclosure, unavailable endpoint, or a
  blocked check.

## Additive horizons

The separate managed-session family now covers physical-client launch, checked
readiness, terminal/IDE handoff, shutdown, and overlay restoration for exact
receipt-bound Prism projections. V1 still leaves cryptographic upstream
endpoint identity, explicit completion/hover/signature query commands, full
native IDE catalog and retained-result parity, instrumented effective-state
ledgers, cold/reload/reload qualification, Blueprint-backed authoring, and
Relay transport open. Those capabilities must extend this evidence model;
they must not redefine a clean canonicalization result as successful script
execution.
