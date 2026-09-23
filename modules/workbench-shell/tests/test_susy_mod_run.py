from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from workbench_shell import susy_mod_run as subject


class SusyModRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.suite = self.root / "suite"
        self.run_id = "susy-mod-20260821T120000000000Z-aaaaaaaaaaaa"
        self.run_root = self.suite / ".workbench/dev-runs" / self.run_id
        self.run_root.mkdir(parents=True)
        self.result_path = self.run_root / "result.json"
        self.result_path.write_text("{}\n", encoding="utf-8")
        raw = self.result_path.read_bytes()
        self.source = {
            "result_id": "workbench-susy-mod-dev-result:sha256:" + "1" * 64,
            "result_uri": self.result_path.as_uri(),
            "result_sha256": sha256(raw).hexdigest(),
            "result_size": len(raw),
            "plan_id": "workbench-susy-mod-dev-plan:sha256:" + "2" * 64,
            "candidate": {
                "path": (self.run_root / "candidate.jar").as_uri(),
                "sha256": "3" * 64,
                "size": 7,
                "mod_ids": ["sample"],
            },
            "pack": {
                "root": str(self.root / "pack"),
                "version": "1.0",
                "manifest_sha256": "4" * 64,
                "metadata_path": "mods/sample.pw.toml",
                "metadata_sha256": "5" * 64,
                "side": "both",
                "applicable_sides": ["client", "server"],
            },
        }
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.outcomes = {
            "client": ("passed", True),
            "server": ("passed", True),
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _source_result(self, *_args: object) -> tuple[Path, dict, dict]:
        return self.run_root, {}, json.loads(json.dumps(self.source))

    def _launch(self, side: str):
        def fake(*_args: object, **kwargs: object) -> dict:
            self.calls.append((side, dict(kwargs)))
            return {"side": side}

        return fake

    def _child_reference(
        self,
        side: str,
        _result: object,
        *,
        run_id: str,
        run_root: Path,
    ) -> dict:
        self.assertEqual(run_id, self.run_id)
        self.assertEqual(run_root, self.run_root)
        outcome, cleanup_safe = self.outcomes[side]
        path = run_root / "runtime" / f"{side}-owner.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        failure_kind = None if outcome == "passed" else "controlled-failure"
        cleanup = {
            "owned_processes_running": not cleanup_safe,
            "errors": [] if cleanup_safe else ["fixture survivor"],
        }
        if side == "client":
            launch_identity = {
                "run_id": self.run_id,
                "stage_id": "workbench-susy-mod-client-stage:sha256:" + "4" * 64,
                "instance_id": "fixture-client-attempt",
                "candidate_sha256": "3" * 64,
                "started_at": "2026-08-21T12:00:00+00:00",
                "launcher_sha256": "5" * 64,
                "java_runtime_id": "sha256:" + "6" * 64,
            }
            owner_id = "workbench-susy-mod-launch:sha256:" + sha256(
                subject._canonical(launch_identity)
            ).hexdigest()
            receipt = {
                "format": subject.LAUNCH_RECEIPT_FORMAT,
                "schema_version": 1,
                "run_id": self.run_id,
                "outcome": outcome,
                "failure_kind": failure_kind,
                "launch_id": owner_id,
                "stage_id": launch_identity["stage_id"],
                "started_at": launch_identity["started_at"],
                "candidate": {"sha256": launch_identity["candidate_sha256"]},
                "launcher": {
                    "instance_id": launch_identity["instance_id"],
                    "sha256": launch_identity["launcher_sha256"],
                    "family": "prism",
                },
                "java": {"runtime_id": launch_identity["java_runtime_id"]},
                "launch_policy": {
                    "account_mode": "offline",
                    "launcher_profile": None,
                    "offline_name": "Workbench",
                    "memory_mib": 8192,
                    "timeout_seconds": 600.0,
                    "compatibility_experiments": ["shared-experiment"],
                },
                "cleanup": cleanup,
            }
        else:
            receipt = {
                "format": subject.SERVER_RECEIPT_FORMAT,
                "schema_version": 2,
                "run_id": self.run_id,
                "outcome": outcome,
                "failure_kind": failure_kind,
                "candidate": {"sha256": "3" * 64},
                "command": ["java", "-Xmx8192M", "-jar", "server.jar"],
                "projection": {
                    "compatibility_experiments": [
                        {"experiment_id": "shared-experiment"},
                        {"experiment_id": "server-experiment"},
                    ]
                },
                "cleanup": cleanup,
            }
            owner_id = "workbench-susy-mod-server-launch:" + sha256(
                subject._canonical(receipt)
            ).hexdigest()
            receipt["receipt_id"] = owner_id
        path.write_text(json.dumps(receipt) + "\n", encoding="utf-8")
        raw = path.read_bytes()
        return {
            "result_format": (
                subject.LAUNCH_RESULT_FORMAT
                if side == "client"
                else subject.SERVER_RESULT_FORMAT
            ),
            "receipt_format": receipt["format"],
            "owner_id": owner_id,
            "outcome": outcome,
            "failure_kind": failure_kind,
            "receipt_uri": path.as_uri(),
            "receipt_sha256": sha256(raw).hexdigest(),
            "receipt_size": len(raw),
            "cleanup_safe": cleanup_safe,
        }

    def _run(self, **kwargs: object) -> dict:
        options = {
            "side": "auto",
            "accept_minecraft_eula": True,
            "client_compatibility_experiments": ("shared-experiment",),
            "server_compatibility_experiments": (
                "shared-experiment",
                "server-experiment",
            ),
        }
        options.update(kwargs)
        with (
            patch.object(subject, "_source_result", self._source_result),
            patch.object(subject, "_preflight_owners"),
            patch.object(
                subject,
                "COMPATIBILITY_EXPERIMENTS",
                {"shared-experiment": "shared.json"},
            ),
            patch.object(
                subject,
                "SERVER_EXPERIMENTS",
                {"shared-experiment", "server-experiment"},
            ),
            patch.object(
                subject, "launch_susy_mod_client", self._launch("client")
            ),
            patch.object(
                subject, "launch_susy_mod_server", self._launch("server")
            ),
            patch.object(subject, "_child_reference", self._child_reference),
        ):
            return subject.run_susy_mod(
                self.suite,
                self.run_id,
                **options,
            )

    def test_auto_both_runs_exact_candidate_through_both_existing_owners(self) -> None:
        result = self._run()

        self.assertEqual(result["outcome"], "passed")
        self.assertEqual([side for side, _ in self.calls], ["client", "server"])
        client = self.calls[0][1]
        server = self.calls[1][1]
        self.assertEqual(client["compatibility_experiments"], ("shared-experiment",))
        self.assertEqual(
            server["compatibility_experiments"],
            ("shared-experiment", "server-experiment"),
        )
        self.assertTrue(server["accept_minecraft_eula"])
        receipt = result["receipt"]
        self.assertEqual(receipt["source"]["candidate"]["sha256"], "3" * 64)
        self.assertEqual(receipt["request"]["selected_sides"], ["client", "server"])
        self.assertTrue(receipt["cleanup"]["all_selected_sides_contained"])
        self.assertIn("Client/server parity: not claimed", subject.render_susy_mod_run(result))
        retry = result["next_actions"][0]
        self.assertFalse(retry["available"])
        self.assertIsNone(retry["argv"])
        self.assertIn("EULA acceptance is per invocation", retry["reason"])

    def test_explicit_template_retry_is_exact_and_non_cli_poll_is_not(self) -> None:
        template = self.root / "server template"
        template.mkdir()
        result = self._run(
            accept_minecraft_eula=False,
            server_template=template,
        )
        retry = result["next_actions"][0]
        self.assertTrue(retry["available"])
        self.assertIn("--server-template", retry["argv"])
        self.assertIn(str(template.resolve()), retry["argv"])

        result = self._run(
            accept_minecraft_eula=False,
            server_template=template,
            client_poll_interval_seconds=0.5,
        )
        retry = result["next_actions"][0]
        self.assertFalse(retry["available"])
        self.assertIn("non-CLI poll interval", retry["reason"])

    def test_auto_client_only_marks_server_not_applicable_without_calling_it(self) -> None:
        self.source["pack"]["side"] = "client"
        self.source["pack"]["applicable_sides"] = ["client"]
        result = self._run(
            accept_minecraft_eula=False,
            server_compatibility_experiments=(),
        )

        self.assertEqual([side for side, _ in self.calls], ["client"])
        self.assertEqual(result["outcome"], "passed")
        self.assertEqual(result["receipt"]["sides"]["server"]["state"], "not-applicable")
        self.assertFalse(result["receipt"]["sides"]["server"]["selected"])

    def test_client_only_completed_receipt_renders_and_reopens_with_inert_server_request(
        self,
    ) -> None:
        self.source["pack"]["side"] = "client"
        self.source["pack"]["applicable_sides"] = ["client"]
        result = self._run(
            accept_minecraft_eula=False,
            server_compatibility_experiments=(),
        )

        self.assertIn(
            "SUSY applicable-side dev run: passed",
            subject.render_susy_mod_run(result),
        )
        with (
            patch.object(subject, "_source_result", self._source_result),
            patch.object(
                subject,
                "COMPATIBILITY_EXPERIMENTS",
                {"shared-experiment": "shared.json"},
            ),
        ):
            reopened = subject.reopen_susy_mod_run(
                self.suite,
                self.run_id,
                result["dev_run_id"],
            )
        self.assertEqual(reopened["outcome"], "passed")

    def test_reopen_accepts_server_owner_canonical_experiment_order(self) -> None:
        result = self._run(
            server_compatibility_experiments=(
                "server-experiment",
                "shared-experiment",
            ),
        )

        with (
            patch.object(subject, "_source_result", self._source_result),
            patch.object(
                subject,
                "COMPATIBILITY_EXPERIMENTS",
                {"shared-experiment": "shared.json"},
            ),
        ):
            reopened = subject.reopen_susy_mod_run(
                self.suite,
                self.run_id,
                result["dev_run_id"],
            )
        self.assertEqual(reopened["outcome"], "passed")

    def test_explicit_inapplicable_both_rejects_before_owner_or_residue(self) -> None:
        self.source["pack"]["side"] = "client"
        self.source["pack"]["applicable_sides"] = ["client"]
        with (
            patch.object(subject, "_source_result", self._source_result),
            patch.object(subject, "_preflight_owners") as preflight,
            self.assertRaisesRegex(subject.SusyModRunError, "not applicable"),
        ):
            subject.run_susy_mod(self.suite, self.run_id, side="both")
        preflight.assert_not_called()
        self.assertFalse((self.run_root / "runtime/dev-runs").exists())
        self.assertEqual(self.calls, [])

    def test_auto_client_only_rejects_unused_server_inputs(self) -> None:
        self.source["pack"]["side"] = "client"
        self.source["pack"]["applicable_sides"] = ["client"]
        with (
            patch.object(subject, "_source_result", self._source_result),
            patch.object(subject, "_preflight_owners") as preflight,
            self.assertRaisesRegex(subject.SusyModRunError, "server inputs"),
        ):
            subject.run_susy_mod(
                self.suite,
                self.run_id,
                side="auto",
                accept_minecraft_eula=True,
            )
        preflight.assert_not_called()
        self.assertFalse((self.run_root / "runtime/dev-runs").exists())

    def test_cleanly_contained_failure_preserves_result_and_runs_other_side(self) -> None:
        self.outcomes["client"] = ("failed", True)
        result = self._run()

        self.assertEqual([side for side, _ in self.calls], ["client", "server"])
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(result["receipt"]["sides"]["client"]["state"], "failed")
        self.assertEqual(result["receipt"]["sides"]["server"]["state"], "passed")
        self.assertTrue(result["receipt"]["cleanup"]["all_selected_sides_contained"])

    def test_clean_client_cancellation_stops_the_second_side(self) -> None:
        self.outcomes["client"] = ("failed", True)

        def cancelled_reference(*args: object, **kwargs: object) -> dict:
            reference = self._child_reference(*args, **kwargs)
            if args[0] == "client":
                reference["failure_kind"] = "cancelled"
            return reference

        with (
            patch.object(subject, "_source_result", self._source_result),
            patch.object(subject, "_preflight_owners"),
            patch.object(
                subject,
                "COMPATIBILITY_EXPERIMENTS",
                {"shared-experiment": "shared.json"},
            ),
            patch.object(
                subject,
                "SERVER_EXPERIMENTS",
                {"shared-experiment", "server-experiment"},
            ),
            patch.object(
                subject, "launch_susy_mod_client", self._launch("client")
            ),
            patch.object(
                subject, "launch_susy_mod_server", self._launch("server")
            ),
            patch.object(subject, "_child_reference", cancelled_reference),
        ):
            result = subject.run_susy_mod(
                self.suite,
                self.run_id,
                side="auto",
                accept_minecraft_eula=True,
                client_compatibility_experiments=("shared-experiment",),
                server_compatibility_experiments=(
                    "shared-experiment",
                    "server-experiment",
                ),
            )

        self.assertEqual(result["outcome"], "failed")
        self.assertEqual([side for side, _ in self.calls], ["client"])
        self.assertEqual(result["receipt"]["sides"]["server"]["state"], "not-run")

    def test_cleanup_residue_stops_the_second_side_and_is_not_parity(self) -> None:
        self.outcomes["client"] = ("failed", False)
        result = self._run()

        self.assertEqual([side for side, _ in self.calls], ["client"])
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(result["receipt"]["sides"]["server"]["state"], "not-run")
        self.assertEqual(
            result["receipt"]["cleanup"]["unresolved_side"], "client"
        )
        self.assertFalse(result["receipt"]["cleanup"]["all_selected_sides_contained"])

    def test_cleanup_residue_makes_retry_unavailable(self) -> None:
        self.outcomes["client"] = ("failed", False)
        template = self.root / "server-template"
        template.mkdir()
        result = self._run(
            accept_minecraft_eula=False,
            server_template=template,
        )

        retry = result["next_actions"][0]
        self.assertFalse(retry["available"])
        self.assertIsNone(retry["argv"])
        self.assertIn("custody", retry["reason"])

    def test_reopen_rejects_resealed_wrong_unresolved_custody_side(self) -> None:
        self.outcomes["client"] = ("failed", False)
        result = self._run()
        receipt_path = Path(result["receipt_uri"].removeprefix("file://"))
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(receipt["cleanup"]["unresolved_side"], "client")
        receipt["cleanup"]["unresolved_side"] = "server"
        receipt.pop("receipt_id")
        receipt["receipt_id"] = subject.DEV_RUN_ID_PREFIX + sha256(
            subject._canonical(receipt)
        ).hexdigest()
        receipt_path.write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        with (
            patch.object(subject, "_source_result", self._source_result),
            self.assertRaises(subject.SusyModRunError),
        ):
            subject.reopen_susy_mod_run(
                self.suite,
                self.run_id,
                result["dev_run_id"],
            )

    def test_parent_keyboard_interrupt_is_terminal_and_stops_the_second_side(self) -> None:
        client = Mock(side_effect=KeyboardInterrupt)
        server = Mock()
        with (
            patch.object(subject, "_source_result", self._source_result),
            patch.object(subject, "_preflight_owners"),
            patch.object(
                subject,
                "COMPATIBILITY_EXPERIMENTS",
                {"shared-experiment": "shared.json"},
            ),
            patch.object(
                subject,
                "SERVER_EXPERIMENTS",
                {"shared-experiment", "server-experiment"},
            ),
            patch.object(subject, "launch_susy_mod_client", client),
            patch.object(subject, "launch_susy_mod_server", server),
        ):
            try:
                result = subject.run_susy_mod(
                    self.suite,
                    self.run_id,
                    side="auto",
                    accept_minecraft_eula=True,
                    client_compatibility_experiments=("shared-experiment",),
                    server_compatibility_experiments=(
                        "shared-experiment",
                        "server-experiment",
                    ),
                )
            except KeyboardInterrupt:
                self.fail("the unified owner leaked KeyboardInterrupt without terminalizing its receipt")

        self.assertEqual(result["outcome"], "failed")
        receipt = result["receipt"]
        self.assertEqual(receipt["state"], "complete")
        self.assertEqual(receipt["sides"]["client"]["state"], "failed")
        self.assertEqual(receipt["sides"]["server"]["state"], "not-run")
        self.assertEqual(receipt["cleanup"]["unresolved_side"], "client")
        self.assertFalse(receipt["cleanup"]["all_selected_sides_contained"])
        server.assert_not_called()
        attempt = Path(result["receipt_uri"].removeprefix("file://")).parent
        self.assertFalse((attempt / ".lock").exists())

    def test_runner_identity_failure_does_not_leave_a_locked_attempt(self) -> None:
        with (
            patch.object(
                subject,
                "_runner_identity",
                side_effect=subject.SusyModRunError("fixture runner failure"),
            ),
            self.assertRaisesRegex(subject.SusyModRunError, "fixture runner failure"),
        ):
            self._run()

        runtime = self.run_root / "runtime"
        self.assertEqual(list(runtime.rglob(".lock")), [])

    def test_missing_server_consent_is_preflight_and_leaves_no_attempt(self) -> None:
        with (
            patch.object(subject, "_source_result", self._source_result),
            patch.object(subject, "_preflight_owners") as preflight,
            self.assertRaisesRegex(subject.SusyModRunError, "requires exactly one"),
        ):
            subject.run_susy_mod(self.suite, self.run_id, side="both")
        preflight.assert_not_called()
        self.assertFalse((self.run_root / "runtime/dev-runs").exists())

    def test_reopen_revalidates_parent_and_child_bytes(self) -> None:
        result = self._run()
        dev_run_id = result["dev_run_id"]
        with (
            patch.object(subject, "_source_result", self._source_result),
            patch.object(
                subject,
                "COMPATIBILITY_EXPERIMENTS",
                {"shared-experiment": "shared.json"},
            ),
        ):
            reopened = subject.reopen_susy_mod_run(
                self.suite, self.run_id, dev_run_id
            )
        self.assertTrue(reopened["reused"])
        self.assertEqual(reopened["receipt"]["receipt_id"], result["receipt"]["receipt_id"])

        owner_path = Path(
            result["receipt"]["sides"]["client"]["owner"]["receipt_uri"].removeprefix(
                "file://"
            )
        )
        owner_path.write_text("tampered\n", encoding="utf-8")
        with (
            patch.object(subject, "_source_result", self._source_result),
            self.assertRaisesRegex(subject.SusyModRunError, "owner receipt bytes"),
        ):
            subject.reopen_susy_mod_run(self.suite, self.run_id, dev_run_id)

    def test_reopen_rejects_self_resealed_contradictory_outcome(self) -> None:
        self.outcomes["client"] = ("failed", True)
        result = self._run()
        self.assertEqual(result["outcome"], "failed")
        receipt_path = Path(result["receipt_uri"].removeprefix("file://"))
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["outcome"] = "passed"
        receipt.pop("receipt_id")
        receipt["receipt_id"] = subject.DEV_RUN_ID_PREFIX + sha256(
            subject._canonical(receipt)
        ).hexdigest()
        receipt_path.write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        with (
            patch.object(subject, "_source_result", self._source_result),
            self.assertRaises(subject.SusyModRunError),
        ):
            subject.reopen_susy_mod_run(
                self.suite,
                self.run_id,
                result["dev_run_id"],
            )

    def test_reopen_rejects_resealed_request_or_cleanup_claims(self) -> None:
        for label, mutate in (
            (
                "memory",
                lambda receipt: receipt["request"].update(memory_mib=4096),
            ),
            (
                "server-timeout",
                lambda receipt: receipt["request"]["server"].update(
                    timeout_seconds=601.0
                ),
            ),
            (
                "server-shutdown-timeout",
                lambda receipt: receipt["request"]["server"].update(
                    shutdown_timeout_seconds=181.0
                ),
            ),
            (
                "server-poll-interval",
                lambda receipt: receipt["request"]["server"].update(
                    poll_interval_seconds=0.5
                ),
            ),
            (
                "unresolved-cleanup",
                lambda receipt: receipt["cleanup"].update(
                    unresolved_side="client"
                ),
            ),
        ):
            with self.subTest(label=label):
                result = self._run()
                receipt_path = Path(result["receipt_uri"].removeprefix("file://"))
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                mutate(receipt)
                receipt["request_id"] = subject.DEV_RUN_REQUEST_ID_PREFIX + sha256(
                    subject._canonical(receipt["request"])
                ).hexdigest()
                receipt.pop("receipt_id")
                receipt["receipt_id"] = subject.DEV_RUN_ID_PREFIX + sha256(
                    subject._canonical(receipt)
                ).hexdigest()
                receipt_path.write_text(
                    json.dumps(receipt, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )

                with (
                    patch.object(subject, "_source_result", self._source_result),
                    self.assertRaises(subject.SusyModRunError),
                ):
                    subject.reopen_susy_mod_run(
                        self.suite,
                        self.run_id,
                        result["dev_run_id"],
                    )

    def test_reopen_rejects_symlinked_dev_runs_ancestor(self) -> None:
        result = self._run()
        retained_dev_runs = self.run_root / "runtime/dev-runs"
        outside_dev_runs = self.root / "outside-dev-runs"
        retained_dev_runs.rename(outside_dev_runs)
        retained_dev_runs.symlink_to(outside_dev_runs, target_is_directory=True)

        with (
            patch.object(subject, "_source_result", self._source_result),
            self.assertRaises(subject.SusyModRunError),
        ):
            subject.reopen_susy_mod_run(
                self.suite,
                self.run_id,
                result["dev_run_id"],
            )

    def test_child_reference_rejects_forged_client_launch_identity(self) -> None:
        receipt_path = (
            self.run_root
            / "runtime/launches/client-attempt/susy-mod-launch-v1.json"
        )
        receipt_path.parent.mkdir(parents=True)
        launch_identity = {
            "run_id": self.run_id,
            "stage_id": "workbench-susy-mod-client-stage:sha256:" + "4" * 64,
            "instance_id": "client-attempt",
            "candidate_sha256": "3" * 64,
            "started_at": "2026-08-21T12:00:00+00:00",
            "launcher_sha256": "5" * 64,
            "java_runtime_id": "workbench-java-runtime:sha256:" + "6" * 64,
        }
        valid_id = "workbench-susy-mod-launch:sha256:" + sha256(
            subject._canonical(launch_identity)
        ).hexdigest()
        forged_id = valid_id[:-1] + ("0" if valid_id[-1] != "0" else "1")
        receipt = {
            "format": subject.LAUNCH_RECEIPT_FORMAT,
            "schema_version": 1,
            "run_id": self.run_id,
            "outcome": "passed",
            "launch_id": forged_id,
            "stage_id": launch_identity["stage_id"],
            "started_at": launch_identity["started_at"],
            "candidate": {"sha256": launch_identity["candidate_sha256"]},
            "launcher": {
                "instance_id": launch_identity["instance_id"],
                "sha256": launch_identity["launcher_sha256"],
            },
            "java": {"runtime_id": launch_identity["java_runtime_id"]},
            "cleanup": {"owned_processes_running": False, "errors": []},
            "target": {"receipt_uri": receipt_path.as_uri()},
        }
        receipt_path.write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        owner_result = {
            "format": subject.LAUNCH_RESULT_FORMAT,
            "schema_version": 1,
            "outcome": "passed",
            "receipt": receipt,
        }

        with self.assertRaises(subject.SusyModRunError):
            subject._child_reference(
                "client",
                owner_result,
                run_id=self.run_id,
                run_root=self.run_root,
            )

    def test_reopen_rejects_resealed_child_for_a_different_candidate(self) -> None:
        result = self._run()
        receipt_path = Path(result["receipt_uri"].removeprefix("file://"))
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        owner = receipt["sides"]["client"]["owner"]
        owner_path = Path(owner["receipt_uri"].removeprefix("file://"))
        child = json.loads(owner_path.read_text(encoding="utf-8"))
        child["candidate"]["sha256"] = "7" * 64
        child["launch_id"] = subject._client_launch_id(child)
        owner_path.write_text(
            json.dumps(child, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        raw = owner_path.read_bytes()
        owner["owner_id"] = child["launch_id"]
        owner["receipt_sha256"] = sha256(raw).hexdigest()
        owner["receipt_size"] = len(raw)
        receipt.pop("receipt_id")
        receipt["receipt_id"] = subject.DEV_RUN_ID_PREFIX + sha256(
            subject._canonical(receipt)
        ).hexdigest()
        receipt_path.write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        with (
            patch.object(subject, "_source_result", self._source_result),
            self.assertRaises(subject.SusyModRunError),
        ):
            subject.reopen_susy_mod_run(
                self.suite,
                self.run_id,
                result["dev_run_id"],
            )

    def test_each_invocation_is_a_new_physical_attempt_without_rebuilding(self) -> None:
        first = self._run()
        first_calls = len(self.calls)
        second = self._run()

        self.assertEqual(first_calls, 2)
        self.assertEqual(len(self.calls), 4)
        self.assertNotEqual(first["dev_run_id"], second["dev_run_id"])
        self.assertEqual(
            first["receipt"]["source"]["result_id"],
            second["receipt"]["source"]["result_id"],
        )
        self.assertEqual(
            first["receipt"]["source"]["candidate"]["sha256"],
            second["receipt"]["source"]["candidate"]["sha256"],
        )


if __name__ == "__main__":
    unittest.main()
