#!/usr/bin/env python3

"""Publish an exact Mixin compiler/AP invocation-custody receipt."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "modules/project-intelligence/src"))

from workbench_project_intelligence import (  # noqa: E402
    ArtifactScanError,
    ClassifiedBuildFileInput,
    ExactBuildFileInput,
    MAX_BUILD_INPUT_BYTES,
    MIXIN_BUILD_INPUT_FORMAT,
    MixinCompilerAPInvocationInput,
    build_mixin_compiler_ap_build_receipt,
    render_mixin_compiler_ap_build_receipt,
)


class _DuplicateJsonKey(ValueError):
    pass


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(f"duplicate key {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number {value}")


def _regular_bytes(path: Path, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ArtifactScanError(f"{label} is not a regular, non-symlink file: {path}")
    try:
        size = path.stat().st_size
        if size > MAX_BUILD_INPUT_BYTES:
            raise ArtifactScanError(
                f"{label} exceeds {MAX_BUILD_INPUT_BYTES} input bytes: {path}"
            )
        return path.read_bytes()
    except OSError as exc:
        raise ArtifactScanError(f"cannot read {label} {path}: {exc}") from exc


def _declared_bytes(path: Path, label: str) -> bytes | None:
    if path.is_symlink():
        raise ArtifactScanError(f"{label} is a symlink: {path}")
    if not path.exists():
        return None
    return _regular_bytes(path, label)


def _object(value: Any, keys: set[str], context: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ArtifactScanError(f"{context} has an unsupported V1 shape")
    return value


def _string(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ArtifactScanError(f"{context} is invalid")
    return value


def _parse_spec(raw: bytes, base: Path) -> MixinCompilerAPInvocationInput:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_json_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError, _DuplicateJsonKey, ValueError) as exc:
        raise ArtifactScanError(f"build input specification is invalid JSON: {exc}") from exc

    spec = _object(
        value,
        {
            "annotation_processor_options",
            "compiler",
            "compiler_options",
            "diagnostics",
            "format",
            "inputs",
            "invocation_label",
            "outputs",
            "processors",
            "schema_version",
            "sources",
        },
        "build input specification",
    )
    if spec.get("format") != MIXIN_BUILD_INPUT_FORMAT or spec.get("schema_version") != 1:
        raise ArtifactScanError("build input specification format is unsupported")

    def exact_file(item: Any, context: str) -> ExactBuildFileInput:
        row = _object(item, {"label", "logical_name", "path"}, context)
        label = _string(row.get("label"), f"{context} label")
        logical_name = _string(row.get("logical_name"), f"{context} logical_name")
        raw_path = _string(row.get("path"), f"{context} path")
        path = Path(raw_path)
        if not path.is_absolute():
            path = base / path
        return ExactBuildFileInput(
            label=label,
            logical_name=logical_name,
            data=_declared_bytes(path, context),
        )

    def file_array(items: Any, context: str) -> list[ExactBuildFileInput]:
        if not isinstance(items, list):
            raise ArtifactScanError(f"{context} must be an array")
        return [exact_file(item, f"{context} {index}") for index, item in enumerate(items)]

    def classified_array(items: Any, context: str) -> list[ClassifiedBuildFileInput]:
        if not isinstance(items, list):
            raise ArtifactScanError(f"{context} must be an array")
        rows: list[ClassifiedBuildFileInput] = []
        for index, item in enumerate(items):
            item_context = f"{context} {index}"
            row = _object(item, {"kind", "label", "logical_name", "path"}, item_context)
            rows.append(
                ClassifiedBuildFileInput(
                    kind=_string(row.get("kind"), f"{item_context} kind"),
                    file=exact_file(
                        {
                            "label": row["label"],
                            "logical_name": row["logical_name"],
                            "path": row["path"],
                        },
                        item_context,
                    ),
                )
            )
        return rows

    compiler = _object(
        spec.get("compiler"),
        {
            "compiler_identity_capture",
            "executable",
            "runtime_artifacts",
            "runtime_identity_capture",
        },
        "compiler",
    )
    for field in ("compiler_options", "annotation_processor_options"):
        if not isinstance(spec.get(field), list):
            raise ArtifactScanError(f"{field} must be an ordered array")

    return MixinCompilerAPInvocationInput(
        invocation_label=_string(spec.get("invocation_label"), "invocation_label"),
        compiler_executable=exact_file(compiler["executable"], "compiler executable"),
        runtime_artifacts=file_array(compiler["runtime_artifacts"], "runtime artifacts"),
        compiler_identity_capture=exact_file(
            compiler["compiler_identity_capture"], "compiler identity capture"
        ),
        runtime_identity_capture=exact_file(
            compiler["runtime_identity_capture"], "runtime identity capture"
        ),
        processor_artifacts=file_array(spec["processors"], "processor artifacts"),
        compiler_options=spec["compiler_options"],
        annotation_processor_options=spec["annotation_processor_options"],
        diagnostics=exact_file(spec["diagnostics"], "diagnostics capture"),
        sources=file_array(spec["sources"], "sources"),
        inputs=classified_array(spec["inputs"], "inputs"),
        outputs=classified_array(spec["outputs"], "outputs"),
    )


def _publish(path: Path, payload: bytes) -> None:
    if path.is_symlink() or not path.parent.is_dir():
        raise ArtifactScanError(f"Mixin compiler/AP receipt output path is unsafe: {path}")
    temporary_name: str | None = None
    try:
        with NamedTemporaryFile("wb", dir=path.parent, delete=False) as temporary:
            temporary_name = temporary.name
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    except OSError as exc:
        raise ArtifactScanError(f"cannot publish receipt {path}: {exc}") from exc
    finally:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                pass


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="assemble-mixin-compiler-ap-build-receipt",
        description=(
            "Bind exact caller-declared compiler/AP inputs and outputs without "
            "claiming reproducible-build proof."
        ),
    )
    parser.add_argument("input_spec", type=Path)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args(argv)

    try:
        spec_bytes = _regular_bytes(args.input_spec, "build input specification")
        policy_bytes = _regular_bytes(args.policy, "build policy")
        invocation = _parse_spec(spec_bytes, args.input_spec.parent)
        receipt = build_mixin_compiler_ap_build_receipt(invocation, policy_bytes)
        _publish(
            args.output,
            render_mixin_compiler_ap_build_receipt(receipt, compact=args.compact),
        )
    except (ArtifactScanError, OSError, TypeError, KeyError, ValueError) as exc:
        parser.error(str(exc))

    if receipt["summary"]["custody_state"] == "partial" and not args.allow_partial:
        print(
            f"Mixin compiler/AP custody is partial; receipt published to {args.output}",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
