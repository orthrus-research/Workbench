"""Identity-bearing base projection embedded by the current Workspace Home."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import shlex
import tomllib
from typing import Any, Mapping, Sequence

from workbench_project_intelligence import ProjectInspectionError
from workbench_project_intelligence.workspace_doctor import new_report

from .bootstrap import inspect_project


HOME_FORMAT = "workbench-workspace-home-v1"
HOME_SCHEMA_VERSION = 1
HOME_ACTION_LIMIT = 5


class WorkspaceHomeError(RuntimeError):
    """Raised when a truthful workspace Home cannot be produced."""


def _problem_summary(problems: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    blockers = sum(row.get("severity") == "blocker" for row in problems)
    warnings = sum(row.get("severity") == "warning" for row in problems)
    information = sum(row.get("severity") == "info" for row in problems)
    return {
        "status": (
            "blocked"
            if blockers
            else "attention"
            if warnings or information
            else "ready"
        ),
        "blockers": blockers,
        "warnings": warnings,
        "information": information,
    }


def _pack_manifest_probe(
    workspace: Path,
) -> tuple[dict[str, Any] | None, str | None]:
    manifest = workspace / "pack.toml"
    if not manifest.exists() and not manifest.is_symlink():
        return None, None
    if manifest.is_symlink():
        return None, "Packwiz manifest is a symlink and was not trusted"
    if not manifest.is_file():
        return None, "Packwiz manifest is not a regular file"
    try:
        if manifest.stat().st_size > 2 * 1024 * 1024:
            return None, "Packwiz manifest exceeds the 2 MiB inspection bound"
        value = tomllib.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        return None, f"Packwiz manifest is unreadable or malformed: {exc}"
    if not isinstance(value, dict):
        return None, "Packwiz manifest must be an object"
    name = value.get("name")
    if not isinstance(name, str) or not name:
        return None, "Packwiz manifest has no nonempty name"
    pack_format = value.get("pack-format")
    if not isinstance(pack_format, str) or not pack_format.startswith("packwiz:"):
        return None, "Packwiz manifest has no supported pack-format"
    versions = value.get("versions")
    if not isinstance(versions, dict):
        return None, "Packwiz manifest has no versions table"
    minecraft_version = versions.get("minecraft")
    if not isinstance(minecraft_version, str) or not minecraft_version:
        return None, "Packwiz manifest has no nonempty Minecraft version"
    loaders = [
        {"id": key, "version": version}
        for key, version in sorted(versions.items())
        if key != "minecraft"
        and isinstance(key, str)
        and key
        and isinstance(version, str)
        and version
    ]
    if not loaders:
        return None, "Packwiz manifest has no nonempty loader declaration"
    record = {
        "name": name,
        "pack_format": pack_format,
        "minecraft_version": minecraft_version,
        "loaders": loaders,
    }
    index = value.get("index")
    if not isinstance(index, dict):
        return None, "Packwiz manifest has no index table"
    index_file = index.get("file")
    hash_format = index.get("hash-format")
    declared_hash = index.get("hash")
    if (
        not isinstance(index_file, str)
        or not index_file
        or hash_format != "sha256"
        or not isinstance(declared_hash, str)
        or len(declared_hash) != 64
        or any(character not in "0123456789abcdef" for character in declared_hash)
    ):
        return None, "Packwiz index declaration is incomplete or unsupported"
    relative_index = Path(index_file)
    if relative_index.is_absolute():
        return record, "Packwiz index path must be relative"
    raw_index_path = workspace / relative_index
    if raw_index_path.is_symlink():
        return record, f"Packwiz index is a symlink and was not trusted: {raw_index_path}"
    index_path = raw_index_path.resolve()
    if not index_path.is_relative_to(workspace):
        return record, "Packwiz index path escapes the workspace"
    if index_path.is_symlink() or not index_path.is_file():
        return record, f"Packwiz index is not a bounded regular file: {index_path}"
    try:
        if index_path.stat().st_size > 64 * 1024 * 1024:
            return record, "Packwiz index exceeds the 64 MiB Home inspection bound"
        actual_hash = sha256(index_path.read_bytes()).hexdigest()
    except OSError as exc:
        return record, f"Packwiz index could not be read: {exc}"
    record["index"] = {
        "file": index_file,
        "hash_format": hash_format,
        "declared_hash": declared_hash,
        "actual_sha256": actual_hash,
        "matches_declared_hash": actual_hash == declared_hash,
    }
    if actual_hash != declared_hash:
        return record, (
            "Packwiz index does not match its declared SHA-256: "
            f"declared {declared_hash}; actual {actual_hash}"
        )
    return record, None


def _action(
    action_id: str,
    title: str,
    purpose: str,
    argv: Sequence[str] | None,
    *,
    blockers: Sequence[str] = (),
    unavailable_reason: str | None = None,
) -> dict[str, Any]:
    available = argv is not None and not blockers
    return {
        "id": action_id,
        "title": title,
        "purpose": purpose,
        "available": available,
        "argv": list(argv) if available else None,
        "blockers": list(blockers),
        "unavailable_reason": None if available else unavailable_reason,
    }


def _exact_flow_preflights(suite: Path, workspace: Path) -> dict[str, str | None]:
    """Ask the current owners whether the two flagship read paths can run."""

    results: dict[str, str | None] = {
        "change-recipe": None,
        "understand-recipes": None,
    }
    try:
        from workbench_blueprints.profile_construction import recipe_change_authority

        recipe_change_authority("supersymmetry").recipe_change_options(suite, workspace)
    except (ImportError, OSError, UnicodeError, ValueError) as exc:
        results["change-recipe"] = str(exc)
    try:
        from workbench_atlas_recipe_health import (
            discover_recipe_health_operational_context,
        )

        discover_recipe_health_operational_context(workspace)
    except (ImportError, OSError, UnicodeError, ValueError) as exc:
        results["understand-recipes"] = str(exc)
    return results


def _has_searchable_project_surface(
    workspace: Path, surfaces: Mapping[str, Any]
) -> bool:
    """Return true only when Doctor's source roots contain a regular file."""

    for key in ("source", "resources", "groovy", "configuration"):
        relatives = surfaces.get(key)
        if not isinstance(relatives, list):
            continue
        for relative in relatives:
            if not isinstance(relative, str):
                continue
            root = (workspace / relative).resolve()
            if not root.is_relative_to(workspace) or not root.is_dir() or root.is_symlink():
                continue
            try:
                if any(
                    candidate.is_file() and not candidate.is_symlink()
                    for candidate in root.rglob("*")
                ):
                    return True
            except OSError:
                continue
    return False


