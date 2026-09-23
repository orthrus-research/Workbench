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
AGENT_ROOT = (
    FIXTURE
    / "src/cleanmixTraceAgent/java/dev/workbench/crucible/cleanmixtrace"
)
AGENT = AGENT_ROOT / "CleanMixDiscoveryTraceAgent.java"
RUNTIME = AGENT_ROOT / "CleanMixServiceComponentRuntime.java"
PATCHED = (
    FIXTURE
    / "src/cleanmixTracePatched/java/org/spongepowered/asm/service/MixinService.java"
)
BUILD = FIXTURE / "build.gradle"
TOOL = (
    ROOT / "profiles/platforms/cleanroom/tools"
    / "import_cleanmix_service_components.py"
)
SCHEMA = (
    ROOT / "modules/crucible/schemas"
    / "mixin-selected-service-components-receipt-v1.schema.json"
)


def _load_tool():
    spec = importlib.util.spec_from_file_location("cleanmix_components_import", TOOL)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {TOOL}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CleanMixServiceComponentsCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tool = _load_tool()

    def test_component_observer_reuses_the_exact_guarded_substitution(self) -> None:
        agent = AGENT.read_text()
        runtime = RUNTIME.read_text()
        self.assertIn("CleanMixServiceComponentRuntime.start", agent)
        self.assertIn("CleanMixServiceComponentRuntime.transformApplied", agent)
        self.assertIn("EXPECTED_TARGET_SHA256", agent)
        self.assertNotIn("ServiceLoader", runtime)
        self.assertNotIn("getContextClassLoader", runtime)

    def test_patched_source_observes_only_the_selected_service_and_normal_logger(self) -> None:
        source = PATCHED.read_text()
        self.assertIn("CleanMixServiceComponentRuntime.serviceSelected(provider)", source)
        self.assertIn("this.service.getLogger(\"CleanMix\")", source)
        self.assertIn("CleanMixServiceComponentRuntime.loggerObserved(logger)", source)
        self.assertNotIn("getAuditTrail", source)

    def test_audit_capture_reads_the_existing_field_without_lazy_access(self) -> None:
        runtime = RUNTIME.read_text()
        self.assertIn('fieldValue(selectedService, "auditTrail")', runtime)
        self.assertNotIn('invoke(selectedService, "getAuditTrail")', runtime)
        self.assertIn("normal runtime lifecycle", runtime)

    def test_server_run_enables_one_agent_for_either_or_both_outputs(self) -> None:
        build = BUILD.read_text()
        self.assertIn("workbenchCleanMixComponentOutput", build)
        self.assertIn("cleanmixTraceOutput.isPresent() || cleanmixComponentOutput.isPresent()", build)
        self.assertEqual(build.count('jvmArgs "-javaagent:'), 1)
        self.assertIn("workbench.cleanmix.service_components.enabled", build)
        self.assertIn("workbenchCleanMixComponentOutput must not already exist", build)

    def test_exact_profile_expectations_cover_every_stable_role(self) -> None:
        self.assertEqual(
            {
                "audit_trail",
                "bytecode_provider",
                "class_provider",
                "class_tracker",
                "logger",
                "service",
                "service_classloader",
                "transformer_provider",
            },
            set(self.tool.EXPECTED_COMPONENT_CLASSES),
        )

    def test_retained_exact_receipt_if_available(self) -> None:
        evidence = (
            ROOT / ".workbench/evidence/cleanmix-service-components-v1"
            / "exact-0.6.8-alpha/receipt.json"
        )
        if not evidence.is_file():
            self.skipTest("retained ignored C01 receipt is unavailable")
        value = json.loads(evidence.read_bytes())
        schema = json.loads(SCHEMA.read_bytes())
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(value)
        self.assertEqual(value["health"]["end_health"], "healthy")
        self.assertEqual(value["summary"]["component_count"], 8)
        self.assertEqual(
            {
                row["role"]: row["implementation_class"]
                for row in value["components"]
            },
            self.tool.EXPECTED_COMPONENT_CLASSES,
        )


if __name__ == "__main__":
    unittest.main()
