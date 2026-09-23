from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[4]
FIXTURE = (
    ROOT
    / "profiles/platforms/cleanroom/fixtures/cleanmix-handler-regression"
)
JAVA = FIXTURE / "src/main/java"
RESOURCES = FIXTURE / "src/main/resources"


def _read(relative: str) -> str:
    return (FIXTURE / relative).read_text(encoding="utf-8")


class CleanMixHandlerRegressionFixtureTests(unittest.TestCase):
    def test_readme_keeps_execution_and_acceptance_distinct(self) -> None:
        readme = _read("README.md")
        self.assertIn("bounded regression oracle, not a general compatibility claim", readme)
        self.assertIn(
            "Native transformer entry is deliberately separate from delegated Mixin",
            readme,
        )
        self.assertIn("one cannot stand in for another", readme)

    def test_fixture_is_a_native_fml_mod_with_an_order_observer(self) -> None:
        manifest = _read("src/main/resources/META-INF/MANIFEST.MF")
        self.assertIn("ModType: CRL", manifest)
        self.assertIn(
            "FMLCorePlugin: "
            "dev.workbench.cleanmixhandler.bootstrap.HarnessLoadingPlugin",
            manifest,
        )
        self.assertIn("FMLCorePluginContainsFMLMod: true", manifest)
        self.assertIn(
            "MixinConfigs: mixins.workbench.cleanmix-handler-regression.json",
            manifest,
        )

        plugin = _read(
            "src/main/java/dev/workbench/cleanmixhandler/bootstrap/"
            "HarnessLoadingPlugin.java"
        )
        self.assertIn("implements IFMLLoadingPlugin", plugin)
        self.assertIn("TransformOrderProbe.class.getName()", plugin)
        self.assertIn("@IFMLLoadingPlugin.SortingIndex(Integer.MIN_VALUE)", plugin)

    def test_observer_records_actual_chain_entry_and_never_changes_bytes(self) -> None:
        observer = _read(
            "src/main/java/dev/workbench/cleanmixhandler/bootstrap/"
            "TransformOrderProbe.java"
        )
        self.assertIn("implements IClassTransformer", observer)
        self.assertIn(
            '"workbench.cleanmix.handler.observed_transform_order"', observer
        )
        self.assertIn("PARENT_TARGET.equals(transformedName)", observer)
        self.assertIn("CHILD_TARGET.equals(transformedName)", observer)
        self.assertIn('surrounded.contains("," + transformedName + ",")', observer)
        self.assertIn('TRACE_PROPERTY + ".entry_count"', observer)
        record_at = observer.index(
            "properties.setProperty(TRACE_PROPERTY, trace);"
        )
        emit_at = observer.index("ENTRY_PREFIX + ordinal")
        final_return_at = observer.rindex("return basicClass;")
        self.assertLess(record_at, emit_at)
        self.assertLess(emit_at, final_return_at)
        self.assertNotIn("new ClassReader", observer)
        self.assertNotIn("ClassWriter", observer)

    def test_child_first_result_is_hard_gated_by_observed_order(self) -> None:
        harness = _read(
            "src/main/java/dev/workbench/cleanmixhandler/HarnessMain.java"
        )
        self.assertIn("findClassDirectly(loader, CHILD_TARGET)", harness)
        self.assertIn("TransformOrderProbe.TRACE_PROPERTY", harness)
        self.assertIn(
            'field("observed_transform_order", observedTransformOrder)',
            harness,
        )
        self.assertIn(
            'bool("transform_order_oracle", transformOrderMatches)', harness
        )
        self.assertIn(
            'if (!"active".equals(observerHealth) || !transformOrderMatches)',
            harness,
        )
        self.assertIn("native transform order oracle failed", harness)

    def test_two_level_plain_override_fixture_is_preserved(self) -> None:
        parent_target = _read(
            "src/main/java/dev/workbench/cleanmixhandler/target/"
            "ParentTarget.java"
        )
        child_target = _read(
            "src/main/java/dev/workbench/cleanmixhandler/target/"
            "ChildTarget.java"
        )
        parent_mixin = _read(
            "src/main/java/dev/workbench/cleanmixhandler/mixin/"
            "ParentTargetMixin.java"
        )
        child_mixin = _read(
            "src/main/java/dev/workbench/cleanmixhandler/mixin/"
            "ChildTargetMixin.java"
        )
        self.assertIn("class ChildTarget extends ParentTarget", child_target)
        self.assertIn("@Inject(method = \"exercise\"", parent_mixin)
        self.assertIn(
            "class ChildTargetMixin extends ParentTargetMixin", child_mixin
        )
        self.assertIn("@Override", child_mixin)
        self.assertNotIn("@Inject", child_mixin)
        self.assertIn("originalBodyCount++", parent_target)

    def test_fixture_does_not_entangle_recurrent_complex(self) -> None:
        corpus = "\n".join(
            path.read_text(encoding="utf-8")
            for root in (JAVA, RESOURCES)
            for path in sorted(root.rglob("*"))
            if path.is_file()
        ).lower()
        self.assertNotIn("recurrent complex", corpus)
        self.assertNotIn("recurrent_complex", corpus)


if __name__ == "__main__":
    unittest.main()
