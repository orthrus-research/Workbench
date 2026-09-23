#!/usr/bin/env python3

"""Evaluate exact mod archives with the bound Cleanroom Mixin Doctor policy."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile
from typing import Sequence


ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "modules/project-intelligence/src"))
sys.path.insert(0, str(ROOT / "profiles/platforms/cleanroom/src"))

from workbench_cleanroom_mixin_doctor import (  # noqa: E402
    DoctorError,
    inspect_artifact_paths,
    render_report,
)


DEFAULT_POLICY = (
    ROOT
    / "profiles/platforms/cleanroom/mixins"
    / "cleanroom-mixin-doctor-policy-v1.json"
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="workbench-cleanroom-mixin-doctor",
        description=(
            "Evaluate exact JAR/ZIP bytes with the experimental Cleanroom "
            "Mixin policy without loading archive code."
        ),
    )
    parser.add_argument("artifacts", metavar="ARTIFACT", nargs="+")
    parser.add_argument(
        "--policy",
        type=Path,
        default=DEFAULT_POLICY,
        help="exact policy JSON (defaults to the repository Cleanroom profile)",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="emit canonical compact JSON instead of indented JSON",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="write atomically to this path instead of standard output",
    )
    parser.add_argument(
        "--fail-on",
        choices=("none", "reject", "review"),
        default="none",
        help=(
            "after publishing the report, exit 1 for the selected disposition "
            "threshold (review also includes reject)"
        ),
    )
    return parser


def _write_atomic(path: Path, payload: bytes) -> None:
    if not path.parent.is_dir():
        raise DoctorError(f"output parent is not a directory: {path.parent}")
    temporary_name: str | None = None
    try:
        with NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    except OSError as exc:
        raise DoctorError(f"cannot publish report {path}: {exc}") from exc
    finally:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                pass


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    try:
        report = inspect_artifact_paths(
            arguments.artifacts,
            policy_path=arguments.policy,
        )
        payload = render_report(report, compact=arguments.compact)
        if arguments.output is None:
            sys.stdout.buffer.write(payload)
        else:
            _write_atomic(arguments.output, payload)
        disposition = report["summary"]["disposition"]
        if arguments.fail_on == "reject" and disposition == "reject":
            return 1
        if arguments.fail_on == "review" and disposition in {"review", "reject"}:
            return 1
    except DoctorError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
