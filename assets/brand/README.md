# Workbench brand assets

`workbench-brand-v1.json` is the source of truth for the first public
Workbench mark. It is an independently constructed set of declared geometric
primitives and does not trace or incorporate the earlier user-supplied woven
mark.

The mark depicts a workbench top and legs around a compact `W`. Its source
record, canonical SVG, IntelliJ SVG, and VS Code PNG are licensed with the
repository under `LGPL-3.0-only`.

Regenerate the client assets with:

```text
python3 tools/render_brand_assets.py render
```

Check that tracked outputs still match the source record with:

```text
python3 tools/render_brand_assets.py check
```

The renderer uses only Python's standard library. It emits deterministic SVG
and a 256-pixel, five-times-supersampled RGBA PNG.
