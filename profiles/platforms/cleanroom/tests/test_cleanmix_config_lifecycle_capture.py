from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[4]
FIXTURE = (
    ROOT
    / "profiles/platforms/cleanroom/candidates/0.6.8-alpha"
    / "worldgen-observatory-fixture"
)
AGENT_ROOT = FIXTURE / "src/cleanmixTraceAgent/java/dev/workbench/crucible/cleanmixtrace"
AGENT = AGENT_ROOT / "CleanMixDiscoveryTraceAgent.java"
RUNTIME = AGENT_ROOT / "CleanMixConfigLifecycleRuntime.java"
BUILD = FIXTURE / "build.gradle"
TOOL = ROOT / "profiles/platforms/cleanroom/tools/import_cleanmix_config_lifecycle.py"
SCHEMA = ROOT / "modules/crucible/schemas/mixin-config-lifecycle-receipt-v1.schema.json"


def _load_tool():
    spec = importlib.util.spec_from_file_location("cleanmix_config_import", TOOL)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {TOOL}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CleanMixConfigLifecycleCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tool = _load_tool()

    def test_agent_weaves_only_the_exact_hash_guarded_cleanmix_classes(self) -> None:
        source = AGENT.read_text()
        self.assertIn("CleanMixConfigLifecycleRuntime.start", source)
        self.assertIn("7d2ff2c3cb1780c2cb6273ab5426869104b6e8ac7db961033bc1156c11038d74", source)
        self.assertIn("426ea93ecb32d50f5ca7d4dfcbea9a20f964c6723d6cc89f1e802902942d1b4c", source)
        self.assertIn("expected \" + expected + \" lifecycle hooks", source)
        self.assertEqual(set(self.tool.EXPECTED_TARGETS), {
            "org.spongepowered.asm.mixin.Mixins",
            "org.spongepowered.asm.mixin.transformer.Config",
            "org.spongepowered.asm.mixin.transformer.MixinConfig",
            "org.spongepowered.asm.mixin.transformer.MixinProcessor",
        })

    def test_runtime_observes_native_lifecycle_without_provider_discovery(self) -> None:
        source = RUNTIME.read_text()
        for seam in (
            "configCreateStarted",
            "featureCheckReturned",
            "registrationReturned",
            "phaseEligibility",
            "stageStarted",
            "batchPromoted",
        ):
            self.assertIn(seam, source)
        self.assertNotIn("ServiceLoader", source)
        self.assertNotIn("getContextClassLoader", source)
        self.assertIn("selected.setAccessible(true)", source)

    def test_server_run_supports_the_config_lifecycle_capture(self) -> None:
        build = BUILD.read_text()
        self.assertIn("workbenchCleanMixConfigLifecycleOutput", build)
        self.assertIn("workbench.cleanmix.config_lifecycle.enabled", build)
        self.assertIn("workbenchCleanMixConfigLifecycleOutput must not already exist", build)
        self.assertEqual(build.count('jvmArgs "-javaagent:'), 1)

    def test_retained_exact_receipt_if_available(self) -> None:
        receipt_path = (
            ROOT / ".workbench/evidence/cleanmix-config-lifecycle-v1"
            / "exact-0.6.8-alpha/receipt.json"
        )
        if not receipt_path.is_file():
            self.skipTest("retained ignored P01 receipt is unavailable")
        receipt = json.loads(receipt_path.read_bytes())
        schema = json.loads(SCHEMA.read_bytes())
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(receipt)
        active = [
            row for row in receipt["configurations"]
            if row["terminal"]["outcome"] == "active"
        ]
        self.assertEqual(1, len(active))
        self.assertEqual(self.tool.EXPECTED_CONFIG, active[0]["requested_config"])
        self.assertEqual([False, False, True], [row["eligible"] for row in active[0]["phase_checks"]])
        self.assertEqual("complete", receipt["health"]["state"])


if __name__ == "__main__":
    unittest.main()
