# Shared IDE test assets

This directory contains the small set of source-level fixtures shared by the
Workbench core and canonical client tests. It is not a client implementation or
a release input.

- `d01-code-source-agent/` and `d01_jar_only_runtime_v1.gradle` support the
  Cleanroom development-loop runtime test.
- `diagnose_live_fixture.py` supplies the live Diagnose fixture used by both
  clients.
- `vscode_process_isolation_v1.mjs` verifies VS Code child-process cleanup.

Canonical clients live beside these fixtures under [`clients/`](../README.md).
