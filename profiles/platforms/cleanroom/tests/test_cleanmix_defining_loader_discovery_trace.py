from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import unittest

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[4]
FIXTURE = (
    ROOT
    / "profiles/platforms/cleanroom/candidates/0.6.8-alpha"
    / "worldgen-observatory-fixture"
)
AGENT = (
    FIXTURE
    / "src/cleanmixTraceAgent/java/dev/workbench/crucible/cleanmixtrace"
    / "CleanMixDiscoveryTraceAgent.java"
)
RUNTIME = AGENT.with_name("CleanMixDiscoveryTraceRuntime.java")
PATCHED = (
    FIXTURE
    / "src/cleanmixTracePatched/java/org/spongepowered/asm/service/MixinService.java"
)
BUILD = FIXTURE / "build.gradle"
TOOL = (
    ROOT / "profiles/platforms/cleanroom/tools"
    / "import_cleanmix_defining_loader_discovery_trace.py"
)
SCHEMA = (
    ROOT / "modules/crucible/schemas"
    / "mixin-defining-loader-discovery-trace-receipt-v2.schema.json"
)


def _load_tool():
    spec = importlib.util.spec_from_file_location("cleanmix_trace_import", TOOL)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {TOOL}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CleanMixDefiningLoaderDiscoveryTraceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tool = _load_tool()

    def test_agent_is_exact_hash_guarded_and_never_enumerates(self) -> None:
        source = AGENT.read_text()
        runtime = RUNTIME.read_text()
        self.assertIn(self.tool.EXPECTED_MIXIN_SERVICE_CLASS_SHA256, source)
        self.assertIn("return null;", source)
        self.assertIn("instrumentation.addTransformer", source)
        self.assertNotIn("ServiceLoader", source)
        self.assertNotIn("getContextClassLoader", source)
        self.assertNotIn("ServiceLoader", runtime)
        self.assertNotIn("getContextClassLoader", runtime)

    def test_patched_source_uses_the_original_defining_loader_iterators(self) -> None:
        source = PATCHED.read_text()
        self.assertIn(
            "ServiceLoader.<IMixinServiceBootstrap>load(\n"
            "                    IMixinServiceBootstrap.class, this.getClass().getClassLoader())",
            source,
        )
        self.assertIn(
            "ServiceLoader.<IMixinService>load(\n"
            "                    IMixinService.class, this.getClass().getClassLoader())",
            source,
        )
        self.assertNotIn("getContextClassLoader", source)
        self.assertIn("service.isValid()", source)
        self.assertIn("workbench$serviceSelected", source)
        self.assertIn("catch (Throwable ignored) { }", source)

    def test_agent_is_opt_in_on_the_server_java_process(self) -> None:
        build = BUILD.read_text()
        self.assertIn("workbenchCleanMixTraceOutput", build)
        self.assertIn("-javaagent:", build)
        self.assertIn("workbench.cleanmix.discovery_trace.enabled", build)
        self.assertIn("workbenchCleanMixTraceOutput must not already exist", build)

    def test_retained_exact_receipt_if_available(self) -> None:
        evidence = (
            ROOT / ".workbench/evidence/cleanmix-discovery-trace-v2"
            / "exact-0.6.8-alpha/receipt.json"
        )
        if not evidence.is_file():
            self.skipTest("retained ignored D01 receipt is unavailable")
        value = json.loads(evidence.read_bytes())
        schema = json.loads(SCHEMA.read_bytes())
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(value)
        self.assertEqual(value["health"]["end_health"], "healthy")
        self.assertEqual(value["selection"]["provider_class"], self.tool.EXPECTED_SERVICE)
        self.assertEqual(value["summary"]["bootstrap_attempt_count"], 1)
        self.assertEqual(value["summary"]["service_attempt_count"], 1)
        self.assertFalse(value["boundaries"]["thread_context_enumeration_performed"])


if __name__ == "__main__":
    unittest.main()
