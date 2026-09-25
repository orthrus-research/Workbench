"""Small user-facing editor for durable Workbench location choices."""

from __future__ import annotations

import argparse
import json
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
    set_location,
)


def main(argv: Sequence[str] | None = None) -> int:
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
    workspace.add_argument("operation", choices=["list", "add", "remove", "default", "clear-default"])
    workspace.add_argument("name", nargs="?")
    workspace.add_argument("path", nargs="?")
    workspace.add_argument("--default", action="store_true")
    migration = actions.add_parser("migrate", help="copy earlier user records into the stable home")
    migration.add_argument("--dry-run", action="store_true")
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
        result = inspect_legacy_config_migration() if selected.dry_run else migrate_legacy_config()
        print(f"Legacy configuration: {result['source']}")
        print(f"Stable configuration: {result['destination']}")
        for row in result["files"]:
            print(f"  {row['name']}: {row['state']}")
        print(f"State: {result['state']}")
        return 1 if result["state"] == "conflict" else 0
    if selected.operation == "list":
        if selected.name is not None or selected.path is not None or selected.default:
            parser.error("workspace list takes no name, path, or --default")
        for row in load_workspaces()["entries"]:
            print(f"{row['name']}\t{row['path']}")
        return 0
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
