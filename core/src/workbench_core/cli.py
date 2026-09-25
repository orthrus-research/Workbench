"""Core commands and dispatch to installed modules, without domain imports."""

from __future__ import annotations

import argparse
import errno
from importlib import metadata
import json
import os
from pathlib import Path
import sys
from typing import Sequence

from workbench_api import ExecutionContext, ModuleError
from workbench_api.state_paths import default_runtime_state_root
from .modules import discover, dispatch
from .environment_resolution import resolve_environment


def source_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "core/pyproject.toml").is_file() and (parent / "workbench.toml").is_file() and Path(__file__).resolve().is_relative_to(parent / "core/src/workbench_core"):
            return parent
    return Path(__file__).resolve().parent


def _main(argv: Sequence[str] | None = None) -> int:
    from .host_services import install_local_host_services
    install_local_host_services()
    arguments = list(sys.argv[1:] if argv is None else argv)
    caller_environment = dict(os.environ)
    root = source_root()
    if (root / "core/pyproject.toml").is_file():
        from .development import enable_source_checkout
        enable_source_checkout(root)
    from .dispatch_setup import user_setup_environment
    with user_setup_environment(arguments) as activated:
        if not activated:
            return 2
        try:
            if arguments[:1] == ["settings"] or arguments[:2] == ["environment", "resolve"]:
                return _dispatch(arguments, root, caller_environment=caller_environment)
            from .module_cli import disabled_profiles, main as package_main
            from .package_guard import PackageActivity
            from workbench_api.profiles import profile_scope
            with profile_scope(disabled=disabled_profiles(default_runtime_state_root(root))):
                if arguments[:1] in (["modules"], ["profiles"]):
                    return package_main(arguments[1:], root=root, kind=arguments[0])
                with PackageActivity():
                    return _dispatch(arguments, root, caller_environment=caller_environment)
        except BrokenPipeError:
            raise
        except (ModuleError, OSError, ValueError) as exc:
            print(f"Workbench: {exc}", file=sys.stderr)
            return 2


def _dispatch(
    arguments: list[str], root: Path, *, caller_environment: dict[str, str] | None = None
) -> int:
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
    if arguments[:1] == ["settings"]:
        from .settings_cli import main as settings
        return settings(arguments[1:])
    if arguments[:2] == ["environment", "resolve"]:
        return _dispatch_available(arguments, root, (), caller_environment=caller_environment)
    if arguments[:2] == ["sandbox", "recover"]:
        from .axiom_sandbox import recover_axiom
        parser = argparse.ArgumentParser(prog="workbench sandbox recover")
        parser.add_argument("--state-root", type=Path)
        selected = parser.parse_args(arguments[2:])
        state = selected.state_root or resolve_environment(
            root, environment=caller_environment
        ).state_root
        print(json.dumps({"schema": "workbench.sandbox-recovery.v1", "recovered_sessions":
                          recover_axiom(state)}, sort_keys=True))
        return 0
    from .module_cli import profile_admission_scope
    with profile_admission_scope(default_runtime_state_root(root)) as modules:
        return _dispatch_available(arguments, root, modules, caller_environment=caller_environment)


def _dispatch_available(
    arguments: list[str], root: Path, modules, *, caller_environment: dict[str, str] | None = None
) -> int:
    if arguments[:2] == ["runtime", "preflight"]:
        from .manual_artifacts import main as preflight
        from workbench_api.profiles import profile_resources
        return preflight(arguments[2:], profiles=profile_resources("manual-artifacts"))
    if arguments[:1] in (["storage"], ["runtime"], ["world"]):
        from .storage.cli import main as storage
        resolved = resolve_environment(root, environment=caller_environment)
        return storage(arguments, root=root, workspace_root=resolved.state_root)
    if arguments[:1] == ["environment"]:
        parser = argparse.ArgumentParser(prog="workbench environment")
        parser.add_argument("action", choices=["status", "resolve"])
        parser.add_argument("workspace", nargs="?", type=Path)
        parser.add_argument("--json", action="store_true")
        selected = parser.parse_args(arguments[1:])
        if selected.workspace is not None and selected.action != "resolve":
            parser.error("a workspace target is accepted only by environment resolve")
        if selected.action == "resolve":
            resolved = resolve_environment(
                root,
                workspace=selected.workspace,
                environment=caller_environment,
            )
            if selected.json:
                print(json.dumps(resolved.record, indent=2, sort_keys=True))
            else:
                print("Workbench environment")
                print(f"  Configuration: {resolved.configuration_home}")
                print(f"  Workspace: {resolved.workspace} ({resolved.record['workspace']['source']})")
                print(f"  State: {resolved.state_root} ({resolved.record['state_root']['source']})")
                for role, entry in resolved.record["locations"].items():
                    print(f"  {role.replace('_', ' ').capitalize()}: {entry['path']} ({entry['source']})")
                print(f"  Settings: {resolved.record['settings']['path']}")
                profile = resolved.record["profile_configuration_reference"] or "none selected"
                print(f"  Profile reference: {profile}")
                for tool, candidate in resolved.record["tool_candidates"].items():
                    print(f"  {tool.replace('_', ' ').capitalize()} candidate: {candidate or 'none selected'}")
            return 0
        from .environment_status import inspect_environment_status
        observation = inspect_environment_status(root)
        observation["native_core"] = {"distribution": "workbench-core", "version": metadata.version("workbench-core"), "state_root": str(default_runtime_state_root(root))}
        print(json.dumps(observation, indent=2, sort_keys=True))
        return 0
    if not arguments or arguments[:1] in (["-h"], ["--help"]):
        from workbench_api.profiles import profiles
        admitted_profiles = {profile.id for profile in profiles()}
        print("Workbench Core: setup, settings, repair, tooling, sandbox recover, environment status, environment resolve, storage, runtime, world, modules list, profiles list, version")
        for module in modules:
            if module.module:
                for capability in module.module.capabilities:
                    if set(capability.requires_profiles) <= admitted_profiles:
                        print(f"  {' '.join(capability.command)}: {capability.description}")
        return 0
    if "--help" in arguments or "-h" in arguments:
        from .setup_cli import _state_root, _workspace

        values = os.environ if caller_environment is None else caller_environment
        context = ExecutionContext(
            _workspace(values.get("WORKBENCH_WORKSPACE") or Path.cwd()),
            _state_root(
                values.get("WORKBENCH_STATE_ROOT")
                or default_runtime_state_root(root, environment=values)
            ),
        )
    else:
        resolved = resolve_environment(root, environment=caller_environment)
        context = ExecutionContext(
            resolved.workspace,
            resolved.state_root,
            locations=resolved.locations,
        )
    return dispatch(arguments, context, modules)


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
