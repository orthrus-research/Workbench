"""Focused contract tests for external existing-project qualification."""

from __future__ import annotations

from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


MODULE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = MODULE_ROOT.parents[1]
TEST_TEMP_ROOT = REPOSITORY_ROOT / ".workbench" / "qualification-test-tmp"
for source in (
    REPOSITORY_ROOT / "modules/crucible/src",
    REPOSITORY_ROOT / "modules/project-intelligence/src",
    MODULE_ROOT / "src",
):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from supersymmetry_project_fixture import (  # noqa: E402
    create_supersymmetry_project,
)
from workbench_shell.project_qualification import (  # noqa: E402
    PLAN_FORMAT,
    STATUS_FORMAT,
    ProjectQualificationError,
    _git_directory,
    _identity,
    apply_qualification_plan,
    build_qualification_plan,
    main,
    qualification_status,
)
import workbench_shell.project_qualification as qualification_module  # noqa: E402


def _temporary_directory() -> tempfile.TemporaryDirectory[str]:
    """Keep security-sensitive fixtures on the checkout's native filesystem.

    WSL commonly inherits ``TEMP`` from Windows.  Python then places its
    default temporary directories on DrvFS, where POSIX owner-only modes
    cannot be established.  The ignored Workbench state area is native on
    every supported checkout and avoids a hard-coded Unix-only temp path.
    """

    TEST_TEMP_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt":
        TEST_TEMP_ROOT.chmod(0o700)
    return tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT)


class _TerminalBuffer(StringIO):
    def isatty(self) -> bool:
        return True


class _InterruptOnWrite(StringIO):
    def write(self, value: str) -> int:
        raise KeyboardInterrupt


def _status(project: Path, state_root: Path) -> dict[str, object]:
    return qualification_status(
        REPOSITORY_ROOT,
        project,
        profile_selector="supersymmetry",
        state_root=state_root,
    )


