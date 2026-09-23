# Atlas recipe query-index operation V1

Status: additive operational record for disposable Atlas query storage.

Format identity: `workbench-atlas-recipe-index-operation-v1`.

## Purpose

Categorical graph manifests and canonical JSONL streams are authoritative. The
SQLite query index is a derived accelerator and is excluded from graph-set
identity. A graph may therefore be valid evidence while search is not currently
executable because its index descriptor or file is absent, stale, corrupt, or
unsafe.

The public repair command is:

```bash
workbench atlas recipes index GRAPH \
  --max-source-bytes 8589934592 \
  --max-index-bytes 4294967296 --json
```

The operation:

1. validates every authoritative stream before staging;
2. rejects declared stream bytes above `max_source_bytes`;
3. requires at least twice `max_index_bytes` in free space for the staged
   database and SQLite `VACUUM`;
4. applies SQLite's page-count ceiling so the staged database cannot publish
   above `max_index_bytes`;
5. emits bounded progress by completed records and source bytes;
6. verifies the staged database against the exact graph streams;
7. atomically replaces `query-index.sqlite3`, then atomically updates only the
   manifest's `query_index` descriptor; and
8. proves that `graph_set_id` did not change.

The result retains the exact source record/byte counts, requested bounds, disk
preflight, prior and published descriptors, mutated derived paths, progress
phases, and a content-addressed operation ID. It explicitly records that
authoritative graph evidence was not mutated.

The command is recoverable rather than transactional across its two atomic
replacements. A crash between index replacement and descriptor replacement can
only leave a descriptor mismatch; context/search fail closed and rerunning the
same bounded command repairs it.

## Operational context

`workbench atlas recipes context GRAPH` emits
`workbench-atlas-recipe-health-operational-context-v1` for graph roots. It
separates `evidence_capabilities` from currently executable `capabilities` and
includes:

- exact index state and reason code;
- whether search is executable now;
- whether the derived failure supports rebuild; and
- the repair argv and its derived-storage-only authority.

The original `workbench-atlas-recipe-health-context-v1` remains unchanged in
V1 search, inspection, and impact evidence records after an index is opened and
verified.
