"""Explicit developer selection and fresh, immutable operation observations.

Selection is retained in an existing Work Session header. No global last-pack
pointer, runtime provisioning, source marker or second session journal exists.
Profiles receive Project Intelligence observations, never this Shell object.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Mapping
from urllib.parse import urlparse
from urllib.request import url2pathname

from workbench_api.profile_extensions import (
    profile_extension_identity,
    ProfileExtensionError,
)
from workbench_api.profiles import profiles
from workbench_project_intelligence import inspect_workspace
from workbench_project_intelligence.working_tree import observe_source


class DeveloperContextError(ValueError):
    """Selection or operation inputs are unavailable or no longer exact."""


def verify_developer_owner_reference(
    reference: Mapping[str, Any], selection: DeveloperSelection, *, suite_root: Path
) -> dict[str, Any]:
    """Reproduce an exact retained artifact, never an execution outcome."""
    if reference.get("owner_id") in {"crucible", "core", "axiom"}:
        from workbench_core.check_storage import read_json, seal
        from workbench_crucible.developer_checks import validate_result
        from .work_session import utc_now

        path = _path(reference["uri"])
        value = (read_json(path, byte_limit=None)
                 if reference.get("owner_id") == "axiom" and path.name == "result.json"
                 else read_json(path))
        kind = value.get("format")
        if reference.get('owner_id') == 'axiom' and kind == 'workbench-check-snapshot-publication-v1':
            from .developer_material_checks import open_snapshot
            if path.name != 'publication.json' or path.parent.name != 'snapshot':
                raise DeveloperContextError('material snapshot storage identity changed')
            identity = path.parent.parent.name
            with open_snapshot(path.parents[4], identity, selection) as opened:
                if (opened.publication != value or reference.get('record_kind') != kind
                        or reference.get('record_id') != value['id']
                        or reference.get('digest') != 'sha256:' + sha256(path.read_bytes()).hexdigest()):
                    raise DeveloperContextError('material snapshot owner reference changed')
                return {**reference, 'last_verified_state': value['summary']['state'], 'verified_at': utc_now()}
        if reference.get("owner_id") == "axiom":
            from .developer_material_checks import reopen, _load
            if path.name not in {"request.json", "result.json"} or path.parent.name != value["attempt_id"]:
                raise DeveloperContextError("material owner storage identity changed")
            if path.name == "request.json":
                _, observed, _ = _load(path.parents[3], value["attempt_id"], selection)
            else:
                observed, _ = reopen(path.parents[3], value["attempt_id"], selection)
            if observed != value:
                raise DeveloperContextError("material owner evidence changed")
        elif reference.get("owner_id") == "core":
            kinds = {
                "workbench-environment-request-v1": "environment-request",
                "workbench-environment-result-v1": "environment-result",
            }
            if (
                kind not in kinds
                or seal(kinds[kind], {k: v for k, v in value.items() if k != "id"})
                != value
            ):
                raise DeveloperContextError("environment owner record changed")
        elif kind == "workbench-saved-check-result-v4":
            validate_result(value)
        elif kind == "workbench-check-comparison-v2":
            from workbench_crucible.check_comparison import compare_results, validate_comparison
            from .developer_checks import _reopen
            validate_comparison(value)
            if path.parent.name != "comparisons" or path.parent.parent.name != value["candidate"]["attempt_id"]:
                raise DeveloperContextError("comparison storage identity changed")
            checks_root = path.parents[4]
            before, _ = _reopen(checks_root, value["reference"]["attempt_id"], selection)
            after, _ = _reopen(checks_root, value["candidate"]["attempt_id"], selection)
            if compare_results(before, after) != value:
                raise DeveloperContextError("comparison evidence changed")
        elif kind == "workbench-saved-check-request-v4":
            if (
                seal(
                    "saved-check-request", {k: v for k, v in value.items() if k != "id"}
                )
                != value
            ):
                raise DeveloperContextError("saved check request changed")
        else:
            raise DeveloperContextError("unknown check owner record")
        if (
            value.get("workspace_uri") != selection.pack_uri
            or value.get("selection_id") != selection.id
            or reference.get("record_kind") != kind
            or reference.get("record_id") != value.get("id")
            or reference.get("digest")
            != "sha256:" + sha256(path.read_bytes()).hexdigest()
        ):
            raise DeveloperContextError("saved check owner reference changed")
        return {
            **reference,
            "last_verified_state": value["state"],
            "verified_at": utc_now(),
        }
    from .developer_feature import _read_record, validate_material_fluid_recipe_plan
    from .developer_source_feature import validate_source_feature_plan
    from .work_session import utc_now
    from workbench_blueprints.profile_construction import recipe_change_authority

    path = _path(reference["uri"])
    value, raw = _read_record(path, "developer owner artifact")
    kind = value.get("kind", value.get("format"))
    expected_owner = "blueprints"
    state = "retained-plan"
    if kind == "workbench-supersymmetry-recipe-change-plan":
        recipe_change_authority(selection.pack_profile).validate_recipe_change_plan(
            value
        )
    elif kind == "workbench-developer-material-fluid-recipe-plan":
        validate_material_fluid_recipe_plan(value)
    elif kind == "workbench-developer-source-feature-plan":
        validate_source_feature_plan(value, suite_root=suite_root)
    elif kind == "workbench-recipe-comparison-preparation-v1":
        expected_owner, state = "workbench-shell", "prepared-not-run"
        body = {key: item for key, item in value.items() if key != "id"}
        identity = (
            "recipe-preparation:sha256:" + sha256(_json(body).encode()).hexdigest()
        )
        if (
            value.get("id") != identity
            or value.get("state") != state
            or value.get("selection_id") != selection.id
            or value.get("authority")
            != {
                "runtime_launched": False,
                "source_mutated": False,
                "execution_authorized": False,
            }
        ):
            raise DeveloperContextError("preparation identity or authority changed")
    else:
        raise DeveloperContextError("unsupported developer artifact owner")
    digest = "sha256:" + sha256(raw).hexdigest()
    if (
        value.get("workspace_uri") != selection.pack_uri
        or reference.get("owner_id") != expected_owner
        or reference.get("record_kind") != kind
        or reference.get("record_id") != value.get("id")
        or reference.get("digest") != digest
    ):
        raise DeveloperContextError(
            "developer artifact identity, workspace or bytes changed"
        )
    return {**reference, "last_verified_state": state, "verified_at": utc_now()}


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _path(uri: str) -> Path:
    if type(uri) is not str or len(uri) > 8192:
        raise DeveloperContextError("selection URI must be bounded text")
    parsed = urlparse(uri)
    if parsed.scheme != "file" or parsed.netloc or parsed.query or parsed.fragment:
        raise DeveloperContextError("selection requires a local absolute file URI")
    path = Path(url2pathname(parsed.path))
    if not path.is_absolute() or path.resolve().as_uri() != uri:
        raise DeveloperContextError("selection URI is not canonical")
    return path


@dataclass(frozen=True)
class DeveloperSelection:
    pack_uri: str
    pack_profile: str
    platform_profile: str
    variant: str
    mod_uri: str | None = None

    def __post_init__(self) -> None:
        _path(self.pack_uri)
        if self.mod_uri is not None:
            _path(self.mod_uri)
        for value in (self.pack_profile, self.platform_profile, self.variant):
            if (
                type(value) is not str
                or re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", value) is None
            ):
                raise DeveloperContextError(
                    "selection profile identities must be explicit stable names"
                )

    @property
    def workspace(self) -> Path:
        return _path(self.pack_uri)

    @property
    def id(self) -> str:
        return (
            "developer-selection:sha256:"
            + sha256(_json(asdict(self)).encode()).hexdigest()
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> DeveloperSelection:
        if type(value) is not dict or set(value) != {
            "pack_uri",
            "pack_profile",
            "platform_profile",
            "variant",
            "mod_uri",
        }:
            raise DeveloperContextError("developer selection fields are invalid")
        return cls(**value)


@dataclass(frozen=True)
class DeveloperOperation:
    selection: DeveloperSelection
    snapshot_json: str

    def as_dict(self) -> dict[str, Any]:
        return json.loads(self.snapshot_json)

    @property
    def id(self) -> str:
        return (
            "developer-operation:sha256:"
            + sha256(self.snapshot_json.encode()).hexdigest()
        )

    def require_fresh(self) -> None:
        if (
            observe_developer_context(self.selection).snapshot_json
            != self.snapshot_json
        ):
            raise DeveloperContextError(
                "operation inputs changed; prepare a fresh operation"
            )


def observe_developer_context(selection: DeveloperSelection) -> DeveloperOperation:
    admitted = {profile.id: profile for profile in profiles()}
    pack = admitted.get(selection.pack_profile)
    platform = admitted.get(selection.platform_profile)
    if (
        pack is None
        or pack.kind != "pack"
        or platform is None
        or platform.kind != "platform"
    ):
        raise DeveloperContextError(
            "selected pack/platform profiles are not admitted and enabled"
        )
    resources = []
    for profile in (pack, platform):
        for role in sorted(profile.resources):
            path = profile.resource(role)
            if path.stat().st_size > 4 * 1024 * 1024:
                raise DeveloperContextError(
                    "selected profile resource exceeds its byte bound"
                )
            raw = path.read_bytes()
            resources.append(
                {
                    "profile": profile.id,
                    "role": role,
                    "sha256": sha256(raw).hexdigest(),
                    "size": len(raw),
                }
            )
    pack_bytes = pack.resource("profile").read_bytes()
    platform_bytes = platform.resource("profile").read_bytes()
    source = observe_source(selection.workspace)
    context = inspect_workspace(
        selection.workspace,
        pack_profile_path=pack.resource("profile"),
        platform_profile_path=platform.resource("profile"),
        pack_selection=selection.variant,
        pack_profile_bytes=pack_bytes,
        platform_profile_bytes=platform_bytes,
    )
    if source != observe_source(selection.workspace):
        raise DeveloperContextError("source changed while inspecting selected profiles")
    owners = []
    for group in (
        "workbench.recipe_changes",
        "workbench.quest_changes",
        "workbench.recipe_observers",
        "workbench.runtime_pairs",
        "workbench.source_interpreters",
    ):
        try:
            owners.append(
                {"state": "available", **profile_extension_identity(group, pack.id)}
            )
        except ProfileExtensionError as exc:
            owners.append(
                {
                    "group": group,
                    "profile_id": pack.id,
                    "state": "unavailable",
                    "reason": str(exc),
                }
            )
    # Re-read exact resources after composition; observations are not a lease.
    for row in resources:
        raw = admitted[row["profile"]].resource(row["role"]).read_bytes()
        if sha256(raw).hexdigest() != row["sha256"] or len(raw) != row["size"]:
            raise DeveloperContextError("profile resources changed during observation")
    payload = {
        "format": "workbench-developer-operation-v1",
        "selection": asdict(selection),
        "selection_id": selection.id,
        "source": source,
        "workspace_context": context,
        "mod_source": None
        if selection.mod_uri is None
        else observe_source(_path(selection.mod_uri)),
        "profile_resources": resources,
        "owners": owners,
        "authority": {"execution_authorized": False, "qualification_granted": False},
    }
    return DeveloperOperation(selection, _json(payload))


def require_private_storage(selection: DeveloperSelection, storage: Path) -> Path:
    storage = storage.expanduser().resolve()
    sources = [selection.workspace] + (
        [] if selection.mod_uri is None else [_path(selection.mod_uri)]
    )
    if any(storage == source or storage.is_relative_to(source) for source in sources):
        raise DeveloperContextError(
            "retain developer state outside the selected source checkouts"
        )
    return storage


def retain_selection(
    selection: DeveloperSelection, state_root: Path, *, frontend: Mapping[str, Any]
) -> dict[str, Any]:
    from .work_session import STORAGE_DIRECTORY, WorkSessionStore

    require_private_storage(selection, state_root / STORAGE_DIRECTORY)

    operation = observe_developer_context(selection)
    observed = operation.as_dict()
    session = WorkSessionStore(state_root).create(
        task={
            "task_id": "developer-workflow",
            "owner_id": "workbench-shell",
            "label": "Developer context",
            "owner_record_ref": None,
        },
        workspace={
            "identity_id": "workspace:sha256:"
            + sha256(selection.pack_uri.encode()).hexdigest(),
            "canonical_root": str(selection.workspace),
            "root_uri": selection.pack_uri,
            "source_revision": observed["source"]["revision"],
            "dirty_fingerprint": observed["source"]["source_sha256"],
        },
        identities={
            "core_id": "workbench-core",
            "catalog_id": "workbench-developer-operation-v1",
            "host_adapter_id": "workbench-local-host",
            "pack_profile_id": observed["workspace_context"]["pack"][
                "profile_family_id"
            ],
            "platform_profile_id": observed["workspace_context"]["platform"][
                "profile_id"
            ],
            "developer_selection": asdict(selection),
        },
        frontend=frontend,
        label="Developer selection",
        limitations=[
            "Selection is navigation, not execution or qualification authority."
        ],
    )
    return {
        "session_id": session["session_id"],
        "selection_id": selection.id,
        "operation_id": operation.id,
        "observation": observed,
    }


def selected_context(
    state_root: Path, session_id: str, *, workspace: Path | None = None
) -> DeveloperSelection:
    from .work_session import WorkSessionStore

    if re.fullmatch(r"work-session-v2-[0-9a-f]{32}", session_id) is None:
        raise DeveloperContextError(
            "select an exact Work Session ID; latest/global selection is not accepted"
        )
    opened = WorkSessionStore(state_root).open(session_id)
    if opened["integrity"]["journal_state"] != "verified":
        raise DeveloperContextError("selected Work Session journal is not verified")
    selection = DeveloperSelection.from_dict(
        opened["session"]["identities"].get("developer_selection")
    )
    if opened["session"]["workspace"]["root_uri"] != selection.pack_uri:
        raise DeveloperContextError(
            "Work Session and developer selection workspace differ"
        )
    return (
        selection
        if workspace is None
        else replace(selection, pack_uri=workspace.expanduser().resolve().as_uri())
    )
