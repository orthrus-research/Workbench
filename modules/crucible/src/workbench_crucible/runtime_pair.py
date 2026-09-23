"""Controlled runtime-pair contract owned by Crucible, independent of Shell."""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Callable, Mapping
from workbench_api.profile_extensions import (
    ProfileExtensionError,
    require_profile_extension,
)

PAIR_REQUEST_FORMAT = "workbench-feature-runtime-pair-request-v1"
PAIR_RESULT_FORMAT = "workbench-feature-runtime-pair-result-v1"


def runtime_pair_owner(profile_id: str) -> Any:
    owner = require_profile_extension("workbench.runtime_pairs", profile_id)
    if not callable(getattr(owner, "runtime_pair_from_config", None)):
        raise ProfileExtensionError(
            f"{profile_id} has an incomplete runtime-pair contract"
        )
    return owner


class FeatureRuntimePairPorts(ABC):
    """Owner port for one exact physical-side pair.

    Implementations are expected to use Crucible/runtime owners and return
    their own digest-bound references.  The workspace validates composition
    semantics but does not reproduce or reinterpret game observations.
    """

    @abstractmethod
    def run_pair(
        self,
        request: dict[str, Any],
        *,
        suite_root: Path,
        plan: Mapping[str, Any],
        attempt_root: Path,
    ) -> Mapping[str, Any]:
        raise NotImplementedError


@dataclass(frozen=True)
class RuntimeExecutionServices:
    """Explicit installed execution services; provided per invocation, never global.

    These ports carry mechanisms and custody owners, not profile policy. They
    do not authorize a launch; the caller must supply an approved execution.
    """

    sha256_file: Callable[..., Any]
    captured_disposable_groovy_log: Callable[..., Any]
    disposable_runtime_receipt_evidence: Callable[..., Any]
    material_fluid_runtime_compatibility_policy: Callable[..., Any]
    summarize_disposable_runtime: Callable[..., Any]
    INSTALLER_MAIN_CLASS: str
    PackwizMaterializationError: type[Exception]
    packwiz_optional_decisions: Callable[..., Any]
    validate_refreshed_pack: Callable[..., Any]
    verify_packwiz_final_state: Callable[..., Any]
    write_packwiz_initial_state: Callable[..., Any]
    copy_tracked_workspace: Callable[..., Any]
    observe_project_runtime: Callable[..., Any]
    require_runtime_processes_closed: Callable[..., Any]
    plan_project_runtime: Callable[..., Any]
    load_pack: Callable[..., Any]
    runtime_tree: Callable[..., Any]
    stop_process_group: Callable[..., Any]
    copy_seed: Callable[..., Any]
    expected_server_mods: Callable[..., Any]
    run_owned_logged: Callable[..., Any]
    server_seed_paths: Callable[..., Any]
    server_materialization_version: Callable[..., Any]
    verify_server_materialization_identity: Callable[..., Any]

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if field.name == "INSTALLER_MAIN_CLASS":
                if type(value) is not str or not value:
                    raise ValueError("runtime installer identity must be explicit")
            elif not callable(value):
                raise ValueError(
                    f"runtime execution service {field.name} is unavailable"
                )
