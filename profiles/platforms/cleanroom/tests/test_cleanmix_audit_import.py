from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[4]
TOOL_PATH = (
    ROOT / "profiles/platforms/cleanroom/tools/import_cleanmix_audit.py"
)
CRUCIBLE_SOURCE = ROOT / "modules/crucible/src"


def _load_tool():
    spec = importlib.util.spec_from_file_location("import_cleanmix_audit", TOOL_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {TOOL_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


AUDIT = """[09:22:58] [main/DEBUG] [CleanMix]: Preparing config mixins.example.json
[09:22:59] [main/INFO] [CleanMix/Audit]: APPLY mixins.example.json:MixinWorld from mod example_mod -> net.minecraft.world.World
[09:23:00] [Server thread/INFO] [CleanMix/Audit]: POSTPROCESS example.Accessor
[09:23:01] [Server thread/INFO] [CleanMix/Audit]: GENERATE example.Generated (by ArgsClassGenerator)
""".encode("utf-8")


class CleanMixAuditImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tool = _load_tool()

    def test_maps_apply_to_started_and_distinguishes_postprocess(self) -> None:
        rows = self.tool.import_audit_bytes(
            AUDIT,
            owner_bindings={"example_mod": "a" * 64},
        )
        self.assertEqual(
            [row["stage"] for row in rows],
            ["apply_started", "mixin_postprocess_seen", "generated"],
        )
        apply = rows[0]
        self.assertEqual(apply["subject"]["config"], "mixins.example.json")
        self.assertEqual(apply["subject"]["mixin"], "MixinWorld")
        self.assertEqual(
            apply["subject"]["target_class"], "net.minecraft.world.World"
        )
        self.assertEqual(apply["subject"]["artifact_sha256"], "a" * 64)
        self.assertEqual(apply["phase"], "UNKNOWN")
        self.assertIn("application entry only", " ".join(apply["evidence"]["limitations"]))

    def test_unknown_structured_audit_event_fails_closed(self) -> None:
        value = b"[00:00:00] [main/INFO] [CleanMix/Audit]: COMPLETED x\n"
        with self.assertRaisesRegex(self.tool.AuditImportError, "unknown"):
            self.tool.import_audit_bytes(value)

    def test_java_thread_name_may_contain_slashes(self) -> None:
        value = (
            b"[00:00:00] [worker/io/INFO] [CleanMix/Audit]: "
            b"POSTPROCESS example.Target\n"
        )
        rows = self.tool.import_audit_bytes(value)
        self.assertEqual("worker/io", rows[0]["thread"])

    def test_malformed_timestamped_line_fails_closed(self) -> None:
        with self.assertRaisesRegex(self.tool.AuditImportError, "malformed"):
            self.tool.import_audit_bytes(b"[broken] [CleanMix/Audit]: APPLY x\n")

    def test_owner_bindings_reject_ambiguous_artifact(self) -> None:
        value = {
            "format": self.tool.OWNER_BINDINGS_FORMAT,
            "bindings": [
                {"owner_label": "one", "artifact_sha256": "a" * 64},
                {"owner_label": "two", "artifact_sha256": "a" * 64},
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bindings.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(self.tool.AuditImportError, "ambiguously"):
                self.tool._load_owner_bindings(path)

    def test_cli_output_is_accepted_by_ledger_assembler(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audit = root / "cleanmix.log"
            observations = root / "observations.json"
            ledger = root / "ledger.json"
            bindings = root / "bindings.json"
            audit.write_bytes(AUDIT)
            bindings.write_text(
                json.dumps(
                    {
                        "format": self.tool.OWNER_BINDINGS_FORMAT,
                        "bindings": [
                            {
                                "owner_label": "example_mod",
                                "artifact_sha256": "a" * 64,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            imported = subprocess.run(
                [
                    sys.executable,
                    str(TOOL_PATH),
                    "--audit",
                    str(audit),
                    "--output",
                    str(observations),
                    "--session-id",
                    "session:test",
                    "--launch-id",
                    "launch:test",
                    "--profile-id",
                    "workbench-platform:cleanroom:test",
                    "--side",
                    "dedicated_server",
                    "--component-receipt-sha256",
                    "b" * 64,
                    "--launch-state",
                    "complete",
                    "--owner-bindings",
                    str(bindings),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(imported.returncode, 0, imported.stderr)
            self.assertEqual(imported.stdout.strip(), "3")
            assembled = subprocess.run(
                [
                    sys.executable,
                    str(
                        ROOT
                        / "modules/crucible/tools/assemble_mixin_transformation_ledger.py"
                    ),
                    "--input",
                    str(observations),
                    "--output",
                    str(ledger),
                ],
                cwd=ROOT,
                env={"PYTHONPATH": str(CRUCIBLE_SOURCE)},
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(assembled.returncode, 0, assembled.stderr)
            value = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertEqual(value["summary"]["apply_started_count"], 1)
            self.assertEqual(value["summary"]["final_class_defined_count"], 0)

    def test_real_retained_audit_when_available(self) -> None:
        path = (
            ROOT
            / ".workbench/evidence/worldgen-observatory/exact-runtime-v2/aa-1/server/logs/cleanmix.log"
        )
        if not path.is_file():
            self.skipTest("retained ignored CleanMix audit is unavailable")
        rows = self.tool.import_audit_bytes(path.read_bytes())
        apply_rows = [row for row in rows if row["stage"] == "apply_started"]
        self.assertEqual(len(apply_rows), 6)
        self.assertTrue(
            all(row["outcome"] == "observed" for row in apply_rows)
        )
        self.assertFalse(
            any(row["stage"] == "final_class_defined" for row in rows)
        )


if __name__ == "__main__":
    unittest.main()