def _git_status(project: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(project), "status", "--porcelain=v1"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


class ProjectQualificationTests(unittest.TestCase):
    maxDiff = None

    def test_status_is_read_only_and_reports_an_absent_ready_binding(self) -> None:
        with _temporary_directory() as temporary:
            root = Path(temporary)
            project = create_supersymmetry_project(root)
            state_root = root / "external-state"
            before = _git_status(project)

            status = _status(project, state_root)

            self.assertEqual(STATUS_FORMAT, status["format"])
            self.assertEqual("read-only", status["operation_class"])
            self.assertEqual("ready", status["state"])
            self.assertTrue(status["can_apply"])
            self.assertFalse(status["qualified"])
            self.assertEqual("absent", status["binding"]["state"])
            self.assertEqual(
                ["profile-conformance", "packwiz-index-integrity"],
                [row["id"] for row in status["checks"]],
            )
            self.assertEqual([], status["workspace"]["dirty_entries"])
            self.assertFalse(state_root.exists())
            self.assertEqual(before, _git_status(project))
            self.assertFalse((project / ".workbench").exists())

    def test_nested_pack_is_rejected_before_repository_wide_git_observation(self) -> None:
        with _temporary_directory() as temporary:
            root = Path(temporary)
            repository = root / "repository"
            repository.mkdir()
            project = create_supersymmetry_project(repository)
            sibling = repository / "sibling-private-name.txt"
            sibling.write_text("sibling-private-content\n", encoding="utf-8")
            calls: list[tuple[str, ...]] = []

            def observe(observed_workspace: Path, arguments: tuple[str, ...]):
                calls.append(tuple(arguments))
                if tuple(arguments) == ("rev-parse", "--absolute-git-dir"):
                    if Path(observed_workspace) == project:
                        return subprocess.CompletedProcess(
                            args=[],
                            returncode=0,
                            stdout=str(project / ".git") + "\n",
                            stderr="",
                        )
                    return subprocess.CompletedProcess(
                        args=[],
                        returncode=0,
                        stdout=str(REPOSITORY_ROOT / ".git") + "\n",
                        stderr="",
                    )
                if tuple(arguments) == ("rev-parse", "--show-toplevel"):
                    return subprocess.CompletedProcess(
                        args=[],
                        returncode=0,
                        stdout=str(repository) + "\n",
                        stderr="",
                    )
                self.fail(f"unscoped Git observation ran for nested pack: {arguments}")

            with patch(
                "workbench_shell.project_qualification.run_git_observation",
                side_effect=observe,
            ), patch(
                "workbench_shell.project_qualification._workspace_content_fingerprint"
            ) as fingerprint:
                with self.assertRaisesRegex(
                    ProjectQualificationError,
                    "must be the Git repository root",
                ) as raised:
                    _status(project, root / "state")

            self.assertEqual(
                [
                    ("rev-parse", "--absolute-git-dir"),
                    ("rev-parse", "--absolute-git-dir"),
                    ("rev-parse", "--show-toplevel"),
                ],
                calls,
            )
            fingerprint.assert_not_called()
            self.assertNotIn(sibling.name, str(raised.exception))
            self.assertNotIn("sibling-private-content", str(raised.exception))

    def test_index_mismatch_is_attention_and_can_be_qualified_externally(self) -> None:
        with _temporary_directory() as temporary:
            root = Path(temporary)
            project = create_supersymmetry_project(root)
            state_root = root / "external-state"
            (project / "index.toml").write_text("\n", encoding="utf-8")
            before = _git_status(project)

            status = _status(project, state_root)
            plan = build_qualification_plan(status)

            self.assertEqual("attention", status["state"])
            self.assertTrue(status["can_apply"])
            self.assertEqual("attention", status["checks"][1]["state"])
            self.assertIn("Packwiz index", status["limitations"][1])
            self.assertEqual(PLAN_FORMAT, plan["format"])
            self.assertEqual("atomic-private-record-create", plan["actions"][0]["operation"])

            result = apply_qualification_plan(
                REPOSITORY_ROOT,
                project,
                profile_selector="supersymmetry",
                state_root=state_root,
                expected_plan_id=plan["plan_id"],
            )

            self.assertEqual("qualified", result["outcome"])
            self.assertEqual("attention", result["qualification"]["state"])
            self.assertTrue(result["qualification"]["qualified"])
            self.assertEqual(
                [
                    [
                        "workbench",
                        "project",
                        "qualify",
                        str(project),
                        "--profile",
                        "supersymmetry",
                        "--status",
                        "--state-root",
                        str(state_root.resolve()),
                    ]
                ],
                result["next_commands"],
            )
            binding_path = Path(result["binding"]["path"])
            self.assertTrue(binding_path.is_file())
            if os.name != "nt":
                self.assertEqual(0o600, binding_path.stat().st_mode & 0o777)
            self.assertEqual(before, _git_status(project))
            self.assertFalse((project / ".workbench").exists())

    def test_normal_source_drift_between_plan_and_apply_is_provenance_only(self) -> None:
        with _temporary_directory() as temporary:
            root = Path(temporary)
            project = create_supersymmetry_project(root)
            state_root = root / "external-state"
            plan = build_qualification_plan(_status(project, state_root))
            planned_workspace = plan["workspace"]

            recipe = project / "groovy/recipes.zs"
            recipe.write_text("// ordinary recipe work\n", encoding="utf-8")
            drifted_plan = build_qualification_plan(_status(project, state_root))

            self.assertEqual(plan["plan_id"], drifted_plan["plan_id"])
            self.assertNotEqual(
                planned_workspace["dirty_fingerprint"],
                drifted_plan["workspace"]["dirty_fingerprint"],
            )
            result = apply_qualification_plan(
                REPOSITORY_ROOT,
                project,
                profile_selector="supersymmetry",
                state_root=state_root,
                expected_plan_id=plan["plan_id"],
            )

            self.assertEqual(plan["plan_id"], result["applied_plan_id"])
            self.assertNotEqual(planned_workspace, result["qualification"]["workspace"])
            self.assertTrue(result["qualification"]["workspace"]["dirty"])
            self.assertIn(
                "?? groovy/recipes.zs",
                result["qualification"]["workspace"]["dirty_entries"],
            )
            recipe.write_text("// continued ordinary recipe work\n", encoding="utf-8")
            current = _status(project, state_root)
            self.assertTrue(current["qualified"])
            self.assertEqual("current", current["binding"]["state"])

    def test_relevant_manifest_or_index_drift_rejects_an_old_plan(self) -> None:
        for relative, replacement in (
            ("index.toml", "changed index evidence\n"),
            ("pack.toml", None),
        ):
            with self.subTest(relative=relative), _temporary_directory() as temporary:
                root = Path(temporary)
                project = create_supersymmetry_project(root)
                state_root = root / "external-state"
                plan = build_qualification_plan(_status(project, state_root))
                target = project / relative
                if replacement is None:
                    replacement = target.read_text(encoding="utf-8").replace(
                        'version = "test"',
                        'version = "changed"',
                    )
                target.write_text(replacement, encoding="utf-8")

                with self.assertRaisesRegex(
                    ProjectQualificationError,
                    "does not match the current qualification plan",
                ):
                    apply_qualification_plan(
                        REPOSITORY_ROOT,
                        project,
                        profile_selector="supersymmetry",
                        state_root=state_root,
                        expected_plan_id=plan["plan_id"],
                    )
                self.assertFalse(state_root.exists())

    def test_retained_binding_rejects_a_non_object_id_git_revision(self) -> None:
        with _temporary_directory() as temporary:
            root = Path(temporary)
            project = create_supersymmetry_project(root)
            state_root = root / "external-state"
            plan = build_qualification_plan(_status(project, state_root))
            result = apply_qualification_plan(
                REPOSITORY_ROOT,
                project,
                profile_selector="supersymmetry",
                state_root=state_root,
                expected_plan_id=plan["plan_id"],
            )
            binding_path = Path(result["binding"]["path"])
            retained = json.loads(binding_path.read_text(encoding="utf-8"))
            retained["workspace"]["revision"] = "main"
            body = dict(retained)
            body.pop("state_revision")
            retained["state_revision"] = _identity(
                "workbench-project-qualification-state",
                body,
            )
            binding_path.write_text(
                json.dumps(retained, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ProjectQualificationError,
                "Git revision is invalid",
            ):
                _status(project, state_root)

    def test_incompatible_plan_is_structured_but_cannot_apply(self) -> None:
        with _temporary_directory() as temporary:
            root = Path(temporary)
            project = create_supersymmetry_project(root)
            state_root = root / "external-state"
            manifest = project / "pack.toml"
            manifest.write_text(
                manifest.read_text(encoding="utf-8").replace(
                    'name = "Supersymmetry"',
                    'name = "Another Pack"',
                ),
                encoding="utf-8",
            )

            status = _status(project, state_root)
            plan = build_qualification_plan(status)

            self.assertEqual("incompatible", status["state"])
            self.assertFalse(status["can_apply"])
            self.assertFalse(status["qualified"])
            self.assertEqual("incompatible", status["checks"][0]["state"])
            self.assertFalse(plan["can_apply"])
            self.assertEqual([], plan["actions"])
            self.assertEqual(False, plan["consent"]["required"])
            with self.assertRaisesRegex(ProjectQualificationError, "incompatible"):
                apply_qualification_plan(
                    REPOSITORY_ROOT,
                    project,
                    profile_selector="supersymmetry",
                    state_root=state_root,
                    expected_plan_id=plan["plan_id"],
                )
            self.assertFalse(state_root.exists())

            output = StringIO()
            exit_code = main(
                [
                    str(project),
                    "--profile",
                    "supersymmetry",
                    "--plan",
                    "--json",
                ],
                root=REPOSITORY_ROOT,
                default_state_root=state_root,
                output=output,
                error=StringIO(),
            )
            self.assertEqual(0, exit_code)
            self.assertEqual("incompatible", json.loads(output.getvalue())["state"])

    def test_bare_json_is_one_read_only_plan_even_on_a_terminal(self) -> None:
        with _temporary_directory() as temporary:
            root = Path(temporary)
            project = create_supersymmetry_project(root)
            state_root = root / "external-state"
            output = _TerminalBuffer()

            exit_code = main(
                [str(project), "--profile", "supersymmetry", "--json"],
                root=REPOSITORY_ROOT,
                default_state_root=state_root,
                input_stream=_TerminalBuffer("y\n"),
                output=output,
                error=StringIO(),
            )

            self.assertEqual(0, exit_code)
            parsed = json.loads(output.getvalue())
            self.assertEqual(PLAN_FORMAT, parsed["format"])
            self.assertFalse(state_root.exists())

    def test_router_uses_the_product_spine_state_convention(self) -> None:
        with _temporary_directory() as temporary:
            root = Path(temporary)
            project = create_supersymmetry_project(root)
            explicit = root / "shared-state"
            environment = os.environ.copy()
            environment["WORKBENCH_STATE_ROOT"] = str(explicit)
            environment["HOME"] = str(root)
            environment["USERPROFILE"] = str(root)
            environment["XDG_STATE_HOME"] = str(root / "user-state")
            environment["LOCALAPPDATA"] = str(root / "user-state")

            completed = subprocess.run(
                [
                    sys.executable,
                    str(REPOSITORY_ROOT / "tools/workbench.py"),
                    "project",
                    "qualify",
                    str(project),
                    "--profile",
                    "supersymmetry",
                    "--plan",
                    "--json",
                ],
                cwd=REPOSITORY_ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(0, completed.returncode, completed.stderr)
            plan = json.loads(completed.stdout)
            expected_root = explicit / "product-spine/project-qualification-v1"
            self.assertTrue(
                Path(plan["binding"]["path"]).is_relative_to(expected_root)
            )
            self.assertFalse(explicit.exists())

    def test_plain_interactive_flow_requires_y_consent(self) -> None:
        with _temporary_directory() as temporary:
            root = Path(temporary)
            project = create_supersymmetry_project(root)
            declined_state = root / "declined-state"
            declined_output = _TerminalBuffer()

            declined = main(
                [str(project), "--profile", "supersymmetry"],
                root=REPOSITORY_ROOT,
                default_state_root=declined_state,
                input_stream=_TerminalBuffer("n\n"),
                output=declined_output,
                error=StringIO(),
            )
            self.assertEqual(0, declined)
            self.assertIn("Apply this qualification? [y/N]", declined_output.getvalue())
            self.assertIn("nothing was changed", declined_output.getvalue())
            self.assertFalse(declined_state.exists())

            accepted_state = root / "accepted-state"
            accepted_output = _TerminalBuffer()
            accepted = main(
                [str(project), "--profile", "supersymmetry"],
                root=REPOSITORY_ROOT,
                default_state_root=accepted_state,
                input_stream=_TerminalBuffer("y\n"),
                output=accepted_output,
                error=StringIO(),
            )
            self.assertEqual(0, accepted)
            self.assertIn("Workbench project qualification · Saved", accepted_output.getvalue())
            self.assertIn("--state-root", accepted_output.getvalue())
            self.assertIn(str(accepted_state.resolve()), accepted_output.getvalue())
            self.assertTrue(accepted_state.exists())

    def test_state_root_rejects_symbolic_and_physical_checkout_aliases(self) -> None:
        with _temporary_directory() as temporary:
            root = Path(temporary)
            project = create_supersymmetry_project(root)
            inside = project / "private-state"
            inside.mkdir()
            alias = root / "state-alias"
            try:
                alias.symlink_to(inside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("directory symlinks are unavailable on this host")

            with self.assertRaisesRegex(
                ProjectQualificationError,
                "symbolic or non-directory components",
            ):
                _status(project, alias / "nested")

            physical = root / "physical-state"
            with patch(
                "workbench_shell.project_qualification.os.path.samefile",
                return_value=True,
            ):
                with self.assertRaisesRegex(
                    ProjectQualificationError,
                    "physically aliases",
                ):
                    _status(project, physical)
            self.assertFalse(physical.exists())

    def test_state_root_rejects_same_git_directory_alias(self) -> None:
        with _temporary_directory() as temporary:
            root = Path(temporary)
            project = create_supersymmetry_project(root)
            state_root = root / "external-state"
            shared_git = project / ".git"

            with patch(
                "workbench_shell.project_qualification._same_physical_path",
                return_value=False,
            ), patch(
                "workbench_shell.project_qualification._git_directory",
                return_value=shared_git,
            ):
                with self.assertRaisesRegex(
                    ProjectQualificationError,
                    "aliases the target checkout Git directory",
                ):
                    _status(project, state_root)
            self.assertFalse(state_root.exists())

    def test_state_identity_observation_failures_are_not_treated_as_distinct(self) -> None:
        # This test does not create private retained state, so the platform
        # default temp location is intentional: it provides an ordinary path
        # outside the Workbench repository whose non-repository result can be
        # tested without contradicting its real ancestor markers.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = create_supersymmetry_project(root)
            state_root = root / "external-state"

            with patch(
                "workbench_shell.project_qualification.os.path.samefile",
                side_effect=PermissionError("identity denied"),
            ):
                with self.assertRaisesRegex(
                    ProjectQualificationError,
                    "physical identity cannot be compared safely",
                ):
                    _status(project, state_root)

            timeout = subprocess.TimeoutExpired(
                cmd=["git", "rev-parse", "--absolute-git-dir"],
                timeout=5,
            )
            with patch(
                "workbench_shell.project_qualification.run_git_observation",
                side_effect=timeout,
            ):
                with self.assertRaisesRegex(
                    ProjectQualificationError,
                    "qualification state Git directory cannot be observed safely",
                ):
                    _git_directory(root, required=False)

            non_repository = subprocess.CompletedProcess(
                args=[],
                returncode=128,
                stdout="",
                stderr="fatal: kein Repository in diesem Verzeichnis\n",
            )
            with patch(
                "workbench_shell.project_qualification.run_git_observation",
                return_value=non_repository,
            ):
                self.assertIsNone(_git_directory(root, required=False))

            ambiguous_failure = subprocess.CompletedProcess(
                args=[],
                returncode=128,
                stdout="",
                stderr="fatal: detected dubious ownership in repository\n",
            )
            marked_state = root / "marked-state"
            (marked_state / ".git").mkdir(parents=True)
            with patch(
                "workbench_shell.project_qualification.run_git_observation",
                return_value=ambiguous_failure,
            ):
                with self.assertRaisesRegex(
                    ProjectQualificationError,
                    "qualification state Git directory observation failed",
                ):
                    _git_directory(marked_state, required=False)

            bare_state = root / "bare-state"
            (bare_state / "objects").mkdir(parents=True)
            (bare_state / "refs").mkdir()
            (bare_state / "HEAD").write_text(
                "ref: refs/heads/main\n",
                encoding="utf-8",
            )
            with patch(
                "workbench_shell.project_qualification.run_git_observation",
                return_value=ambiguous_failure,
            ):
                with self.assertRaisesRegex(
                    ProjectQualificationError,
                    "qualification state Git directory observation failed",
                ):
                    _git_directory(bare_state, required=False)

            empty_success = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="",
                stderr="",
            )
            with patch(
                "workbench_shell.project_qualification.run_git_observation",
                return_value=empty_success,
            ):
                with self.assertRaisesRegex(
                    ProjectQualificationError,
                    "qualification state Git returned no directory identity",
                ):
                    _git_directory(root, required=False)

            missing_identity = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=str(root / "missing-git-directory") + "\n",
                stderr="",
            )
            with patch(
                "workbench_shell.project_qualification.run_git_observation",
                return_value=missing_identity,
            ):
                with self.assertRaisesRegex(
                    ProjectQualificationError,
                    "qualification state Git directory identity is unavailable",
                ):
                    _git_directory(root, required=False)

    def test_state_alias_created_during_apply_is_rechecked_before_a_file_write(self) -> None:
        with _temporary_directory() as temporary:
            root = Path(temporary)
            project = create_supersymmetry_project(root)
            state_root = root / "external-state"
            plan = build_qualification_plan(_status(project, state_root))
            same_physical_path = qualification_module._same_physical_path

            def alias_after_creation(left: Path, right: Path) -> bool:
                if (
                    state_root.exists()
                    and Path(left) == state_root
                    and Path(right) == project
                ):
                    return True
                return same_physical_path(left, right)

            with patch(
                "workbench_shell.project_qualification._same_physical_path",
                side_effect=alias_after_creation,
            ):
                with self.assertRaisesRegex(
                    ProjectQualificationError,
                    "physically aliases",
                ):
                    apply_qualification_plan(
                        REPOSITORY_ROOT,
                        project,
                        profile_selector="supersymmetry",
                        state_root=state_root,
                        expected_plan_id=plan["plan_id"],
                    )

            self.assertEqual([], list(state_root.rglob("*.json")))
            self.assertEqual([], list(state_root.rglob("*.lock")))

    def test_apply_fails_closed_when_owner_private_state_cannot_be_enforced(self) -> None:
        with _temporary_directory() as temporary:
            root = Path(temporary)
            project = create_supersymmetry_project(root)
            state_root = root / "external-state"
            plan = build_qualification_plan(_status(project, state_root))

            with patch(
                "workbench_shell.project_qualification.secure_private_path",
                side_effect=qualification_module.HostFilesystemError(
                    "path is not owner-private"
                ),
            ):
                with self.assertRaisesRegex(
                    ProjectQualificationError,
                    "cannot secure qualification state: path is not owner-private",
                ):
                    apply_qualification_plan(
                        REPOSITORY_ROOT,
                        project,
                        profile_selector="supersymmetry",
                        state_root=state_root,
                        expected_plan_id=plan["plan_id"],
                    )

            self.assertEqual([], list(state_root.rglob("*.json")))
            self.assertFalse((project / ".workbench").exists())

    def test_plain_output_rejects_or_escapes_terminal_control_injection(self) -> None:
        with _temporary_directory() as temporary:
            root = Path(temporary)
            state_root = root / "external-state"
            for control in ("\x1b", "\x9b", "\u202e"):
                with self.subTest(control=repr(control)):
                    error = StringIO()
                    exit_code = main(
                        [
                            str(root / f"unsafe{control}workspace"),
                            "--profile",
                            "supersymmetry",
                            "--plan",
                        ],
                        root=REPOSITORY_ROOT,
                        default_state_root=state_root,
                        output=StringIO(),
                        error=error,
                    )
                    self.assertEqual(2, exit_code)
                    self.assertNotIn(control, error.getvalue())
                    self.assertIn("terminal control characters", error.getvalue())

            argv_error = StringIO()
            exit_code = main(
                [
                    str(root / "safe-workspace"),
                    "--profile",
                    "supersymmetry",
                    "--unknown\x1b[2J\nforged prompt",
                ],
                root=REPOSITORY_ROOT,
                default_state_root=state_root,
                output=StringIO(),
                error=argv_error,
            )
            self.assertEqual(2, exit_code)
            self.assertNotIn("\x1b", argv_error.getvalue())
            self.assertNotIn("\nforged prompt", argv_error.getvalue())
            self.assertIn(
                "command argument contains terminal control",
                argv_error.getvalue(),
            )

            project = create_supersymmetry_project(root)
            git_error = StringIO()
            poisoned = subprocess.CompletedProcess(
                args=[],
                returncode=1,
                stdout="",
                stderr="failure\x1b[2J\nforged diagnostic",
            )
            with patch(
                "workbench_shell.project_qualification.run_git_observation",
                return_value=poisoned,
            ):
                exit_code = main(
                    [str(project), "--profile", "supersymmetry", "--plan"],
                    root=REPOSITORY_ROOT,
                    default_state_root=state_root,
                    output=StringIO(),
                    error=git_error,
                )
            self.assertEqual(2, exit_code)
            self.assertNotIn("\x1b", git_error.getvalue())
            self.assertNotIn("\nforged diagnostic", git_error.getvalue())
            self.assertIn("\\u001b", git_error.getvalue())
            self.assertIn("\\u000a", git_error.getvalue())

            status = _status(project, state_root)
            status["checks"][0]["detail"] = "inspection\x1b[2J\nforged line"
            rendered = _TerminalBuffer()
            with patch(
                "workbench_shell.project_qualification.qualification_status",
                return_value=status,
            ):
                exit_code = main(
                    [str(project), "--profile", "supersymmetry"],
                    root=REPOSITORY_ROOT,
                    default_state_root=state_root,
                    input_stream=_TerminalBuffer("n\n"),
                    output=rendered,
                    error=StringIO(),
                )
            self.assertEqual(0, exit_code)
            self.assertNotIn("\x1b", rendered.getvalue())
            self.assertNotIn("\nforged line", rendered.getvalue())
            self.assertIn("\\u001b", rendered.getvalue())
            self.assertIn("\\u000a", rendered.getvalue())

    def test_interrupt_after_atomic_replace_reports_committed_binding(self) -> None:
        with _temporary_directory() as temporary:
            root = Path(temporary)
            project = create_supersymmetry_project(root)
            state_root = root / "external-state"
            plan = build_qualification_plan(_status(project, state_root))
            real_replace = os.replace

            def replace_then_interrupt(source: Path, destination: Path) -> None:
                real_replace(source, destination)
                raise KeyboardInterrupt

            output = StringIO()
            error = StringIO()
            with patch(
                "workbench_shell.project_qualification.os.replace",
                side_effect=replace_then_interrupt,
            ):
                exit_code = main(
                    [
                        str(project),
                        "--profile",
                        "supersymmetry",
                        "--apply",
                        plan["plan_id"],
                        "--json",
                    ],
                    root=REPOSITORY_ROOT,
                    default_state_root=state_root,
                    output=output,
                    error=error,
                )

            self.assertEqual(130, exit_code)
            self.assertIn("qualification committed before cancellation", error.getvalue())
            self.assertNotIn("nothing was changed", output.getvalue() + error.getvalue())
            committed = _status(project, state_root)
            self.assertTrue(committed["qualified"])
            self.assertEqual("current", committed["binding"]["state"])

    def test_interrupt_after_apply_returns_does_not_claim_rollback(self) -> None:
        with _temporary_directory() as temporary:
            root = Path(temporary)
            project = create_supersymmetry_project(root)
            state_root = root / "external-state"
            plan = build_qualification_plan(_status(project, state_root))
            error = StringIO()

            exit_code = main(
                [
                    str(project),
                    "--profile",
                    "supersymmetry",
                    "--apply",
                    plan["plan_id"],
                    "--json",
                ],
                root=REPOSITORY_ROOT,
                default_state_root=state_root,
                output=_InterruptOnWrite(),
                error=error,
            )

            self.assertEqual(130, exit_code)
            self.assertIn(
                "qualification committed before cancellation",
                error.getvalue(),
            )
            self.assertNotIn("nothing was changed", error.getvalue())
            committed = _status(project, state_root)
            self.assertTrue(committed["qualified"])
            self.assertEqual("current", committed["binding"]["state"])

    def test_profile_selection_is_explicit_and_exact(self) -> None:
        with _temporary_directory() as temporary:
            root = Path(temporary)
            project = create_supersymmetry_project(root)
            with self.assertRaisesRegex(
                ProjectQualificationError,
                "--profile supersymmetry is required",
            ):
                qualification_status(
                    REPOSITORY_ROOT,
                    project,
                    profile_selector="another-pack",
                    state_root=root / "state",
                )


if __name__ == "__main__":
    unittest.main()
