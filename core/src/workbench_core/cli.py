"""Core commands and dispatch to installed modules, without domain imports."""

from __future__ import annotations

import argparse
import errno
from importlib import metadata
import json
from pathlib import Path
import sys
from typing import Sequence

from workbench_api import ModuleError
from workbench_api.state_paths import default_runtime_state_root
from .modules import discover, dispatch
from .physical_context import resolve_physical_context


def source_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "core/pyproject.toml").is_file() and (parent / "workbench.toml").is_file() and Path(__file__).resolve().is_relative_to(parent / "core/src/workbench_core"):
            return parent
    return Path(__file__).resolve().parent


def _main(argv: Sequence[str] | None = None) -> int:
    from .host_services import install_local_host_services
    install_local_host_services()
    arguments = list(sys.argv[1:] if argv is None else argv)
    root = source_root()
    if (root / "core/pyproject.toml").is_file():
        from .development import enable_source_checkout
        enable_source_checkout(root)
    from .dispatch_setup import _activate_user_setup
    if not _activate_user_setup(arguments):
        return 2
    try:
        from .module_cli import disabled_profiles, main as package_main
        from .package_guard import PackageActivity
        from workbench_api.profiles import profile_scope
        with profile_scope(disabled=disabled_profiles(default_runtime_state_root(root))):
            if arguments[:1] in (["modules"], ["profiles"]):
                return package_main(arguments[1:], root=root, kind=arguments[0])
            with PackageActivity():
                return _dispatch(arguments, root)
    except BrokenPipeError:
        raise
    except (ModuleError, OSError, ValueError) as exc:
        print(f"Workbench: {exc}", file=sys.stderr)
        return 2


def _dispatch(arguments: list[str], root: Path) -> int:
    if arguments[:1] in (["version"], ["--version"]):
        version = metadata.version("workbench-core")
        value = {"component_id": "workbench-core", "version": version}
        print(json.dumps(value, sort_keys=True) if "--json" in arguments else f"Workbench Core {version}")
        return 0
    if arguments[:1] == ["setup"]:
        from .setup_cli import main as setup
        from workbench_api.resources import repository_root
        resources = root if (root / "core/pyproject.toml").is_file() else repository_root(__file__)
        return setup(arguments[1:], root=resources)
    if arguments[:1] == ["repair"]:
        from .repair_cli import main as repair
        return repair(arguments[1:])
    if arguments[:1] == ["tooling"]:
        from .tooling_provision import main as tooling
        return tooling(arguments[1:])
    from .module_cli import profile_admission_scope
    with profile_admission_scope(default_runtime_state_root(root)) as modules:
        return _dispatch_available(arguments, root, modules)


def _dispatch_available(arguments: list[str], root: Path, modules) -> int:
    if arguments[:2] == ["runtime", "preflight"]:
        from .manual_artifacts import main as preflight
        from workbench_api.profiles import profile_resources
        return preflight(arguments[2:], profiles=profile_resources("manual-artifacts"))
    if arguments[:1] in (["storage"], ["runtime"], ["world"]):
        from .storage.cli import main as storage
        physical = resolve_physical_context(root)
        return storage(arguments, root=root, workspace_root=physical.state_root)
    if arguments[:1] == ["environment"]:
        from .environment_status import inspect_environment_status
        parser = argparse.ArgumentParser(prog="workbench environment")
        parser.add_argument("action", choices=["status", "paths"])
        parser.add_argument("--json", action="store_true")
        selected = parser.parse_args(arguments[1:])
        if selected.action == "paths":
            physical = resolve_physical_context(root)
            print(json.dumps(physical.record(), indent=2, sort_keys=True))
            return 0
        observation = inspect_environment_status(root)
        observation["native_core"] = {"distribution": "workbench-core", "version": metadata.version("workbench-core"), "state_root": str(default_runtime_state_root(root))}
        print(json.dumps(observation, indent=2, sort_keys=True))
        return 0
    if not arguments or arguments[:1] in (["-h"], ["--help"]):
        from workbench_api.profiles import profiles
        admitted_profiles = {profile.id for profile in profiles()}
        print("Workbench Core: setup, repair, tooling, environment status, environment paths, storage, runtime, world, modules list, profiles list, version")
        for module in modules:
            if module.module:
                for capability in module.module.capabilities:
                    if set(capability.requires_profiles) <= admitted_profiles:
                        print(f"  {' '.join(capability.command)}: {capability.description}")
        return 0
    physical = resolve_physical_context(
        root,
        include_saved_setup="--help" not in arguments and "-h" not in arguments,
    )
    return dispatch(arguments, physical.execution_context(), modules)


def main(argv: Sequence[str] | None = None) -> int:
    from .terminal import _configure_utf8_terminal_streams, _silence_broken_stdout
    _configure_utf8_terminal_streams()
    try:
        return _main(argv)
    except KeyboardInterrupt:
        print("\nWorkbench cancelled.", file=sys.stderr)
        return 130
    except OSError as exc:
        if not isinstance(exc, BrokenPipeError) and exc.errno != errno.EPIPE:
            raise
        _silence_broken_stdout()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
