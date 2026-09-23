"""Failure-first tests for the additive Workspace Home V2 core."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import io
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock


MODULE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = MODULE_ROOT.parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "modules/project-intelligence/src"))
sys.path.insert(0, str(REPOSITORY_ROOT / "modules/atlas/src"))
sys.path.insert(0, str(REPOSITORY_ROOT / "modules/blueprints/src"))
sys.path.insert(0, str(REPOSITORY_ROOT / "modules/crucible/src"))
sys.path.insert(0, str(MODULE_ROOT / "src"))

from workbench_core.development import enable_source_checkout  # noqa: E402
enable_source_checkout(REPOSITORY_ROOT)
from workbench_shell.catalog import build_catalog  # noqa: E402
from workbench_api.state_paths import (  # noqa: E402
    default_product_spine_state_root,
)
from workbench_api.profiles import profile_scope  # noqa: E402
from workbench_core.human_presentation import human_command  # noqa: E402
from workbench_shell.work_session import WorkSessionStore  # noqa: E402
from workbench_shell.workspace_dashboard import (  # noqa: E402
    OwnerRecordPort,
    WorkspaceHomeV2Error,
    _adoption_state_revision,
    _canonical_bytes,
    _eligibility_digest,
    _home_id,
    _read_adoption,
    _stable_file_custody,
    adopt_workspace_home_v2,
    build_workspace_home_v2,
    load_product_capability_owner_port,
    load_workspace_home_adoption,
    render_workspace_home_v2,
    reopen_workspace_home_v2,
    validate_workspace_home_v2,
    work_session_summary_owner_port,
    workspace_home_adoption_exists,
    workspace_home_binding_id,
)


class _TTY(io.StringIO):
    def isatty(self) -> bool:
        return True


def _adopt_in_process(
    workspace_text: str,
    state_text: str,
    output: multiprocessing.Queue,
) -> None:
    try:
        result = adopt_workspace_home_v2(
            REPOSITORY_ROOT,
            Path(workspace_text),
            state_root=Path(state_text),
        )
    except WorkspaceHomeV2Error as exc:
        output.put(("error", str(exc)))
    else:
        output.put(("ok", result["adoption"]["binding_id"]))


def _tree_snapshot(root: Path) -> list[tuple[str, str, int]]:
    rows: list[tuple[str, str, int]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            rows.append((relative, "symlink:" + os.readlink(path), 0))
        elif path.is_dir():
            rows.append((relative, "directory", path.stat().st_mode & 0o777))
        else:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            rows.append((relative, digest, path.stat().st_mode & 0o777))
    return rows


class WorkspaceHomeV2Tests(unittest.TestCase):
    def _workspace(self, base: Path, *, searchable: bool = False) -> Path:
        workspace = base / "workspace"
        workspace.mkdir()
        if searchable:
            source = workspace / "src/main/groovy"
            source.mkdir(parents=True)
            (source / "Example.groovy").write_text(
                "println 'cleanroom'\n",
                encoding="utf-8",
            )
        return workspace

    def _session_port(
        self,
        base: Path,
        workspace: Path,
    ) -> tuple[dict[str, object], OwnerRecordPort]:
        initial = build_workspace_home_v2(REPOSITORY_ROOT, workspace)
        store = WorkSessionStore(base / "session-state")
        created = store.create(
            task={
                "task_id": "workspace-home-test",
                "owner_id": "workbench-shell",
                "label": "Workspace Home V2",
                "owner_record_ref": None,
            },
            workspace={
                "identity_id": initial["workspace"]["workspace_id"],
                "canonical_root": initial["workspace"]["root"],
                "root_uri": None,
                "source_revision": initial["workspace"]["source_revision"],
                "dirty_fingerprint": initial["workspace"]["dirty_fingerprint"],
            },
            identities={
                "core_id": "workbench-shell:v2",
                "catalog_id": "workbench-command-catalog:v2",
                "host_adapter_id": "crucible-host-adapter:v3",
                "platform_profile_id": "cleanroom:provisional",
                "pack_profile_id": None,
            },
            frontend={
                "frontend_id": "workbench-home-v2:test",
                "kind": "test",
                "version": "1",
                "instance_id": None,
                "process_id": None,
            },
        )
        summary = store.status(str(created["session_id"]))
        return summary, work_session_summary_owner_port(summary)

    def _git_commit(self, workspace: Path, message: str) -> None:
        subprocess.run(
            ["git", "add", "."],
            cwd=workspace,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Workbench Test",
                "-c",
                "user.email=workbench@example.invalid",
                "commit",
                "-m",
                message,
            ],
            cwd=workspace,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def test_unknown_workspace_exposes_only_the_exact_owner_admitted_new_kind(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            workspace = self._workspace(Path(temporary))
            home = build_workspace_home_v2(REPOSITORY_ROOT, workspace)
            self.assertEqual("workbench-workspace-home-v2", home["format"])
            self.assertLessEqual(len(home["jobs"]), 5)
            self.assertTrue(
                all("availability_basis" in job for job in home["jobs"])
            )
            self.assertEqual("available", home["new_project"]["state"])
            self.assertEqual(
                ["workbench-new-project-kind:cleanroom-mod"],
                home["new_project"]["admitted_kinds"],
            )
            self.assertEqual([], home["new_project"]["blockers"])
            self.assertEqual(
                "workbench new cleanroom-mod preview --help",
                home["new_project"]["next_safe_action"],
            )
            self.assertEqual(1, len(home["new_project"]["owner_ref_ids"]))
            owner = next(
                row
                for row in home["owner_records"]
                if row["id"] == home["new_project"]["owner_ref_ids"][0]
            )
            self.assertEqual("new-project-construction", owner["kind"])
            self.assertEqual("cleanroom-platform-profile", owner["owner_id"])
            self.assertEqual("verified", owner["integrity"])

    def test_open_is_deterministic_read_only_and_does_not_use_network(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            workspace = self._workspace(Path(temporary), searchable=True)
            before = _tree_snapshot(workspace)
            registry = (
                REPOSITORY_ROOT
                / "modules/workbench-shell/data/component-registry-v2.json"
            )
            registry_before = (registry.read_bytes(), registry.stat().st_mtime_ns)
            with mock.patch(
                "socket.create_connection",
                side_effect=AssertionError("Home open attempted network access"),
            ):
                first = build_workspace_home_v2(REPOSITORY_ROOT, workspace)
                second = build_workspace_home_v2(REPOSITORY_ROOT, workspace)
            self.assertEqual(first, second)
            self.assertEqual("open", first["operation"])
            self.assertTrue(first["read_only"])
            self.assertEqual("none", first["local_state_effect"])
            self.assertEqual(before, _tree_snapshot(workspace))
            self.assertFalse((workspace / ".workbench").exists())
            self.assertEqual(
                registry_before,
                (registry.read_bytes(), registry.stat().st_mtime_ns),
            )

    def test_jobs_use_exact_catalog_action_digest_arguments_and_revision(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            workspace = self._workspace(Path(temporary), searchable=True)
            catalog_port = load_product_capability_owner_port(REPOSITORY_ROOT)
            home = build_workspace_home_v2(
                REPOSITORY_ROOT,
                workspace,
                capability_catalog_record=catalog_port,
            )
            catalog = build_catalog(REPOSITORY_ROOT)
            rows_by_command = {
                row["catalog_action"]["command_id"]: row
                for row in catalog_port.value["capabilities"]
                if row["catalog_action"] is not None
            }
            jobs = {row["id"]: row for row in home["jobs"]}
            health = jobs["workspace-health"]
            self.assertEqual("available", health["state"])
            self.assertEqual([], health["blockers"])
            self.assertEqual("doctor.inspect", health["command_id"])
            self.assertEqual(
                catalog.command("doctor.inspect").action_digest(
                    root=REPOSITORY_ROOT
                ),
                health["action_digest"],
            )
            self.assertEqual(
                {"workspace": home["workspace"]["root"]},
                health["arguments"],
            )
            self.assertEqual(
                home["workspace"]["workspace_revision"],
                health["eligibility_binding"]["workspace_revision"],
            )
            self.assertEqual(
                rows_by_command["doctor.inspect"],
                health["capability"],
            )
            self.assertEqual(
                health["capability_id"],
                health["capability"]["capability_id"],
            )
            self.assertEqual("experimental", health["capability"]["availability"])
            self.assertEqual(
                {"kind": "process", "registered": True, "executable": True},
                health["capability"]["handler"],
            )
            self.assertNotIn("owner_state", health["capability"])
            self.assertNotIn("evidence_level", health["capability"])
            self.assertNotIn("placement", health["capability"])
            doctor_ref = next(
                row
                for row in home["owner_records"]
                if row["kind"] == "workspace-context"
            )
            self.assertEqual(
                {
                    "kind": "owner-context-resolution",
                    "scope": "workspace-context",
                    "owner_ref_id": doctor_ref["id"],
                    "owner_record_revision": doctor_ref["record_revision"],
                    "command_id": "doctor.inspect",
                    "capability_id": health["capability_id"],
                    "global_capability_effect": "retained-unmodified",
                },
                health["availability_basis"],
            )
            search = jobs["search-workspace"]
            self.assertEqual("available", search["state"])
            self.assertEqual([], search["blockers"])
            self.assertEqual(
                rows_by_command["explorer.search"],
                search["capability"],
            )
            self.assertEqual(
                search["capability_id"],
                search["capability"]["capability_id"],
            )
            self.assertEqual(
                search["capability_key"],
                search["capability"]["capability_key"],
            )
            self.assertEqual(
                {
                    "project": home["workspace"]["root"],
                    "source": "project",
                },
                search["arguments"],
            )
            self.assertEqual(
                catalog_port.value["catalog_id"],
                home["capability_catalog"]["catalog_id"],
            )
            drifted_axis = deepcopy(home)
            drifted_health = next(
                row
                for row in drifted_axis["jobs"]
                if row["id"] == "workspace-health"
            )
            drifted_health["capability"]["handler"]["executable"] = False
            drifted_health["eligibility_digest"] = _eligibility_digest(
                drifted_health
            )
            drifted_axis["home_id"] = _home_id(drifted_axis)
            with self.assertRaisesRegex(
                WorkspaceHomeV2Error,
                "exact product capability",
            ):
                validate_workspace_home_v2(
                    drifted_axis,
                    suite_root=REPOSITORY_ROOT,
                )
            drifted_basis = deepcopy(home)
            drifted_health = next(
                row
                for row in drifted_basis["jobs"]
                if row["id"] == "workspace-health"
            )
            drifted_health["availability_basis"][
                "global_capability_effect"
            ] = "authoritative"
            drifted_health["eligibility_digest"] = _eligibility_digest(
                drifted_health
            )
            drifted_basis["home_id"] = _home_id(drifted_basis)
            with self.assertRaisesRegex(
                WorkspaceHomeV2Error,
                "availability basis",
            ):
                validate_workspace_home_v2(
                    drifted_basis,
                    suite_root=REPOSITORY_ROOT,
                )
            invented_context = deepcopy(home)
            invented_search = next(
                row
                for row in invented_context["jobs"]
                if row["id"] == "search-workspace"
            )
            invented_search["availability_basis"] = {
                "kind": "owner-context-resolution",
                "scope": "workspace-context",
                "owner_ref_id": doctor_ref["id"],
                "owner_record_revision": doctor_ref["record_revision"],
                "command_id": invented_search["command_id"],
                "capability_id": invented_search["capability_id"],
                "global_capability_effect": "retained-unmodified",
            }
            invented_search["eligibility_digest"] = _eligibility_digest(
                invented_search
            )
            invented_context["home_id"] = _home_id(invented_context)
            with self.assertRaisesRegex(
                WorkspaceHomeV2Error,
                "availability basis",
            ):
                validate_workspace_home_v2(
                    invented_context,
                    suite_root=REPOSITORY_ROOT,
                )
            stale_context = deepcopy(home)
            stale_doctor_ref = next(
                row
                for row in stale_context["owner_records"]
                if row["kind"] == "workspace-context"
            )
            stale_doctor_ref["freshness"] = "stale"
            stale_context["home_id"] = _home_id(stale_context)
            with self.assertRaisesRegex(
                WorkspaceHomeV2Error,
                "Project Intelligence revision",
            ):
                validate_workspace_home_v2(
                    stale_context,
                    suite_root=REPOSITORY_ROOT,
                )

    def test_cleanroom_daily_loop_fixture_retains_exact_t01_catalog_rows(self) -> None:
        workspace = (
            REPOSITORY_ROOT
            / "profiles/platforms/cleanroom/fixtures/generic-mod-daily-loop"
        )
        catalog_port = load_product_capability_owner_port(REPOSITORY_ROOT)
        with mock.patch.dict(
            os.environ,
            {
                "WORKBENCH_CLEANROOM_FIXTURE_GRADLEW": "",
                "WORKBENCH_CLEANROOM_FIXTURE_JAVA_HOME": "",
            },
        ):
            missing_tools = build_workspace_home_v2(
                REPOSITORY_ROOT,
                workspace,
                capability_catalog_record=catalog_port,
            )
        missing_job = next(
            row
            for row in missing_tools["jobs"]
            if row["command_id"] == "cleanroom.fixture-build"
        )
        self.assertEqual("unavailable", missing_job["state"])
        self.assertEqual(
            {
                "gradle_cmd": None,
                "java_home": None,
                "expected_input_digest": None,
                "state_root": str(
                    default_product_spine_state_root(REPOSITORY_ROOT)
                    / "cleanroom-fixture"
                ),
            },
            missing_job["arguments"],
        )
        self.assertIn(
            "CLEANROOM_FIXTURE_TOOL_CONFIGURATION_REQUIRED",
            missing_job["blockers"],
        )
        self.assertNotIn("PRODUCT_CAPABILITY_UNAVAILABLE", missing_job["blockers"])
        self.assertEqual(
            "cleanroom-fixture",
            missing_job["availability_basis"]["scope"],
        )
        self.assertEqual(
            missing_job["capability"],
            next(
                row
                for row in catalog_port.value["capabilities"]
                if (row.get("catalog_action") or {}).get("command_id")
                == "cleanroom.fixture-build"
            ),
        )
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            tool_root = Path(temporary)
            gradle = tool_root / "gradle"
            gradle.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            gradle.chmod(0o700)
            java_home = tool_root / "java"
            (java_home / "bin").mkdir(parents=True)
            (java_home / "release").write_text(
                'JAVA_VERSION="25.0.1"\n',
                encoding="utf-8",
            )
            java = java_home / "bin/java"
            java.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            java.chmod(0o700)
            with mock.patch.dict(
                os.environ,
                {
                    "WORKBENCH_CLEANROOM_FIXTURE_GRADLEW": str(gradle),
                    "WORKBENCH_CLEANROOM_FIXTURE_JAVA_HOME": str(java_home),
                },
            ):
                home = build_workspace_home_v2(
                    REPOSITORY_ROOT,
                    workspace,
                    capability_catalog_record=catalog_port,
                )
        rows_by_command = {
            row["catalog_action"]["command_id"]: row
            for row in catalog_port.value["capabilities"]
            if row["catalog_action"] is not None
        }
        for job in home["jobs"]:
            if job["command_id"] is None:
                continue
            self.assertEqual(
                rows_by_command[job["command_id"]],
                job["capability"],
            )
            if job["command_id"] == "doctor.inspect":
                self.assertEqual("available", job["state"])
                self.assertEqual(
                    "owner-context-resolution",
                    job["availability_basis"]["kind"],
                )
            elif job["command_id"] == "cleanroom.fixture-build":
                self.assertEqual("available", job["state"])
                self.assertEqual([], job["blockers"])
                self.assertEqual(
                    {
                        "kind": "owner-context-resolution",
                        "scope": "cleanroom-fixture",
                        "owner_ref_id": job["availability_basis"]["owner_ref_id"],
                        "owner_record_revision": job["availability_basis"][
                            "owner_record_revision"
                        ],
                        "command_id": "cleanroom.fixture-build",
                        "capability_id": job["capability_id"],
                        "global_capability_effect": "retained-unmodified",
                    },
                    job["availability_basis"],
                )
                self.assertEqual("experimental", job["capability"]["availability"])
                self.assertEqual(
                    {"kind": "process", "registered": True, "executable": True},
                    job["capability"]["handler"],
                )
            else:
                self.assertEqual("available", job["state"])
                self.assertNotIn("PRODUCT_CAPABILITY_UNAVAILABLE", job["blockers"])

    def test_canonical_cleanroom_fixture_build_requires_explicit_tools(self) -> None:
        workspace = (
            REPOSITORY_ROOT
            / "profiles/platforms/cleanroom/fixtures/generic-mod-daily-loop"
        )
        with mock.patch.dict(
            os.environ,
            {
                "WORKBENCH_CLEANROOM_FIXTURE_GRADLEW": "",
                "WORKBENCH_CLEANROOM_FIXTURE_JAVA_HOME": "",
            },
        ):
            home = build_workspace_home_v2(REPOSITORY_ROOT, workspace)
        job = next(
            row
            for row in home["jobs"]
            if row["id"] == "cleanroom-fixture-build"
        )
        self.assertEqual("unavailable", job["state"])
        self.assertIn(
            "CLEANROOM_FIXTURE_TOOL_CONFIGURATION_REQUIRED",
            job["blockers"],
        )
        self.assertEqual(
            {
                "gradle_cmd": None,
                "java_home": None,
                "expected_input_digest": None,
                "state_root": str(
                    default_product_spine_state_root(REPOSITORY_ROOT)
                    / "cleanroom-fixture"
                ),
            },
            job["arguments"],
        )
        self.assertIn(
            "WORKBENCH_CLEANROOM_FIXTURE_GRADLEW",
            job["next_safe_action"],
        )
        fixture_ref = next(
            row
            for row in home["owner_records"]
            if row["kind"] == "cleanroom-fixture-lock"
        )
        self.assertEqual("cleanroom-platform-profile", fixture_ref["owner_id"])
        self.assertEqual("current", fixture_ref["freshness"])
        stale_catalog = deepcopy(home)
        stale_job = next(
            row
            for row in stale_catalog["jobs"]
            if row["id"] == "cleanroom-fixture-build"
        )
        stale_job["action_digest"] = "sha256:" + "0" * 64
        stale_job["eligibility_digest"] = _eligibility_digest(stale_job)
        stale_catalog["home_id"] = _home_id(stale_catalog)
        with mock.patch.dict(
            os.environ,
            {
                "WORKBENCH_CLEANROOM_FIXTURE_GRADLEW": "",
                "WORKBENCH_CLEANROOM_FIXTURE_JAVA_HOME": "",
            },
        ):
            with self.assertRaisesRegex(
                WorkspaceHomeV2Error,
                "stale catalog action",
            ):
                validate_workspace_home_v2(
                    stale_catalog,
                    suite_root=REPOSITORY_ROOT,
                )

    def test_cleanroom_fixture_context_is_not_inferred_for_a_copied_tree(self) -> None:
        fixture = (
            REPOSITORY_ROOT
            / "profiles/platforms/cleanroom/fixtures/generic-mod-daily-loop"
        )
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            copied = Path(temporary) / "copied-fixture"
            shutil.copytree(fixture, copied)
            home = build_workspace_home_v2(REPOSITORY_ROOT, copied)
            self.assertNotIn(
                "cleanroom-fixture-build",
                {row["id"] for row in home["jobs"]},
            )
            self.assertFalse(
                any(
                    row["kind"] == "cleanroom-fixture-lock"
                    for row in home["owner_records"]
                )
            )

    def test_disabled_cleanroom_profile_cannot_supply_fixture_authority(self) -> None:
        fixture = (
            REPOSITORY_ROOT
            / "profiles/platforms/cleanroom/fixtures/generic-mod-daily-loop"
        )
        with profile_scope(disabled={"cleanroom"}):
            home = build_workspace_home_v2(REPOSITORY_ROOT, fixture)
        self.assertNotIn("cleanroom-fixture-build", {row["id"] for row in home["jobs"]})
        self.assertFalse(any(
            row["kind"] == "cleanroom-fixture-lock"
            for row in home["owner_records"]
        ))

    def test_cleanroom_fixture_lock_drift_is_retained_as_corrupt_owner(self) -> None:
        workspace = (
            REPOSITORY_ROOT
            / "profiles/platforms/cleanroom/fixtures/generic-mod-daily-loop"
        )

        with mock.patch(
            "workbench_profile_cleanroom.fixture_build._validate_fixture",
            side_effect=RuntimeError("injected complete-tree drift"),
        ):
            home = build_workspace_home_v2(REPOSITORY_ROOT, workspace)
        fixture_ref = next(
            row
            for row in home["owner_records"]
            if row["kind"] == "cleanroom-fixture-lock"
        )
        fixture_job = next(
            row
            for row in home["jobs"]
            if row["id"] == "cleanroom-fixture-build"
        )
        self.assertEqual("corrupt", fixture_ref["freshness"])
        self.assertEqual("failed", fixture_ref["integrity"])
        self.assertIn("CLEANROOM_FIXTURE_LOCK_INVALID", fixture_job["blockers"])
        self.assertEqual("unavailable", fixture_job["state"])

    def test_cleanroom_fixture_tool_preflight_rejects_symlink_and_wrong_java(self) -> None:
        workspace = (
            REPOSITORY_ROOT
            / "profiles/platforms/cleanroom/fixtures/generic-mod-daily-loop"
        )
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            base = Path(temporary)
            gradle = base / "gradle"
            gradle.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            gradle.chmod(0o700)
            gradle_link = base / "gradle-link"
            gradle_link.symlink_to(gradle)
            java_home = base / "java"
            (java_home / "bin").mkdir(parents=True)
            (java_home / "release").write_text(
                'JAVA_VERSION="17.0.1"\n',
                encoding="utf-8",
            )
            java = java_home / "bin/java"
            java.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            java.chmod(0o700)
            with mock.patch.dict(
                os.environ,
                {
                    "WORKBENCH_CLEANROOM_FIXTURE_GRADLEW": str(gradle_link),
                    "WORKBENCH_CLEANROOM_FIXTURE_JAVA_HOME": str(java_home),
                },
            ):
                symlinked = build_workspace_home_v2(REPOSITORY_ROOT, workspace)
            symlinked_job = next(
                row
                for row in symlinked["jobs"]
                if row["id"] == "cleanroom-fixture-build"
            )
            self.assertIn("CLEANROOM_FIXTURE_TOOL_INVALID", symlinked_job["blockers"])
            self.assertEqual(str(gradle_link), symlinked_job["arguments"]["gradle_cmd"])

            with mock.patch.dict(
                os.environ,
                {
                    "WORKBENCH_CLEANROOM_FIXTURE_GRADLEW": str(gradle),
                    "WORKBENCH_CLEANROOM_FIXTURE_JAVA_HOME": str(java_home),
                },
            ):
                wrong_java = build_workspace_home_v2(REPOSITORY_ROOT, workspace)
            wrong_java_job = next(
                row
                for row in wrong_java["jobs"]
                if row["id"] == "cleanroom-fixture-build"
            )
            self.assertIn("CLEANROOM_FIXTURE_TOOL_INVALID", wrong_java_job["blockers"])
            self.assertIn("Java 25", wrong_java_job["next_safe_action"])

    def test_cleanroom_fixture_tool_replacement_invalidates_retained_inputs(self) -> None:
        workspace = (
            REPOSITORY_ROOT
            / "profiles/platforms/cleanroom/fixtures/generic-mod-daily-loop"
        )
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            base = Path(temporary)
            gradle = base / "gradle"
            gradle.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            gradle.chmod(0o700)
            java_home = base / "java"
            (java_home / "bin").mkdir(parents=True)
            (java_home / "release").write_text(
                'JAVA_VERSION="25.0.1"\n',
                encoding="utf-8",
            )
            java = java_home / "bin/java"
            java.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            java.chmod(0o700)
            environment = {
                "WORKBENCH_CLEANROOM_FIXTURE_GRADLEW": str(gradle),
                "WORKBENCH_CLEANROOM_FIXTURE_JAVA_HOME": str(java_home),
            }
            with mock.patch.dict(os.environ, environment):
                home = build_workspace_home_v2(REPOSITORY_ROOT, workspace)
            job = next(
                row
                for row in home["jobs"]
                if row["id"] == "cleanroom-fixture-build"
            )
            self.assertFalse(
                any(
                    blocker.startswith("CLEANROOM_FIXTURE_TOOL")
                    for blocker in job["blockers"]
                )
            )
            self.assertIsNone(job["next_safe_action"])
            self.assertRegex(
                job["arguments"]["expected_input_digest"],
                r"^sha256:[0-9a-f]{64}$",
            )
            self.assertEqual(
                {
                    "fixture-cleanup-init",
                    "fixture-owner-lock",
                    "fixture-owner-schema",
                    "gradle-executable",
                    "java-executable",
                    "java-release",
                    "profile-preflight-tool",
                },
                {row["kind"] for row in job["tool_inputs"]},
            )
            gradle.write_text("#!/bin/sh\nexit 7\n", encoding="utf-8")
            with mock.patch.dict(os.environ, environment):
                with self.assertRaisesRegex(
                    WorkspaceHomeV2Error,
                    "preflight inputs are stale",
                ):
                    validate_workspace_home_v2(
                        home,
                        suite_root=REPOSITORY_ROOT,
                    )

    def test_untracked_bytes_not_only_path_and_count_bind_workspace_revision(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            workspace = self._workspace(Path(temporary))
            subprocess.run(
                ["git", "init", "--quiet"],
                cwd=workspace,
                check=True,
            )
            untracked = workspace / "same-path.txt"
            untracked.write_text("one\n", encoding="utf-8")
            first = build_workspace_home_v2(REPOSITORY_ROOT, workspace)
            untracked.write_text("two\n", encoding="utf-8")
            second = build_workspace_home_v2(REPOSITORY_ROOT, workspace)
            self.assertNotEqual(
                first["workspace"]["dirty_fingerprint"],
                second["workspace"]["dirty_fingerprint"],
            )
            self.assertNotEqual(
                first["workspace"]["workspace_revision"],
                second["workspace"]["workspace_revision"],
            )

    def test_file_custody_keeps_ctime_within_each_metadata_view(self) -> None:
        def metadata(**changes: int) -> SimpleNamespace:
            values = {
                "st_dev": 11,
                "st_ino": 22,
                "st_mode": stat.S_IFREG | 0o600,
                "st_nlink": 1,
                "st_size": 33,
                "st_mtime_ns": 44,
                "st_ctime_ns": 55,
            }
            values.update(changes)
            return SimpleNamespace(**values)

        path_before = metadata(st_ctime_ns=100)
        path_current = metadata(st_ctime_ns=100)
        handle_opened = metadata(st_ctime_ns=200)
        handle_after = metadata(st_ctime_ns=200)
        self.assertTrue(
            _stable_file_custody(
                path_before,
                handle_opened,
                handle_after,
                path_current,
            )
        )
        mutations = (
            (
                path_before,
                handle_opened,
                handle_after,
                metadata(st_ctime_ns=101),
            ),
            (
                path_before,
                handle_opened,
                metadata(st_ctime_ns=201),
                path_current,
            ),
            (
                path_before,
                metadata(st_ctime_ns=200, st_mtime_ns=45),
                metadata(st_ctime_ns=200, st_mtime_ns=45),
                path_current,
            ),
            (
                path_before,
                handle_opened,
                handle_after,
                metadata(st_ctime_ns=100, st_nlink=2),
            ),
        )
        for observed in mutations:
            with self.subTest(observed=observed):
                self.assertFalse(_stable_file_custody(*observed))

    def test_normal_home_hashing_accepts_stable_ntfs_ctime_views(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            workspace = self._workspace(Path(temporary))
            source = workspace / "gradle.properties"
            source.write_text("org.gradle.daemon=false\n", encoding="utf-8")
            baseline = build_workspace_home_v2(REPOSITORY_ROOT, workspace)
            measured = source.stat()
            real_stat = os.stat
            real_fstat = os.fstat

            def view(metadata: os.stat_result, *, ctime_ns: int) -> SimpleNamespace:
                values = {
                    field: getattr(metadata, field)
                    for field in (
                        "st_mode",
                        "st_dev",
                        "st_ino",
                        "st_nlink",
                        "st_uid",
                        "st_gid",
                        "st_size",
                        "st_atime_ns",
                        "st_mtime_ns",
                        "st_ctime_ns",
                    )
                }
                values["st_ctime_ns"] = ctime_ns
                return SimpleNamespace(**values)

            def path_stat(path: object, *args: object, **kwargs: object):
                metadata = real_stat(path, *args, **kwargs)
                if isinstance(path, (str, os.PathLike)) and Path(path) == source:
                    return view(metadata, ctime_ns=100)
                return metadata

            def handle_stat(descriptor: int):
                metadata = real_fstat(descriptor)
                if (metadata.st_dev, metadata.st_ino) == (
                    measured.st_dev,
                    measured.st_ino,
                ):
                    return view(metadata, ctime_ns=200)
                return metadata

            with mock.patch(
                "workbench_shell.workspace_dashboard.os.stat",
                side_effect=path_stat,
            ), mock.patch(
                "workbench_shell.workspace_dashboard.os.fstat",
                side_effect=handle_stat,
            ):
                observed = build_workspace_home_v2(REPOSITORY_ROOT, workspace)
            self.assertEqual(
                baseline["workspace"]["dirty_fingerprint"],
                observed["workspace"]["dirty_fingerprint"],
            )

    def test_stale_catalog_action_and_changed_arguments_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            workspace = self._workspace(Path(temporary))
            home = build_workspace_home_v2(REPOSITORY_ROOT, workspace)
            stale = deepcopy(home)
            stale_job = next(
                row for row in stale["jobs"] if row["id"] == "workspace-health"
            )
            stale_job["action_digest"] = "sha256:" + "0" * 64
            stale_job["eligibility_digest"] = _eligibility_digest(stale_job)
            stale["home_id"] = _home_id(stale)
            with self.assertRaisesRegex(WorkspaceHomeV2Error, "stale catalog action"):
                validate_workspace_home_v2(stale, suite_root=REPOSITORY_ROOT)
            changed = deepcopy(home)
            changed_job = next(
                row for row in changed["jobs"] if row["id"] == "workspace-health"
            )
            changed_job["arguments"] = {"workspace": "/different-workspace"}
            changed_job["eligibility_digest"] = _eligibility_digest(changed_job)
            changed["home_id"] = _home_id(changed)
            with self.assertRaisesRegex(WorkspaceHomeV2Error, "catalog action"):
                validate_workspace_home_v2(changed, suite_root=REPOSITORY_ROOT)

    def test_session_and_catalog_exact_source_identities_are_retained(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            base = Path(temporary)
            workspace = self._workspace(base, searchable=True)
            summary, session_port = self._session_port(base, workspace)
            catalog_port = load_product_capability_owner_port(REPOSITORY_ROOT)
            home = build_workspace_home_v2(
                REPOSITORY_ROOT,
                workspace,
                session_record=session_port,
                capability_catalog_record=catalog_port,
            )
            self.assertEqual(summary["session_id"], home["session"]["session_id"])
            self.assertEqual(
                summary["session_record_id"], home["session"]["record_id"]
            )
            self.assertEqual(
                catalog_port.value["catalog_id"],
                home["capability_catalog"]["catalog_id"],
            )
            self.assertEqual(
                home["capability_catalog"]["catalog_id"],
                home["capability_catalog"]["record_id"],
            )
            self.assertEqual("current", home["freshness"]["session"])
            forged = deepcopy(home)
            forged["session"]["session_id"] = "work-session-v2-" + "0" * 32
            forged["home_id"] = _home_id(forged)
            with self.assertRaisesRegex(WorkspaceHomeV2Error, "owner reference"):
                validate_workspace_home_v2(forged)

    def test_corrupt_owner_records_are_isolated_from_workspace_health(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            workspace = self._workspace(Path(temporary), searchable=True)

            def reject(_: object) -> None:
                raise ValueError("corrupt fixture")

            bad_session = OwnerRecordPort(
                kind="work-session",
                owner_id="workbench-shell",
                value={"format_version": "workbench-work-session-summary-v1"},
                validator=reject,
                expected_format="workbench-work-session-summary-v1",
            )
            bad_catalog = OwnerRecordPort(
                kind="product-capability-catalog",
                owner_id="workbench-shell",
                value={
                    "format": "workbench-product-capability-catalog-v1"
                },
                validator=reject,
                expected_format="workbench-product-capability-catalog-v1",
            )
            home = build_workspace_home_v2(
                REPOSITORY_ROOT,
                workspace,
                session_record=bad_session,
                capability_catalog_record=bad_catalog,
            )
            self.assertEqual("corrupt", home["session"]["freshness"])
            self.assertIsNone(home["session"]["session_id"])
            self.assertEqual("corrupt", home["capability_catalog"]["freshness"])
            health = next(row for row in home["jobs"] if row["id"] == "workspace-health")
            self.assertEqual("unavailable", health["state"])
            self.assertIn("CAPABILITY_CATALOG_CORRUPT", health["blockers"])
            self.assertNotIn("WORK_SESSION_CORRUPT", health["blockers"])
            self.assertIsNone(health["capability"])
            for job in home["jobs"]:
                if job["id"] != "workspace-health":
                    self.assertEqual("unavailable", job["state"])
                    self.assertIn("WORK_SESSION_CORRUPT", job["blockers"])
                    if job["command_id"] is not None:
                        self.assertIn("CAPABILITY_CATALOG_CORRUPT", job["blockers"])

    def test_adopt_and_reopen_bind_exact_session_outside_target(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            base = Path(temporary)
            workspace = self._workspace(base)
            state = base / "suite-state"
            summary, session_port = self._session_port(base, workspace)
            before = _tree_snapshot(workspace)
            opened = build_workspace_home_v2(REPOSITORY_ROOT, workspace)
            binding_id = workspace_home_binding_id(
                opened["workspace"]["workspace_id"]
            )
            self.assertFalse(
                workspace_home_adoption_exists(
                    REPOSITORY_ROOT,
                    binding_id,
                    state_root=state,
                )
            )
            adopted = adopt_workspace_home_v2(
                REPOSITORY_ROOT,
                workspace,
                state_root=state,
                session_record=session_port,
            )
            retained = load_workspace_home_adoption(
                REPOSITORY_ROOT,
                binding_id,
                state_root=state,
            )
            reopened = reopen_workspace_home_v2(
                REPOSITORY_ROOT,
                binding_id,
                state_root=state,
                session_record=session_port,
            )
            self.assertEqual(binding_id, adopted["adoption"]["binding_id"])
            self.assertEqual(summary["session_id"], retained["session_id"])
            self.assertEqual(summary["session_id"], reopened["session"]["session_id"])
            self.assertEqual("current", reopened["adoption"]["freshness"])
            self.assertTrue(
                workspace_home_adoption_exists(
                    REPOSITORY_ROOT,
                    binding_id,
                    state_root=state,
                )
            )
            self.assertEqual(before, _tree_snapshot(workspace))
            self.assertFalse((workspace / ".workbench").exists())

    def test_load_adoption_is_read_only_and_returns_a_defensive_copy(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            base = Path(temporary)
            workspace = self._workspace(base)
            state = base / "state"
            adopted = adopt_workspace_home_v2(
                REPOSITORY_ROOT,
                workspace,
                state_root=state,
            )
            binding_id = adopted["adoption"]["binding_id"]
            suffix = binding_id.rsplit(":", 1)[-1]
            binding_path = state / "workspace-home-v2/adoptions" / f"{suffix}.json"
            lock_path = state / "workspace-home-v2/locks" / f"{suffix}.lock"
            before = {
                path: (path.read_bytes(), path.stat().st_mtime_ns)
                for path in (binding_path, lock_path)
            }
            first = load_workspace_home_adoption(
                REPOSITORY_ROOT,
                binding_id,
                state_root=state,
            )
            first["owner_record_revisions"].clear()
            second = load_workspace_home_adoption(
                REPOSITORY_ROOT,
                binding_id,
                state_root=state,
            )
            self.assertTrue(second["owner_record_revisions"])
            self.assertEqual(
                before,
                {
                    path: (path.read_bytes(), path.stat().st_mtime_ns)
                    for path in (binding_path, lock_path)
                },
            )

    def test_interrupted_adoption_residue_is_recovered_and_reported(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            base = Path(temporary)
            workspace = self._workspace(base)
            state = base / "state"
            home = build_workspace_home_v2(REPOSITORY_ROOT, workspace)
            binding_id = workspace_home_binding_id(home["workspace"]["workspace_id"])
            suffix = binding_id.rsplit(":", 1)[-1]
            bindings = state / "workspace-home-v2/adoptions"
            locks = state / "workspace-home-v2/locks"
            bindings.mkdir(parents=True, mode=0o700)
            locks.mkdir(mode=0o700)
            state.chmod(0o700)
            (state / "workspace-home-v2").chmod(0o700)
            bindings.chmod(0o700)
            locks.chmod(0o700)
            orphan = bindings / f".{suffix}.json.interrupted.tmp"
            orphan.write_text("partial", encoding="utf-8")
            orphan.chmod(0o600)
            adopted = adopt_workspace_home_v2(
                REPOSITORY_ROOT,
                workspace,
                state_root=state,
            )
            self.assertTrue(orphan.exists())
            self.assertEqual(
                1,
                adopted["adoption"]["interrupted_write_count"],
            )
            self.assertEqual("required", adopted["adoption"]["recovery_state"])
            self.assertTrue(
                workspace_home_adoption_exists(
                    REPOSITORY_ROOT,
                    binding_id,
                    state_root=state,
                )
            )

    def test_concurrent_adoption_has_one_winner_and_no_corrupt_binding(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            base = Path(temporary)
            workspace = self._workspace(base)
            state = base / "state"
            context = multiprocessing.get_context("fork")
            output = context.Queue()
            workers = [
                context.Process(
                    target=_adopt_in_process,
                    args=(str(workspace), str(state), output),
                )
                for _ in range(2)
            ]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(20)
                self.assertFalse(worker.is_alive())
                self.assertEqual(0, worker.exitcode)
            results = [output.get(timeout=5) for _ in workers]
            self.assertEqual(1, sum(kind == "ok" for kind, _ in results))
            self.assertEqual(1, sum(kind == "error" for kind, _ in results))
            binding_id = next(value for kind, value in results if kind == "ok")
            self.assertTrue(
                workspace_home_adoption_exists(
                    REPOSITORY_ROOT,
                    binding_id,
                    state_root=state,
                )
            )

    def test_stale_revision_blocks_non_health_jobs_on_reopen(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            base = Path(temporary)
            workspace = self._workspace(base, searchable=True)
            subprocess.run(
                ["git", "init", "--quiet"],
                cwd=workspace,
                check=True,
            )
            self._git_commit(workspace, "initial")
            state = base / "state"
            adopted = adopt_workspace_home_v2(
                REPOSITORY_ROOT,
                workspace,
                state_root=state,
            )
            (workspace / "src/main/groovy/Example.groovy").write_text(
                "println 'changed'\n",
                encoding="utf-8",
            )
            self._git_commit(workspace, "change")
            reopened = reopen_workspace_home_v2(
                REPOSITORY_ROOT,
                adopted["adoption"]["binding_id"],
                state_root=state,
            )
            self.assertEqual("stale", reopened["adoption"]["freshness"])
            for job in reopened["jobs"]:
                if job["id"] == "workspace-health":
                    self.assertNotIn("ADOPTION_BINDING_STALE", job["blockers"])
                else:
                    self.assertEqual("unavailable", job["state"])
                    self.assertIn("ADOPTION_BINDING_STALE", job["blockers"])

    def test_reopen_rediscovering_physically_moved_workspace_keeps_old_binding(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            base = Path(temporary)
            workspace = self._workspace(base, searchable=True)
            state = base / "state"
            summary, session_port = self._session_port(base, workspace)
            adopted = adopt_workspace_home_v2(
                REPOSITORY_ROOT,
                workspace,
                state_root=state,
                session_record=session_port,
            )
            binding_id = adopted["adoption"]["binding_id"]
            original_workspace_id = adopted["workspace"]["workspace_id"]
            retained_before = load_workspace_home_adoption(
                REPOSITORY_ROOT,
                binding_id,
                state_root=state,
            )
            moved = base / "moved-workspace"
            os.rename(workspace, moved)

            with self.assertRaisesRegex(WorkspaceHomeV2Error, "workspace path"):
                reopen_workspace_home_v2(
                    REPOSITORY_ROOT,
                    binding_id,
                    state_root=state,
                    session_record=session_port,
                )

            reopened = reopen_workspace_home_v2(
                REPOSITORY_ROOT,
                binding_id,
                state_root=state,
                replacement_workspace_path=moved,
                session_record=session_port,
            )
            retained_after = load_workspace_home_adoption(
                REPOSITORY_ROOT,
                binding_id,
                state_root=state,
            )
            self.assertEqual(retained_before, retained_after)
            self.assertEqual(binding_id, reopened["adoption"]["binding_id"])
            self.assertEqual(
                original_workspace_id,
                reopened["adoption"]["adopted_workspace_id"],
            )
            self.assertEqual(
                str(workspace.resolve()),
                reopened["adoption"]["adopted_workspace_root"],
            )
            self.assertEqual(str(moved.resolve()), reopened["workspace"]["root"])
            self.assertNotEqual(
                original_workspace_id,
                reopened["workspace"]["workspace_id"],
            )
            self.assertEqual(summary["session_id"], reopened["session"]["session_id"])
            self.assertEqual("stale", reopened["session"]["freshness"])
            self.assertEqual("stale", reopened["adoption"]["freshness"])
            self.assertIn(
                "WORKSPACE_ID_CHANGED",
                reopened["adoption"]["stale_reasons"],
            )
            self.assertIn(
                "WORKSPACE_ROOT_CHANGED",
                reopened["adoption"]["stale_reasons"],
            )
            self.assertEqual(
                ["WORKSPACE_LOCATION_CHANGED"],
                reopened["adoption"]["recovery_reasons"],
            )
            self.assertEqual("required", reopened["adoption"]["recovery_state"])
            for job in reopened["jobs"]:
                self.assertEqual("unavailable", job["state"])
                self.assertIn("ADOPTION_BINDING_STALE", job["blockers"])
                self.assertIn("ADOPTION_RECOVERY_REQUIRED", job["blockers"])

    def test_reopen_compares_retained_owner_and_source_revisions(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            base = Path(temporary)
            workspace = self._workspace(base)
            state = base / "state"
            catalog = load_product_capability_owner_port(REPOSITORY_ROOT)
            adopted = adopt_workspace_home_v2(
                REPOSITORY_ROOT,
                workspace,
                state_root=state,
                capability_catalog_record=catalog,
            )
            binding_id = adopted["adoption"]["binding_id"]
            without_catalog = reopen_workspace_home_v2(
                REPOSITORY_ROOT,
                binding_id,
                state_root=state,
            )
            self.assertEqual("stale", without_catalog["adoption"]["freshness"])
            self.assertIn(
                "OWNER_RECORD_REVISIONS_CHANGED",
                without_catalog["adoption"]["stale_reasons"],
            )

            retained = load_workspace_home_adoption(
                REPOSITORY_ROOT,
                binding_id,
                state_root=state,
            )
            retained["source_revision"] = "unversioned:" + "0" * 64
            retained["state_revision"] = _adoption_state_revision(retained)
            suffix = binding_id.rsplit(":", 1)[-1]
            binding_path = state / "workspace-home-v2/adoptions" / f"{suffix}.json"
            binding_path.write_bytes(_canonical_bytes(retained))
            binding_path.chmod(0o600)
            changed_source = reopen_workspace_home_v2(
                REPOSITORY_ROOT,
                binding_id,
                state_root=state,
                capability_catalog_record=catalog,
            )
            self.assertEqual("stale", changed_source["adoption"]["freshness"])
            self.assertIn(
                "SOURCE_REVISION_CHANGED",
                changed_source["adoption"]["stale_reasons"],
            )

    def test_stale_work_session_is_retained_but_cannot_authorize_jobs(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            base = Path(temporary)
            workspace = self._workspace(base, searchable=True)
            subprocess.run(
                ["git", "init", "--quiet"],
                cwd=workspace,
                check=True,
            )
            self._git_commit(workspace, "initial")
            summary, session_port = self._session_port(base, workspace)
            (workspace / "src/main/groovy/Example.groovy").write_text(
                "println 'new revision'\n",
                encoding="utf-8",
            )
            self._git_commit(workspace, "next")
            catalog_port = load_product_capability_owner_port(REPOSITORY_ROOT)
            home = build_workspace_home_v2(
                REPOSITORY_ROOT,
                workspace,
                session_record=session_port,
                capability_catalog_record=catalog_port,
            )
            self.assertEqual(summary["session_id"], home["session"]["session_id"])
            self.assertEqual("stale", home["session"]["freshness"])
            health = next(row for row in home["jobs"] if row["id"] == "workspace-health")
            self.assertEqual("available", health["state"])
            self.assertNotIn("WORK_SESSION_STALE", health["blockers"])
            self.assertEqual(
                "owner-context-resolution",
                health["availability_basis"]["kind"],
            )
            for job in home["jobs"]:
                if job["id"] != "workspace-health":
                    self.assertIn("WORK_SESSION_STALE", job["blockers"])

    def test_corrupt_and_unsafe_adoption_state_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            base = Path(temporary)
            workspace = self._workspace(base)
            state = base / "state"
            adopted = adopt_workspace_home_v2(
                REPOSITORY_ROOT,
                workspace,
                state_root=state,
            )
            binding_id = adopted["adoption"]["binding_id"]
            suffix = binding_id.rsplit(":", 1)[-1]
            binding_path = state / "workspace-home-v2/adoptions" / f"{suffix}.json"
            binding_path.chmod(0o644)
            with self.assertRaisesRegex(WorkspaceHomeV2Error, "owner-private"):
                workspace_home_adoption_exists(
                    REPOSITORY_ROOT,
                    binding_id,
                    state_root=state,
                )
            binding_path.write_text("{}\n", encoding="utf-8")
            binding_path.chmod(0o600)
            with self.assertRaises(WorkspaceHomeV2Error):
                load_workspace_home_adoption(
                    REPOSITORY_ROOT,
                    binding_id,
                    state_root=state,
                )
            with self.assertRaises(WorkspaceHomeV2Error):
                workspace_home_adoption_exists(
                    REPOSITORY_ROOT,
                    binding_id,
                    state_root=state,
                )
            healthy = build_workspace_home_v2(REPOSITORY_ROOT, workspace)
            self.assertEqual("workspace-health", healthy["jobs"][0]["id"])
            self.assertIn(
                "CAPABILITY_CATALOG_UNAVAILABLE",
                healthy["jobs"][0]["blockers"],
            )

    def test_adoption_read_rejects_path_replacement_after_metadata_check(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            base = Path(temporary)
            workspace = self._workspace(base)
            state = base / "state"
            adopted = adopt_workspace_home_v2(
                REPOSITORY_ROOT,
                workspace,
                state_root=state,
            )
            binding_id = adopted["adoption"]["binding_id"]
            original = load_workspace_home_adoption(
                REPOSITORY_ROOT,
                binding_id,
                state_root=state,
            )
            replacement = deepcopy(original)
            replacement["source_revision"] = "unversioned:" + "f" * 64
            replacement["state_revision"] = _adoption_state_revision(replacement)
            suffix = binding_id.rsplit(":", 1)[-1]
            binding_path = state / "workspace-home-v2/adoptions" / f"{suffix}.json"
            replacement_path = binding_path.with_suffix(".replacement")
            replacement_path.write_bytes(_canonical_bytes(replacement))
            replacement_path.chmod(0o600)
            real_stat = os.stat
            replaced = False

            def replace_after_stat(path: object, *args: object, **kwargs: object):
                nonlocal replaced
                metadata = real_stat(path, *args, **kwargs)
                if Path(path) == binding_path and not replaced:
                    replaced = True
                    os.replace(replacement_path, binding_path)
                return metadata

            with mock.patch(
                "workbench_shell.workspace_dashboard.os.stat",
                side_effect=replace_after_stat,
            ):
                with self.assertRaises(WorkspaceHomeV2Error):
                    load_workspace_home_adoption(
                        REPOSITORY_ROOT,
                        binding_id,
                        state_root=state,
                    )

    def test_adoption_read_accepts_stable_ntfs_ctime_views(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            base = Path(temporary)
            workspace = self._workspace(base)
            state = base / "state"
            adopted = adopt_workspace_home_v2(
                REPOSITORY_ROOT,
                workspace,
                state_root=state,
            )
            binding_id = adopted["adoption"]["binding_id"]
            suffix = binding_id.rsplit(":", 1)[-1]
            binding_path = state / "workspace-home-v2/adoptions" / f"{suffix}.json"
            expected = load_workspace_home_adoption(
                REPOSITORY_ROOT,
                binding_id,
                state_root=state,
            )
            measured = binding_path.stat()
            real_stat = os.stat
            real_fstat = os.fstat

            def view(metadata: os.stat_result, *, ctime_ns: int) -> SimpleNamespace:
                values = {
                    field: getattr(metadata, field)
                    for field in (
                        "st_mode",
                        "st_dev",
                        "st_ino",
                        "st_nlink",
                        "st_uid",
                        "st_gid",
                        "st_size",
                        "st_atime_ns",
                        "st_mtime_ns",
                        "st_ctime_ns",
                    )
                }
                values["st_ctime_ns"] = ctime_ns
                return SimpleNamespace(**values)

            def path_stat(path: object, *args: object, **kwargs: object):
                metadata = real_stat(path, *args, **kwargs)
                if isinstance(path, (str, os.PathLike)) and Path(path) == binding_path:
                    return view(metadata, ctime_ns=100)
                return metadata

            def handle_stat(descriptor: int):
                metadata = real_fstat(descriptor)
                if (metadata.st_dev, metadata.st_ino) == (
                    measured.st_dev,
                    measured.st_ino,
                ):
                    return view(metadata, ctime_ns=200)
                return metadata

            with mock.patch(
                "workbench_shell.workspace_dashboard.os.stat",
                side_effect=path_stat,
            ), mock.patch(
                "workbench_shell.workspace_dashboard.os.fstat",
                side_effect=handle_stat,
            ):
                retained = load_workspace_home_adoption(
                    REPOSITORY_ROOT,
                    binding_id,
                    state_root=state,
                )
            self.assertEqual(expected, retained)

    def test_adoption_read_uses_acl_privacy_on_windows(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            base = Path(temporary)
            workspace = self._workspace(base)
            state = base / "state"
            adopted = adopt_workspace_home_v2(
                REPOSITORY_ROOT,
                workspace,
                state_root=state,
            )
            binding_id = adopted["adoption"]["binding_id"]
            suffix = binding_id.rsplit(":", 1)[-1]
            binding_path = state / "workspace-home-v2/adoptions" / f"{suffix}.json"
            expected = load_workspace_home_adoption(
                REPOSITORY_ROOT,
                binding_id,
                state_root=state,
            )
            binding_path.chmod(0o666)

            with mock.patch(
                "workbench_shell.workspace_dashboard.os.name",
                "nt",
            ), mock.patch(
                "workbench_shell.workspace_dashboard.private_path",
                return_value=True,
            ):
                self.assertEqual(expected, _read_adoption(binding_path))

            with mock.patch(
                "workbench_shell.workspace_dashboard.os.name",
                "nt",
            ), mock.patch(
                "workbench_shell.workspace_dashboard.private_path",
                return_value=False,
            ):
                with self.assertRaisesRegex(WorkspaceHomeV2Error, "owner-private"):
                    _read_adoption(binding_path)

            with mock.patch(
                "workbench_shell.workspace_dashboard.os.name",
                "posix",
            ), mock.patch(
                "workbench_shell.workspace_dashboard.private_path",
                return_value=True,
            ):
                with self.assertRaisesRegex(WorkspaceHomeV2Error, "owner-private"):
                    _read_adoption(binding_path)

    def test_adoption_publish_does_not_swallow_directory_fsync_failure(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            base = Path(temporary)
            workspace = self._workspace(base)
            state = base / "state"
            real_fsync = os.fsync

            def fail_directory_fsync(descriptor: int) -> None:
                if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                    raise OSError("injected directory fsync failure")
                real_fsync(descriptor)

            with mock.patch(
                "workbench_shell.workspace_dashboard.os.fsync",
                side_effect=fail_directory_fsync,
            ):
                with self.assertRaises(WorkspaceHomeV2Error):
                    adopt_workspace_home_v2(
                        REPOSITORY_ROOT,
                        workspace,
                        state_root=state,
                    )

    def test_workspace_state_and_binding_symlinks_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            base = Path(temporary)
            workspace = self._workspace(base)
            workspace_link = base / "workspace-link"
            workspace_link.symlink_to(workspace, target_is_directory=True)
            with self.assertRaisesRegex(WorkspaceHomeV2Error, "symlink"):
                build_workspace_home_v2(REPOSITORY_ROOT, workspace_link)
            actual_state = base / "actual-state"
            actual_state.mkdir(mode=0o700)
            state_link = base / "state-link"
            state_link.symlink_to(actual_state, target_is_directory=True)
            with self.assertRaisesRegex(WorkspaceHomeV2Error, "symlink"):
                adopt_workspace_home_v2(
                    REPOSITORY_ROOT,
                    workspace,
                    state_root=state_link,
                )
            state = base / "state"
            adopted = adopt_workspace_home_v2(
                REPOSITORY_ROOT,
                workspace,
                state_root=state,
            )
            binding_id = adopted["adoption"]["binding_id"]
            suffix = binding_id.rsplit(":", 1)[-1]
            binding_path = state / "workspace-home-v2/adoptions" / f"{suffix}.json"
            target = base / "target.json"
            target.write_text(json.dumps({"not": "an adoption"}), encoding="utf-8")
            binding_path.unlink()
            binding_path.symlink_to(target)
            with self.assertRaises(WorkspaceHomeV2Error):
                workspace_home_adoption_exists(
                    REPOSITORY_ROOT,
                    binding_id,
                    state_root=state,
                )

    def test_repeated_adopt_and_session_substitution_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            base = Path(temporary)
            workspace = self._workspace(base)
            state = base / "state"
            summary, session_port = self._session_port(base, workspace)
            adopted = adopt_workspace_home_v2(
                REPOSITORY_ROOT,
                workspace,
                state_root=state,
                session_record=session_port,
            )
            with self.assertRaisesRegex(WorkspaceHomeV2Error, "already adopted"):
                adopt_workspace_home_v2(
                    REPOSITORY_ROOT,
                    workspace,
                    state_root=state,
                    session_record=session_port,
                )
            replacement = OwnerRecordPort(
                kind="work-session",
                owner_id="workbench-shell",
                value={"format_version": "workbench-work-session-summary-v1"},
                validator=lambda _: (_ for _ in ()).throw(ValueError("bad")),
                expected_format="workbench-work-session-summary-v1",
            )
            with self.assertRaisesRegex(WorkspaceHomeV2Error, "differs"):
                reopen_workspace_home_v2(
                    REPOSITORY_ROOT,
                    adopted["adoption"]["binding_id"],
                    state_root=state,
                    session_record=replacement,
                )
            retained = load_workspace_home_adoption(
                REPOSITORY_ROOT,
                adopted["adoption"]["binding_id"],
                state_root=state,
            )
            self.assertEqual(summary["session_id"], retained["session_id"])

    def test_renderer_makes_workspace_control_characters_inert(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            workspace = Path(temporary) / "workspace\n\x1b[31m"
            workspace.mkdir()
            home = build_workspace_home_v2(REPOSITORY_ROOT, workspace)
            rendered = render_workspace_home_v2(home)
            self.assertNotIn("\x1b", rendered)
            self.assertIn("\\n", rendered)
            self.assertIn("\\x1b", rendered)

    def test_renderer_groups_unavailable_jobs_as_plain_english_setup(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            workspace = self._workspace(Path(temporary))
            home = build_workspace_home_v2(REPOSITORY_ROOT, workspace)
            contract_before = deepcopy(home)
            rendered = render_workspace_home_v2(home)

            self.assertIn("Workbench Home", rendered)
            self.assertIn("[ATTENTION]", rendered)
            self.assertIn("Ready now", rendered)
            self.assertIn("Needs setup", rendered)
            self.assertIn("Start here  →  workbench setup", rendered)
            self.assertIn("[ATTENTION] Check workspace health", rendered)
            self.assertIn("[ATTENTION] Search this workspace", rendered)
            self.assertIn("[ATTENTION] Run a development client", rendered)
            self.assertIn("Open a project folder that contains source", rendered)
            self.assertIn(
                "Select a supported Cleanroom development profile",
                rendered,
            )
            self.assertNotIn("Status: attention", rendered)
            for job in home["jobs"]:
                for blocker in job["blockers"]:
                    self.assertNotIn(blocker, rendered)
            self.assertEqual(contract_before, home)

    def test_renderer_shows_ready_job_as_public_copyable_command(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            base = Path(temporary) / "parent with spaces"
            base.mkdir()
            workspace = self._workspace(base)
            home = build_workspace_home_v2(REPOSITORY_ROOT, workspace)
            health = home["jobs"][0]
            health.update(
                {
                    "state": "available",
                    "argv": [
                        sys.executable,
                        str(REPOSITORY_ROOT / "tools/workbench.py"),
                        "doctor",
                        str(workspace),
                    ],
                    "blockers": [],
                    "unavailable_reason": None,
                }
            )

            # The renderer normally accepts only a validated Home record. This
            # test isolates presentation so it does not depend on refreshing
            # the source-fingerprinted canonical catalog fixture.
            with mock.patch(
                "workbench_shell.workspace_dashboard.validate_workspace_home_v2"
            ):
                rendered = render_workspace_home_v2(home)

            self.assertIn(
                "[READY] Check workspace health  →  "
                + human_command(["workbench", "doctor", str(workspace)]),
                rendered,
            )
            self.assertNotIn(str(REPOSITORY_ROOT / "tools/workbench.py"), rendered)
            self.assertNotIn(sys.executable, rendered)

    def test_renderer_presents_new_project_as_a_ready_action(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            workspace = self._workspace(Path(temporary))
            home = build_workspace_home_v2(REPOSITORY_ROOT, workspace)

            self.assertEqual("available", home["new_project"]["state"])
            rendered = render_workspace_home_v2(home)

            self.assertIn(
                "[READY] Create a Cleanroom mod project  →  "
                + home["new_project"]["next_safe_action"],
                rendered,
            )
            self.assertNotIn(home["new_project"]["reason"], rendered)
            for kind in home["new_project"]["admitted_kinds"]:
                self.assertNotIn(kind, rendered)

    def test_renderer_colors_only_semantic_labels_for_a_tty(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            workspace = self._workspace(Path(temporary))
            home = build_workspace_home_v2(REPOSITORY_ROOT, workspace)

            rendered = render_workspace_home_v2(
                home,
                stream=_TTY(),
                environ={"TERM": "xterm-256color"},
            )
            no_color = render_workspace_home_v2(
                home,
                stream=_TTY(),
                environ={"NO_COLOR": ""},
            )

            self.assertIn("\x1b[33m[ATTENTION]\x1b[0m", rendered)
            self.assertIn("[ATTENTION]", no_color)
            self.assertNotIn("\x1b[", no_color)


if __name__ == "__main__":
    unittest.main()
