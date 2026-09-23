from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[4]
FIXTURE = (
    ROOT / "profiles/platforms/cleanroom/candidates/0.6.8-alpha"
    / "worldgen-observatory-fixture"
)
AGENT_ROOT = FIXTURE / "src/cleanmixTraceAgent/java/dev/workbench/crucible/cleanmixtrace"
AGENT = AGENT_ROOT / "CleanMixDiscoveryTraceAgent.java"
RUNTIME = AGENT_ROOT / "FoundationFinalDefinitionRuntime.java"
BUILD = FIXTURE / "build.gradle"
TOOL = ROOT / "profiles/platforms/cleanroom/tools/import_foundation_final_definitions.py"
SCHEMA = ROOT / "modules/crucible/schemas/mixin-final-class-definition-receipt-v1.schema.json"


def _load_tool():
    spec = importlib.util.spec_from_file_location("foundation_definition_import", TOOL)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {TOOL}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FoundationFinalDefinitionCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tool = _load_tool()

    def test_agent_weaves_exact_foundation_findclass_seam(self) -> None:
        source = AGENT.read_text()
        self.assertIn("FoundationFinalDefinitionRuntime.start", source)
        self.assertIn("41cc84afadb4155ea5d15f5132f08b23327e6bf51edfb5615d1a6f5c6eb7706f", source)
        self.assertIn("findClass", source)

    def test_runtime_requires_successful_return_and_exact_dump_entry(self) -> None:
        source = RUNTIME.read_text()
        for seam in (
            "findStarted",
            "findReturned",
            "cached_before",
            "dump_relative_path",
            "final_bytecode_sha256",
        ):
            self.assertIn(seam, source)
        self.assertNotIn("defineClass(", source)

    def test_server_run_forces_foundation_dump_for_definition_capture(self) -> None:
        build = BUILD.read_text()
        self.assertIn("workbenchCleanMixFinalDefinitionOutput", build)
        self.assertIn("workbenchCleanMixFinalDefinitionTargets", build)
        self.assertIn("systemProperty 'foundation.dump', 'true'", build)
        self.assertEqual(build.count('jvmArgs "-javaagent:'), 1)

    def test_exact_profile_expectations_bind_foundation(self) -> None:
        self.assertEqual(64, len(self.tool.EXPECTED_FOUNDATION_SHA256))
        self.assertEqual(64, len(self.tool.EXPECTED_ACTUAL_CLASS_LOADER_SHA256))

    def test_retained_exact_receipt_if_available(self) -> None:
        evidence = (
            ROOT / ".workbench/evidence/foundation-final-definition-v1"
            / "exact-0.6.8-alpha/receipt.json"
        )
        if not evidence.is_file():
            self.skipTest("retained ignored F01 receipt is unavailable")
        value = json.loads(evidence.read_bytes())
        schema = json.loads(SCHEMA.read_bytes())
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(value)
        self.assertEqual("complete", value["health"]["state"])
        self.assertGreater(value["summary"]["defined_target_count"], 0)
        self.assertTrue(value["boundaries"]["final_class_bytes_proved"])


if __name__ == "__main__":
    unittest.main()
