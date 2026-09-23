#!/usr/bin/env python3

"""Render and check the repository-native Workbench mark."""

from __future__ import annotations

import argparse
import binascii
import json
from pathlib import Path
import struct
import sys
from typing import Any, Iterable, Sequence
import zlib


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets/brand/workbench-brand-v1.json"
SUPERSAMPLE = 5


class BrandAssetError(ValueError):
    pass


def _load() -> dict[str, Any]:
    try:
        value = json.loads(SOURCE.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BrandAssetError(f"cannot read brand source: {exc}") from exc
    if value.get("format") != "workbench-brand-v1" or value.get("schema_version") != 1:
        raise BrandAssetError("unsupported brand source")
    if value.get("canvas") != {"width": 40, "height": 40}:
        raise BrandAssetError("brand canvas must remain 40 by 40")
    origin = value.get("origin", {})
    if (
        origin.get("kind") != "repository-native-geometric-construction"
        or origin.get("third_party_visual_input") is not False
        or origin.get("license") != "LGPL-3.0-only"
    ):
        raise BrandAssetError("brand origin is incomplete")
    return value


def _color(value: str) -> tuple[int, int, int, int]:
    if len(value) != 7 or not value.startswith("#"):
        raise BrandAssetError(f"unsupported color {value!r}")
    try:
        return tuple(int(value[index : index + 2], 16) for index in (1, 3, 5)) + (255,)
    except ValueError as exc:
        raise BrandAssetError(f"unsupported color {value!r}") from exc


def _path(points: Iterable[Iterable[int]]) -> str:
    rows = [tuple(point) for point in points]
    if len(rows) < 3 or any(len(point) != 2 for point in rows):
        raise BrandAssetError("a polygon needs at least three two-axis points")
    return "M" + " L".join(f"{x} {y}" for x, y in rows) + " Z"


def render_svg(value: dict[str, Any]) -> bytes:
    rows = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40" viewBox="0 0 40 40">'
    ]
    for shape in value["shapes"]:
        fill = shape["fill"]
        _color(fill)
        if shape["kind"] == "rounded-rectangle":
            rows.append(
                f'  <rect x="{shape["x"]}" y="{shape["y"]}" '
                f'width="{shape["width"]}" height="{shape["height"]}" '
                f'rx="{shape["radius"]}" fill="{fill}"/>'
            )
        elif shape["kind"] == "polygon":
            rows.append(f'  <path d="{_path(shape["points"])}" fill="{fill}"/>')
        else:
            raise BrandAssetError(f"unsupported shape {shape.get('kind')!r}")
    rows.append("</svg>")
    return ("\n".join(rows) + "\n").encode("utf-8")


def _inside_polygon(x: float, y: float, points: list[list[int]]) -> bool:
    inside = False
    previous_x, previous_y = points[-1]
    for current_x, current_y in points:
        crosses = (current_y > y) != (previous_y > y)
        if crosses:
            boundary = (previous_x - current_x) * (y - current_y) / (
                previous_y - current_y
            ) + current_x
            if x < boundary:
                inside = not inside
        previous_x, previous_y = current_x, current_y
    return inside


def _inside_rounded_rectangle(x: float, y: float, shape: dict[str, Any]) -> bool:
    left = float(shape["x"])
    top = float(shape["y"])
    right = left + float(shape["width"])
    bottom = top + float(shape["height"])
    radius = float(shape["radius"])
    if not (left <= x <= right and top <= y <= bottom):
        return False
    if left + radius <= x <= right - radius or top + radius <= y <= bottom - radius:
        return True
    center_x = left + radius if x < left + radius else right - radius
    center_y = top + radius if y < top + radius else bottom - radius
    return (x - center_x) ** 2 + (y - center_y) ** 2 <= radius**2


def _chunk(kind: bytes, payload: bytes) -> bytes:
    body = kind + payload
    return (
        struct.pack(">I", len(payload))
        + body
        + struct.pack(">I", binascii.crc32(body) & 0xFFFFFFFF)
    )


def render_png(value: dict[str, Any]) -> bytes:
    size = value["outputs"]["vscode_png_size"]
    if type(size) is not int or size != 256:
        raise BrandAssetError("VS Code PNG size must remain 256")
    high_size = size * SUPERSAMPLE
    canvas_width = value["canvas"]["width"]
    scale = high_size / canvas_width
    pixels = bytearray(high_size * high_size * 4)
    for shape in value["shapes"]:
        color = _color(shape["fill"])
        for pixel_y in range(high_size):
            y = (pixel_y + 0.5) / scale
            for pixel_x in range(high_size):
                x = (pixel_x + 0.5) / scale
                if shape["kind"] == "rounded-rectangle":
                    inside = _inside_rounded_rectangle(x, y, shape)
                else:
                    inside = _inside_polygon(x, y, shape["points"])
                if inside:
                    offset = (pixel_y * high_size + pixel_x) * 4
                    pixels[offset : offset + 4] = bytes(color)

    rows = bytearray()
    samples = SUPERSAMPLE * SUPERSAMPLE
    for output_y in range(size):
        rows.append(0)
        for output_x in range(size):
            totals = [0, 0, 0, 0]
            for sample_y in range(SUPERSAMPLE):
                high_y = output_y * SUPERSAMPLE + sample_y
                for sample_x in range(SUPERSAMPLE):
                    high_x = output_x * SUPERSAMPLE + sample_x
                    offset = (high_y * high_size + high_x) * 4
                    for channel in range(4):
                        totals[channel] += pixels[offset + channel]
            rows.extend((total + samples // 2) // samples for total in totals)
    signature = b"\x89PNG\r\n\x1a\n"
    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return signature + _chunk(b"IHDR", header) + _chunk(
        b"IDAT", zlib.compress(bytes(rows), level=9)
    ) + _chunk(b"IEND", b"")


def expected_outputs(value: dict[str, Any]) -> dict[Path, bytes]:
    svg = render_svg(value)
    outputs = value["outputs"]
    return {
        ROOT / outputs["canonical_svg"]: svg,
        ROOT / outputs["intellij_svg"]: svg,
        ROOT / outputs["vscode_png"]: render_png(value),
    }


def render() -> None:
    for path, raw in expected_outputs(_load()).items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        print(path.relative_to(ROOT))


def check() -> None:
    mismatches = []
    for path, expected in expected_outputs(_load()).items():
        try:
            observed = path.read_bytes()
        except OSError:
            observed = None
        if observed != expected:
            mismatches.append(path.relative_to(ROOT).as_posix())
    if mismatches:
        raise BrandAssetError("brand outputs drifted: " + ", ".join(mismatches))
    print("Workbench brand assets match their repository-native source")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("render", "check"))
    args = parser.parse_args(argv)
    try:
        render() if args.command == "render" else check()
        return 0
    except (BrandAssetError, OSError) as exc:
        print(f"brand asset operation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
