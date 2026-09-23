"""Adversarial tests for paired SUSY dedicated-server change checks."""

from __future__ import annotations

import base64
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/workbench-shell/src"
PROJECT_INTELLIGENCE = ROOT / "modules/project-intelligence/src"
ATLAS = ROOT / "modules/atlas/src"
BLUEPRINTS = ROOT / "modules/blueprints/src"
TESTS = Path(__file__).resolve().parent

for path in (TESTS, ATLAS, BLUEPRINTS, PROJECT_INTELLIGENCE, SOURCE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import test_susy_mod_server as server_fixture  # noqa: E402
from workbench_shell.susy_mod_check import (  # noqa: E402
    CHECK_RECEIPT_FORMAT,
    CHECK_RESULT_FORMAT,
    SusyModCheckError,
    check_susy_mod_server,
    render_susy_mod_check,
)
from workbench_shell.susy_mod_server import (  # noqa: E402
    SERVER_RECEIPT_FORMAT,
    SERVER_RESULT_FORMAT,
    _log_diagnostics,
    SusyModServerError,
)
import workbench_shell.susy_mod_check as susy_mod_check  # noqa: E402
import workbench_shell.susy_mod_server as susy_mod_server  # noqa: E402


RUN_ID = server_fixture.RUN_ID


def _canonical(value: dict) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _seal(prefix: str, value: dict, field: str) -> dict:
    sealed = deepcopy(value)
    sealed.pop(field, None)
    value[field] = prefix + sha256(_canonical(sealed)).hexdigest()
    return value


def _file_record(path: Path) -> dict:
    payload = path.read_bytes()
    return {
        "uri": path.resolve().as_uri(),
        "sha256": sha256(payload).hexdigest(),
        "size": len(payload),
    }


def _issue(level: str, logger: str, message: str, second: int = 0) -> str:
    return (
        f"[12:00:{second:02d}] [Server thread/{level}] "
        f"[{logger}]: {message}"
    )


def _paired_fake_java() -> str:
    """A direct fake server that hashes whichever paired artifact it receives."""

    return r'''#!/bin/sh
set -eu
probe_metadata=
for argument in "$@"; do
  case "$argument" in
    -javaagent:*=*) probe_metadata=${argument#*=} ;;
  esac
done
WORKBENCH_PAIR_PROBE="$probe_metadata" WORKBENCH_PAIR_PID="$$" \
  /usr/bin/python3 - <<'PY'
import base64
from hashlib import sha256
import json
import os
from pathlib import Path

metadata = json.loads(base64.urlsafe_b64decode(os.environ["WORKBENCH_PAIR_PROBE"]))
source = (Path.cwd() / "mods" / metadata["filename"]).resolve()
payload = source.read_bytes()
pid = int(os.environ["WORKBENCH_PAIR_PID"])
proof = {
    "format": "workbench-forge-loaded-source-probe-v1",
    "nonce": metadata["nonce"],
    "process": f"{pid}@paired-fake",
    "pid": pid,
    "mods": [
        {
            "mod_id": mod_id,
            "version": "1.0",
            "source_path": str(source),
            "sha256": sha256(payload).hexdigest(),
            "size": len(payload),
        }
        for mod_id in metadata["mod_ids"]
    ],
}
target = Path(metadata["output"])
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps(proof, sort_keys=True) + "\n", encoding="utf-8")
PY
mkdir -p logs
cat > logs/latest.log <<'EOF'
[12:00:00] [Server thread/INFO] [FML]: Forge Mod Loader has successfully loaded 2 mods
[12:00:00] [Server thread/INFO] [net.minecraft.server.dedicated.DedicatedServer]: Done (1.000s)! For help, type "help" or "?"
[12:00:00] [Server thread/INFO] [FTB Library]: Reloaded server in 3ms
EOF
printf '%s\n' 'GroovyScript server scripts completed.' > logs/groovy_server.log
if IFS= read -r command; then
  printf '%s\n' "$command" > fake-stop-command.txt
  cat >> logs/latest.log <<'EOF'
[12:00:01] [Server thread/INFO] [net.minecraft.server.dedicated.DedicatedServer]: Stopping the server
[12:00:01] [Server thread/INFO] [net.minecraft.server.MinecraftServer]: Stopping server
[12:00:01] [Server thread/INFO] [net.minecraft.server.MinecraftServer]: Saving players
[12:00:01] [Server thread/INFO] [net.minecraft.server.MinecraftServer]: Saving worlds
EOF
fi
exit 0
'''


def _compile_fake_probe(
    build_root: Path,
    *,
    projected_instance: Path,
    java_executable: Path,
    launcher_host: dict,
    nonce: str,
    expected_mod_ids: list[str],
):
    del java_executable, launcher_host
    build_root.mkdir(parents=True)
    jar = build_root / "workbench-candidate-loaded-agent.jar"
    jar.write_bytes(b"paired fixture java agent")
    proof = (
        projected_instance
        / ".workbench/candidate-loaded-probe/loaded-source-v1.json"
    )
    metadata = base64.urlsafe_b64encode(
        json.dumps(
            {
                "filename": "sample-1.0.jar",
                "mod_ids": sorted(expected_mod_ids),
                "nonce": nonce,
                "output": str(proof),
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).decode("ascii")
    return (
        {
            "format": "fixture-loaded-source-probe-build-v1",
            "jar_sha256": sha256(jar.read_bytes()).hexdigest(),
            "jar_size": jar.stat().st_size,
        },
        proof,
        f"-javaagent:/paired-fixture-agent.jar={metadata}",
        jar,
    )


class _PairFixture:
    """One retained build plus file-backed, independently fresh fake legs."""

    def __init__(self, root: Path) -> None:
        self.retained = server_fixture._RetainedBuild(root)
        self.root = root
        self.calls: list[dict] = []
        self.serial = 0
        self.results: dict[str, dict] = {}
        self.template_before = server_fixture._snapshot(self.retained.template)
        self.result_before = (self.retained.run_root / "result.json").read_bytes()
        self.candidate_before = self.retained.candidate.read_bytes()

    def leg(
        self,
        role: str,
        *,
        issues: tuple[str, ...] = (),
        outcome: str = "passed",
        failure_kind: str | None = None,
        crash: bool = False,
        cleanup_safe: bool = True,
        inputs_unchanged: bool = True,
        checkpoint_reached: bool = True,
        template_token: str = "1",
        java_token: str = "2",
    ) -> dict:
        self.serial += 1
        attempt_id = f"fixture-{role}-{self.serial}"
        leg_root = (
            self.retained.run_root
            / "runtime/fake-server-legs"
            / attempt_id
        )
        projection = leg_root / "projection"
        latest = projection / ".minecraft/logs/latest.log"
        latest.parent.mkdir(parents=True)
        lines = [
            "[12:00:00] [Server thread/INFO] [FML]: "
            "Forge Mod Loader has successfully loaded 2 mods",
            "[12:00:00] [Server thread/INFO] "
            "[net.minecraft.server.dedicated.DedicatedServer]: "
            'Done (1.000s)! For help, type "help" or "?"',
            *issues,
            "[12:00:10] [Server thread/INFO] "
            "[net.minecraft.server.dedicated.DedicatedServer]: Stopping the server",
            "[12:00:10] [Server thread/INFO] "
            "[net.minecraft.server.MinecraftServer]: Stopping server",
            "[12:00:10] [Server thread/INFO] "
            "[net.minecraft.server.MinecraftServer]: Saving players",
            "[12:00:10] [Server thread/INFO] "
            "[net.minecraft.server.MinecraftServer]: Saving worlds",
        ]
        latest.write_text("\n".join(lines) + "\n", encoding="utf-8")
        stdout = leg_root / "server.stdout.log"
        stdout.write_text("fixture server output\n", encoding="utf-8")
        evidence = {
            "latest_log": _file_record(latest),
            "stdout": _file_record(stdout),
        }
        if crash:
            crash_path = projection / ".minecraft/crash-reports/crash.txt"
            crash_path.parent.mkdir(parents=True)
            crash_path.write_text(
                "---- Minecraft Crash Report ----\nfixture crash\n",
                encoding="utf-8",
            )
            evidence["crash_report"] = _file_record(crash_path)

        baseline = role == "baseline"
        artifact = (
            self.retained.template / "mods/sample-1.0.jar"
            if baseline
            else self.retained.candidate
        )
        artifact_payload = artifact.read_bytes()
        safe = cleanup_safe and inputs_unchanged
        receipt = {
            "format": SERVER_RECEIPT_FORMAT,
            "schema_version": 2,
            "attempt_id": attempt_id,
            "run_id": RUN_ID,
            "outcome": outcome,
            "failure_kind": failure_kind,
            "detail": None,
            "side": "dedicated-server",
            "candidate": {
                "path": "mods/sample-1.0.jar",
                "sha256": sha256(artifact_payload).hexdigest(),
                "size": len(artifact_payload),
                "mod_ids": ["sample"],
                "role": "pack-baseline-control" if baseline else "candidate",
            },
            "template": {
                "root_uri": self.retained.template.resolve().as_uri(),
                "payload": {
                    "tree_sha256": "sha256:" + template_token * 64,
                    "file_count": 8,
                    "total_bytes": 1024,
                },
                "pack_binding": {
                    "manifest_sha256": "3" * 64,
                    "index_sha256": "4" * 64,
                    "server_entry_count": 2,
                },
            },
            "projection": {
                "root_uri": projection.resolve().as_uri(),
                "compatibility_experiments": [],
            },
            "java": {
                "identity": {"runtime_id": "sha256:" + java_token * 64},
            },
            "evidence": evidence,
            "diagnostics": _log_diagnostics(latest.read_text(encoding="utf-8")),
            "checkpoint": (
                None
                if not checkpoint_reached
                else {"id": "dedicated-server-ready", "marker": lines[1]}
            ),
            "loaded_source_proof": (
                None
                if not checkpoint_reached
                else {
                    "format": "workbench-forge-loaded-source-probe-v1",
                    "pid": 1000 + self.serial,
                    "mods": [
                        {
                            "mod_id": "sample",
                            "source_path": str(artifact),
                            "sha256": sha256(artifact_payload).hexdigest(),
                            "size": len(artifact_payload),
                        }
                    ],
                }
            ),
            "process": {
                "pid": 1000 + self.serial,
                "exit_code": 0 if safe else None,
                "stop_command_attempted": True,
                "stop_command_sent": True,
                "clean_stop": safe,
                "shutdown_acknowledgment": {"complete": safe},
            },
            "cleanup": {
                "owned_processes_running": not cleanup_safe,
                "errors": [] if cleanup_safe else ["fixture survivor"],
            },
            "immutable_inputs": {
                "retained_result_unchanged": inputs_unchanged,
                "candidate_artifact_unchanged": inputs_unchanged,
                "server_template_unchanged": inputs_unchanged,
            },
            "claims": {
                "exact_candidate_loaded": not baseline and checkpoint_reached,
                "exact_runtime_subject_loaded": checkpoint_reached,
                "dedicated_server_ready": checkpoint_reached,
                "clean_shutdown": safe,
                "runtime_health_clean": not issues and not crash,
                "client_server_parity": False,
            },
            "runner": {
                "source_sha256": "5" * 64,
                "source_size": 100,
            },
            "limitations": [],
        }
        if baseline:
            receipt["runtime_subject"] = "pack-baseline"
        _seal("workbench-susy-mod-server-launch:", receipt, "receipt_id")
        return {
            "format": SERVER_RESULT_FORMAT,
            "schema_version": 2,
            "outcome": outcome,
            "receipt": receipt,
            "next_actions": [],
        }

    def launcher(self, *args, **kwargs):
        self.calls.append({"args": args, "kwargs": deepcopy(kwargs)})
        role = "baseline" if kwargs.get("_subject") == "pack-baseline" else "candidate"
        return deepcopy(self.results[role])

    def assert_inputs_unchanged(self, testcase: unittest.TestCase) -> None:
        testcase.assertEqual(
            self.template_before,
            server_fixture._snapshot(self.retained.template),
        )
        testcase.assertEqual(
            self.result_before,
            (self.retained.run_root / "result.json").read_bytes(),
        )
        testcase.assertEqual(
            self.candidate_before,
            self.retained.candidate.read_bytes(),
        )


class SusyModServerCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = _PairFixture(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_check(self, **kwargs) -> dict:
        with patch.object(
            susy_mod_check,
            "launch_susy_mod_server",
            side_effect=self.fixture.launcher,
        ):
            return check_susy_mod_server(
                self.fixture.retained.suite,
                RUN_ID,
                server_template=self.fixture.retained.template,
                server_java=self.fixture.root / "jdk/bin/java",
                compatibility_experiments=("susy-server-shutdown-bridge",),
                memory_mib=6144,
                timeout_seconds=91.5,
                shutdown_timeout_seconds=37.25,
                poll_interval_seconds=0.01,
                sample_limit=12,
                **kwargs,
            )

    def test_paired_fresh_legs_share_every_public_input_and_seal_receipt(self) -> None:
        self.fixture.results = {
            "baseline": self.fixture.leg("baseline"),
            "candidate": self.fixture.leg("candidate"),
        }

        result = self.run_check()

        self.assertEqual(CHECK_RESULT_FORMAT, result["format"])
        self.assertEqual("no-observed-regression", result["verdict"])
        self.assertEqual("passed", result["outcome"])
        self.assertEqual(2, len(self.fixture.calls))
        baseline_call, candidate_call = self.fixture.calls
        self.assertEqual(
            (self.fixture.retained.suite, RUN_ID), baseline_call["args"]
        )
        self.assertEqual(baseline_call["args"], candidate_call["args"])
        self.assertEqual("pack-baseline", baseline_call["kwargs"].pop("_subject"))
        self.assertEqual("candidate", candidate_call["kwargs"].pop("_subject"))
        self.assertEqual(baseline_call["kwargs"], candidate_call["kwargs"])

        receipt = result["receipt"]
        self.assertEqual(CHECK_RECEIPT_FORMAT, receipt["format"])
        self.assertEqual(
            {"baseline", "candidate"}, set(receipt["legs"])
        )
        self.assertNotEqual(
            receipt["legs"]["baseline"]["attempt_id"],
            receipt["legs"]["candidate"]["attempt_id"],
        )
        self.assertNotEqual(
            receipt["legs"]["baseline"]["projection"]["root_uri"],
            receipt["legs"]["candidate"]["projection"]["root_uri"],
        )
        runner = receipt["runner"]
        runner_path = Path(unquote(urlparse(runner["source_uri"]).path))
        self.assertEqual(
            sha256(runner_path.read_bytes()).hexdigest(), runner["sha256"]
        )
        self.assertEqual(runner_path.stat().st_size, runner["size"])
        self.assertEqual(
            ["susy-server-shutdown-bridge"],
            receipt["inputs"]["compatibility_experiments"],
        )
        self.assertEqual(6144, receipt["inputs"]["memory_mib"])
        observed_id = receipt.pop("receipt_id")
        self.assertEqual(
            "workbench-susy-mod-server-check:" + sha256(_canonical(receipt)).hexdigest(),
            observed_id,
        )
        self.fixture.assert_inputs_unchanged(self)

    def test_human_render_separates_smoke_success_from_pack_health(self) -> None:
        issue_lines = (
            _issue("FATAL", "GregTech Core", "invalid recipe"),
            _issue("ERROR", "Pyrotech", "advancement failed", 1),
            _issue("WARN", "FML", "signature mismatch", 2),
        )
        self.fixture.results = {
            "baseline": self.fixture.leg("baseline", issues=issue_lines),
            "candidate": self.fixture.leg("candidate", issues=issue_lines),
        }

        rendered = render_susy_mod_check(self.run_check())

        self.assertIn(
            "Selected runtime experiments: susy-server-shutdown-bridge",
            rendered,
        )
        self.assertIn("Baseline load/ready/stop smoke: passed", rendered)
        self.assertIn(
            "Baseline pack health: issues-observed · 1 FATAL · 1 ERROR · 1 WARN",
            rendered,
        )
        self.assertIn("Candidate load/ready/stop smoke: passed", rendered)
        self.assertIn(
            "Candidate diagnostic delta: 0 new ERROR/FATAL"
            " · +0 total issues · -0 resolved · 3 persisted",
            rendered,
        )

    def test_shared_diagnosis_offers_exact_compatibility_retry(self) -> None:
        experiment_id = "susy-reccomplex-susycore-0112-flag"
        baseline = self.fixture.leg(
            "baseline",
            issues=(_issue("FATAL", "mixin", "named variable mismatch"),),
            outcome="failed",
            failure_kind="fatal-startup-log",
        )
        candidate = self.fixture.leg(
            "candidate",
            issues=(_issue("FATAL", "mixin", "named variable mismatch"),),
            outcome="failed",
            failure_kind="fatal-startup-log",
        )
        for leg in (baseline, candidate):
            receipt = leg["receipt"]
            receipt["diagnosis"] = {
                "pattern_id": "susy-reccomplex-named-variable-0112-v1",
                "available_experiment": {
                    "id": experiment_id,
                    "spec_uri": "file:///fixture/susycore-0112.json",
                    "spec_sha256": "a" * 64,
                    "spec_size": 100,
                    "scope": "disposable launcher projection only",
                    "applicability": {
                        "pack_version": "0.1.16.12",
                        "susycore": "0.1.112",
                    },
                },
            }
            receipt.pop("receipt_id")
            _seal("workbench-susy-mod-server-launch:", receipt, "receipt_id")
        self.fixture.results = {"baseline": baseline, "candidate": candidate}

        result = self.run_check()

        self.assertEqual("incomparable", result["verdict"])
        self.assertEqual("failed", result["outcome"])
        action = result["next_actions"][0]
        self.assertEqual(
            "retry-server-change-check-with-diagnosed-experiment", action["id"]
        )
        self.assertEqual(
            ["--runtime-experiment", experiment_id], action["argv"][-2:]
        )
        rendered = render_susy_mod_check(result)
        self.assertIn(
            "Selected runtime experiments: susy-server-shutdown-bridge",
            rendered,
        )
        self.assertIn(experiment_id, rendered)

        candidate_receipt = candidate["receipt"]
        candidate_receipt["diagnosis"]["available_experiment"][
            "spec_sha256"
        ] = "b" * 64
        candidate_receipt.pop("receipt_id")
        _seal(
            "workbench-susy-mod-server-launch:",
            candidate_receipt,
            "receipt_id",
        )
        self.fixture.results = {"baseline": baseline, "candidate": candidate}
        mismatch = self.run_check()
        self.assertEqual(
            "run-server-change-check-again", mismatch["next_actions"][0]["id"]
        )
        self.assertNotIn(
            experiment_id, mismatch["next_actions"][0]["argv"]
        )

    def test_rejects_logs_or_projections_outside_custody_and_symlink_logs(self) -> None:
        for variant in (
            "projection-outside-run",
            "log-outside-projection",
            "symlink-log",
        ):
            with self.subTest(variant=variant):
                root = Path(self.temporary.name) / variant
                fixture = _PairFixture(root)
                baseline_result = fixture.leg("baseline")
                receipt = baseline_result["receipt"]
                latest = server_fixture._uri_path(
                    receipt["evidence"]["latest_log"]["uri"]
                )
                payload = latest.read_bytes()

                if variant == "projection-outside-run":
                    external_projection = root / "outside-run/projection"
                    external_latest = (
                        external_projection / ".minecraft/logs/latest.log"
                    )
                    external_latest.parent.mkdir(parents=True)
                    external_latest.write_bytes(payload)
                    receipt["projection"]["root_uri"] = (
                        external_projection.resolve().as_uri()
                    )
                    receipt["evidence"]["latest_log"] = _file_record(
                        external_latest
                    )
                    _seal(
                        "workbench-susy-mod-server-launch:",
                        receipt,
                        "receipt_id",
                    )
                elif variant == "log-outside-projection":
                    external_latest = (
                        fixture.retained.run_root
                        / "runtime/detached-latest.log"
                    )
                    external_latest.parent.mkdir(parents=True, exist_ok=True)
                    external_latest.write_bytes(payload)
                    receipt["evidence"]["latest_log"] = _file_record(
                        external_latest
                    )
                    _seal(
                        "workbench-susy-mod-server-launch:",
                        receipt,
                        "receipt_id",
                    )
                else:
                    target = latest.with_name("latest-real.log")
                    latest.replace(target)
                    latest.symlink_to(target)

                fixture.results = {
                    "baseline": baseline_result,
                    "candidate": fixture.leg("candidate"),
                }
                with patch.object(
                    susy_mod_check,
                    "launch_susy_mod_server",
                    side_effect=fixture.launcher,
                ):
                    with self.assertRaises(SusyModCheckError):
                        check_susy_mod_server(
                            fixture.retained.suite,
                            RUN_ID,
                            server_template=fixture.retained.template,
                        )
                self.assertEqual(1, len(fixture.calls))

    def test_rejects_tampered_log_bytes_or_claimed_digest(self) -> None:
        for variant in ("bytes", "digest"):
            with self.subTest(variant=variant):
                root = Path(self.temporary.name) / ("tampered-log-" + variant)
                fixture = _PairFixture(root)
                baseline_result = fixture.leg("baseline")
                receipt = baseline_result["receipt"]
                latest = server_fixture._uri_path(
                    receipt["evidence"]["latest_log"]["uri"]
                )
                if variant == "bytes":
                    latest.write_bytes(latest.read_bytes() + b"tampered\n")
                else:
                    receipt["evidence"]["latest_log"]["sha256"] = "0" * 64
                    _seal(
                        "workbench-susy-mod-server-launch:",
                        receipt,
                        "receipt_id",
                    )

                fixture.results = {
                    "baseline": baseline_result,
                    "candidate": fixture.leg("candidate"),
                }
                with patch.object(
                    susy_mod_check,
                    "launch_susy_mod_server",
                    side_effect=fixture.launcher,
                ):
                    with self.assertRaises(SusyModCheckError):
                        check_susy_mod_server(
                            fixture.retained.suite,
                            RUN_ID,
                            server_template=fixture.retained.template,
                        )
                self.assertEqual(1, len(fixture.calls))

    def test_rejects_server_receipt_with_invalid_self_identity(self) -> None:
        baseline_result = self.fixture.leg("baseline")
        baseline_result["receipt"]["receipt_id"] = (
            "workbench-susy-mod-server-launch:" + "0" * 64
        )
        self.fixture.results = {
            "baseline": baseline_result,
            "candidate": self.fixture.leg("candidate"),
        }

        with self.assertRaises(SusyModCheckError):
            self.run_check()

        self.assertEqual(1, len(self.fixture.calls))
        self.fixture.assert_inputs_unchanged(self)

    def test_candidate_preflight_failure_retains_incomparable_receipt(self) -> None:
        baseline_result = self.fixture.leg("baseline")

        def fail_candidate(*args, **kwargs):
            self.fixture.calls.append(
                {"args": args, "kwargs": deepcopy(kwargs)}
            )
            if kwargs.get("_subject") == "pack-baseline":
                return deepcopy(baseline_result)
            raise SusyModServerError("fixture candidate preflight failed")

        with patch.object(
            susy_mod_check,
            "launch_susy_mod_server",
            side_effect=fail_candidate,
        ):
            result = check_susy_mod_server(
                self.fixture.retained.suite,
                RUN_ID,
                server_template=self.fixture.retained.template,
            )

        self.assertEqual(2, len(self.fixture.calls))
        self.assertEqual("incomparable", result["verdict"])
        self.assertEqual("failed", result["outcome"])
        receipt = result["receipt"]
        self.assertIsNone(receipt["legs"]["candidate"])
        self.assertEqual(
            "fixture candidate preflight failed",
            receipt["candidate_preflight_error"],
        )
        self.assertFalse(receipt["comparisons"]["comparable"])
        observed_id = receipt["receipt_id"]
        self.assertEqual(
            "workbench-susy-mod-server-check:"
            + sha256(_canonical({
                key: value
                for key, value in receipt.items()
                if key != "receipt_id"
            })).hexdigest(),
            observed_id,
        )
        retained_path = server_fixture._uri_path(result["receipt_uri"])
        self.assertEqual(
            receipt,
            json.loads(retained_path.read_text(encoding="utf-8")),
        )
        self.fixture.assert_inputs_unchanged(self)

    def test_safe_application_failure_still_attempts_candidate(self) -> None:
        baseline_error = _issue("ERROR", "example/Loader", "baseline failed safely")
        self.fixture.results = {
            "baseline": self.fixture.leg(
                "baseline",
                issues=(baseline_error,),
                outcome="failed",
                failure_kind="post-ready-terminal-log",
            ),
            "candidate": self.fixture.leg("candidate"),
        }

        result = self.run_check()

        self.assertEqual(2, len(self.fixture.calls))
        self.assertEqual("candidate-improvement", result["verdict"])
        self.assertEqual(
            "failed", result["receipt"]["legs"]["baseline"]["outcome"]
        )
        self.fixture.assert_inputs_unchanged(self)

    def test_unsafe_cleanup_stops_before_candidate_and_is_incomparable(self) -> None:
        self.fixture.results = {
            "baseline": self.fixture.leg(
                "baseline",
                outcome="failed",
                failure_kind="server-clean-stop-failed",
                cleanup_safe=False,
            ),
            "candidate": self.fixture.leg("candidate"),
        }

        result = self.run_check()

        self.assertEqual(1, len(self.fixture.calls))
        self.assertEqual("pack-baseline", self.fixture.calls[0]["kwargs"]["_subject"])
        self.assertEqual("incomparable", result["verdict"])
        self.assertIsNone(result["receipt"]["legs"]["candidate"])
        self.fixture.assert_inputs_unchanged(self)

    def test_reported_input_drift_stops_before_candidate(self) -> None:
        self.fixture.results = {
            "baseline": self.fixture.leg(
                "baseline",
                outcome="failed",
                failure_kind="input-or-cleanup-integrity-failed",
                inputs_unchanged=False,
            ),
            "candidate": self.fixture.leg("candidate"),
        }

        result = self.run_check()

        self.assertEqual(1, len(self.fixture.calls))
        self.assertEqual("incomparable", result["verdict"])
        comparisons = result["receipt"]["comparisons"]
        self.assertFalse(comparisons["safe_to_continue_after_baseline"])
        self.fixture.assert_inputs_unchanged(self)

    def test_independent_remeasurement_stops_if_input_drifts_between_legs(self) -> None:
        self.fixture.results = {
            "baseline": self.fixture.leg("baseline"),
            "candidate": self.fixture.leg("candidate"),
        }

        def mutate_after_baseline(*args, **kwargs):
            result = self.fixture.launcher(*args, **kwargs)
            if kwargs.get("_subject") == "pack-baseline":
                self.fixture.retained.candidate.write_bytes(b"drifted between legs")
            return result

        with patch.object(
            susy_mod_check,
            "launch_susy_mod_server",
            side_effect=mutate_after_baseline,
        ):
            result = check_susy_mod_server(
                self.fixture.retained.suite,
                RUN_ID,
                server_template=self.fixture.retained.template,
            )

        self.assertEqual(1, len(self.fixture.calls))
        self.assertEqual("incomparable", result["verdict"])
        self.assertFalse(
            result["receipt"]["comparisons"]["inputs_unchanged_between_legs"]
        )

    def test_verdicts_cover_regression_improvement_and_incomparable(self) -> None:
        cases = (
            (
                "same",
                (_issue("ERROR", "example/A", "same"),),
                (_issue("ERROR", "example/A", "same", 1),),
                False,
                "no-observed-regression",
            ),
            (
                "new-error",
                (),
                (_issue("ERROR", "example/New", "candidate only"),),
                False,
                "candidate-regression",
            ),
            (
                "candidate-crash",
                (),
                (),
                True,
                "candidate-regression",
            ),
            (
                "improved",
                (_issue("FATAL", "example/Old", "baseline only"),),
                (),
                False,
                "candidate-improvement",
            ),
        )
        for label, baseline_issues, candidate_issues, crash, expected in cases:
            with self.subTest(label=label):
                root = Path(self.temporary.name) / label
                fixture = _PairFixture(root)
                fixture.results = {
                    "baseline": fixture.leg("baseline", issues=baseline_issues),
                    "candidate": fixture.leg(
                        "candidate",
                        issues=candidate_issues,
                        outcome="failed" if crash else "passed",
                        failure_kind="minecraft-crash-report" if crash else None,
                        crash=crash,
                    ),
                }
                with patch.object(
                    susy_mod_check,
                    "launch_susy_mod_server",
                    side_effect=fixture.launcher,
                ):
                    result = check_susy_mod_server(
                        fixture.retained.suite,
                        RUN_ID,
                        server_template=fixture.retained.template,
                        sample_limit=12,
                    )
                self.assertEqual(expected, result["verdict"])
                fixture.assert_inputs_unchanged(self)

    def test_multiset_diff_is_duplicate_aware_normalized_and_stably_sorted(self) -> None:
        self.fixture.results = {
            "baseline": self.fixture.leg(
                "baseline",
                issues=(
                    _issue("ERROR", "zeta/Loader", "duplicate Thing@ABCDEF", 1),
                    _issue("ERROR", "alpha/Loader", "removed", 2),
                    _issue("ERROR", "zeta/Loader", "duplicate Thing@123456", 3),
                ),
            ),
            "candidate": self.fixture.leg(
                "candidate",
                issues=(
                    _issue("ERROR", "zeta/Loader", "duplicate Thing@999999", 8),
                    _issue("FATAL", "beta/Loader", "new at /tmp/run-123", 9),
                ),
            ),
        }

        result = self.run_check()

        self.assertEqual("candidate-regression", result["verdict"])
        diff = result["receipt"]["diagnostics"]["diff"]
        self.assertEqual(1, diff["unchanged_count"])
        self.assertEqual(2, diff["removed_count"])
        self.assertEqual(1, diff["added_count"])
        for key in ("added", "removed", "unchanged"):
            signatures = [row["signature"] for row in diff[key]]
            self.assertEqual(sorted(signatures), signatures)
        duplicate = next(
            row for row in diff["removed"] if row["logger"] == "zeta/Loader"
        )
        self.assertEqual(1, duplicate["count"])
        self.fixture.assert_inputs_unchanged(self)

    def test_fml_biome_guess_type_order_is_not_a_false_runtime_delta(self) -> None:
        prefix = (
            "No types have been added to Biome tardis:farmlands, "
            "types have been assigned on a best-effort guess: "
        )
        self.fixture.results = {
            "baseline": self.fixture.leg(
                "baseline",
                issues=(_issue("WARN", "FML", prefix + "[PLAINS, HOT]"),),
            ),
            "candidate": self.fixture.leg(
                "candidate",
                issues=(_issue("WARN", "FML", prefix + "[HOT, PLAINS]"),),
            ),
        }

        result = self.run_check()

        self.assertEqual("no-observed-regression", result["verdict"])
        diff = result["receipt"]["diagnostics"]["diff"]
        self.assertEqual(0, diff["added_count"])
        self.assertEqual(0, diff["removed_count"])
        self.assertEqual(1, diff["unchanged_count"])

    def test_real_server_compositor_runs_distinct_baseline_and_candidate_fakes(self) -> None:
        fixture = self.fixture.retained
        java = fixture.root / "jdk/bin/java"
        server_fixture._write(java, _paired_fake_java(), executable=True)
        before_template = server_fixture._snapshot(fixture.template)
        before_result = (fixture.run_root / "result.json").read_bytes()
        before_candidate = fixture.candidate.read_bytes()

        with (
            patch.object(susy_mod_server, "ensure_java_runtime", return_value={}),
            patch.object(
                susy_mod_server,
                "_selected_java",
                return_value=(
                    java,
                    {
                        "runtime_id": "sha256:" + "7" * 64,
                        "probe": {"runtime_version": "25.0.4+7-LTS"},
                    },
                ),
            ),
            patch.object(
                susy_mod_server,
                "_compile_probe_agent",
                side_effect=_compile_fake_probe,
            ),
        ):
            result = check_susy_mod_server(
                fixture.suite,
                RUN_ID,
                server_template=fixture.template,
                server_java=java,
                memory_mib=1024,
                timeout_seconds=2.0,
                shutdown_timeout_seconds=1.0,
                poll_interval_seconds=0.01,
            )

        self.assertEqual("no-observed-regression", result["verdict"])
        legs = result["receipt"]["legs"]
        baseline_projection = server_fixture._uri_path(
            legs["baseline"]["projection"]["root_uri"]
        )
        candidate_projection = server_fixture._uri_path(
            legs["candidate"]["projection"]["root_uri"]
        )
        self.assertNotEqual(baseline_projection, candidate_projection)
        self.assertEqual(
            server_fixture.BASELINE,
            (
                baseline_projection
                / ".minecraft/mods/sample-1.0.jar"
            ).read_bytes(),
        )
        self.assertEqual(
            server_fixture.CANDIDATE,
            (
                candidate_projection
                / ".minecraft/mods/sample-1.0.jar"
            ).read_bytes(),
        )
        self.assertEqual(before_template, server_fixture._snapshot(fixture.template))
        self.assertEqual(before_result, (fixture.run_root / "result.json").read_bytes())
        self.assertEqual(before_candidate, fixture.candidate.read_bytes())

    def test_different_java_or_template_inputs_are_incomparable(self) -> None:
        for field in ("java", "template"):
            with self.subTest(field=field):
                root = Path(self.temporary.name) / ("mismatch-" + field)
                fixture = _PairFixture(root)
                fixture.results = {
                    "baseline": fixture.leg("baseline"),
                    "candidate": fixture.leg(
                        "candidate",
                        java_token="9" if field == "java" else "2",
                        template_token="8" if field == "template" else "1",
                    ),
                }
                with patch.object(
                    susy_mod_check,
                    "launch_susy_mod_server",
                    side_effect=fixture.launcher,
                ):
                    result = check_susy_mod_server(
                        fixture.retained.suite,
                        RUN_ID,
                        server_template=fixture.retained.template,
                    )
                self.assertEqual("incomparable", result["verdict"])
                self.assertFalse(
                    result["receipt"]["comparisons"][field + "_identity_equal"]
                )

    def test_experiment_profile_applicability_drift_is_incomparable(self) -> None:
        baseline = self.fixture.leg("baseline")
        candidate = self.fixture.leg("candidate")
        common = {
            "experiment_id": "susy-reccomplex-susycore-0112-flag",
            "patch_id": "workbench-pack:supersymmetry:susycore-0.1.112-reccomplex-flag-v1",
            "operation": "archive-entry-replacement",
            "spec_sha256": "1" * 64,
            "target_path": ".minecraft/mods/supersymmetry-v0.1.112.jar",
            "entry": "supersymmetry/mixins/reccomplex/StructureSpawnContextMixin.class",
            "applicability": {
                "profile_sha256": "2" * 64,
                "patch_id": "workbench-pack:supersymmetry:susycore-0.1.112-reccomplex-flag-v1",
                "pack_version": "0.1.16.12",
                "susycore": "0.1.112",
                "recurrent_complex": "1.4.8.6",
                "runtime_paths": [
                    "mods/supersymmetry-v0.1.112.jar",
                    "mods/RecurrentComplex-1.4.8.6.jar",
                ],
            },
        }
        baseline["receipt"]["projection"]["compatibility_experiments"] = [
            deepcopy(common)
        ]
        candidate_record = deepcopy(common)
        candidate_record["applicability"]["profile_sha256"] = "3" * 64
        candidate["receipt"]["projection"]["compatibility_experiments"] = [
            candidate_record
        ]
        for leg in (baseline, candidate):
            _seal(
                "workbench-susy-mod-server-launch:",
                leg["receipt"],
                "receipt_id",
            )
        self.fixture.results = {"baseline": baseline, "candidate": candidate}

        result = self.run_check()

        self.assertEqual("incomparable", result["verdict"])
        self.assertFalse(
            result["receipt"]["comparisons"][
                "compatibility_experiments_equal"
            ]
        )


if __name__ == "__main__":
    unittest.main()
