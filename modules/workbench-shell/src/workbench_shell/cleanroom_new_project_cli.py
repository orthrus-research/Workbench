"""Public Workbench adapter for the profile-owned Cleanroom constructor V2."""

from __future__ import annotations

import argparse
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any, Mapping, Sequence, TextIO

from workbench_api.state_paths import default_product_spine_state_root


PROFILE_PACKAGE = Path(
    "profiles/platforms/cleanroom/src/workbench_cleanroom_new_project"
)
MAX_RECORD_BYTES = 16 * 1024 * 1024


class CleanroomNewProjectCliV2Error(RuntimeError):
    """The public adapter could not invoke the exact profile owner port."""


def _profile(root: Path) -> Any:
    blueprints_source = root / "modules/blueprints/src"
    try:
        blueprints_metadata = blueprints_source.lstat()
    except OSError as exc:
        raise CleanroomNewProjectCliV2Error(
            "Blueprints fresh-project owner implementation is unavailable"
        ) from exc
    if stat.S_ISLNK(blueprints_metadata.st_mode) or not stat.S_ISDIR(
        blueprints_metadata.st_mode
    ):
        raise CleanroomNewProjectCliV2Error(
            "Blueprints fresh-project owner implementation is unsafe"
        )
    blueprints_path = os.fspath(blueprints_source)
    if blueprints_path not in sys.path:
        sys.path.insert(0, blueprints_path)
    package = root / PROFILE_PACKAGE
    init = package / "__init__.py"
    try:
        metadata = init.lstat()
    except OSError as exc:
        raise CleanroomNewProjectCliV2Error(
            "Cleanroom construction owner implementation is unavailable"
        ) from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise CleanroomNewProjectCliV2Error(
            "Cleanroom construction owner implementation is unsafe"
        )
    module_name = (
        "_workbench_cleanroom_new_project_v2_"
        + sha256(os.fspath(root).encode("utf-8", "strict")).hexdigest()[:16]
    )
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing
    specification = importlib.util.spec_from_file_location(
        module_name,
        init,
        submodule_search_locations=[os.fspath(package)],
    )
    if specification is None or specification.loader is None:
        raise CleanroomNewProjectCliV2Error(
            "Cleanroom construction owner implementation cannot be loaded"
        )
    module = importlib.util.module_from_spec(specification)
    sys.modules[module_name] = module
    try:
        specification.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    required = (
        "apply_cleanroom_mod_construction",
        "build_cleanroom_mod_request",
        "preview_cleanroom_mod_construction",
        "recover_cleanroom_mod_construction",
        "validate_construction_owner",
    )
    if any(not callable(getattr(module, name, None)) for name in required):
        sys.modules.pop(module_name, None)
        raise CleanroomNewProjectCliV2Error(
            "Cleanroom construction owner port is incomplete"
        )
    return module


def load_cleanroom_mod_construction_owner(
    suite_root: Path | str,
) -> dict[str, Any]:
    """Load the exact profile owner through its semantic validator."""

    root = Path(suite_root).expanduser().resolve(strict=True)
    try:
        value = _profile(root).validate_construction_owner(root)
    except (OSError, ValueError) as exc:
        raise CleanroomNewProjectCliV2Error(str(exc)) from exc
    if type(value) is not dict:
        raise CleanroomNewProjectCliV2Error(
            "Cleanroom construction owner did not return one record"
        )
    return value


