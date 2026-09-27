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
    state_root = actions.add_parser(
        "state-root", help="resolve or save one workspace's retained state root",
    )
    state_root.add_argument("operation", choices=("resolve", "select", "clear", "clear-stale"))
    state_root.add_argument("workspace", type=Path)
    state_root.add_argument("role", choices=("product-spine", "feature"))
    state_root.add_argument("path", nargs="?")
    state_root.add_argument("--expected-policy-id")
    state_root.add_argument("--expected-record-id")
    state_root.add_argument("--json", action="store_true")
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
    reconstruction = actions.add_parser(
        "environment", help="share or import one exact workspace environment selection"
    )
    reconstruction.add_argument("operation", choices=("export", "plan", "feasibility", "import"))
    reconstruction.add_argument("source", help="workspace name for export or share file for import")
    reconstruction.add_argument("--name", help="local workspace name for plan or import")
    reconstruction.add_argument("--workspace", help="local workspace directory for plan or import")
    reconstruction.add_argument("--config", help="matching local Configuration V1 manifest")
    reconstruction.add_argument("--java-home", help="local Java path when the share requires one")
    reconstruction.add_argument(
        "--bind-project-source-lock", action="store_true",
        help="export a V2 share with the selected pack variant's exact source-lock file",
    )
    reconstruction.add_argument(
        "--bind-managed-tools", action="store_true",
        help="export a V3 share with the exact managed-tool policy; requires --bind-project-source-lock",
    )
    reconstruction.add_argument(
        "--acquire-managed-java", action="store_true",
        help="acquire the exact managed Java release during import",
    )
    reconstruction.add_argument("--plan-id", help="exact reviewed import plan identity")
    reconstruction.add_argument("--json", action="store_true")
    migration = actions.add_parser("migrate", help="copy earlier user records into the stable home")
    migration.add_argument("--dry-run", action="store_true")
    migration.add_argument("--json", action="store_true")
    migration.add_argument(
        "--file", action="append", choices=("setup-v1.json", "recipe-fixtures-v1.json", "launcher-v1.json"),
        help="import only a named record; repeat to select multiple files",
    )
    selected = parser.parse_args(list(argv) if argv is not None else None)

    if selected.action in (None, "show"):
        from .state_root_selection import load_state_root_selections

        settings = load_settings()
        workspaces = load_workspaces()
        state_roots = load_state_root_selections()
        if getattr(selected, "json", False):
            print(json.dumps({
                "configuration_home": str(default_user_config_home()),
                "settings": settings,
                "workspaces": workspaces,
                "state_roots": state_roots,
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
            print(f"  Retained state roots: {default_user_config_home() / 'state-roots-v1.json'}")
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
    if selected.action == "state-root":
        from .state_root_selection import (
            clear_stale_state_root, effective_state_root, select_state_root,
        )

        suite = Path.cwd() if suite_root is None else suite_root
        if selected.operation != "clear-stale" and selected.expected_record_id is not None:
            parser.error("--expected-record-id is only for state-root clear-stale")
        if selected.operation == "resolve":
            if selected.path is not None:
                parser.error("state-root resolve takes no path")
            result = effective_state_root(
                selected.workspace, selected.role, suite_root=suite,
                expected_policy_id=selected.expected_policy_id,
            )
        elif selected.operation == "clear-stale":
            if selected.path is not None or selected.expected_policy_id is not None:
                parser.error("state-root clear-stale takes no path or policy ID")
            if selected.expected_record_id is None:
                parser.error("state-root clear-stale requires --expected-record-id from settings show")
            result = clear_stale_state_root(
                selected.workspace, selected.role, suite_root=suite,
                expected_record_id=selected.expected_record_id,
            )
        else:
            if selected.operation == "select" and selected.path is None:
                parser.error("state-root select requires a path")
            if selected.operation == "clear" and selected.path is not None:
                parser.error("state-root clear takes no path")
            if selected.expected_policy_id is None:
                parser.error("state-root changes require --expected-policy-id from resolve")
            result = select_state_root(
                selected.workspace, selected.role,
                selected.path if selected.operation == "select" else None,
                suite_root=suite, expected_policy_id=selected.expected_policy_id,
            )
        if selected.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(f"{result['role']}: {result['state_root']} ({result['source']})")
            print(f"  Policy: {result['policy_id']}")
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
    if selected.action == "environment":
        from .environment_reconstruction import (
            apply_import, assess_reconstruction_feasibility, export_share, load_share, plan_import,
        )
        suite = Path.cwd() if suite_root is None else suite_root
        if selected.operation == "export":
            if any((selected.name, selected.workspace, selected.config,
                    selected.java_home, selected.plan_id, selected.acquire_managed_java)):
                parser.error("environment export takes only a named source workspace")
            if selected.bind_managed_tools and not selected.bind_project_source_lock:
                parser.error("--bind-managed-tools requires --bind-project-source-lock")
            result = export_share(
                suite, selected.source,
                bind_project_source_lock=selected.bind_project_source_lock,
                bind_managed_tools=selected.bind_managed_tools,
            )
            if selected.json:
                print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                print(f"Environment share: {result['resource']['path']}")
                print(f"  Share: {result['share']['share_id']}")
            return 0
        if selected.name is None or selected.workspace is None:
            parser.error("environment plan/feasibility/import require --name and --workspace")
        if selected.bind_project_source_lock or selected.bind_managed_tools:
            parser.error("portable lock binding is an export choice")
        if selected.operation in {"plan", "feasibility"} and selected.plan_id is not None:
            parser.error(f"environment {selected.operation} does not take --plan-id")
        if selected.operation == "import" and selected.plan_id is None:
            parser.error("environment import requires the reviewed --plan-id")
        share = load_share(selected.source)
        options = {
            "workspace_name": selected.name,
            "workspace": selected.workspace,
            "config_path": selected.config or "workbench.toml",
            "java_home": selected.java_home,
            "acquire_managed_java": selected.acquire_managed_java,
        }
        if selected.operation == "plan":
            result = plan_import(suite, share, **options)
            if selected.json:
                print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                print(f"Environment import: {result['state']} ({result['action']})")
                print(f"  Plan: {result['plan_id']}")
                for blocker in result["blockers"]:
                    print(f"  Blocked: {blocker}")
                for unresolved in result["unresolved_inputs"]:
                    print(f"  Additional input: {unresolved}")
            return 1 if result["state"] == "blocked" else 0
        if selected.operation == "feasibility":
            result = assess_reconstruction_feasibility(suite, share, **options)
            if selected.json:
                print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                print(f"Environment feasibility: {result['state']}")
                print(f"  Import plan: {result['import_plan_id']} ({result['import_state']})")
                print(f"  Project source: {result['project_source']['state']}")
                print(f"  Optional modules: {result['optional_module_packages']['state']}")
                print(f"  Fixture/tools: {result['profile_fixture_tools']['state']}")
                print(f"  Java: {result['java']['state']}")
                for blocker in result["import_blockers"]:
                    print(f"  Local blocker: {blocker}")
            return 0
        result = apply_import(suite, share, expected_plan_id=selected.plan_id, **options)
        if selected.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(f"Environment selection: {result['outcome']}")
            print(f"  Receipt: {result['resource']['path']}")
            if result.get("managed_java") is not None:
                java = result["managed_java"]
                print(f"  Managed Java: {java['outcome']} ({java['runtime_id']})")
            for unresolved in result["unresolved_inputs"]:
                print(f"  Additional input: {unresolved}")
        return 0
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
        or selected.clear_profile or selected.clear_java
        or (selected.expected_record_id and selected.operation != "add")
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
    result = register_workspace(
        selected.name, selected.path, make_default=selected.default,
        expected_record_id=selected.expected_record_id,
    )
    if selected.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"Saved workspace {selected.name}: {selected.path}")
        if result["default"] == selected.name:
            print(f"Default workspace: {selected.name}")
    return 0


__all__ = ["main"]
