# Atlas bounded production inputs

This directory contains a small, source-controlled Atlas V1 query slice. It is
not a second runtime, quest database, or generated proof store.

- `links.jsonl` binds the canonical diluted-light-oil material to its two
  COMMON runtime nodes and the BetterQuesting task that requests it; current
  query composition consumes this file by default.
- `quest-nodes.jsonl` and `quest-edges.jsonl` retain only quest 173, its direct
  neighbors, quest lines, linked task/resource, and prerequisite edges; current
  query composition consumes these files by default.

The checked-in records are current query inputs only; they contain no
workstation-bound evidence or local paths.

Current query commands accept explicit runtime databases and optional corpus
paths. Their defaults resolve the three checked-in link and quest files through
the shared Atlas layout.