def _workspace_kind(
    doctor: Mapping[str, Any],
    exact_context: Mapping[str, Any] | None,
    pack_manifest: Mapping[str, Any] | None,
) -> str:
    if exact_context is not None:
        return "supersymmetry-pack"
    if pack_manifest is not None:
        return "packwiz-pack"
    target = doctor["target"]
    platform = target["platform"]
    build = target["build"]
    repository = target["repository"]
    if platform.get("kind") == "cleanroom" and build.get("provider") == "gradle":
        return "cleanroom-mod"
    if build.get("provider") == "gradle":
        return "gradle-project"
    if repository.get("state") == "observed":
        return "repository"
    return "directory"


def build_workspace_home(
    suite_root: Path | str,
    requested_path: Path | str,
) -> dict[str, Any]:
    """Compose a small actionable Home without mutating the selected workspace."""

    suite = Path(suite_root).expanduser().resolve(strict=True)
    requested = Path(requested_path).expanduser()
    doctor = new_report(requested, requested_path=requested)
    target = doctor["target"]
    workspace = Path(target["workspace"]["root"])

    exact_context: Mapping[str, Any] | None = None
    exact_context_error: str | None = None
    pack_manifest, pack_manifest_error = _pack_manifest_probe(workspace)
    refreshable_supersymmetry_index = False
    if pack_manifest is not None and pack_manifest["name"] == "Supersymmetry":
        try:
            bootstrap = inspect_project(suite, workspace)
            candidate_context = bootstrap["workspace_context"]
            index = candidate_context.get("project", {}).get("index", {})
            if index.get("matches_declared_hash") is not True:
                # The profile owner has now recognized every required workspace
                # marker, manifest identity, loader, and platform binding.  Do
                # not offer a pack-specific repair based on a display name alone.
                refreshable_supersymmetry_index = True
                raise WorkspaceHomeError(
                    "Packwiz index does not match its declared SHA-256: "
                    f"declared {index.get('declared_hash')}; "
                    f"actual {index.get('actual_sha256')}"
                )
            loader_ids = {
                row.get("id")
                for row in candidate_context.get("project", {}).get("loaders", [])
                if isinstance(row, Mapping)
            }
            if loader_ids != {"forge"}:
                raise WorkspaceHomeError(
                    "the current Supersymmetry profile accepts only the Packwiz "
                    f"Forge loader declaration; observed {sorted(str(row) for row in loader_ids)}"
                )
            exact_context = candidate_context
        except (OSError, ProjectInspectionError, ValueError, WorkspaceHomeError) as exc:
            exact_context_error = str(exc)

    kind = _workspace_kind(doctor, exact_context, pack_manifest)
    surfaces = target["surfaces"]
    platform = target["platform"]
    build = target["build"]
    repository = target["repository"]
    root_text = str(workspace)

    if exact_context is not None:
        project = exact_context["project"]
        pack = exact_context["pack"]
        exact_platform = exact_context["platform"]
        display_name = project["name"]
        profile = {
            "state": "selected",
            "profile_family_id": pack["profile_family_id"],
            "selected_profile": pack["selected_profile"],
            "platform_profile_id": exact_platform["profile_id"],
            "maturity": pack["maturity"],
            "stable_release": bool(pack.get("stable_release", False)),
            "support_claimed": False,
        }
        platform_view = {
            "state": exact_platform["status"],
            "kind": "cleanroom",
            "minecraft_version": exact_platform["minecraft_version"],
            "cleanroom_version": exact_platform["cleanroom_version"],
        }
        pack_loader = {
            "state": "observed",
            "items": list(project["loaders"]),
            "role": "pack-manifest-loader",
        }
        pack_manifest_view = {
            "state": "verified",
            "name": project["name"],
            "pack_format": project["pack_format"],
            "minecraft_version": project["minecraft_version"],
            "index": dict(project["index"]),
        }
    else:
        mods = surfaces.get("mods", [])
        display_name = (
            pack_manifest["name"]
            if pack_manifest is not None
            else mods[0].get("name")
            if isinstance(mods, list)
            and mods
            and isinstance(mods[0], Mapping)
            and mods[0].get("name")
            else workspace.name
        )
        profile = {
            "state": "unresolved",
            "profile_family_id": None,
            "selected_profile": None,
            "platform_profile_id": None,
            "maturity": None,
            "stable_release": False,
            "support_claimed": False,
        }
        if pack_manifest is not None:
            loader_ids = {row["id"] for row in pack_manifest["loaders"]}
            observed_kind = (
                "legacy-forge"
                if loader_ids == {"forge"}
                else next(iter(loader_ids))
                if len(loader_ids) == 1
                else "multiple-pack-loaders"
            )
            platform_view = {
                "state": "observed",
                "kind": observed_kind,
                "minecraft_version": pack_manifest["minecraft_version"],
                "cleanroom_version": None,
            }
            pack_loader = {
                "state": "observed",
                "items": list(pack_manifest["loaders"]),
                "role": "pack-manifest-loader",
            }
            pack_manifest_view = {"state": "observed", **pack_manifest}
        else:
            platform_view = {
                "state": platform.get("state"),
                "kind": platform.get("kind"),
                "minecraft_version": platform.get("minecraft_version"),
                "cleanroom_version": platform.get("cleanroom_version"),
            }
            pack_loader = {
                "state": "unresolved",
                "items": [],
                "role": "pack-manifest-loader",
            }
            pack_manifest_view = {"state": "unresolved"}

    inapplicable_pack_findings = (
        {"BUILD_PROVIDER_UNRESOLVED", "PLATFORM_UNRESOLVED"}
        if pack_manifest is not None
        else set()
    )
    problems = [
        {
            "id": row["id"],
            "severity": row["severity"],
            "title": row["title"],
            "detail": row["detail"],
            "repair": row["repair"],
        }
        for row in doctor["findings"]
        if row["id"] not in inapplicable_pack_findings
    ]
    if pack_manifest is not None and exact_context is None and pack_manifest_error is None:
        problems.append(
            {
                "id": "PACK_PROFILE_UNRESOLVED",
                "severity": "warning",
                "title": "The Packwiz pack has no selected Workbench profile",
                "detail": (
                    "Home observed its manifest and loader but did not infer a "
                    "construction or support profile from the pack name."
                ),
                "repair": {
                    "action": "Select or add an explicit applicable pack profile.",
                    "command": None,
                },
            }
        )
    if pack_manifest_error is not None:
        manifest_issue = pack_manifest is None
        problems.append(
            {
                "id": (
                    "PACK_MANIFEST_INVALID"
                    if manifest_issue
                    else "PACK_INDEX_REFRESHABLE"
                    if refreshable_supersymmetry_index
                    else "PACK_INDEX_INVALID"
                ),
                "severity": "warning",
                "title": (
                    "The Packwiz manifest could not be trusted"
                    if manifest_issue
                    else "The tracked Supersymmetry index needs a disposable refresh"
                    if refreshable_supersymmetry_index
                    else "The Packwiz index could not be verified"
                ),
                "detail": pack_manifest_error,
                "repair": {
                    "action": (
                        "Use Workbench's profile-owned preflight and staged "
                        "materializer; they refresh a tracked copy without changing "
                        "this checkout."
                        if refreshable_supersymmetry_index
                        else "Replace it with a bounded regular UTF-8 TOML manifest."
                    ),
                    "command": (
                        shlex.join(
                            [
                                "workbench",
                                "runtime",
                                "preflight",
                                root_text,
                                "--profile",
                                "supersymmetry",
                            ]
                        )
                        if refreshable_supersymmetry_index
                        else None
                    ),
                },
            }
        )
    parsed_descriptors = {
        str(row.get("descriptor"))
        for row in surfaces.get("mods", [])
        if isinstance(row, Mapping)
    }
    for relative in surfaces.get("resources", [])[:10]:
        descriptor = workspace / relative / "mcmod.info"
        if descriptor.is_file() and not descriptor.is_symlink() and str(descriptor) not in parsed_descriptors:
            problems.append(
                {
                    "id": f"MOD_DESCRIPTOR_INVALID:{relative}",
                    "severity": "warning",
                    "title": "A mod descriptor exists but no mod identity could be read",
                    "detail": f"No valid mod entry was parsed from {descriptor}.",
                    "repair": {
                        "action": "Repair mcmod.info before relying on mod identity or packaging.",
                        "command": None,
                    },
                }
            )
    if exact_context_error is not None and not refreshable_supersymmetry_index:
        problems.append(
            {
                "id": "EXACT_PACK_CONTEXT_UNAVAILABLE",
                "severity": "warning",
                "title": "Supersymmetry markers were found but exact context failed",
                "detail": exact_context_error,
                "repair": {
                    "action": "Run exact inspection to see the complete failing check.",
                    "command": shlex.join(["workbench", "inspect", root_text]),
                },
            }
        )

    health_action = _action(
        "workspace-health",
        "Check workspace health",
        "Show the complete bounded project, Git, build, and platform diagnosis.",
        ["workbench", "doctor", root_text],
    )
    search_available = _has_searchable_project_surface(workspace, surfaces)
    search_action = _action(
        "search-workspace",
        "Search this workspace",
        "Search project declarations without loading or changing the game.",
        (
            [
                "workbench",
                "explore",
                "--project",
                root_text,
                "--source",
                "project",
            ]
            if search_available
            else None
        ),
        blockers=(() if search_available else ("PROJECT_SURFACE_UNAVAILABLE",)),
        unavailable_reason=(
            None
            if search_available
            else "No bounded source, resource, Groovy, or configuration file was found."
        ),
    )
    if exact_context is not None:
        preflights = _exact_flow_preflights(suite, workspace)
        recipe_change_error = preflights["change-recipe"]
        recipe_context_error = preflights["understand-recipes"]
        actions = [
                _action(
                    "change-recipe",
                    "Add or repair a recipe",
                    "Find exact recipe owners before planning a source change.",
                    (
                        [
                            "workbench",
                            "feature",
                            "options",
                            "recipe-change",
                            root_text,
                        ]
                        if recipe_change_error is None
                        else None
                    ),
                    blockers=(
                        ()
                        if recipe_change_error is None
                        else ("RECIPE_OWNER_PREFLIGHT_FAILED",)
                    ),
                    unavailable_reason=recipe_change_error,
                ),
                _action(
                    "understand-recipes",
                    "Understand recipe evidence",
                    "See whether source or observed runtime recipe evidence is available.",
                    (
                        ["workbench", "atlas", "recipes", "context", root_text]
                        if recipe_context_error is None
                        else None
                    ),
                    blockers=(
                        ()
                        if recipe_context_error is None
                        else ("RECIPE_EVIDENCE_PREFLIGHT_FAILED",)
                    ),
                    unavailable_reason=recipe_context_error,
                ),
                search_action,
                health_action,
                _action(
                    "inspect-exact-context",
                    "Inspect the selected pack profile",
                    "Review the exact checkout, profile, and permitted operation boundary.",
                    ["workbench", "inspect", root_text],
                ),
            ]
    else:
        actions = [
            *(
                [
                    _action(
                        "prepare-supersymmetry-runtime",
                        "Prepare the Supersymmetry runtime payload",
                        (
                            "Check publisher-hosted files before provisioning. "
                            "The later materializer refreshes only a disposable "
                            "tracked copy and leaves this checkout unchanged."
                        ),
                        [
                            "workbench",
                            "runtime",
                            "preflight",
                            root_text,
                            "--profile",
                            "supersymmetry",
                        ],
                    )
                ]
                if refreshable_supersymmetry_index
                else []
            ),
            search_action,
            health_action,
            _action(
                "run-development-client",
                "Run a development client",
                "A generic Cleanroom build/run adapter has not selected an exact run profile.",
                None,
                blockers=("EXACT_RUN_PROFILE_REQUIRED",),
                unavailable_reason=(
                    "No exact, applicable Cleanroom development run profile was found."
                ),
            ),
        ]

    actions = actions[:HOME_ACTION_LIMIT]
    gaps: list[dict[str, str]] = []
    if exact_context is None:
        gaps.append(
            {
                "id": "generic-development-loop",
                "summary": (
                    "Workbench can inspect this workspace, but cannot yet derive a safe "
                    + (
                        "pack build/run command without an applicable profile."
                        if kind == "packwiz-pack"
                        else "generic Cleanroom build/run command from it."
                    )
                ),
            }
        )
    if exact_context is not None and not profile["stable_release"]:
        gaps.append(
            {
                "id": "tested-support-decision",
                "summary": (
                    "This exact pack profile is experimental; Home does not turn it into "
                    "a stable support claim."
                ),
            }
        )

    result = {
        "format": HOME_FORMAT,
        "schema_version": HOME_SCHEMA_VERSION,
        "read_only": True,
        "workspace": {
            "requested_path": str(requested.expanduser().resolve()),
            "root": root_text,
            "display_name": display_name,
            "kind": kind,
            "recognition": "exact" if exact_context is not None else "bounded",
        },
        "status": _problem_summary(problems),
        "repository": {
            key: repository.get(key)
            for key in ("state", "root", "branch", "head", "dirty", "changes")
        },
        "context": {
            "platform": platform_view,
            "pack_manifest": pack_manifest_view,
            "pack_loader": pack_loader,
            "profile": profile,
            "build": {
                "state": build.get("state"),
                "provider": build.get("provider"),
                "wrapper": build.get("wrapper"),
            },
            "mod_ids": [
                row["mod_id"]
                for row in surfaces.get("mods", [])
                if isinstance(row, Mapping) and isinstance(row.get("mod_id"), str)
            ],
            "source_surfaces": {
                key: list(surfaces.get(key, []))
                for key in ("source", "resources", "groovy", "configuration")
            },
        },
        "problems": problems,
        "actions": actions,
        "gaps": gaps,
        "owner_records": {
            "workspace_doctor_format": doctor["format"],
            "exact_pack_context_format": (
                exact_context.get("format") if exact_context is not None else None
            ),
        },
        "limitations": [
            "Home is a read-only projection; selecting an action is a separate command.",
            "No build, Gradle task, runtime, world, source file, or retained state was changed.",
        ],
    }
    validate_workspace_home(result)
    return result


