"""Command-line surface for managed Workbench runtimes, worlds, and storage."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

from . import manager


def _preview_group(
    parser: argparse.ArgumentParser, *, include_apply: bool = False
) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--show",
        action="store_true",
        help="show the inert operation plan without changing local state",
    )
    group.add_argument(
        "--json",
        action="store_true",
        help="emit the complete inert operation plan as JSON without changing local state",
    )
    if include_apply:
        group.add_argument(
            "--apply",
            action="store_true",
            help="execute the reviewed operation plan",
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="workbench",
        description="Inventory and safely manage Workbench-owned runtimes and worlds.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    storage = commands.add_parser(
        "storage", help="inspect storage and perform recoverable cleanup"
    )
    storage.add_argument('--checks', action='store_true',
        help='Use Core retained-check storage across all projects and profiles')
    storage.add_argument('--checks-root', type=Path,
        help='Explicit previously used retained-check store, including an inactive custom state location')
    storage_commands = storage.add_subparsers(dest="storage_action", required=True)

    storage_list = storage_commands.add_parser(
        "list", help="inventory Workbench local storage without changing it"
    )
    storage_list.add_argument(
        "--json", action="store_true", help="emit the complete inventory as JSON"
    )

    storage_inspect = storage_commands.add_parser(
        "inspect", help="inspect one inventory resource by exact ID"
    )
    storage_inspect.add_argument("selector")
    storage_inspect.add_argument(
        "--json", action="store_true", help="emit the complete resource record as JSON"
    )

    storage_cleanup = storage_commands.add_parser(
        "cleanup", help="plan or move one managed resource into recoverable trash"
    )
    storage_cleanup.add_argument("selector")
    storage_cleanup.add_argument(
        "--allow-review",
        action="store_true",
        help="allow an explicitly reviewed resource to be planned for cleanup",
    )
    _preview_group(storage_cleanup, include_apply=True)

    storage_restore = storage_commands.add_parser(
        "restore", help="plan or restore one recoverable trash transaction"
    )
    storage_restore.add_argument("selector")
    _preview_group(storage_restore, include_apply=True)

    storage_purge = storage_commands.add_parser(
        "purge", help="permanently purge one exact recoverable trash transaction"
    )
    storage_purge.add_argument("selector")
    storage_purge.add_argument(
        "--confirm",
        metavar="TRASH_ID",
        help="execute only when this exactly names the selected trash transaction",
    )
    _preview_group(storage_purge)

    storage_commands.add_parser('history', help='Show retained, retired, expired and unavailable checks across contexts')
    for name in ('pin', 'unpin'):
        command = storage_commands.add_parser(name)
        command.add_argument('attempt')
        command.add_argument('--reason', required=True)
    group = storage_commands.add_parser('preview-group', help='Recompute allocation for an exact selected set')
    group.add_argument('selectors', nargs='+')
    bundle = storage_commands.add_parser('export-check', help='Export and verify complete local inspectable evidence')
    bundle.add_argument('attempt')
    bundle.add_argument('--destination', required=True, type=Path)
    verify = storage_commands.add_parser('verify-bundle')
    verify.add_argument('destination', type=Path)
    storage_commands.add_parser('reconcile-checks', help='Rebuild custody accounting and reconcile owned derived staging')
    retention = storage_commands.add_parser('retention', help='Inspect, preview or explicitly enable finite check history')
    retention.add_argument('operation', choices=['status', 'preview', 'configure', 'maintain'])
    retention.add_argument('--settings', type=json.loads, help='Complete editable retention settings JSON; omit confirmation for a disclosure preview')
    retention.add_argument('--confirm', help='Exact proposal ID after reviewing scope and permanent effects')

    runtime = commands.add_parser(
        "runtime", help="create a fresh profile-bound disposable runtime"
    )
    runtime_commands = runtime.add_subparsers(dest="runtime_action", required=True)
    runtime_create = runtime_commands.add_parser(
        "create", help="clone an audited profile template into managed storage"
    )
    runtime_create.add_argument(
        "--profile",
        dest="profile_name",
        required=True,
        help="explicit pack profile name; no pack is selected universally",
    )
    runtime_create.add_argument(
        "--runtime-template", "--template", dest="runtime_template", type=Path
    )
    runtime_create.add_argument("--label", required=True)
    runtime_create.add_argument("--seed", type=int)
    runtime_create.add_argument("--level-name", default="world")
    runtime_create.add_argument("--server-port", type=int)
    _preview_group(runtime_create)

    world = commands.add_parser(
        "world", help="snapshot and restore managed disposable worlds"
    )
    world_commands = world.add_subparsers(dest="world_action", required=True)
    world_snapshot = world_commands.add_parser(
        "snapshot", help="copy one managed world into an immutable snapshot"
    )
    world_snapshot.add_argument("selector")
    world_snapshot.add_argument("--label", required=True)
    world_snapshot.add_argument(
        "--world",
        help="select one named world when the managed runtime contains more than one",
    )
    _preview_group(world_snapshot)

    world_restore = world_commands.add_parser(
        "restore", help="restore a snapshot only into a fresh managed world path"
    )
    world_restore.add_argument("snapshot_selector")
    world_restore.add_argument(
        "--profile",
        dest="profile_name",
        required=True,
        help="explicit pack profile for the new disposable runtime",
    )
    world_restore.add_argument("--label", required=True)
    world_restore.add_argument(
        "--runtime-template", "--template", dest="runtime_template", type=Path
    )
    world_restore.add_argument("--level-name")
    world_restore.add_argument("--server-port", type=int)
    _preview_group(world_restore, include_apply=True)

    return parser


def _emit(value: Mapping[str, Any], *, as_json: bool, inventory: bool = False) -> None:
    if as_json:
        print(json.dumps(value, indent=2, sort_keys=True))
        return
    rendered = (
        manager.render_inventory(value)
        if inventory
        else manager.render_operation(value)
    )
    sys.stdout.write(rendered)
    if rendered and not rendered.endswith("\n"):
        sys.stdout.write("\n")


def _render_item(item: Mapping[str, Any]) -> str:
    item_id = item.get("item_id", "unknown")
    lines = [f"Storage item: {item_id}"]
    for label, key in (
        ("Resource", "resource_id"),
        ("Kind", "kind"),
        ("Path", "relative_path"),
    ):
        value = item.get(key)
        if value is not None:
            lines.append(f"{label}: {value}")
    for label, key in (
        ("Custody", "custody"),
        ("Size", "size"),
        ("Last use", "last_use"),
        ("Reproducibility", "reproducibility"),
        ("Deletion", "deletion"),
    ):
        value = item.get(key)
        if isinstance(value, Mapping):
            lines.append(f"{label}: {json.dumps(value, sort_keys=True)}")
    problems = item.get("problems")
    if isinstance(problems, list):
        lines.append(f"Problems: {len(problems)}")
    return "\n".join(lines) + "\n"


def _preview_status(plan: Mapping[str, Any]) -> int:
    return 1 if plan.get("status") == "blocked" else 0


def _require_unchanged(
    preview: Mapping[str, Any], fresh: Mapping[str, Any]
) -> Mapping[str, Any]:
    if fresh.get("plan_id") != preview.get("plan_id"):
        raise manager.RuntimeManagerError(
            "runtime-manager inputs changed after preview; review a fresh plan"
        )
    return fresh


def run(
    argv: Sequence[str],
    *,
    root: Path,
    workspace_root: Path | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv))
    authority_root = root.expanduser().resolve()
    configured_workspace = os.environ.get("WORKBENCH_WORKSPACE")
    root = (
        workspace_root.expanduser().resolve()
        if workspace_root is not None
        else (
            Path(configured_workspace).expanduser().resolve()
            if configured_workspace
            else authority_root
        )
    )

    if args.command == 'storage':
        from .. import check_lifecycle as checks
        if args.checks and args.checks_root:
            parser.error('select default check storage or one explicit historical store')
        if args.checks:
            from workbench_api.state_paths import default_product_spine_state_root
            root = default_product_spine_state_root(authority_root) / 'developer-checks'
        elif args.checks_root:
            root = args.checks_root.expanduser().absolute()
        action = args.storage_action
        if action == 'retention':
            from .. import check_retention
            result = check_retention.dispatch(root, args.operation, settings=args.settings, confirmation=args.confirm)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0 if result.get('state') not in {'deferred', 'deferred-capacity'} else 1
        if action in {'history', 'pin', 'unpin', 'preview-group', 'export-check', 'verify-bundle', 'reconcile-checks'}:
            if action == 'history':
                result = checks.overview(root)
            elif action in {'pin', 'unpin'}:
                result = checks.pin(root, args.attempt, args.reason, remove=action == 'unpin')
            elif action == 'preview-group':
                result = checks.group_preview(root, args.selectors)
            elif action == 'export-check':
                result = checks.export_bundle(root, args.attempt, args.destination)
            elif action == 'verify-bundle':
                result = checks.verify_bundle(args.destination)
            else:
                result = checks.reconcile(root)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0

    if args.command == "storage" and args.storage_action == "list":
        inventory = manager.inventory_storage(root)
        _emit(inventory, as_json=args.json, inventory=True)
        return 0

    if args.command == "storage" and args.storage_action == "inspect":
        inventory = manager.inventory_storage(root)
        item = manager.resolve_inventory_item(inventory, args.selector)
        if args.json:
            print(json.dumps(item, indent=2, sort_keys=True))
        else:
            sys.stdout.write(_render_item(item))
        return 0

    if args.command == "runtime" and args.runtime_action == "create":
        plan = manager.plan_runtime_create(
            root,
            authority_root=authority_root,
            profile_name=args.profile_name,
            runtime_template=args.runtime_template,
            label=args.label,
            seed=args.seed,
            level_name=args.level_name,
            server_port=args.server_port,
        )
        if args.show or args.json:
            _emit(plan, as_json=args.json)
            return _preview_status(plan)
        plan = _require_unchanged(
            plan,
            manager.plan_runtime_create(
                root,
                authority_root=authority_root,
                profile_name=args.profile_name,
                runtime_template=args.runtime_template,
                label=args.label,
                seed=plan["parameters"]["seed"],
                level_name=plan["parameters"]["level_name"],
                server_port=plan["parameters"]["server_port"],
                now=plan["created_at"],
            ),
        )
        result = manager.execute_runtime_create(
            root, plan, authority_root=authority_root
        )
        _emit(result, as_json=False)
        return 0

    if args.command == "world" and args.world_action == "snapshot":
        plan = manager.plan_world_snapshot(
            root, selector=args.selector, label=args.label, world=args.world
        )
        if args.show or args.json:
            _emit(plan, as_json=args.json)
            return _preview_status(plan)
        plan = _require_unchanged(
            plan,
            manager.plan_world_snapshot(
                root,
                selector=args.selector,
                label=args.label,
                world=args.world,
                now=plan["created_at"],
            ),
        )
        result = manager.execute_world_snapshot(root, plan)
        _emit(result, as_json=False)
        return 0

    if args.command == "world" and args.world_action == "restore":
        plan = manager.plan_world_restore(
            root,
            authority_root=authority_root,
            snapshot_selector=args.snapshot_selector,
            profile_name=args.profile_name,
            label=args.label,
            runtime_template=args.runtime_template,
            level_name=args.level_name,
            server_port=args.server_port,
        )
        if not args.apply:
            _emit(plan, as_json=args.json)
            return _preview_status(plan)
        plan = _require_unchanged(
            plan,
            manager.plan_world_restore(
                root,
                authority_root=authority_root,
                snapshot_selector=args.snapshot_selector,
                profile_name=args.profile_name,
                label=args.label,
                runtime_template=args.runtime_template,
                level_name=plan["parameters"]["level_name"],
                server_port=plan["parameters"]["server_port"],
                now=plan["created_at"],
            ),
        )
        result = manager.execute_world_restore(
            root, plan, authority_root=authority_root
        )
        _emit(result, as_json=False)
        return 0

    if args.command == "storage" and args.storage_action == "cleanup":
        plan = manager.plan_cleanup(
            root, selector=args.selector, allow_review=args.allow_review
        )
        if not args.apply:
            _emit(plan, as_json=args.json)
            return _preview_status(plan)
        plan = _require_unchanged(
            plan,
            manager.plan_cleanup(
                root,
                selector=args.selector,
                allow_review=args.allow_review,
                now=plan["created_at"],
            ),
        )
        result = manager.execute_cleanup(root, plan)
        _emit(result, as_json=False)
        return 0

    if args.command == "storage" and args.storage_action == "restore":
        plan = manager.plan_restore_trash(root, selector=args.selector)
        if not args.apply:
            _emit(plan, as_json=args.json)
            return _preview_status(plan)
        plan = _require_unchanged(
            plan,
            manager.plan_restore_trash(
                root, selector=args.selector, now=plan["created_at"]
            ),
        )
        result = manager.execute_restore_trash(root, plan)
        _emit(result, as_json=False)
        return 0

    if args.command == "storage" and args.storage_action == "purge":
        plan = manager.plan_purge_trash(
            root, selector=args.selector, confirmation=args.confirm
        )
        if args.show or args.json or not args.confirm or plan.get("status") != "ready":
            _emit(plan, as_json=args.json)
            return _preview_status(plan)
        plan = _require_unchanged(
            plan,
            manager.plan_purge_trash(
                root,
                selector=args.selector,
                confirmation=args.confirm,
                now=plan["created_at"],
            ),
        )
        result = manager.execute_purge_trash(root, plan)
        _emit(result, as_json=False)
        return 0

    parser.error("unsupported runtime manager command")
    return 2


def main(
    argv: Sequence[str] | None = None,
    *,
    root: Path | None = None,
    workspace_root: Path | None = None,
) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    workbench_root = (
        root.expanduser().resolve()
        if root is not None
        else Path(__file__).resolve().parents[4]
    )
    try:
        return run(
            arguments,
            root=workbench_root,
            workspace_root=workspace_root,
        )
    except (OSError, ValueError, manager.RuntimeManagerError) as exc:
        print(f"Runtime manager failed: {exc}", file=sys.stderr)
        return 2


__all__ = ["build_parser", "main", "run"]


if __name__ == "__main__":
    raise SystemExit(main())
