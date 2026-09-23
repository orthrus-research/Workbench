from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

from jsonschema import Draft202012Validator

from workbench_shell.diagnose_reproduce import (
    DiagnoseReproduceV2Error,
    create_reproduction_capsule,
    diagnose_live_console,
    inspect_reproduction_capsule,
    replay_reproduction_capsule,
)
from workbench_api.events import EventNormalizer, RawLocator as EventRawLocator
from workbench_core.sessions import RetainedSession, live_console_owner_reference


class DiagnoseReproduceV2Tests(unittest.TestCase):
    SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"

    def _assert_schema(self, name: str, value: object) -> None:
        schema = json.loads((self.SCHEMAS / name).read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(value)

    def _failed_owner(self, root: Path) -> dict[str, object]:
        session = RetainedSession(
            root=root,
            command_id="cleanroom.fixture-run",
            argv=["workbench", "dev", "fixture", "run"],
            cwd=root,
            intent="inspect",
            session_id="diagnose-fixture-session",
        )
        messages = (
            ("Mixin target example.Target was not found", "fatal"),
            ("Runtime wrapper reported launch failure", "error"),
            ("Stopping retained Cleanroom target", "info"),
            ("Plain retained owner output", "unknown"),
        )
        offset = 0
        normalizer = EventNormalizer(root=root)
        for line, severity in messages:
            raw = (line + "\n").encode("utf-8")
            retained = session.write_raw("stderr", raw)
            event = normalizer.normalize(
                line,
                source="cleanroom.fixture-run",
                stream="stderr",
                raw_locator=EventRawLocator(
                    artifact=retained.path,
                    byte_start=retained.byte_start,
                    byte_end=retained.byte_end,
                    line=offset + 1,
                    boundary="lf",
                ),
            ).as_dict()
            event["severity"] = severity
            event["outcome_failure"] = severity in {"error", "fatal"}
            session.record_event(event)
            offset += 1
        historical = live_console_owner_reference(root, session.session_id)
        session.finish(
            state="failed",
            process_exit_code=None,
            effective_exit_code=1,
            outcome="observed-required-failure",
        )
        return historical

    def test_diagnosis_keeps_observation_unknowns_and_typed_action_distinct(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            owner_ref = self._failed_owner(root)
            diagnosis = diagnose_live_console(root, owner_ref)

            self.assertEqual(diagnosis["format"], "workbench-diagnosis-v1")
            self.assertEqual(diagnosis["outcome"], "failed")
            self.assertEqual(
                diagnosis["observed_failures"][0]["message"],
                "Mixin target example.Target was not found",
            )
            self.assertEqual(diagnosis["observed_failures"][0]["claim_state"], "observed")
            self.assertEqual(diagnosis["wrappers"][0]["claim_state"], "observed")
            self.assertEqual(diagnosis["shutdown_noise"][0]["claim_state"], "observed")
            self.assertEqual(diagnosis["classifications"], [])
            self.assertEqual(diagnosis["unknowns"][0]["claim_state"], "unknown")
            self.assertEqual(
                diagnosis["next_experiments"][0]["action_id"],
                "workbench.diagnose.inspect-raw",
            )
            self.assertEqual(diagnosis["next_experiments"][0]["mutation"], "read-only")
            self.assertTrue(diagnosis["diagnosis_id"].startswith("workbench-diagnosis:sha256:"))
            self._assert_schema("workbench-diagnosis-v1.schema.json", diagnosis)

            repeated = diagnose_live_console(root, owner_ref)
            self.assertEqual(repeated["diagnosis_id"], diagnosis["diagnosis_id"])

    def test_diagnosis_retains_exact_cleanroom_stage_semantics(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            owner_ref = self._failed_owner(root)
            receipt_digest = "sha256:" + "a" * 64
            diagnosis = diagnose_live_console(
                root,
                owner_ref,
                owner_classifications=[
                    {
                        "classification_id": "cleanroom-dev-loop-stage",
                        "claim_state": "observed",
                        "owner_id": "workbench-shell",
                        "owner_record_id": (
                            "workbench-cleanroom-dev-loop-receipt:" + receipt_digest
                        ),
                        "owner_record_kind": "workbench-cleanroom-dev-loop-receipt",
                        "owner_record_uri": (root / "dev-loop/receipt.json").as_uri(),
                        "owner_record_digest": receipt_digest,
                        "stage": "server",
                        "state": "failed",
                        "required_markers": [
                            "dedicated-server-ready",
                            "common-registry-ready",
                        ],
                        "observed_markers": [],
                        "effective_exit_code": 0,
                        "cleanup_contained": True,
                        "artifact_digest": "sha256:" + "b" * 64,
                        "detail": "The required dedicated-server markers were not observed.",
                    }
                ],
                owner_next_experiments=[
                    {
                        "action_id": "dev.fixture-run",
                        "arguments": {
                            "source_plan_id": (
                                "workbench-cleanroom-dev-loop-plan:sha256:" + "c" * 64
                            ),
                            "sides": ["server"],
                        },
                        "context_digest": receipt_digest,
                        "mutation": "isolated-target-only",
                    }
                ],
            )

            self.assertEqual(
                diagnosis["classifications"][0]["state"],
                "failed",
            )
            self.assertEqual(
                diagnosis["classifications"][0]["observed_markers"],
                [],
            )
            self.assertEqual(
                diagnosis["next_experiments"][1]["action_id"],
                "dev.fixture-run",
            )
            self._assert_schema("workbench-diagnosis-v1.schema.json", diagnosis)

            passed = dict(diagnosis["classifications"][0])
            passed.update(
                {
                    "state": "passed",
                    "observed_markers": list(passed["required_markers"]),
                    "effective_exit_code": 130,
                    "detail": "The owner classified the controlled server stop as passed.",
                }
            )
            passed_diagnosis = diagnose_live_console(
                root,
                owner_ref,
                owner_classifications=[passed],
            )
            self.assertEqual(
                passed_diagnosis["outcome"],
                "inconclusive",
                "generic failed/cancelled process state overruled owner stage truth",
            )

    def test_capsule_is_deterministic_inspectable_and_replays_only_typed_action(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            diagnosis = diagnose_live_console(root, self._failed_owner(root))
            first = root / "first.wb-repro"
            second = root / "second.wb-repro"
            action = {
                "action_id": "cleanroom.fixture-run",
                "arguments": {"variant": "broken-mixin"},
                "mutation": "isolated-target-only",
            }
            first_result = create_reproduction_capsule(
                diagnosis,
                first,
                replay_action=action,
                privacy_review={
                    "approved": True,
                    "excluded": ["credentials", "protected-binaries", "personal-worlds"],
                },
            )
            second_result = create_reproduction_capsule(
                diagnosis,
                second,
                replay_action=action,
                privacy_review={
                    "approved": True,
                    "excluded": ["credentials", "protected-binaries", "personal-worlds"],
                },
            )
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(first_result["capsule_id"], second_result["capsule_id"])
            self._assert_schema(
                "workbench-reproduction-capsule-result-v1.schema.json",
                first_result,
            )

            with ZipFile(first, "r") as archive:
                manifest = json.loads(archive.read("manifest.json"))
            self._assert_schema(
                "workbench-reproduction-capsule-manifest-v1.schema.json",
                manifest,
            )

            inspected = inspect_reproduction_capsule(first)
            self.assertEqual(inspected["capsule_id"], first_result["capsule_id"])
            self.assertEqual(inspected["diagnosis_id"], diagnosis["diagnosis_id"])
            self.assertEqual(inspected["member_count"], 2)
            self._assert_schema(
                "workbench-reproduction-capsule-inspection-v1.schema.json",
                inspected,
            )

            calls: list[dict[str, object]] = []

            def execute(value: dict[str, object]) -> dict[str, object]:
                calls.append(value)
                return {"outcome": "matching-failure", "fingerprint": diagnosis["fingerprint"]}

            replayed = replay_reproduction_capsule(
                first,
                allowed_action_ids={"cleanroom.fixture-run"},
                execute=execute,
            )
            self.assertEqual(replayed["outcome"], "matching-failure")
            self.assertEqual(calls, [action])
            self._assert_schema(
                "workbench-reproduction-capsule-replay-v1.schema.json",
                replayed,
            )

            with self.assertRaisesRegex(DiagnoseReproduceV2Error, "not allowlisted"):
                replay_reproduction_capsule(
                    first,
                    allowed_action_ids={"different.action"},
                    execute=execute,
                )

    def test_capsule_creation_requires_review_and_verification_rejects_drift(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            diagnosis = diagnose_live_console(root, self._failed_owner(root))
            target = root / "failure.wb-repro"
            with self.assertRaisesRegex(DiagnoseReproduceV2Error, "privacy review"):
                create_reproduction_capsule(
                    diagnosis,
                    target,
                    replay_action={
                        "action_id": "cleanroom.fixture-run",
                        "arguments": {},
                        "mutation": "isolated-target-only",
                    },
                    privacy_review={"approved": False, "excluded": []},
                )
            with self.assertRaisesRegex(DiagnoseReproduceV2Error, "sensitive"):
                create_reproduction_capsule(
                    diagnosis,
                    target,
                    replay_action={
                        "action_id": "cleanroom.fixture-run",
                        "arguments": {"apiToken": "seeded-canary"},
                        "mutation": "isolated-target-only",
                    },
                    privacy_review={
                        "approved": True,
                        "excluded": [
                            "credentials",
                            "protected-binaries",
                            "personal-worlds",
                        ],
                    },
                )
            target.write_bytes(b"not-a-capsule")
            retained = root / ".workbench"
            before = {
                path.relative_to(retained).as_posix(): path.read_bytes()
                for path in retained.rglob("*")
                if path.is_file()
            }
            with self.assertRaises(DiagnoseReproduceV2Error):
                inspect_reproduction_capsule(target)
            after = {
                path.relative_to(retained).as_posix(): path.read_bytes()
                for path in retained.rglob("*")
                if path.is_file()
            }
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
