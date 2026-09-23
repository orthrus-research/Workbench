from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[4]
for relative in ("api/src", "profiles/platforms/cleanroom/src"):
    sys.path.insert(0, str(ROOT / relative))

from workbench_api.events import EventNormalizer
from workbench_profile_cleanroom.events import classify


class CleanroomEventPolicyTests(unittest.TestCase):
    @staticmethod
    def normalizer():
        return EventNormalizer(classifiers=(classify,))

    def test_gradle_compiler_and_nonfatal_error_classification(self) -> None:
        normalizer = self.normalizer()
        compiler = normalizer.normalize(
            "src/main/java/dev/example/Mod.java:42:7: error: cannot find symbol",
            source="gradle",
            stream="stderr",
        )
        ordinary_error = normalizer.normalize(
            "[12:01:02] [Server thread/ERROR] [FML]: recoverable lookup failed",
            source="game",
            stream="stderr",
        )
        failed = normalizer.normalize(
            "BUILD FAILED in 2s", source="gradle", stream="stdout"
        )

        self.assertEqual(
            (compiler.kind, compiler.subsystem, compiler.severity),
            ("compiler_diagnostic", "compiler", "error"),
        )
        self.assertFalse(compiler.outcome_failure)
        self.assertEqual(ordinary_error.parse_provenance, "parsed")
        self.assertEqual(ordinary_error.message, "recoverable lookup failed")
        self.assertEqual(ordinary_error.severity, "error")
        self.assertEqual(ordinary_error.subsystem, "cleanroom-fml")
        self.assertFalse(ordinary_error.outcome_failure)
        self.assertEqual((failed.kind, failed.subsystem), ("build", "gradle"))
        self.assertTrue(failed.outcome_failure)


    def test_domain_classifiers_cover_cleanmix_mixin_groovy_registry_worldgen(self) -> None:
        normalizer = self.normalizer()
        cases = {
            "cleanmix": (
                "[12:00:00] [main/INFO] [CleanMix]: applying compatibility handler",
                "mixin",
                "cleanmix",
            ),
            "mixin": (
                "org.spongepowered.asm.mixin.throwables.MixinApplyError: failed",
                "mixin",
                "mixin",
            ),
            "groovy": (
                "GroovyScript MultipleCompilationErrorsException startup failed",
                "groovy",
                "groovy",
            ),
            "registry": (
                "RegistryEvent.Register missing mappings for example:block",
                "registry",
                "registry",
            ),
            "worldgen": (
                "PopulateChunkEvent cascading worldgen detected",
                "worldgen",
                "worldgen",
            ),
        }
        for name, (line, expected_kind, expected_subsystem) in cases.items():
            with self.subTest(name=name):
                event = normalizer.normalize(line, source="game", stream="stderr")
                self.assertEqual(event.kind, expected_kind)
                self.assertEqual(event.subsystem, expected_subsystem)
                self.assertIn(event.parse_provenance, {"parsed", "heuristic"})


    def test_exception_crash_and_workbench_outcome_markers_are_distinct(self) -> None:
        normalizer = self.normalizer()
        caught = normalizer.normalize(
            "java.lang.IllegalStateException: caught and retried",
            source="game",
            stream="stderr",
        )
        crash = normalizer.normalize(
            "---- Minecraft Crash Report ----", source="game", stream="stderr"
        )
        required = normalizer.normalize(
            "[workbench] required check validation failed",
            source="workbench",
            stream="system",
        )
        stage = normalizer.normalize(
            "WORKBENCH_STAGE launch started",
            source="workbench",
            stream="system",
        )
        synthetic = normalizer.normalize_stage("process-exit", "completed")

        self.assertEqual(caught.kind, "exception")
        self.assertFalse(caught.outcome_failure)
        self.assertEqual(crash.severity, "fatal")
        self.assertTrue(crash.outcome_failure)
        self.assertTrue(required.outcome_failure)
        self.assertEqual(required.kind, "stage")
        self.assertFalse(stage.outcome_failure)
        self.assertEqual((stage.kind, stage.severity), ("stage", "info"))
        self.assertEqual((synthetic.kind, synthetic.subsystem), ("stage", "workbench"))
        self.assertEqual(
            (synthetic.raw_locator.artifact, synthetic.raw_locator.byte_end),
            (None, 0),
        )
