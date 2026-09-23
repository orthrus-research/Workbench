from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/project-intelligence/src"
import sys

sys.path.insert(0, str(SOURCE))

from workbench_project_intelligence import (  # noqa: E402
    _pr_preparation_core as pr_preparation_core,
    pr_preparation as pr_preparation_v2_module,
)
from workbench_project_intelligence._pr_preparation_core import (  # noqa: E402
    PullRequestPreparationError,
)
from workbench_project_intelligence.pr_preparation import (  # noqa: E402
    apply_pr_preparation_plan_v2,
    build_pr_preparation_plan_v2,
    load_pr_preparation_receipt_v2,
    load_pull_request_provider_profile,
    verify_pr_preparation_receipt_v2,
)
from workbench_project_intelligence.project_acquisition import (  # noqa: E402
    load_acquisition_profile,
)


@unittest.skipUnless(shutil.which("git"), "Git is required for PR preparation tests")
class PullRequestPreparationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.git = str(Path(shutil.which("git") or "git").resolve())
        self.remote = self.root / "remote.git"
        self.author = self.root / "author"
        self.checkout = self.root / "checkout"
        self.state = self.root / "state"
        self._git("init", "--bare", str(self.remote))
        self._git("init", "--initial-branch", "master-ceu", str(self.author))
        self._git("-C", str(self.author), "config", "user.name", "Workbench Test")
        self._git("-C", str(self.author), "config", "user.email", "workbench@example.invalid")
        self._write_pack("base")
        self._commit("base")
        self.original_base_oid = self._git("-C", str(self.author), "rev-parse", "HEAD")
        self._git("-C", str(self.author), "remote", "add", "origin", str(self.remote))
        self._git("-C", str(self.author), "push", "origin", "master-ceu")
        self._git("-C", str(self.author), "switch", "-c", "pull-request")
        (self.author / "groovy/postInit/recipes.groovy").write_text(
            "mods.gregtech.assembler.recipeBuilder()\n",
            encoding="utf-8",
        )
        self._commit("pull request")
        self.original_head_oid = self._git("-C", str(self.author), "rev-parse", "HEAD")
        self._git(
            "-C",
            str(self.author),
            "push",
            "origin",
            "HEAD:refs/pull/42/head",
        )
        head_tree = self._git(
            "-C", str(self.author), "rev-parse", f"{self.original_head_oid}^{{tree}}"
        )
        self.provider_merge_oid = self._git(
            "-C",
            str(self.author),
            "commit-tree",
            head_tree,
            "-p",
            self.original_base_oid,
            "-p",
            self.original_head_oid,
            "-m",
            "provider merge fixture",
        )
        self._git(
            "-C",
            str(self.author),
            "push",
            "origin",
            f"{self.provider_merge_oid}:refs/pull/42/merge",
            f"{self.provider_merge_oid}:refs/heads/master-ceu",
        )
        self._git(
            "clone",
            "--branch",
            "master-ceu",
            str(self.remote),
            str(self.checkout),
        )
        profile = json.loads(
            (ROOT / "profiles/packs/supersymmetry/acquisition-v1.json").read_text(
                encoding="utf-8"
            )
        )
        profile["remote"]["url"] = str(self.remote)
        self.profile_path = self.root / "profile.json"
        self.profile_path.write_text(
            json.dumps(profile, sort_keys=True) + "\n", encoding="utf-8"
        )
        self.profile = load_acquisition_profile(self.profile_path)
        self.provider_profile = load_pull_request_provider_profile(
            ROOT / "profiles/packs/supersymmetry/github-pr-provider-v1.json"
        )
        self.provider_metadata = self.root / "provider-pr-42.json"
        self._write_provider_metadata()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _git(self, *arguments: str) -> str:
        completed = subprocess.run(
            [self.git, *arguments],
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()

    def _write_pack(self, marker: str) -> None:
        for directory in ("config", "groovy/postInit", "mods"):
            (self.author / directory).mkdir(parents=True, exist_ok=True)
        (self.author / "pack.toml").write_text(f"name = '{marker}'\n", encoding="utf-8")
        (self.author / "index.toml").write_text("hash-format = 'sha256'\n", encoding="utf-8")
        (self.author / "groovy/runConfig.json").write_text("{}\n", encoding="utf-8")
        (self.author / "groovy/postInit/recipes.groovy").write_text(
            f"// {marker}\n", encoding="utf-8"
        )

    def _commit(self, message: str) -> None:
        self._git("-C", str(self.author), "add", ".")
        self._git("-C", str(self.author), "commit", "-m", message)

    def _provider_response(
        self,
        *,
        state: str = "closed",
        merged: bool = True,
        base_oid: str | None = None,
        head_oid: str | None = None,
        merge_oid: str | None | object = ...,
    ) -> dict[str, object]:
        selected_merge = self.provider_merge_oid if merge_oid is ... else merge_oid
        return {
            "number": 42,
            "html_url": "https://github.com/SymmetricDevs/Supersymmetry/pull/42",
            "state": state,
            "merged": merged,
            "base": {
                "ref": "master-ceu",
                "sha": base_oid or self.original_base_oid,
                "repo": {"full_name": "SymmetricDevs/Supersymmetry"},
            },
            "head": {
                "ref": "recipe-review",
                "sha": head_oid or self.original_head_oid,
                "repo": {"full_name": "Contributor/Supersymmetry"},
            },
            "merge_commit_sha": selected_merge,
            "title": "ignored provider presentation field",
        }

    def _write_provider_metadata(self, value: dict[str, object] | None = None) -> None:
        self.provider_metadata.write_text(
            json.dumps(value or self._provider_response(), sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _plan_v2(self) -> dict[str, object]:
        return build_pr_preparation_plan_v2(
            self.profile,
            self.provider_profile,
            pull_request=42,
            repository=self.checkout,
            state_root=self.state,
            git_executable=self.git,
            provider_metadata_path=self.provider_metadata,
        )

    def test_v2_retains_and_verifies_the_provider_historical_delta(self) -> None:
        plan = self._plan_v2()
        self.assertEqual("workbench-pr-preparation-plan-v2", plan["format"])
        self.assertEqual(self.original_base_oid, plan["base_oid"])
        self.assertEqual(self.original_head_oid, plan["head_oid"])
        self.assertEqual(self.provider_merge_oid, plan["provider_merge_oid"])
        self.assertEqual("closed", plan["pull_request_state"])
        self.assertTrue(plan["pull_request_merged"])
        local_config_before = self._git(
            "-C", str(self.checkout), "config", "--local", "--list"
        )
        observed_v2_git_arguments: list[tuple[str, ...]] = []
        run_git = pr_preparation_v2_module._run_git

        def observe_v2_git(
            executable: str,
            arguments: tuple[str, ...],
            *,
            environment: object,
            timeout: float,
        ) -> subprocess.CompletedProcess[str]:
            observed_v2_git_arguments.append(tuple(arguments))
            return run_git(
                executable,
                arguments,
                environment=environment,
                timeout=timeout,
            )

        with mock.patch.object(
            pr_preparation_v2_module, "_run_git", side_effect=observe_v2_git
        ):
            result = apply_pr_preparation_plan_v2(
                self.profile,
                self.provider_profile,
                plan,
                provider_metadata_path=self.provider_metadata,
            )
        receipt = load_pr_preparation_receipt_v2(result["receipt_path"])
        self.assertTrue(observed_v2_git_arguments)
        for arguments in observed_v2_git_arguments:
            self.assertEqual(("-c", "core.longpaths=true"), arguments[:2])
        self.assertEqual(
            local_config_before,
            self._git("-C", str(self.checkout), "config", "--local", "--list"),
        )
        token = str(plan["plan_id"]).rsplit(":", 1)[-1][:32]
        namespace = f"refs/workbench/review-v2/supersymmetry/pr-42/{token}"
        self.assertEqual(f"{namespace}/base", receipt["base_ref"])
        self.assertEqual(f"{namespace}/head", receipt["head_ref"])
        self.assertEqual(
            f"{namespace}/provider-merge", receipt["provider_merge_ref"]
        )
        schema = json.loads(
            (
                ROOT
                / "modules/project-intelligence/schemas/workbench-pr-preparation-receipt-v2.schema.json"
            ).read_text(encoding="utf-8")
        )
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(receipt)
        self.assertEqual(receipt, load_pr_preparation_receipt_v2(result["receipt_path"]))
        verified = verify_pr_preparation_receipt_v2(
            receipt,
            repository=self.checkout,
            acquisition_profile=self.profile,
            provider_profile=self.provider_profile,
            git_executable=self.git,
        )
        self.assertEqual("provider-base-to-head", receipt["delta_kind"])
        self.assertEqual(
            ["groovy/postInit/recipes.groovy"],
            verified["repository_changed_paths"],
        )
        self.assertEqual(
            self.original_base_oid,
            self._git("-C", str(self.checkout), "rev-parse", receipt["base_ref"]),
        )
        self.assertEqual(
            self.original_head_oid,
            self._git("-C", str(self.checkout), "rev-parse", receipt["head_ref"]),
        )
        self.assertEqual(
            self.provider_merge_oid,
            self._git(
                "-C", str(self.checkout), "rev-parse", receipt["provider_merge_ref"]
            ),
        )

        self.remote.rename(self.root / "remote-offline.git")
        self.provider_metadata.unlink()
        offline = verify_pr_preparation_receipt_v2(
            receipt,
            repository=self.checkout,
            acquisition_profile=self.profile,
            provider_profile=self.provider_profile,
            git_executable=self.git,
        )
        self.assertEqual(verified, offline)

    def test_v2_atomic_update_ref_uses_exact_lf_bytes(self) -> None:
        oid = "a" * 40
        ref = "refs/workbench/review-v2/supersymmetry/pr-42/token/base"
        observed: dict[str, object] = {}

        def complete(
            command: list[str], **keywords: object
        ) -> subprocess.CompletedProcess[bytes]:
            observed["command"] = command
            observed["keywords"] = keywords
            return subprocess.CompletedProcess(command, 0)

        with (
            mock.patch.object(
                pr_preparation_v2_module, "_resolve_owned_ref", return_value=None
            ),
            mock.patch.object(
                pr_preparation_v2_module.subprocess, "run", side_effect=complete
            ),
        ):
            created = pr_preparation_v2_module._retain_immutable_refs(
                self.git,
                self.checkout,
                {ref: oid},
                environment=None,
            )

        self.assertEqual(((ref, oid),), created)
        command = observed["command"]
        self.assertIsInstance(command, list)
        self.assertEqual(
            [self.git, "-c", "core.longpaths=true", "-C", str(self.checkout)],
            command[:5],
        )
        keywords = observed["keywords"]
        self.assertIsInstance(keywords, dict)
        transaction = keywords["input"]
        self.assertEqual(
            f"start\ncreate {ref} {oid}\nprepare\ncommit\n".encode("utf-8"),
            transaction,
        )
        self.assertIsInstance(transaction, bytes)
        self.assertNotIn(b"\r", transaction)
        for text_option in ("text", "encoding", "errors"):
            self.assertNotIn(text_option, keywords)

    def test_v2_update_ref_diagnostic_is_byte_bounded_and_decoded_after_exit(
        self,
    ) -> None:
        oid = "b" * 40
        ref = "refs/workbench/review-v2/supersymmetry/pr-42/token/head"

        def fail(
            command: list[str], **keywords: object
        ) -> subprocess.CompletedProcess[bytes]:
            error = keywords["stderr"]
            error.write(b"failure-\xff" + (b"x" * 2000))
            error.flush()
            return subprocess.CompletedProcess(command, 128)

        with (
            mock.patch.object(
                pr_preparation_v2_module, "_resolve_owned_ref", return_value=None
            ),
            mock.patch.object(
                pr_preparation_v2_module.subprocess, "run", side_effect=fail
            ),
            self.assertRaises(PullRequestPreparationError) as raised,
        ):
            pr_preparation_v2_module._retain_immutable_refs(
                self.git,
                self.checkout,
                {ref: oid},
                environment=None,
            )

        message = str(raised.exception)
        self.assertIn("failure-\ufffd", message)
        self.assertTrue(message.endswith("..."))
        self.assertLess(len(message.encode("utf-8")), 1200)

    def test_git_repository_and_diff_observations_enable_long_paths_locally(
        self,
    ) -> None:
        text_result = subprocess.CompletedProcess(
            [self.git],
            0,
            stdout=self.original_base_oid + "\n",
            stderr="",
        )
        with mock.patch.object(
            pr_preparation_core,
            "_run_git",
            return_value=text_result,
        ) as run_text:
            observed = pr_preparation_core._git_text(
                self.git,
                self.checkout,
                ("rev-parse", "--verify", "HEAD^{commit}"),
                environment=None,
            )
        self.assertEqual(self.original_base_oid, observed)
        self.assertEqual(
            ("-c", "core.longpaths=true", "-C", str(self.checkout)),
            run_text.call_args.args[1][:4],
        )

        bytes_result = subprocess.CompletedProcess(
            [self.git],
            0,
            stdout=b"",
            stderr=b"",
        )
        with mock.patch.object(
            pr_preparation_core,
            "_run_git_bytes",
            return_value=bytes_result,
        ) as run_bytes:
            self.assertEqual(
                (),
                pr_preparation_core._changed_paths(
                    self.git,
                    self.checkout,
                    self.original_base_oid,
                    self.original_head_oid,
                    environment=None,
                ),
            )
        self.assertEqual(
            ("-c", "core.longpaths=true"),
            run_bytes.call_args.args[1][:2],
        )

    def test_v2_rejects_unbounded_or_path_shaped_project_identity(self) -> None:
        for project_id in (
            "../escape",
            ".hidden",
            "a..b",
            "CON",
            "con.json",
            "x" * 65,
            "project.lock",
            "project.LOCK",
        ):
            with self.subTest(project_id=project_id):
                acquisition = dict(self.profile)
                acquisition["project_id"] = project_id
                provider = dict(self.provider_profile)
                provider["project_id"] = project_id
                with self.assertRaisesRegex(
                    PullRequestPreparationError,
                    "bounded safe component",
                ):
                    build_pr_preparation_plan_v2(
                        acquisition,
                        provider,
                        pull_request=42,
                        repository=self.checkout,
                        state_root=self.state,
                        git_executable=self.git,
                        provider_metadata_path=self.provider_metadata,
                    )

    def test_v2_plan_rejects_state_root_with_symlink_ancestor(self) -> None:
        physical = self.root / "physical-state"
        physical.mkdir()
        alias = self.root / "state-alias"
        try:
            alias.symlink_to(physical, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"directory symlinks are unavailable: {exc}")

        with self.assertRaisesRegex(
            PullRequestPreparationError,
            "symbolic or reparse components",
        ):
            build_pr_preparation_plan_v2(
                self.profile,
                self.provider_profile,
                pull_request=42,
                repository=self.checkout,
                state_root=alias / "nested",
                git_executable=self.git,
                provider_metadata_path=self.provider_metadata,
            )
        self.assertFalse((physical / "nested").exists())

    def test_v2_plan_rejects_simulated_windows_junction_component(self) -> None:
        junction = self.root / "state-junction"
        junction.mkdir()

        with (
            mock.patch.object(
                pr_preparation_v2_module,
                "_windows_reparse_component",
                side_effect=lambda path, _info: path == junction,
            ),
            self.assertRaisesRegex(
                PullRequestPreparationError,
                "symbolic or reparse components",
            ),
        ):
            build_pr_preparation_plan_v2(
                self.profile,
                self.provider_profile,
                pull_request=42,
                repository=self.checkout,
                state_root=junction / "nested",
                git_executable=self.git,
                provider_metadata_path=self.provider_metadata,
            )

    def test_v2_apply_rejects_late_state_root_alias_replacement(self) -> None:
        self.state.mkdir()
        plan = self._plan_v2()
        original_revalidate = pr_preparation_v2_module._revalidate_planned_state_root
        revalidation_calls = 0
        displaced = self.root / "state-before-replacement"

        def replace_before_receipt_mutation(value: object) -> Path:
            nonlocal revalidation_calls
            revalidation_calls += 1
            if revalidation_calls == 2:
                self.state.rename(displaced)
                try:
                    self.state.symlink_to(displaced, target_is_directory=True)
                except OSError as exc:
                    self.skipTest(f"directory symlinks are unavailable: {exc}")
            return original_revalidate(value)  # type: ignore[arg-type]

        with (
            mock.patch.object(
                pr_preparation_v2_module,
                "_revalidate_planned_state_root",
                side_effect=replace_before_receipt_mutation,
            ),
            mock.patch.object(
                pr_preparation_v2_module,
                "_write_json_exclusive",
                wraps=pr_preparation_v2_module._write_json_exclusive,
            ) as write_receipt,
            self.assertRaisesRegex(
                PullRequestPreparationError,
                "symbolic or reparse components",
            ),
        ):
            apply_pr_preparation_plan_v2(
                self.profile,
                self.provider_profile,
                plan,
                provider_metadata_path=self.provider_metadata,
            )

        self.assertEqual(2, revalidation_calls)
        write_receipt.assert_not_called()
        self.assertEqual([], list(displaced.rglob("*.json")))
        self.assertEqual(
            "",
            self._git(
                "-C",
                str(self.checkout),
                "for-each-ref",
                "refs/workbench",
            ),
        )

    def test_v2_existing_ref_is_verified_inside_atomic_transaction(self) -> None:
        oid = self.original_base_oid
        ref = "refs/workbench/review-v2/supersymmetry/pr-42/existing/base"
        self._git("-C", str(self.checkout), "update-ref", ref, oid)

        def race_delete(
            _executable: str,
            _repository: Path,
            _ref: str,
            *,
            environment: object,
        ) -> str:
            self._git("-C", str(self.checkout), "update-ref", "-d", ref, oid)
            return oid

        with (
            mock.patch.object(
                pr_preparation_v2_module,
                "_resolve_owned_ref",
                side_effect=race_delete,
            ),
            self.assertRaisesRegex(
                PullRequestPreparationError,
                "retain immutable Workbench refs atomically",
            ),
        ):
            pr_preparation_v2_module._retain_immutable_refs(
                self.git,
                self.checkout,
                {ref: oid},
                environment=None,
            )
        self.assertEqual(
            "",
            self._git("-C", str(self.checkout), "for-each-ref", ref),
        )

    def test_v2_owned_ref_cleanup_never_dereferences_symbolic_ref(self) -> None:
        oid = self.original_base_oid
        branch = "refs/heads/workbench-symref-protected"
        owned = "refs/workbench/staging/pr-v2/" + ("d" * 32) + "/base"
        self._git("-C", str(self.checkout), "update-ref", branch, oid)
        self._git("-C", str(self.checkout), "symbolic-ref", owned, branch)

        with self.assertRaisesRegex(
            PullRequestPreparationError,
            "symbolic and was not followed",
        ):
            pr_preparation_v2_module._resolve_owned_ref(
                self.git,
                self.checkout,
                owned,
                environment=None,
            )
        try:
            pr_preparation_v2_module._delete_owned_refs_if_exact(
                self.git,
                self.checkout,
                ((owned, oid),),
                environment=None,
            )
        except PullRequestPreparationError:
            pass
        self.assertEqual(
            oid,
            self._git("-C", str(self.checkout), "rev-parse", "--verify", branch),
        )

    def test_v2_receipt_failure_preserves_primary_when_atomic_cleanup_fails(
        self,
    ) -> None:
        primary = PullRequestPreparationError("injected receipt publication failure")
        original_cleanup = pr_preparation_v2_module._delete_owned_refs_if_exact
        cleanup_calls = 0

        def cleanup(
            executable: str,
            repository: Path,
            values: object,
            *,
            environment: object,
        ) -> None:
            nonlocal cleanup_calls
            cleanup_calls += 1
            if cleanup_calls == 1:
                original_cleanup(
                    executable,
                    repository,
                    values,
                    environment=environment,
                )
                return
            raise PullRequestPreparationError("injected atomic cleanup failure")

        with (
            mock.patch.object(
                pr_preparation_v2_module,
                "_write_json_exclusive",
                side_effect=primary,
            ),
            mock.patch.object(
                pr_preparation_v2_module,
                "_delete_owned_refs_if_exact",
                side_effect=cleanup,
            ),
            self.assertRaises(PullRequestPreparationError) as raised,
        ):
            apply_pr_preparation_plan_v2(
                self.profile,
                self.provider_profile,
                self._plan_v2(),
                provider_metadata_path=self.provider_metadata,
            )

        self.assertIs(primary, raised.exception)
        self.assertEqual(2, cleanup_calls)
        self.assertIn(
            "Temporary Workbench ref cleanup also failed",
            "\n".join(getattr(raised.exception, "__notes__", ())),
        )

    def test_v2_staging_cleanup_failure_removes_new_immutable_refs_atomically(
        self,
    ) -> None:
        original_cleanup = pr_preparation_v2_module._delete_owned_refs_if_exact
        cleanup_calls = 0

        def cleanup(
            executable: str,
            repository: Path,
            values: object,
            *,
            environment: object,
        ) -> None:
            nonlocal cleanup_calls
            cleanup_calls += 1
            if cleanup_calls == 1:
                raise PullRequestPreparationError("injected staging cleanup failure")
            original_cleanup(
                executable,
                repository,
                values,
                environment=environment,
            )

        with (
            mock.patch.object(
                pr_preparation_v2_module,
                "_delete_owned_refs_if_exact",
                side_effect=cleanup,
            ),
            self.assertRaisesRegex(
                PullRequestPreparationError,
                "injected staging cleanup failure",
            ),
        ):
            apply_pr_preparation_plan_v2(
                self.profile,
                self.provider_profile,
                self._plan_v2(),
                provider_metadata_path=self.provider_metadata,
            )

        self.assertEqual(2, cleanup_calls)
        self.assertEqual(
            "",
            self._git(
                "-C",
                str(self.checkout),
                "for-each-ref",
                "refs/workbench/review-v2",
            ),
        )

    def test_v2_rejects_provider_movement_before_fetch(self) -> None:
        plan = self._plan_v2()
        moved = self._provider_response(state="open", merged=False, merge_oid=None)
        self._write_provider_metadata(moved)

        with self.assertRaisesRegex(PullRequestPreparationError, "fresh V2 plan"):
            apply_pr_preparation_plan_v2(
                self.profile,
                self.provider_profile,
                plan,
                provider_metadata_path=self.provider_metadata,
            )
        self.assertEqual(
            "",
            self._git(
                "-C", str(self.checkout), "for-each-ref", "refs/workbench/review-v2"
            ),
        )

    def test_v2_revalidates_provider_after_fetch_and_cleans_staging(self) -> None:
        stable = self._provider_response()
        moved = self._provider_response(state="open", merged=False, merge_oid=None)
        observations = iter((stable, stable, moved))

        def observer(
            _profile: object, _number: int, _timeout: float
        ) -> dict[str, object]:
            return next(observations)

        plan = build_pr_preparation_plan_v2(
            self.profile,
            self.provider_profile,
            pull_request=42,
            repository=self.checkout,
            state_root=self.state,
            git_executable=self.git,
            provider_observer=observer,
        )
        with self.assertRaisesRegex(PullRequestPreparationError, "during fetch"):
            apply_pr_preparation_plan_v2(
                self.profile,
                self.provider_profile,
                plan,
                provider_observer=observer,
            )
        self.assertEqual(
            "",
            self._git("-C", str(self.checkout), "for-each-ref", "refs/workbench"),
        )

    def test_v2_supports_exact_provider_absence_of_merge_identity(self) -> None:
        self._write_provider_metadata(
            self._provider_response(state="open", merged=False, merge_oid=None)
        )
        result = apply_pr_preparation_plan_v2(
            self.profile,
            self.provider_profile,
            self._plan_v2(),
            provider_metadata_path=self.provider_metadata,
        )
        receipt = load_pr_preparation_receipt_v2(result["receipt_path"])
        self.assertIsNone(receipt["provider_merge_oid"])
        self.assertIsNone(receipt["provider_merge_ref"])
        verified = verify_pr_preparation_receipt_v2(
            receipt,
            repository=self.checkout,
            git_executable=self.git,
        )
        self.assertEqual(1, verified["repository_changed_file_count"])

    def test_v2_rejects_unavailable_provider_object_without_retained_refs(self) -> None:
        unavailable = "f" * 40
        self._write_provider_metadata(
            self._provider_response(head_oid=unavailable, merge_oid=None)
        )
        with self.assertRaisesRegex(PullRequestPreparationError, "fetch failed"):
            apply_pr_preparation_plan_v2(
                self.profile,
                self.provider_profile,
                self._plan_v2(),
                provider_metadata_path=self.provider_metadata,
            )
        self.assertEqual(
            "",
            self._git("-C", str(self.checkout), "for-each-ref", "refs/workbench"),
        )

    def test_v2_receipt_identity_tamper_fails_closed(self) -> None:
        result = apply_pr_preparation_plan_v2(
            self.profile,
            self.provider_profile,
            self._plan_v2(),
            provider_metadata_path=self.provider_metadata,
        )
        path = Path(str(result["receipt_path"]))
        value = json.loads(path.read_text(encoding="utf-8"))
        value["base_name"] = "other-base"
        path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(PullRequestPreparationError, "identity"):
            load_pr_preparation_receipt_v2(path)

    def test_v2_receipt_rejects_path_shaped_project_identity_even_when_recomputed(
        self,
    ) -> None:
        result = apply_pr_preparation_plan_v2(
            self.profile,
            self.provider_profile,
            self._plan_v2(),
            provider_metadata_path=self.provider_metadata,
        )
        path = Path(str(result["receipt_path"]))
        value = json.loads(path.read_text(encoding="utf-8"))
        value["project_id"] = "../escape"
        value["receipt_id"] = pr_preparation_core._identity(
            "workbench-pr-preparation-v2",
            pr_preparation_v2_module._receipt_identity_body(value),
        )
        path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")

        with self.assertRaisesRegex(
            PullRequestPreparationError,
            "project identity must be one bounded safe component",
        ):
            load_pr_preparation_receipt_v2(path)

    def test_v2_network_timeout_is_finite_positive_and_bounded(self) -> None:
        def observer(
            _profile: object, _number: int, _timeout: float
        ) -> dict[str, object]:
            self.fail("invalid timeout reached the provider observer")

        for timeout in (0.0, -1.0, float("nan"), float("inf"), 601.0):
            with self.subTest(timeout=timeout):
                with self.assertRaisesRegex(
                    PullRequestPreparationError,
                    "network timeout must be finite, positive",
                ):
                    build_pr_preparation_plan_v2(
                        self.profile,
                        self.provider_profile,
                        pull_request=42,
                        repository=self.checkout,
                        state_root=self.state,
                        git_executable=self.git,
                        provider_observer=observer,
                        network_timeout=timeout,
                    )


if __name__ == "__main__":
    unittest.main()
