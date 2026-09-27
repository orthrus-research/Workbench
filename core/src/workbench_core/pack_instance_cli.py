"""Core command surface for a selected Supersymmetry Prism instance source.

Textual collects paths and decisions. This adapter sends them to Core custody
and persists only the selected source identity in the user's dotfile.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
from typing import Sequence

from workbench_api.profiles import profile_resources
from workbench_api.resources import repository_root
from workbench_api.state_paths import default_runtime_state_root
from workbench_api.modules import EnvironmentSelection

from .artifact_store import fetch_verified_artifact
from .configuration import default_client_configuration_path
from .durable_records import read_private_single_link_bytes
from .environment_resolution import resolve_environment
from .managed_java import CoreManagedJava
from .pack_instance_choice import load_pack_instance_choice, save_pack_instance_choice
from .user_config_home import default_user_config_home
from .user_preferences import load_workspaces, resolve_expression


def _source(archive: Path | None, action: str, state_root: Path, config_home: Path,
            expected_plan_id: str | None) -> dict:
    if action == "official-reopen":
        from .pack_release_curseforge_composition import (
            reopen_curseforge_client_composition_by_plan_id,
        )
        assert expected_plan_id is not None
        return reopen_curseforge_client_composition_by_plan_id(
            expected_plan_id=expected_plan_id, state_root=state_root,
            config_home=config_home,
        )
    from .pack_release_prism_zip_composition import (
        apply_prism_zip_composition, plan_prism_zip_composition,
        reopen_prism_zip_composition,
    )
    if action == "zip-plan":
        assert archive is not None
        return plan_prism_zip_composition(archive, state_root=state_root,
                                          config_home=config_home)
    if action == "zip-import":
        assert archive is not None and expected_plan_id is not None
        return apply_prism_zip_composition(
            archive, state_root=state_root, config_home=config_home,
            expected_plan_id=expected_plan_id,
        )
    assert expected_plan_id is not None
    return reopen_prism_zip_composition(
        state_root=state_root, config_home=config_home,
        expected_plan_id=expected_plan_id,
    )


def _zip_stage(archive: Path | None, action: str, state_root: Path,
               expected_plan_id: str | None) -> dict:
    from .pack_release_prism_zip_stage import (
        apply_prism_zip_stage, plan_prism_zip_stage, reopen_prism_zip_stage,
    )
    if action == "zip-stage-plan":
        assert archive is not None
        operation = plan_prism_zip_stage(archive, state_root=state_root)
    elif action == "zip-stage-apply":
        assert archive is not None and expected_plan_id is not None
        operation = apply_prism_zip_stage(
            archive, state_root=state_root, expected_plan_id=expected_plan_id,
        )
    else:
        assert expected_plan_id is not None
        operation = reopen_prism_zip_stage(
            state_root=state_root, expected_plan_id=expected_plan_id,
        )
    return {"schema": "workbench.pack-instance.v1", "action": action,
            "stage": operation}


def _fresh_service(state_root: Path, config_home: Path, optional_mode: str):
    from .pack_release import PackReleaseService, load_authority
    from .pack_release_fresh_setup import OfficialFreshReleaseService
    resources = {
        role: profile_resources(role).get("supersymmetry")
        for role in ("release-authority", "release-client-layout-policy",
                     "release-resourcepack-input-policy")
    }
    if any(value is None for value in resources.values()):
        raise ValueError("Supersymmetry release setup profile is unavailable")
    authority_path = resources["release-authority"]
    assert authority_path is not None
    release = PackReleaseService(
        load_authority(authority_path), config_home=config_home,
        state_root=state_root,
    )
    optional = None if optional_mode == "default" else ()
    return OfficialFreshReleaseService(
        release, authority_path=authority_path,
        layout_policy_path=resources["release-client-layout-policy"],
        resourcepack_policy_path=resources["release-resourcepack-input-policy"],
        optional_selected=optional,
        credential_provider=lambda: os.environ.get("WORKBENCH_CURSEFORGE_API_KEY"),
    )


def _fresh(action: str, *, state_root: Path, config_home: Path,
           optional_mode: str, project_id: int | None, file_id: int | None,
           override_plan_id: str | None) -> dict:
    service = _fresh_service(state_root, config_home, optional_mode)
    if action == "fresh-status":
        detail = service.status()
    elif action == "fresh-overrides":
        detail = service.retain_overrides()
    elif action == "fresh-file":
        assert project_id is not None and file_id is not None
        detail = service.acquire_file(project_id, file_id)
    else:
        assert override_plan_id is not None
        detail = service.publish(override_plan_id=override_plan_id)
    return {"schema": "workbench.pack-instance.v1", "action": action,
            "fresh": detail}


def _file_pair(value: str) -> tuple[int, int]:
    if re.fullmatch(r"[1-9][0-9]*:[1-9][0-9]*", value) is None:
        raise argparse.ArgumentTypeError("file pair must be PROJECT:FILE")
    project_id, file_id = (int(part) for part in value.split(":"))
    if project_id >= 2**63 or file_id >= 2**63:
        raise argparse.ArgumentTypeError("file pair IDs exceed their supported range")
    return project_id, file_id


def _fresh_policy(action: str, *, state_root: Path, config_home: Path,
                  resourcepack_pairs: tuple[tuple[int, int], ...],
                  optional_pairs: tuple[tuple[int, int], ...],
                  expected_policy_plan_id: str | None) -> dict:
    service = _fresh_service(state_root, config_home, "default")
    if action == "fresh-policy-status":
        detail = service.policy_status()
    elif action == "fresh-policy-reopen":
        detail = service.reopen_pending_policy()
    elif action == "fresh-policy-plan":
        detail = service.plan_policy_review(
            resourcepack_pairs=resourcepack_pairs,
            optional_selected=optional_pairs,
        )
    else:
        assert expected_policy_plan_id is not None
        detail = service.apply_policy_review(
            resourcepack_pairs=resourcepack_pairs,
            optional_selected=optional_pairs,
            expected_plan_id=expected_policy_plan_id,
        )
    return {"schema": "workbench.pack-instance.v1", "action": action,
            "policy": detail}

def _install_resources(*, require_bootstrap: bool = True) -> tuple[Path, dict | None]:
    policies = profile_resources("release-client-install-policy")
    if "supersymmetry" not in policies:
        raise ValueError("Supersymmetry installation profile is unavailable")
    from .pack_release_client_install import load_release_install_policy
    policy = load_release_install_policy(policies["supersymmetry"])
    if not require_bootstrap:
        return policies["supersymmetry"], None
    toolchains = profile_resources("runtime-toolchain")
    if "cleanroom" not in toolchains:
        raise ValueError("Cleanroom installation profile is unavailable")
    try:
        runtime = json.loads(toolchains["cleanroom"].read_text(encoding="utf-8"))
        bootstrap = runtime["bootstrap"]
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        raise ValueError("Cleanroom runtime toolchain is unavailable") from exc
    if (type(runtime) is not dict
            or runtime.get("format") != "workbench-cleanroom-native-runtime-lock-v1"
            or runtime.get("cleanroom_version") != policy["cleanroom_version"]
            or type(bootstrap) is not dict
            or bootstrap.get("sha256") != policy["cleanroom_client"]["sha256"].removeprefix("sha256:")
            or not isinstance(bootstrap.get("url"), str)
            or not bootstrap["url"].startswith("https://")):
        raise ValueError("Cleanroom bootstrap does not match the install policy")
    return policies["supersymmetry"], {
        "url": bootstrap["url"], "sha256": bootstrap["sha256"],
        "size": policy["cleanroom_client"]["size"],
    }


def _selected_install_context(state_root: Path, config_home: Path,
                              suite_root: Path) -> tuple[dict, dict, Path, Path, EnvironmentSelection]:
    choice = load_pack_instance_choice(config_home)
    if (choice["source_kind"] not in {"user-prism-zip", "published-release"}
            or choice["source_plan_id"] is None
            or choice["launcher_root"] is None
            or choice["workspace_name"] is None):
        raise ValueError("select a retained Supersymmetry source, Prism folder, and workspace")
    source = _source(None, "zip-reopen" if choice["source_kind"] == "user-prism-zip"
                     else "official-reopen", state_root, config_home,
                     choice["source_plan_id"])
    workspaces = load_workspaces()
    entry = next((row for row in workspaces["entries"]
                  if row["name"] == choice["workspace_name"]), None)
    if entry is None:
        raise ValueError("selected workspace is no longer registered")
    environment = dict(os.environ)
    environment.pop("WORKBENCH_WORKSPACE", None)
    environment.pop("WORKBENCH_JAVA_HOME", None)
    resolved = resolve_environment(
        suite_root,
        workspace=resolve_expression(entry["path"], environment=environment),
        environment=environment,
    )
    if (resolved.record["workspaces"]["record_id"] != workspaces["record_id"]
            or resolved.operation_selection().workspace_name != entry["name"]):
        raise ValueError("selected workspace changed after review")
    operation = resolved.operation_selection()
    return choice, source, Path(choice["launcher_root"]), resolved.state_root, operation


def _root(action: str, *, state_root: Path, config_home: Path,
          expected_plan_id: str | None) -> dict:
    from .pack_release_client_install import (
        abandon_interrupted_prism_data_root, plan_prism_data_root,
        reconcile_prism_data_root,
    )
    choice = load_pack_instance_choice(config_home)
    if choice["launcher_root"] is None:
        raise ValueError("select a Prism data folder before recovery review")
    launcher = Path(choice["launcher_root"])
    if action == "root-plan":
        operation = plan_prism_data_root(launcher, state_root=state_root)
    else:
        if expected_plan_id is None:
            raise ValueError("select the exact reviewed Prism data folder plan")
        operation = {
            "root-reconcile": reconcile_prism_data_root,
            "root-abandon": abandon_interrupted_prism_data_root,
        }[action](launcher, state_root=state_root, expected_plan_id=expected_plan_id)
    return {"schema": "workbench.pack-instance.v1", "action": action,
            "prism_root": operation}


def _install(action: str, *, state_root: Path, config_home: Path,
             suite_root: Path, expected_plan_id: str | None) -> dict:
    from .pack_release_client_install import (
        abandon_interrupted_release_client_install,
        apply_release_client_install, plan_release_client_install,
        reopen_release_client_install, reconcile_release_client_install,
        plan_prism_data_root, initialize_prism_data_root,
    )
    from .tooling_provision import prepare_prism_launcher
    if (not (suite_root / "workbench.toml").is_file()
            and not (suite_root / "core/pyproject.toml").is_file()):
        suite_root = repository_root(__file__)
    choice, source, launcher, java_state_root, selection = _selected_install_context(
        state_root, config_home, suite_root,
    )
    config_path = selection.profile_configuration or default_client_configuration_path(suite_root)
    platform = source.get("source_platform")
    if (action in {"install-prepare", "install-apply", "install-reconcile"}
            and choice.get("source_kind") == "user-prism-zip"
            and isinstance(platform, dict) and platform.get("kind") == "forge"
            and selection.java_home is None
            and selection.managed_java_feature is None):
        from .runtime_java import load_java_runtime_policy
        implicit_feature = load_java_runtime_policy(
            suite_root, config_path=config_path,
        )["feature_version"]
        if implicit_feature != 8:
            raise ValueError(
                "Imported Forge needs a Java choice before installation. "
                "Choose Change Java choice in Textual and select managed Java 8 "
                "or a custom Java path."
            )
    policy_path, bootstrap = _install_resources(
        require_bootstrap=choice.get("source_kind") != "user-prism-zip",
    )
    if action == "install-prepare":
        root_plan = plan_prism_data_root(launcher, state_root=state_root)
        if root_plan["action"] == "reconcile":
            raise ValueError("Prism data folder needs a recovery choice: "
                             + root_plan["plan_id"])
        elif root_plan["state"] != "ready":
            raise ValueError("Prism data folder needs review: " + ", ".join(root_plan["blockers"]))
        elif root_plan["action"] == "initialize":
            initialize_prism_data_root(
                launcher, state_root=state_root, expected_plan_id=root_plan["plan_id"],
            )
        prepare_prism_launcher(state_root)
        if bootstrap is not None:
            fetch_verified_artifact(
                url=bootstrap["url"], expected_sha256=bootstrap["sha256"],
                expected_size=bootstrap["size"], state_root=state_root,
                label="Cleanroom client bootstrap", timeout_seconds=60,
            )
    java = CoreManagedJava(state_root=java_state_root, selection=selection).ensure(
        suite_root, config_path=config_path,
    )
    arguments = {
        "policy_path": policy_path, "state_root": state_root,
        "config_home": config_home, "launcher_root": launcher,
        "managed_java_result": java,
    }
    if action == "install-prepare":
        operation = plan_release_client_install(source, **arguments)
    else:
        if expected_plan_id is None:
            raise ValueError("select the exact reviewed release installation")
        operation = {
            "install-apply": apply_release_client_install,
            "install-reopen": reopen_release_client_install,
            "install-reconcile": reconcile_release_client_install,
            "install-abandon": abandon_interrupted_release_client_install,
        }[action](source, expected_plan_id=expected_plan_id, **arguments)
    return {"schema": "workbench.pack-instance.v1", "action": action,
            "source_plan_id": choice["source_plan_id"], "installation": operation}


def _launch(action: str, *, state_root: Path, config_home: Path,
            expected_install_plan_id: str, expected_launch_plan_id: str | None,
            mode: str) -> dict:
    from .pack_release_client_launch import (
        plan_release_client_launch, run_release_client_launch,
    )
    choice = load_pack_instance_choice(config_home)
    if (choice["source_kind"] not in {"user-prism-zip", "published-release"}
            or choice["source_plan_id"] is None or choice["launcher_root"] is None):
        raise ValueError("select a retained Supersymmetry source and Prism folder")
    source = _source(None, "zip-reopen" if choice["source_kind"] == "user-prism-zip"
                     else "official-reopen", state_root, config_home,
                     choice["source_plan_id"])
    launcher = Path(choice["launcher_root"])
    receipt_path = (state_root / "pack-release-client-installs" /
                    expected_install_plan_id.rsplit(":", 1)[-1] / "receipt.json")
    receipt = json.loads(read_private_single_link_bytes(receipt_path, byte_limit=16384))
    if (type(receipt) is not dict
            or receipt.get("composition_tree_id") != source["tree_id"]
            or receipt.get("source_kind") != (
                "user-prism-zip" if choice["source_kind"] == "user-prism-zip"
                else "official-release-curseforge")):
        raise ValueError("installed instance differs from the saved source choice")
    arguments = {"state_root": state_root, "launcher_root": launcher,
                 "expected_install_plan_id": expected_install_plan_id, "mode": mode}
    operation = (plan_release_client_launch(**arguments) if action == "launch-plan"
                 else run_release_client_launch(
                     **arguments, expected_launch_plan_id=expected_launch_plan_id,
                 ))
    return {"schema": "workbench.pack-instance.v1", "action": action,
            "source_plan_id": choice["source_plan_id"], "launch": operation}


def _installed(state_root: Path, config_home: Path) -> dict:
    from .pack_release_client_launch import plan_release_client_launch
    choice = load_pack_instance_choice(config_home)
    if choice["source_plan_id"] is None or choice["launcher_root"] is None:
        return {"schema": "workbench.pack-instance.v1", "action": "install-status",
                "installations": [], "count": 0}
    source = _source(None, "zip-reopen" if choice["source_kind"] == "user-prism-zip"
                     else "official-reopen", state_root, config_home,
                     choice["source_plan_id"])
    root = state_root / "pack-release-client-installs"
    selected: list[dict] = []
    if root.is_dir() and not root.is_symlink():
        for index, entry in enumerate(root.iterdir()):
            if index >= 1000:
                break
            if (len(selected) >= 100 or not entry.is_dir() or entry.is_symlink()
                    or re.fullmatch(r"[0-9a-f]{64}", entry.name) is None):
                continue
            try:
                receipt = json.loads(read_private_single_link_bytes(
                    entry / "receipt.json", byte_limit=16384,
                ))
                if (type(receipt) is not dict
                        or receipt.get("composition_tree_id") != source["tree_id"]):
                    continue
                plan_id = "workbench-pack-release-client-install-plan:sha256:" + entry.name
                if receipt.get("plan_id") != plan_id:
                    continue
                launch = plan_release_client_launch(
                    state_root=state_root, launcher_root=Path(choice["launcher_root"]),
                    expected_install_plan_id=plan_id, mode="show",
                )
                selected.append({
                    "plan_id": plan_id, "instance_path": launch["instance_path"],
                    "java_selection_state": receipt.get("java_selection_state"),
                    "java_selected_feature": receipt.get("java_selected_feature"),
                })
            except (OSError, ValueError, TypeError, KeyError):
                continue
    selected.sort(key=lambda row: row["instance_path"])
    return {"schema": "workbench.pack-instance.v1", "action": "install-status",
            "installations": selected, "count": len(selected)}


def main(argv: Sequence[str] | None = None, *, suite_root: Path | None = None) -> int:
    parser = argparse.ArgumentParser(prog="workbench pack instance")
    parser.add_argument("action", choices=("choice-show", "choice-select", "zip-plan",
                                           "zip-import", "zip-reopen", "zip-stage-plan",
                                           "zip-stage-apply", "zip-stage-reopen", "fresh-status",
                                           "fresh-overrides", "fresh-file", "fresh-publish",
                                           "fresh-policy-status", "fresh-policy-plan",
                                           "fresh-policy-apply", "fresh-policy-reopen",
                                           "root-plan", "root-reconcile", "root-abandon",
                                           "install-prepare",
                                           "install-apply", "install-reopen",
                                           "install-reconcile", "install-abandon",
                                           "install-status",
                                           "launch-plan", "launch-run"))
    parser.add_argument("--profile", required=True, choices=("supersymmetry",))
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--source-plan-id")
    parser.add_argument("--expected-plan-id")
    parser.add_argument("--expected-record-id")
    parser.add_argument("--expected-root-plan-id")
    parser.add_argument("--expected-install-plan-id")
    parser.add_argument("--expected-launch-plan-id")
    parser.add_argument("--launch-mode", choices=("show", "launch"))
    parser.add_argument("--launcher-root", type=Path)
    parser.add_argument("--workspace-name")
    parser.add_argument("--source-kind", choices=("user-prism-zip", "published-release"))
    parser.add_argument("--optional-mode", choices=("default", "omit"), default="default")
    parser.add_argument("--project-id", type=int)
    parser.add_argument("--file-id", type=int)
    parser.add_argument("--override-plan-id")
    parser.add_argument("--resourcepack-pair", type=_file_pair, action="append", default=[])
    parser.add_argument("--optional-pair", type=_file_pair, action="append", default=[])
    parser.add_argument("--expected-policy-plan-id")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.action in {"zip-plan", "zip-import", "zip-stage-plan", "zip-stage-apply"}:
        if args.archive is None or not args.archive.is_absolute():
            parser.error("ZIP review, staging, and import require an absolute archive path")
    elif args.archive is not None:
        parser.error("only ZIP review, staging, and import accept an archive path")
    if (args.action in {"zip-import", "zip-stage-apply", "zip-stage-reopen"}) != (args.expected_plan_id is not None):
        parser.error("ZIP import and staging actions require --expected-plan-id")
    if args.action in {"zip-reopen", "choice-select"} and not args.source_plan_id:
        parser.error("this action requires --source-plan-id")
    if args.action not in {"zip-reopen", "choice-select"} and args.source_plan_id is not None:
        parser.error("--source-plan-id is only accepted by reopen and choice selection")
    if args.action == "choice-select":
        if not args.expected_record_id:
            parser.error("choice selection requires --expected-record-id")
        if args.launcher_root is not None and not args.launcher_root.is_absolute():
            parser.error("Prism launcher root must be an absolute path")
    elif any((args.expected_record_id, args.launcher_root, args.workspace_name)):
        parser.error("choice fields are only accepted by choice-select")
    if args.action != "choice-select" and args.source_kind is not None:
        parser.error("source kind is only accepted by choice selection")
    if args.action == "fresh-file":
        if args.project_id is None or args.file_id is None:
            parser.error("fresh file acquisition needs exact project and file IDs")
    elif args.project_id is not None or args.file_id is not None:
        parser.error("project and file IDs are only accepted by fresh-file")
    if args.action == "fresh-publish":
        if args.override_plan_id is None:
            parser.error("fresh publication needs an exact override plan ID")
    elif args.override_plan_id is not None:
        parser.error("override plan ID is only accepted by fresh-publish")
    if (args.action not in {"fresh-status", "fresh-overrides", "fresh-file",
                            "fresh-publish"} and args.optional_mode != "default"):
        parser.error("optional file selection is only accepted by fresh acquisition actions")
    if args.action in {"fresh-policy-plan", "fresh-policy-apply"}:
        if not args.resourcepack_pair:
            parser.error("policy review needs at least one resource-pack file pair")
    elif args.resourcepack_pair or args.optional_pair:
        parser.error("policy file pairs are only accepted by review and apply")
    if (args.action == "fresh-policy-apply") != (args.expected_policy_plan_id is not None):
        parser.error("policy apply requires --expected-policy-plan-id; other actions do not accept it")
    if args.action in {"root-reconcile", "root-abandon"}:
        if args.expected_root_plan_id is None:
            parser.error("root recovery requires --expected-root-plan-id")
    elif args.expected_root_plan_id is not None:
        parser.error("only root recovery accepts --expected-root-plan-id")
    if (args.action in {"install-apply", "install-reopen", "install-reconcile",
                        "install-abandon"}
            and args.expected_install_plan_id is None):
        parser.error("install action requires --expected-install-plan-id")
    if (args.action not in {"install-apply", "install-reopen", "install-reconcile",
                            "install-abandon"}
            and args.action not in {"launch-plan", "launch-run"}
            and args.expected_install_plan_id is not None):
        parser.error("only exact install and launch actions accept --expected-install-plan-id")
    if args.action in {"launch-plan", "launch-run"}:
        if args.expected_install_plan_id is None or args.launch_mode is None:
            parser.error("launch requires an exact install plan and mode")
        if (args.action == "launch-run") != (args.expected_launch_plan_id is not None):
            parser.error("launch-run requires --expected-launch-plan-id")
    elif args.expected_launch_plan_id is not None or args.launch_mode is not None:
        parser.error("launch fields are only accepted by launch actions")

    state_root = default_runtime_state_root()
    config_home = default_user_config_home()
    if args.action == "install-status":
        result = _installed(state_root, config_home)
    elif args.action.startswith("root-"):
        result = _root(
            args.action, state_root=state_root, config_home=config_home,
            expected_plan_id=args.expected_root_plan_id,
        )
    elif args.action.startswith("fresh-policy-"):
        result = _fresh_policy(
            args.action, state_root=state_root, config_home=config_home,
            resourcepack_pairs=tuple(args.resourcepack_pair),
            optional_pairs=tuple(args.optional_pair),
            expected_policy_plan_id=args.expected_policy_plan_id,
        )
    elif args.action.startswith("fresh-"):
        result = _fresh(
            args.action, state_root=state_root, config_home=config_home,
            optional_mode=args.optional_mode, project_id=args.project_id,
            file_id=args.file_id, override_plan_id=args.override_plan_id,
        )
    elif args.action.startswith("launch-"):
        result = _launch(
            args.action, state_root=state_root, config_home=config_home,
            expected_install_plan_id=args.expected_install_plan_id,
            expected_launch_plan_id=args.expected_launch_plan_id,
            mode=args.launch_mode,
        )
    elif args.action.startswith("install-"):
        result = _install(
            args.action, state_root=state_root, config_home=config_home,
            suite_root=Path.cwd() if suite_root is None else suite_root,
            expected_plan_id=args.expected_install_plan_id,
        )
    elif args.action.startswith("zip-stage-"):
        result = _zip_stage(args.archive, args.action, state_root,
                            args.expected_plan_id)
    elif args.action.startswith("zip-"):
        result = _source(
            args.archive, args.action, state_root, config_home,
            args.expected_plan_id if args.action == "zip-import" else args.source_plan_id,
        )
        result = {"schema": "workbench.pack-instance.v1", "action": args.action,
                  "source": result}
    elif args.action == "choice-show":
        choice = load_pack_instance_choice(config_home)
        source_state = "none"
        reason = None
        if choice["source_plan_id"] is not None:
            try:
                _source(None, "zip-reopen" if choice["source_kind"] == "user-prism-zip"
                        else "official-reopen", state_root, config_home,
                        choice["source_plan_id"])
                source_state = "retained"
            except (OSError, ValueError) as exc:
                source_state, reason = "unavailable", str(exc)
        result = {"schema": "workbench.pack-instance.v1", "action": "choice-show",
                  "choice": choice, "source_state": source_state, "reason": reason}
    else:
        kind = args.source_kind or "user-prism-zip"
        source = _source(None, "zip-reopen" if kind == "user-prism-zip" else
                         "official-reopen", state_root, config_home,
                         args.source_plan_id)
        choice = save_pack_instance_choice(
            config_home, source_kind=kind,
            source_plan_id=source["plan_id"],
            launcher_root=(str(args.launcher_root) if args.launcher_root is not None else None),
            workspace_name=args.workspace_name,
            expected_record_id=args.expected_record_id,
        )
        result = {"schema": "workbench.pack-instance.v1", "action": "choice-select",
                  "choice": choice, "source_state": "retained"}

    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print("Supersymmetry instance: " + result["action"].replace("-", " "))
        if result.get("source") is not None:
            print(f"  Source: {result['source']['plan_id']}")
        if result.get("fresh") is not None:
            print(f"  Fresh source: {result['fresh']['status']}")
        if result.get("policy") is not None:
            print(f"  Fresh policy: {result['policy']['status']}")
        if result.get("installation") is not None:
            print(f"  Install: {result['installation']['plan_id']}")
            print(f"  Destination: {result['installation'].get('instance_path') or 'retained stage'}")
        if result.get("prism_root") is not None:
            print(f"  Prism data folder: {result['prism_root']['plan_id']}")
        if result.get("launch") is not None:
            print(f"  Launch: {result['launch'].get('plan_id') or result['launch'].get('launch_plan_id')}")
        if result.get("installations") is not None:
            print(f"  Installed instances: {result['count']}")
        if result.get("choice") is not None:
            print(f"  Saved source: {result['choice']['source_plan_id'] or 'none'}")
            print(f"  Source state: {result['source_state']}")
            if result.get("reason"):
                print(f"  Reason: {result['reason']}")
    return 0 if result.get("source_state") != "unavailable" else 2


if __name__ == "__main__":
    raise SystemExit(main())
