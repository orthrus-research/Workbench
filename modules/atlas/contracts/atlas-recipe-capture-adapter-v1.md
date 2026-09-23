# Atlas recipe capture adapter API 1

`atlas recipes import-capture CAPTURE --pack-profile PROFILE --input-manifest
INPUT --output NEW_GRAPH` prepares a new graph through an explicit admitted
`workbench.recipe_graphs` profile extension. It does not launch a game or edit
the capture. Missing, disabled, ambiguous or incompatible profile contributions
fail through the shared profile-extension admission API.

The extension declares integer `PROFILE_API_VERSION = 1` and
`RECIPE_GRAPH_API_VERSION = 1`, and implements:

```python
project_capture(capture_root, output, *, input_manifest,
                max_source_bytes, check_cancelled=None) -> dict
```

The adapter must verify the input through its evidence owner's reader, retain
the actual capture side and lifecycle checkpoint, and preserve exact occurrence,
resource, lookup and source identities. The byte limit bounds admitted input;
the optional cancellation callback raises to stop work. Adapters publish a new
graph atomically and clean up only their own unpublished staging directory.

The receipt has `state: complete`, the resolved output `root` and `graph_set_id`.
Adapter-specific fields describe its admitted scope. Atlas reopens the complete
authoritative graph and compares its identity and output path to the receipt;
an adapter's success flag alone cannot admit a graph.

Supersymmetry's finite GT adapter uses Crucible's retained V1 raw-capture reader.
Its projection preserves historical input identities without asserting that a
retained run represents today's pack. Quantities remain on edges rather than
splitting identical resources into separate nodes. Unsupported selector matching
is retained as observation with incomplete acceptance, never converted to a
proved exact alternative set. Partition limitations appear in inspection and
impact evidence gaps as `graph-projection-limitations`.

The resulting graph is readable by the independent Atlas installation. The
profile package is required for importing that profile's capture, not for
reading an already admitted graph. Importing a capture establishes neither
machine execution nor usable supply, cycle bootstrap, progression reachability,
current profile support or release qualification.


The Supersymmetry profile also accepts a
[developer-selected saved-source binding](../../../profiles/packs/supersymmetry/atlas/developer-recipe-capture-input-v1.md)
for the existing qualified Forge runtime model. New saved source revisions do
not require the historical circuit commit; exact runtime/matcher compatibility
is still checked. This binding is not an installed native capture runner.
