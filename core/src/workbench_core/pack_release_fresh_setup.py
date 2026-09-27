"""Core orchestration for an official fresh Supersymmetry installation source.

Textual selects the optional file IDs and presents progress. Core reopens the
saved release, retains every selected external file, and publishes immutable
source trees. A future Workbench credential provider is injected at runtime;
neither the key nor per-file download URLs become user configuration.
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Callable, Mapping

from workbench_api.host_filesystem import DurableRecordError

from .durable_records import read_private_single_link_bytes
from .pack_release import PackReleaseService
from .pack_release_client_layout import (
    _selected_policy, load_client_layout_policy, plan_release_override_custody,
    apply_release_override_custody, reopen_release_override_custody,
)
from .pack_release_curseforge import (
    CurseForgeAccessUnavailable, _provider_key, _receipt_path,
    acquire_curseforge_file, plan_curseforge_acquisition,
    publish_curseforge_acquisition, reopen_curseforge_file,
)
from .pack_release_curseforge_composition import (
    apply_curseforge_client_composition, plan_curseforge_client_composition,
    reopen_curseforge_client_composition,
    reopen_curseforge_client_composition_by_plan_id,
)
from .pack_release_dynamic_policies import (
    apply_release_dynamic_policies, plan_release_dynamic_policies,
    reopen_release_dynamic_policies, suggest_resourcepack_placements,
)
from .pack_release_local import _canonical, _object
from .pack_release_prism_resourcepacks import load_resourcepack_policy
from .preference_records import update_preference_bytes


SCHEMA = "workbench.pack-release.fresh-setup.v1"
POLICY_CHOICE_FORMAT = "workbench-pack-release-fresh-policy-choice-v1"
_POLICY_CHOICE_BYTES = 16 * 1024
_POLICY_PLAN_ID = re.compile(r"workbench-pack-release-derived-policies-plan:sha256:[0-9a-f]{64}\Z")
_INPUT_ID = re.compile(r"workbench-pack-release-input-plan:sha256:[0-9a-f]{64}\Z")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")


class FreshReleaseUnavailable(ValueError):
    """The user's saved official release is not ready for fresh setup."""