def validate_cleanroom_mod_construction_owner(
    suite_root: Path | str, value: Mapping[str, Any]
) -> dict[str, Any]:
    """Revalidate a caller-supplied copy against the live profile authority."""

    root = Path(suite_root).expanduser().resolve(strict=True)
    try:
        result = _profile(root).validate_construction_owner(root, value)
    except (OSError, ValueError) as exc:
        raise CleanroomNewProjectCliV2Error(str(exc)) from exc
    return dict(result)


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        metadata = path.lstat()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or not 2 <= metadata.st_size <= MAX_RECORD_BYTES
        ):
            raise CleanroomNewProjectCliV2Error(
                f"{label} is not one bounded ordinary file"
            )
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            raw = stream.read(MAX_RECORD_BYTES + 1)
            after = os.fstat(stream.fileno())
    except CleanroomNewProjectCliV2Error:
        raise
    except OSError as exc:
        raise CleanroomNewProjectCliV2Error(f"cannot read {label}") from exc
    identity = lambda row: (
        row.st_dev,
        row.st_ino,
        row.st_mode,
        row.st_nlink,
        row.st_size,
        row.st_mtime_ns,
        row.st_ctime_ns,
    )
    if (
        identity(metadata) != identity(before)
        or identity(before) != identity(after)
        or len(raw) != before.st_size
    ):
        raise CleanroomNewProjectCliV2Error(f"{label} changed while read")
    try:
        value = json.loads(raw.decode("utf-8", "strict"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CleanroomNewProjectCliV2Error(f"{label} is invalid JSON") from exc
    if type(value) is not dict:
        raise CleanroomNewProjectCliV2Error(f"{label} is not one JSON object")
    return value


def _write_fresh(path: Path, value: Mapping[str, Any]) -> None:
    raw = (
        json.dumps(
            dict(value),
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )
    if len(raw) > MAX_RECORD_BYTES:
        raise CleanroomNewProjectCliV2Error("construction plan exceeds its bound")
    destination = Path(os.path.abspath(path.expanduser()))
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(
            destination,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        raise CleanroomNewProjectCliV2Error(
            "construction plan output must be a fresh file"
        ) from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="workbench new cleanroom-mod")
    actions = parser.add_subparsers(dest="action", required=True)
    preview = actions.add_parser("preview")
    preview.add_argument("target", type=Path)
    preview.add_argument(
        "--output-mode",
        choices=("instructions", "direct-apply"),
        default="instructions",
    )
    preview.add_argument("--sequence", type=int, default=0)
    preview.add_argument("--output", type=Path)
    preview.add_argument("--json", action="store_true")
    apply = actions.add_parser("apply")
    apply.add_argument("plan", type=Path)
    apply.add_argument("--state-root", type=Path)
    apply.add_argument("--consent-plan-id", required=True)
    apply.add_argument("--json", action="store_true")
    recover = actions.add_parser("recover")
    recover.add_argument("plan", type=Path)
    recover.add_argument("--state-root", type=Path)
    recover.add_argument("--json", action="store_true")
    return parser


def _state(root: Path, plan: Mapping[str, Any], supplied: Path | None) -> Path:
    if supplied is not None:
        candidate = supplied.expanduser()
        if candidate.is_symlink():
            raise CleanroomNewProjectCliV2Error(
                "construction state root cannot be a symbolic link"
            )
        return Path(os.path.abspath(candidate))
    identity = plan.get("id")
    if not isinstance(identity, str) or not identity:
        raise CleanroomNewProjectCliV2Error(
            "construction plan has no exact identity"
        )
    token = sha256(identity.encode("utf-8", "strict")).hexdigest()
    return default_product_spine_state_root(root) / "new-project-v2" / token


def _render(value: Mapping[str, Any], output: TextIO) -> None:
    plan = value.get("plan")
    plan_id = plan.get("id") if isinstance(plan, Mapping) else None
    output.write(
        f"Cleanroom mod construction {plan_id or 'result'} — "
        f"{value.get('state', 'retained')}\n"
    )


def new_project_main(
    argv: Sequence[str],
    *,
    root: Path,
    output: TextIO = sys.stdout,
    error: TextIO = sys.stderr,
) -> int:
    """Invoke preview/apply/recover without reinterpreting profile truth."""

    arguments = list(argv)
    if arguments[:1] != ["cleanroom-mod"]:
        error.write("Workbench new failed: supported kind is cleanroom-mod\n")
        return 2
    args = _parser().parse_args(arguments[1:])
    suite = root.expanduser().resolve(strict=True)
    try:
        profile = _profile(suite)
        if args.action == "preview":
            request = profile.build_cleanroom_mod_request(
                args.target,
                output_mode=args.output_mode,
                sequence=args.sequence,
                allow_direct_apply=args.output_mode == "direct-apply",
            )
            value = profile.preview_cleanroom_mod_construction(suite, request)
            if args.output is not None:
                plan = value.get("plan")
                if not isinstance(plan, Mapping):
                    raise CleanroomNewProjectCliV2Error(
                        "construction preview returned no plan"
                    )
                _write_fresh(args.output, plan)
        else:
            plan = _read_json(args.plan, "Cleanroom construction plan")
            state_root = _state(suite, plan, args.state_root)
            if args.action == "apply":
                value = profile.apply_cleanroom_mod_construction(
                    suite,
                    plan,
                    state_root,
                    consent_plan_id=args.consent_plan_id,
                )
            else:
                value = profile.recover_cleanroom_mod_construction(
                    suite, plan, state_root
                )
        if args.json:
            output.write(json.dumps(value, indent=2, sort_keys=True) + "\n")
        else:
            _render(value, output)
        return 0 if value.get("state") not in {"rejected", "review-required"} else 1
    except BrokenPipeError:
        raise
    except (CleanroomNewProjectCliV2Error, OSError, ValueError) as exc:
        error.write(f"Workbench new failed: {exc}\n")
        return 2


__all__ = [
    "CleanroomNewProjectCliV2Error",
    "load_cleanroom_mod_construction_owner",
    "new_project_main",
    "validate_cleanroom_mod_construction_owner",
]
