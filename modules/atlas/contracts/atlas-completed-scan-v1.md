# Completed Atlas scan exchange V1

A completed scan lets a developer send another developer the graph and saved
audit of one captured environment. The recipient can inspect that evidence
without the original workspace, server, Java installation or profile.

Atlas owns the completed-scan schema and stored-audit reader. Core owns archive
transport and publication; Crucible supplies the capture reader. The standalone
installation consists of API, Core, Atlas and Crucible. Shell, pack profiles,
Pack Program Studio and Material Semantics are unnecessary for these commands.

## Command journey

Create the initial archive through the capture owner, which verifies the
completed original attempt:

```text
workbench capture recipes export ATTEMPT_ID --output "branch-scan.zip" --json
```

That command requires Shell and access to its original retained attempt. The
remaining commands belong to standalone Atlas and use the same flags on
Windows and Linux:

```text
workbench atlas scans import "branch-scan.zip" --destination "Imported scan" --json
workbench atlas scans show "Imported scan"
workbench atlas scans audit "Imported scan" --summary --json
workbench atlas scans audit "Imported scan" --finding missing-producer-candidate --limit 25 --json
workbench atlas scans audit "Imported scan" --lookup-state active --text circuit --offset 25 --limit 25 --json
workbench atlas scans export "Imported scan" --output "forwarded-scan.zip" --json
```

Import verifies transport and domain bindings in private staging before
publishing a new destination. Existing destinations are never overwritten.
Failures and cancellation produce no successful import or export result.
Keep notes outside the imported directory: its verified inventory excludes
additional files other than Core's separate import receipt.

`show` reports the original result identity, graph path, captured coverage,
saved audit totals and policy status. Human output summarizes the historical
scope. `--json` returns the complete versioned record, with Unicode preserved
through JSON escapes for portable redirected output.
Human commands print a brief verification notice to standard error before
reading the data. JSON commands keep standard output to one result document.
Opening a scan or reading saved findings still verifies the complete included
capture and graph; paging limits the response size, not verification cost.

Use the reported graph path for existing Atlas commands:

```text
workbench atlas recipes context "Imported scan/graph" --json
workbench atlas recipes search "Imported scan/graph" copper --json
workbench atlas recipes inspect "Imported scan/graph" SELECTION_ID --json
```

## Reading the stored audit

`workbench atlas scans audit` reads the retained report and verifies its
structure, references, bindings and counts. It never launches Minecraft,
projects a new graph or runs the dead-end evaluator.

With no selection flags, it returns the stored summary and matching-row count
without recipe rows. Filters or explicit paging request a page, defaulting to
100 rows. All result formats preserve the original audit totals and coverage;
filters affect only the matching rows and their count.

| Option | Meaning |
| --- | --- |
| `--summary` | Return totals and matching count without recipe rows; may accompany filters. |
| `--finding` | Select one stored finding category listed below. |
| `--lookup-state` | Select `active`, `inactive` or `unknown`. |
| `--text` | Case-insensitive match in saved recipe/map IDs and keys, plus related item/fluid IDs, keys and observed names. |
| `--offset` | Nonnegative, zero-based offset in matching rows. |
| `--limit` | Between 0 and 10,000 rows. Zero requests no recipe rows. |
| `--json` | Return the complete page record. |

`--summary` cannot accompany explicit `--offset` or `--limit`. The finding
categories are `missing-producer-candidate`, `no-output-use-candidate`,
`both-sides-candidate`, `stranded-output-candidate` and `structural-cycle`.
Multiple filters apply together. Empty text, invalid paging and unknown filter
values are refused.

The `workbench-atlas-cached-recipe-audit-page-v1` result contains unchanged
stored recipe rows in `items`; `page` contains `offset`, `limit`, `returned`,
`total_matching` and `next_offset`. Follow a non-null `next_offset` with the
same filters and limit. The page also retains the audit hash, policy, context,
summary, coverage, scan identity and local graph path. `recomputed` is always
false. `policy_status` distinguishes `same-implementation` from
`different-implementation`; a changed installed evaluator never silently
changes the saved findings.

## Required envelope

V1 exports the full required data subset:

- `graph/`: its V2 manifest, authoritative node/edge partitions and query index.
- `capture/`: the original capture manifest, completion marker and every declared
  observation payload, including auxiliary preparation evidence.
- `input-manifest.json` and `audit.json`.
- The original `request.json`, `prepared.json`, `launch.json`,
  `runtime-lock.json`, `protocol.json` and `result.json`.

This preserves source/runtime identities, policy and original path strings as
provenance. Those original paths are not resolved on the recipient's machine.
The archive contains no copied workspace, mods, game, JDK, execution projection,
process streams or observer build directory. It provides data for review rather
than an executable environment. Slim or graph-only exports are outside V1.

Core's `workbench-archive-v1` manifest records each member's portable relative
path, byte count and SHA-256 alongside opaque completed-scan metadata. The
domain metadata format is `workbench-atlas-completed-scan-v1`; it binds the
graph, capture, input and audit identities, original result, policy, coverage
and summary. Import rejects missing, unexpected, changed or incompatible data
and contradictory graph/capture/audit bindings. Transport rejects unsafe member
paths and file-type or name collisions. Its default bounds are 100,000 members,
64 GiB of payload and a 16 MiB archive manifest.
Domain readers additionally bound individual metadata records to 32 MiB,
the stored audit to 1 GiB, and original capture payloads to 1 GiB in total.

Core records local extraction separately in `import-receipt.json`. Re-export
rehashes the original member set and preserves its manifest identity and all
payload bytes. It omits the local receipt. The ZIP container bytes are not the
portable identity; different compression implementations may encode the same
original envelope differently.

## Interpretation and trust

These commands admit an internally consistent historical snapshot. They report
content integrity as verified, publisher authenticity as unverified, original
execution custody as not included, and matching to the current target as
unassessed. The original result's execution claim remains provenance; importing
its metadata does not repeat that execution or supply omitted process custody.

The first supported audit concerns the captured finite GT recipe graph.
External acquisition, terminal uses, other recipe families, machine execution,
quantity/chance feasibility and transitive supply retain the original report's
limitations. Missing links are review candidates. Structural cycles do not
establish deadlock or bootstrap supply. Each scan keeps its own identities;
this format does not establish correspondence between recipes in different
captures or explain differences between platforms.

## Python boundary

The public façade is `workbench_atlas_recipe_health.completed_scan`:

- `import_scan(archive, destination, *, check_cancelled=None)`
- `show_scan(directory, *, check_cancelled=None)`
- `export_scan(directory, destination, *, check_cancelled=None)`
- `read_cached_recipe_audit(directory, *, finding=None, lookup_state=None,
  text=None, offset=0, limit=100, check_cancelled=None)`

The first three return `workbench-atlas-completed-scan-view-v1` records; the
audit reader returns the page format above. An import adds its local receipt;
an export adds archive path/size/hash information. Domain failures raise
`ScanError`, a `RecipeHealthError`. The CLI reports failure with exit status 2
and does not print a successful partial record. The APIs accept a cancellation
callback; clients do not reimplement transport or the evaluator.
