# Core-owned native-tool output

The process API supports byte results for small protocol calls and file-backed
capture for retained checks. Both use the same Core supervisor, environment
policy, cancellation and process-tree closure. Capturing protocol bytes does not
interpret them as console health; native exit status remains independent of
domain validation. Ordinary console sessions keep their existing interpretation.

`capture_process` requires an explicit owner binding and a new directory under
owner-private storage. Core writes stdout and stderr once, observes their exact
length and SHA-256 during transport, and flushes the files before committing a
`workbench-process-capture-v1` manifest. A complete reference contains the manifest
identity and owner binding; live stream references include their path, length and
digest. Captured nonzero exits are complete process observations, not successes.

Modules read stream references through `open_process_output`. Reads are forward
only; successful closure verifies the complete bytes and rejects replacement,
truncation, changed content and symbolic/hard links. The capture manifest uses
relative stream paths so evidence can move with its containing attempt. Reopening
files does not execute another worker or establish current-source validity.

Cancellation and process failure leave explicitly incomplete evidence under the
same owner. A killed publisher or physical storage failure may leave files without
a committed manifest; those files cannot supply a complete response reference.
Existing attempt leases and interruption handling protect and account for this
partial storage. Existing captures are never overwritten or silently retried.

Saved Axiom material checks allocate capture within their existing Core attempt,
bind it to the exact prepared request, and include the streams and capture records
in snapshot reference closure and Core custody. Existing storage accounting,
pinning, verified export, retirement, restoration and purge govern those files.
No new retention policy is enabled by using file capture.

This transport representation retains the original wire bytes alongside the
current parsed result and snapshot. It therefore adds retained disk usage until
subsequent representation changes remove redundant copies. The current decoder
still loads the complete JSON response. The separate
[diagnostic delivery path](AXIOM-DIAGNOSTIC-DELIVERY.md) lets compatible IDE clients
read committed findings before snapshot publication/indexing. File capture alone
is neither streaming diagnostic delivery nor a memory or latency guarantee. No
output ceiling is added to Axiom's currently suspended resource targets.
