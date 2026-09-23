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
RUNTIME = AGENT_ROOT / "CleanMixTransformerChainRuntime.java"
BUILD = FIXTURE / "build.gradle"
TOOL = ROOT / "profiles/platforms/cleanroom/tools/import_cleanmix_transformer_chain.py"
SCHEMA = ROOT / "modules/crucible/schemas/mixin-transformer-chain-epoch-receipt-v1.schema.json"


def _load_tool():
    spec = importlib.util.spec_from_file_location("cleanmix_chain_import", TOOL)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {TOOL}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CleanMixTransformerChainCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tool = _load_tool()

    def test_agent_weaves_exact_cleanroom_owned_chain_seams(self) -> None:
        source = AGENT.read_text()
        self.assertIn("CleanMixTransformerChainRuntime.start", source)
        self.assertIn("7c44263e004ecf0647a1789bd210c42b1a148647ce39768ed7f31395d0d23185", source)
        self.assertIn("6acbc3726fc8b5d1087ced2e21c63329666fc2fd48fda13fb0a249cb15f72c29", source)
        self.assertIn("getDelegatedLegacyTransformers", source)
        self.assertIn("onRefresh", source)

    def test_runtime_retains_both_chain_and_exclusion_layers(self) -> None:
        source = RUNTIME.read_text()
        for seam in (
            "liveTransformers",
            "delegationAccessStarted",
            "delegationAccessCompleted",
            "provider_exclusions",
            "foundation_transformer_exclusions",
            "foundation_code_source_uri",
        ):
            self.assertIn(seam, source)
        self.assertNotIn("registerTransformer(", source)
        self.assertNotIn("ServiceLoader", source)

    def test_server_run_supports_chain_capture_without_a_second_agent(self) -> None:
        build = BUILD.read_text()
        self.assertIn("workbenchCleanMixTransformerChainOutput", build)
        self.assertIn("workbench.cleanmix.transformer_chain.enabled", build)
        self.assertIn("workbenchCleanMixTransformerChainOutput must not already exist", build)
        self.assertEqual(build.count('jvmArgs "-javaagent:'), 1)

    def test_exact_profile_expectations_bind_cleanroom_and_foundation(self) -> None:
        self.assertEqual(
            "com.cleanroommc.cleanmix.service.FoundationTransformerProvider",
            self.tool.EXPECTED_PROVIDER,
        )
        self.assertEqual(64, len(self.tool.EXPECTED_FOUNDATION_SHA256))
        self.assertEqual(2, len(self.tool.EXPECTED_TARGETS))

    def test_retained_exact_receipt_if_available(self) -> None:
        evidence = (
            ROOT / ".workbench/evidence/cleanmix-transformer-chain-v1"
            / "exact-0.6.8-alpha/receipt.json"
        )
        if not evidence.is_file():
            self.skipTest("retained ignored T01 receipt is unavailable")
        value = json.loads(evidence.read_bytes())
        schema = json.loads(SCHEMA.read_bytes())
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(value)
        self.assertEqual("complete", value["health"]["state"])
        self.assertGreater(value["summary"]["epoch_count"], 0)
        self.assertTrue(value["boundaries"]["transformer_chain_epochs_proved"])


if __name__ == "__main__":
    unittest.main()
