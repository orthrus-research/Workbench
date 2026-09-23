# Workbench brand assets

`orthrus-research-avatar.png` is a 256-pixel copy of the
[Orthrus Research GitHub organization avatar](https://github.com/orthrus-research),
retrieved on 2026-09-23. The root README displays it at 64 pixels to identify
the parent organization. It is a raster reference for the README, not the
source for generated client icons.

`workbench-brand-v1.json` remains the source for the current Workbench client
icon set. It is an independently constructed set of declared geometric
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
