"""Small user-facing editor for durable Workbench location choices."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Sequence

from .user_config_home import default_user_config_home
from .user_config_migration import inspect_legacy_config_migration, migrate_legacy_config
from .user_preferences import (
    LOCATION_ROLES,
    choose_default_workspace,
    default_settings_path,
    default_workspaces_path,
    load_settings,
    load_workspaces,
    register_workspace,
    remove_workspace,
    resolve_expression,
    set_location,
    set_workspace_selection,
)


def main(argv: Sequence[str] | None = None, *, suite_root: Path | None = None) -> int:
    parser = argparse.ArgumentParser(prog="workbench settings")
    actions = parser.add_subparsers(dest="action")
    show = actions.add_parser("show", help="show durable user choices")
    show.add_argument("--json", action="store_true")
    set_parser = actions.add_parser("set", help="save one location choice")
    set_parser.add_argument("role", choices=sorted(LOCATION_ROLES))
    set_parser.add_argument("path")
    unset = actions.add_parser("unset", help="return one location to its default")
    unset.add_argument("role", choices=sorted(LOCATION_ROLES))
    workspace = actions.add_parser("workspace", help="register or list named workspaces")
    workspace.add_argument("operation", choices=["list", "add", "remove", "default", "clear-default", "select", "acquire"])
    workspace.add_argument("name", nargs="?")
    workspace.add_argument("path", nargs="?")
    workspace.add_argument("--default", action="store_true")
    workspace.add_argument("--json", action="store_true")
    workspace.add_argument("--profile-config")
    workspace.add_argument("--java-home")
    workspace.add_argument("--java-feature", type=int, choices=(8,))
    workspace.add_argument("--clear-profile", action="store_true")
    workspace.add_argument("--clear-java", action="store_true")
    workspace.add_argument("--expected-record-id")
    migration = actions.add_parser("migrate", help="copy earlier user records into the stable home")
    migration.add_argument("--dry-run", action="store_true")
    migration.add_argument("--json", action="store_true")
    migration.add_argument(
        "--file", action="append", choices=("setup-v1.json", "recipe-fixtures-v1.json", "launcher-v1.json"),
        help="import only a named record; repeat to select multiple files",
    )
    selected = parser.parse_args(list(argv) if argv is not None else None)

    if selected.action in (None, "show"):
        settings = load_settings()
        workspaces = load_workspaces()
        if getattr(selected, "json", False):
            print(json.dumps({
                "configuration_home": str(default_user_config_home()),
                "settings": settings,
                "workspaces": workspaces,
            }, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(f"Workbench settings: {default_user_config_home()}")
            print(f"  Locations: {default_settings_path()}")
            if settings["locations"]:
                for role, path in settings["locations"].items():
                    print(f"    {role}: {path}")
            else:
                print("    Defaults in use")
            print(f"  Workspaces: {default_workspaces_path()}")
            if workspaces["entries"]:
                for row in workspaces["entries"]:
                    marker = " (default)" if row["name"] == workspaces["default"] else ""
                    print(f"    {row['name']}: {row['path']}{marker}")
                    if workspaces["schema_version"] >= 2:
                        print(f"      profile: {row['profile_config'] or 'none selected'}")
                        java = (f"managed Java {row['managed_java_feature']}" if row.get('managed_java_feature')
                                else row['java_home'] or 'profile default')
                        print(f"      Java: {java}")
            else:
                print("    No named workspaces")
            print("  Run 'workbench environment resolve' to see effective paths.")
        return 0
    if selected.action == "set":
        set_location(selected.role, selected.path)
        print(f"Saved {selected.role}: {selected.path}")
        return 0
    if selected.action == "unset":
        set_location(selected.role, None)
        print(f"Using the default for {selected.role}")
        return 0
    if selected.action == "migrate":
        result = (
            inspect_legacy_config_migration(filenames=selected.file)
            if selected.dry_run else migrate_legacy_config(filenames=selected.file)
        )
        if selected.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(f"Legacy configuration: {result['source']}")
            print(f"Stable configuration: {result['destination']}")
            for row in result["files"]:
                print(f"  {row['name']}: {row['state']}")
            print(f"State: {result['state']}")
        return 1 if result["state"] == "conflict" else 0
    if selected.operation == "list":
        if selected.name is not None or selected.path is not None or selected.default or selected.profile_config is not None or selected.java_home is not None or selected.java_feature is not None or selected.clear_profile or selected.clear_java or selected.expected_record_id:
            parser.error("workspace list takes no name, path, or --default")
        record = load_workspaces()
        if selected.json:
            print(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            for row in record["entries"]:
                print(f"{row['name']}\t{row['path']}")
        return 0
    if selected.operation == "select":
        if selected.name is None or selected.path is not None or selected.default:
            parser.error("workspace select requires NAME and no PATH or --default")
        if selected.profile_config is not None and selected.clear_profile:
            parser.error("choose --profile-config or --clear-profile")
        if sum((selected.java_home is not None, selected.java_feature is not None, selected.clear_java)) > 1:
            parser.error("choose --java-home, --java-feature, or --clear-java")
        choices = {}
        if selected.profile_config is not None or selected.clear_profile:
            choices["profile_config"] = selected.profile_config
        if selected.java_home is not None or selected.clear_java:
            choices["java_home"] = selected.java_home
            if selected.clear_java:
                choices["managed_java_feature"] = None
        if selected.java_feature is not None:
            choices["managed_java_feature"] = selected.java_feature
        if not choices:
            parser.error("workspace select requires a profile or Java choice")
        record = set_workspace_selection(
            selected.name, **choices, expected_record_id=selected.expected_record_id,
        )
        if selected.json:
            print(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(f"Saved workspace selection: {selected.name}")
        return 0
    if selected.operation == "acquire":
        if selected.name is None or selected.path is not None or selected.default or selected.profile_config is not None or selected.java_home is not None or selected.java_feature is not None or selected.clear_profile or selected.clear_java:
            parser.error("workspace acquire requires NAME and an optional --expected-record-id")
        from .environment_resolution import resolve_environment
        from .managed_java import CoreManagedJava
        registry = load_workspaces()
        if selected.expected_record_id is not None and registry["record_id"] != selected.expected_record_id:
            parser.error("user workspaces changed after review")
        entry = next((row for row in registry["entries"] if row["name"] == selected.name), None)
        if entry is None:
            parser.error(f"workspace is not registered: {selected.name}")
        if entry.get("java_home") is not None:
            parser.error("workspace uses a supplied Java path; there is nothing to acquire")
        suite = Path.cwd() if suite_root is None else suite_root
        selection_environment = dict(os.environ)
        selection_environment.pop("WORKBENCH_JAVA_HOME", None)
        resolved = resolve_environment(
            suite,
            workspace=resolve_expression(entry["path"], environment=selection_environment),
            environment=selection_environment,
        )
        if resolved.record["workspaces"]["record_id"] != registry["record_id"]:
            parser.error("user workspaces changed after review")
        operation = resolved.operation_selection()
        if operation.java_home is not None:
            parser.error("workspace uses a supplied Java path; there is nothing to acquire")
        config = operation.profile_configuration or Path("workbench.toml")
        result = CoreManagedJava(state_root=resolved.state_root, selection=operation).ensure(
            suite, config_path=config,
        )
        if selected.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(f"Java: {result['outcome']} ({result['source']})")
        return 0
    if (
        selected.profile_config is not None or selected.java_home is not None or selected.java_feature is not None
        or selected.clear_profile or selected.clear_java or selected.expected_record_id
    ):
        parser.error("profile and Java choices require workspace select")
    if selected.operation in {"remove", "default", "clear-default"}:
        if selected.path is not None or selected.default:
            parser.error(f"workspace {selected.operation} takes no path or --default")
        if selected.operation == "clear-default":
            if selected.name is not None:
                parser.error("workspace clear-default takes no name")
            choose_default_workspace(None)
            print("Using Setup V1 or the process workspace default")
            return 0
        if selected.name is None:
            parser.error(f"workspace {selected.operation} requires NAME")
        if selected.operation == "remove":
            remove_workspace(selected.name)
            print(f"Removed workspace {selected.name}")
        else:
            choose_default_workspace(selected.name)
            print(f"Default workspace: {selected.name}")
        return 0
    if selected.name is None or selected.path is None:
        parser.error("workspace add requires NAME and PATH")
    result = register_workspace(selected.name, selected.path, make_default=selected.default)
    print(f"Saved workspace {selected.name}: {selected.path}")
    if result["default"] == selected.name:
        print(f"Default workspace: {selected.name}")
    return 0


__all__ = ["main"]