def validate_workspace_home(value: Mapping[str, Any]) -> None:
    """Reject internally inconsistent Home projections at the product boundary."""

    if value.get("format") != HOME_FORMAT or value.get("schema_version") != 1:
        raise WorkspaceHomeError("workspace Home identity is invalid")
    if value.get("read_only") is not True:
        raise WorkspaceHomeError("workspace Home must be read-only")
    workspace = value.get("workspace")
    if not isinstance(workspace, Mapping) or not all(
        isinstance(workspace.get(key), str) and workspace.get(key)
        for key in ("requested_path", "root", "display_name", "kind", "recognition")
    ):
        raise WorkspaceHomeError("workspace Home lacks a complete workspace identity")
    actions = value.get("actions")
    if not isinstance(actions, list) or not actions or len(actions) > HOME_ACTION_LIMIT:
        raise WorkspaceHomeError("workspace Home must expose one to five actions")
    identifiers: set[str] = set()
    for action in actions:
        if not isinstance(action, Mapping):
            raise WorkspaceHomeError("workspace Home action must be an object")
        action_id = action.get("id")
        if not isinstance(action_id, str) or not action_id or action_id in identifiers:
            raise WorkspaceHomeError("workspace Home action IDs must be unique strings")
        identifiers.add(action_id)
        available = action.get("available")
        argv = action.get("argv")
        blockers = action.get("blockers")
        if type(available) is not bool or not isinstance(blockers, list):
            raise WorkspaceHomeError(f"workspace Home action is malformed: {action_id}")
        if available:
            if (
                not isinstance(argv, list)
                or not argv
                or any(not isinstance(item, str) or not item for item in argv)
                or blockers
            ):
                raise WorkspaceHomeError(
                    f"available workspace Home action lacks exact argv: {action_id}"
                )
        elif argv is not None or not blockers:
            raise WorkspaceHomeError(
                f"blocked workspace Home action must name blockers: {action_id}"
            )
        if not available and not isinstance(action.get("unavailable_reason"), str):
            raise WorkspaceHomeError(
                f"blocked workspace Home action must explain why: {action_id}"
            )
    problems = value.get("problems")
    if not isinstance(problems, list) or any(
        not isinstance(problem, Mapping) for problem in problems
    ):
        raise WorkspaceHomeError("workspace Home problems must be a list of objects")
    if value.get("status") != _problem_summary(problems):
        raise WorkspaceHomeError("workspace Home status does not match its problems")


__all__ = [
    "HOME_ACTION_LIMIT",
    "HOME_FORMAT",
    "HOME_SCHEMA_VERSION",
    "WorkspaceHomeError",
    "build_workspace_home",
    "validate_workspace_home",
]