class OfficialFreshReleaseService:
    """Expose bounded steps for one Textual-managed fresh setup session."""

    def __init__(
        self, release_service: PackReleaseService, *, authority_path: Path,
        layout_policy_path: Path, resourcepack_policy_path: Path,
        optional_selected: tuple[tuple[int, int], ...] | None = None,
        credential_provider: Callable[[], str | None] | None = None,
    ) -> None:
        self.release_service = release_service
        self.authority_path = authority_path
        self.layout_policy_path = layout_policy_path
        self.resourcepack_policy_path = resourcepack_policy_path
        self.optional_selected = optional_selected
        self.credential_provider = credential_provider

    @property
    def state_root(self) -> Path:
        return self.release_service.state_root

    @property
    def config_home(self) -> Path:
        return self.release_service.choice_path.parent

    @property
    def policy_choice_path(self) -> Path:
        return self.config_home / "pack-release-fresh-policy-choice.json"

    def _selected_release(self) -> tuple[dict[str, Any], Path]:
        reviewed = self.release_service.inputs()
        if reviewed.get("status") != "planned" or type(reviewed.get("input_plan")) is not dict:
            raise FreshReleaseUnavailable(
                "selected official release archive is unavailable: "
                + str(reviewed.get("reason") or reviewed.get("artifact_state") or "not prepared")
            )
        input_plan = reviewed["input_plan"]
        selected = reviewed.get("selected")
        path = selected.get("artifact_path") if type(selected) is dict else None
        if type(path) is not str or not Path(path).is_absolute():
            raise FreshReleaseUnavailable("selected official release archive has no retained Core path")
        return input_plan, Path(path)

    def _read_policy_choice(self) -> dict[str, Any] | None:
        try:
            raw = read_private_single_link_bytes(
                self.policy_choice_path, byte_limit=_POLICY_CHOICE_BYTES,
            )
        except DurableRecordError as exc:
            if exc.code == "unavailable" and not self.policy_choice_path.exists() \
                    and not self.policy_choice_path.is_symlink():
                return None
            raise FreshReleaseUnavailable("saved fresh release policy choice is unsafe") from exc
        value = _object(raw, "saved fresh release policy choice")
        if (set(value) != {"format", "schema_version", "profile", "input_plan_id",
                           "release_id", "version", "asset_sha256", "policy_plan_id",
                           "policy_tree_id", "policy_content_sha256",
                           "resourcepack_pairs", "optional_selected"}
                or value["format"] != POLICY_CHOICE_FORMAT
                or value["schema_version"] != 1 or value["profile"] != "supersymmetry"
                or type(value["input_plan_id"]) is not str
                or _INPUT_ID.fullmatch(value["input_plan_id"]) is None
                or type(value["policy_plan_id"]) is not str
                or _POLICY_PLAN_ID.fullmatch(value["policy_plan_id"]) is None
                or type(value["policy_tree_id"]) is not str
                or re.fullmatch(r"workbench-tree-v1:[0-9a-f]{32}", value["policy_tree_id"]) is None
                or type(value["policy_content_sha256"]) is not str
                or _SHA256.fullmatch(value["policy_content_sha256"]) is None
                or type(value["asset_sha256"]) is not str
                or _SHA256.fullmatch(value["asset_sha256"]) is None
                or type(value["release_id"]) is not str
                or re.fullmatch(r"(?:profile-release|github-release):sha256:[0-9a-f]{64}",
                                value["release_id"]) is None
                or type(value["version"]) is not str
                or re.fullmatch(r"[0-9]+(?:\.[0-9]+){3}", value["version"]) is None):
            raise FreshReleaseUnavailable("saved fresh release policy choice is invalid")
        for key in ("resourcepack_pairs", "optional_selected"):
            rows = value[key]
            if type(rows) is not list:
                raise FreshReleaseUnavailable("saved fresh release policy selection is invalid")
            pairs = []
            for row in rows:
                if (type(row) is not dict or set(row) != {"project_id", "file_id"}
                        or type(row["project_id"]) is not int
                        or type(row["file_id"]) is not int
                        or not 0 < row["project_id"] < 2**63
                        or not 0 < row["file_id"] < 2**63):
                    raise FreshReleaseUnavailable("saved fresh release policy selection is invalid")
                pairs.append((row["project_id"], row["file_id"]))
            if pairs != sorted(set(pairs)):
                raise FreshReleaseUnavailable("saved fresh release policy selection is invalid")
        if not value["resourcepack_pairs"] or raw != _canonical(value) + b"\n":
            raise FreshReleaseUnavailable("saved fresh release policy choice changed")
        return value

    def reopen_pending_policy(self) -> dict[str, Any]:
        """Recover the durable mapping choice and its exact Core policy tree."""
        choice = self._read_policy_choice()
        if choice is None:
            return {"schema": SCHEMA, "action": "reopen-pending-policy",
                    "status": "missing", "policy_result": None}
        result = reopen_release_dynamic_policies(
            expected_plan_id=choice["policy_plan_id"],
            state_root=self.state_root, config_home=self.config_home,
        )
        if (result["tree_id"] != choice["policy_tree_id"]
                or result["tree_content_sha256"] != choice["policy_content_sha256"]
                or any(result[key] != choice[key] for key in (
                    "input_plan_id", "release_id", "version", "asset_sha256"))):
            raise FreshReleaseUnavailable("saved fresh release policy tree changed")
        try:
            current = self.release_service._read_choice()["selected"]
            same = all(current.get(key) == choice[key] for key in (
                "release_id", "version", "asset_sha256"))
        except (ValueError, OSError, DurableRecordError):
            same = False
        return {
            "schema": SCHEMA, "action": "reopen-pending-policy",
            "status": "ready" if same else "stale",
            "input_plan_id": choice["input_plan_id"],
            "policy_plan_id": choice["policy_plan_id"],
            "resourcepack_pairs": choice["resourcepack_pairs"],
            "optional_selected": choice["optional_selected"],
            "policy_result": result,
        }

    def _resolved_policy_paths(self, input_plan: Mapping[str, Any]) -> tuple[Path, Path]:
        try:
            layout = load_client_layout_policy(self.layout_policy_path)
            _selected_policy(input_plan, layout)
            plan_curseforge_acquisition(
                input_plan, resourcepack_policy_path=self.resourcepack_policy_path,
                optional_selected=tuple(sorted((row["project_id"], row["file_id"])
                                               for row in layout["optional_selected"])),
            )
            return self.layout_policy_path, self.resourcepack_policy_path
        except (ValueError, OSError, DurableRecordError):
            pass
        pending = self.reopen_pending_policy()
        if (pending["status"] == "ready"
                and pending["input_plan_id"] == input_plan["plan_id"]):
            result = pending["policy_result"]
            return Path(result["layout_policy_path"]), Path(result["resourcepack_policy_path"])
        raise FreshReleaseUnavailable(
            "new official release needs a reviewed resource-pack placement before download")

    def policy_status(self) -> dict[str, Any]:
        """Show baseline readiness or the review needed for a newer release."""
        try:
            input_plan, _ = self._selected_release()
            layout_path, resourcepack_path = self._resolved_policy_paths(input_plan)
            source = ("baseline" if layout_path == self.layout_policy_path
                      else "reviewed")
            return {
                "schema": SCHEMA, "action": "policy-status", "status": "ready",
                "source": source, "input_plan_id": input_plan["plan_id"],
                "layout_policy_path": str(layout_path),
                "resourcepack_policy_path": str(resourcepack_path),
            }
        except FreshReleaseUnavailable as exc:
            try:
                input_plan, _ = self._selected_release()
            except FreshReleaseUnavailable:
                return {"schema": SCHEMA, "action": "policy-status",
                        "status": "unavailable", "reason": str(exc)}
            suggestions = suggest_resourcepack_placements(
                input_plan, baseline_policy_path=self.resourcepack_policy_path,
            )
            required = sorted(
                ({"project_id": row["project_id"], "file_id": row["file_id"]}
                 for row in input_plan["external_files"] if row["required"]),
                key=lambda row: (row["project_id"], row["file_id"]),
            )
            optional = sorted(
                ({"project_id": row["project_id"], "file_id": row["file_id"]}
                 for row in input_plan["external_files"] if not row["required"]),
                key=lambda row: (row["project_id"], row["file_id"]),
            )
            return {
                "schema": SCHEMA, "action": "policy-status",
                "status": "review_required", "reason": str(exc),
                "input_plan_id": input_plan["plan_id"],
                "suggestions": suggestions,
                "required_external_files": required,
                "optional_external_files": optional,
            }

    def plan_policy_review(
        self, *, resourcepack_pairs: tuple[tuple[int, int], ...],
        optional_selected: tuple[tuple[int, int], ...] = (),
    ) -> dict[str, Any]:
        input_plan, archive = self._selected_release()
        plan = plan_release_dynamic_policies(
            input_plan, archive_path=archive, authority_path=self.authority_path,
            baseline_resourcepack_policy_path=self.resourcepack_policy_path,
            resourcepack_pairs=resourcepack_pairs,
            optional_selected=optional_selected,
            state_root=self.state_root, config_home=self.config_home,
        )
        return {
            "schema": SCHEMA, "action": "plan-policy-review", "status": "planned",
            "input_plan_id": input_plan["plan_id"], "policy_plan": plan,
        }

    def apply_policy_review(
        self, *, resourcepack_pairs: tuple[tuple[int, int], ...],
        optional_selected: tuple[tuple[int, int], ...] = (),
        expected_plan_id: str,
    ) -> dict[str, Any]:
        input_plan, archive = self._selected_release()
        result = apply_release_dynamic_policies(
            input_plan, archive_path=archive, authority_path=self.authority_path,
            baseline_resourcepack_policy_path=self.resourcepack_policy_path,
            resourcepack_pairs=resourcepack_pairs,
            optional_selected=optional_selected,
            state_root=self.state_root, config_home=self.config_home,
            expected_plan_id=expected_plan_id,
        )
        current, _ = self._selected_release()
        if current["plan_id"] != input_plan["plan_id"]:
            raise FreshReleaseUnavailable("selected release changed before saving policy choice")
        layout = load_client_layout_policy(Path(result["layout_policy_path"]))
        resourcepack = load_resourcepack_policy(Path(result["resourcepack_policy_path"]))
        choice = {
            "format": POLICY_CHOICE_FORMAT, "schema_version": 1,
            "profile": "supersymmetry", "input_plan_id": input_plan["plan_id"],
            "release_id": input_plan["release_id"], "version": input_plan["version"],
            "asset_sha256": input_plan["asset_sha256"],
            "policy_plan_id": result["plan_id"], "policy_tree_id": result["tree_id"],
            "policy_content_sha256": result["tree_content_sha256"],
            "resourcepack_pairs": [
                {"project_id": row["project_id"], "file_id": row["file_id"]}
                for row in resourcepack["placements"]
            ],
            "optional_selected": layout["optional_selected"],
        }
        update_preference_bytes(
            self.policy_choice_path, lambda _previous: _canonical(choice) + b"\n",
            byte_limit=_POLICY_CHOICE_BYTES,
        )
        return {
            "schema": SCHEMA, "action": "apply-policy-review", "status": "ready",
            "input_plan_id": input_plan["plan_id"],
            "policy_plan_id": result["plan_id"],
            "policy_result": result,
        }

    def _provider_key(self) -> str:
        if self.credential_provider is None:
            raise CurseForgeAccessUnavailable("Workbench CurseForge access is not configured")
        try:
            return _provider_key(self.credential_provider())
        except CurseForgeAccessUnavailable:
            raise
        except Exception as exc:
            raise CurseForgeAccessUnavailable("Workbench CurseForge access is unavailable") from exc

    def _context(self) -> tuple[
        dict[str, Any], Path, dict[str, Any], tuple[tuple[int, int], ...], Path, Path,
    ]:
        input_plan, archive = self._selected_release()
        layout_path, resourcepack_path = self._resolved_policy_paths(input_plan)
        layout = load_client_layout_policy(layout_path)
        defaults = _selected_policy(input_plan, layout)
        chosen = (tuple(sorted(defaults)) if self.optional_selected is None
                  else self.optional_selected)
        plan = plan_curseforge_acquisition(
            input_plan, resourcepack_policy_path=resourcepack_path,
            optional_selected=chosen,
        )
        return input_plan, archive, plan, chosen, layout_path, resourcepack_path

    def status(self, *, override_plan_id: str | None = None,
               composition_plan_id: str | None = None) -> dict[str, Any]:
        """Reopen the saved release and report every selected exact file ID."""
        try:
            input_plan, _, plan, chosen, layout_path, resourcepack_path = self._context()
        except (FreshReleaseUnavailable, ValueError, OSError) as exc:
            return {
                "schema": SCHEMA, "action": "status", "status": "unavailable",
                "reason": str(exc), "provider_state": "unavailable",
                "input_plan_id": None, "acquisition_plan_id": None,
                "files": [], "selected_file_count": 0, "ready_file_count": 0,
                "override_state": "unavailable", "composition_state": "unavailable",
            }
        try:
            self._provider_key()
            provider_state = "available"
        except CurseForgeAccessUnavailable:
            provider_state = "unavailable"
        rows: list[dict[str, Any]] = []
        for selected in plan["files"]:
            project_id, file_id = selected["project_id"], selected["file_id"]
            receipt = _receipt_path(plan, self.state_root, project_id, file_id)
            state = "pending"
            detail: dict[str, Any] = {}
            if receipt.exists() or receipt.is_symlink():
                try:
                    retained = reopen_curseforge_file(
                        plan, project_id=project_id, file_id=file_id,
                        state_root=self.state_root,
                    )
                    state = "ready"
                    detail = {"filename": retained["filename"],
                              "size": retained["size"], "sha256": retained["sha256"]}
                except (ValueError, OSError):
                    state = "invalid"
            rows.append({**selected, "status": state, **detail})
        override_state = "pending"
        if override_plan_id is not None:
            try:
                reopen_release_override_custody(
                    input_plan, expected_plan_id=override_plan_id,
                    layout_policy_path=layout_path,
                    state_root=self.state_root, config_home=self.config_home,
                )
                override_state = "ready"
            except (ValueError, OSError):
                override_state = "invalid"
        composition_state = "pending"
        if composition_plan_id is not None:
            if override_plan_id is None:
                composition_state = "invalid"
            else:
                try:
                    reopen_curseforge_client_composition(
                        input_plan, layout_policy_path=layout_path,
                        resourcepack_policy_path=resourcepack_path,
                        acquisition_plan_id=plan["plan_id"],
                        override_plan_id=override_plan_id,
                        optional_selected=chosen,
                        state_root=self.state_root, config_home=self.config_home,
                        expected_plan_id=composition_plan_id,
                    )
                    composition_state = "ready"
                except (ValueError, OSError):
                    composition_state = "invalid"
        ready = sum(row["status"] == "ready" for row in rows)
        invalid = any(row["status"] == "invalid" for row in rows)
        return {
            "schema": SCHEMA, "action": "status",
            "status": ("invalid" if invalid else
                       "ready" if ready == len(rows) else "pending"),
            "reason": None, "provider_state": provider_state,
            "input_plan_id": input_plan["plan_id"],
            "release_version": input_plan["version"],
            "acquisition_plan_id": plan["plan_id"],
            "required_file_count": plan["required_count"],
            "optional_file_count": plan["optional_count"],
            "selected_file_count": len(rows), "ready_file_count": ready,
            "files": rows, "override_plan_id": override_plan_id,
            "override_state": override_state,
            "composition_plan_id": composition_plan_id,
            "composition_state": composition_state,
        }

    def retain_overrides(self) -> dict[str, Any]:
        """Retain the selected release ZIP's official override members."""
        input_plan, archive, _, _, layout_path, _ = self._context()
        inputs = dict(
            archive_path=archive, archive_state_root=self.state_root,
            authority_path=self.authority_path,
            layout_policy_path=layout_path,
            state_root=self.state_root, config_home=self.config_home,
        )
        plan = plan_release_override_custody(input_plan, **inputs)
        result = apply_release_override_custody(
            input_plan, **inputs, expected_plan_id=plan["plan_id"],
        )
        return {
            "schema": SCHEMA, "action": "retain-overrides", "status": "ready",
            "input_plan_id": input_plan["plan_id"],
            "override_plan_id": plan["plan_id"],
            "override_tree_id": result["tree_id"],
            "override_file_count": result["override_file_count"],
            "outcome": result["outcome"],
        }

    def acquire_file(self, project_id: int, file_id: int) -> dict[str, Any]:
        """Download one exact ID for Textual progress, or reuse retained bytes."""
        input_plan, _, plan, _, _, _ = self._context()
        receipt = _receipt_path(plan, self.state_root, project_id, file_id)
        key = (None if receipt.exists() or receipt.is_symlink()
               else self._provider_key())
        result = acquire_curseforge_file(
            plan, project_id=project_id, file_id=file_id,
            state_root=self.state_root, provider_key=key,
        )
        return {
            "schema": SCHEMA, "action": "acquire-file", "status": "ready",
            "input_plan_id": input_plan["plan_id"],
            "acquisition_plan_id": plan["plan_id"],
            "project_id": project_id, "file_id": file_id,
            "filename": result["filename"], "size": result["size"],
            "sha256": result["sha256"], "outcome": result["outcome"],
        }

    def publish(self, *, override_plan_id: str) -> dict[str, Any]:
        """Close selected file custody and publish one installable V4 tree."""
        input_plan, _, plan, chosen, layout_path, resourcepack_path = self._context()
        acquired = publish_curseforge_acquisition(
            plan, state_root=self.state_root, config_home=self.config_home,
            expected_plan_id=plan["plan_id"],
        )
        inputs = dict(
            layout_policy_path=layout_path,
            resourcepack_policy_path=resourcepack_path,
            acquisition_plan_id=plan["plan_id"],
            override_plan_id=override_plan_id,
            state_root=self.state_root, config_home=self.config_home,
            optional_selected=chosen,
        )
        reviewed = plan_curseforge_client_composition(input_plan, **inputs)
        composition = apply_curseforge_client_composition(
            input_plan, **inputs, expected_plan_id=reviewed["plan_id"],
        )
        return {
            "schema": SCHEMA, "action": "publish", "status": "ready",
            "input_plan_id": input_plan["plan_id"],
            "acquisition_plan_id": plan["plan_id"],
            "acquisition_tree_id": acquired["tree_id"],
            "override_plan_id": override_plan_id,
            "composition_plan_id": reviewed["plan_id"],
            "composition_result": composition,
        }

    def reopen_composition(self, *, override_plan_id: str,
                           expected_plan_id: str) -> dict[str, Any]:
        """Reopen a saved official setup choice and its exact Core source tree."""
        input_plan, _, plan, chosen, layout_path, resourcepack_path = self._context()
        result = reopen_curseforge_client_composition(
            input_plan, layout_policy_path=layout_path,
            resourcepack_policy_path=resourcepack_path,
            acquisition_plan_id=plan["plan_id"],
            override_plan_id=override_plan_id,
            optional_selected=chosen,
            state_root=self.state_root, config_home=self.config_home,
            expected_plan_id=expected_plan_id,
        )
        return {
            "schema": SCHEMA, "action": "reopen-composition", "status": "ready",
            "input_plan_id": input_plan["plan_id"],
            "acquisition_plan_id": plan["plan_id"],
            "override_plan_id": override_plan_id,
            "composition_plan_id": expected_plan_id,
            "composition_result": result,
        }

    def reopen_by_plan_id(self, expected_plan_id: str) -> dict[str, Any]:
        """Recover a saved V4 source regardless of the current release choice."""
        result = reopen_curseforge_client_composition_by_plan_id(
            expected_plan_id=expected_plan_id,
            state_root=self.state_root, config_home=self.config_home,
        )
        return {
            "schema": SCHEMA, "action": "reopen-by-plan-id", "status": "ready",
            "input_plan_id": result["input_plan_id"],
            "composition_plan_id": expected_plan_id,
            "composition_result": result,
        }


__all__ = ["SCHEMA", "FreshReleaseUnavailable", "OfficialFreshReleaseService"]
