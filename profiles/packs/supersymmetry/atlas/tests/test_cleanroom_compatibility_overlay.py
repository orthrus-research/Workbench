from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[5]
PROBE_SOURCE = (
    ROOT
    / "profiles/packs/supersymmetry/atlas/probes/ultimate-runtime-graph-producer"
)
RUNTIME_GRAPH = ROOT / "profiles/packs/supersymmetry/atlas/runtime-graph"


def _json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"expected an object in {path}")
    return value


class CleanroomCompatibilityOverlayTests(unittest.TestCase):
    def test_current_producer_and_overlay_are_narrowly_bound(self) -> None:
        properties = dict(
            line.split("=", 1)
            for line in (PROBE_SOURCE / "gradle.properties")
            .read_text(encoding="utf-8")
            .splitlines()
            if line and not line.startswith("#")
        )
        self.assertEqual("0.16.0", properties["producer_version"])

        overlay_path = RUNTIME_GRAPH / "cleanroom-compatibility-overlays-v1.json"
        overlay_catalog = _json(overlay_path)
        overlays = overlay_catalog["overlays"]
        selected = [
            row
            for row in overlays
            if row.get("overlay_id")
            == "susy-reccomplex-modify-variable-name-v1"
        ]
        self.assertEqual(1, len(selected))
        self.assertEqual("provisional", selected[0]["profile_state"])
        self.assertEqual(
            "supersymmetry/mixins/reccomplex/StructureSpawnContextMixin.class",
            selected[0]["target"]["entry"],
        )

    def test_shutdown_shim_is_exactly_bound_and_capture_only(self) -> None:
        document = _json(
            RUNTIME_GRAPH / "cleanroom-runtime-compatibility-shims-v1.json"
        )
        self.assertEqual(
            "workbench-supersymmetry-cleanroom-runtime-compatibility-shims-v1",
            document["format"],
        )
        self.assertEqual(1, document["schema_version"])
        self.assertEqual(1, len(document["shims"]))
        shim = document["shims"][0]
        self.assertEqual("provisional", shim["profile_state"])
        self.assertFalse(
            shim["activation"]["ordinary_dimension_unloads_affected"]
        )
        self.assertEqual(
            "workbench.runtimeGraph.compatibility_only",
            shim["activation"]["compatibility_only_property_gate"],
        )
        self.assertFalse(shim["behavior"]["capture_state_affected"])
        self.assertEqual(
            "c2f71ad972320457cc37bacc999a46900964597df00ecc922f7649628560d823",
            shim["failure_binding"]["universal_mod_core"]["sha256"],
        )

    def test_shutdown_bridge_registration_and_cleanup_are_source_bound(self) -> None:
        source = PROBE_SOURCE / "src/main/java/dev/workbench/crucible/runtimegraph"
        mod = (source / "UltimateRuntimeGraphProducerMod.java").read_text(
            encoding="utf-8"
        )
        configuration = (source / "CaptureConfiguration.java").read_text(
            encoding="utf-8"
        )
        bridge = (
            source / "UniversalModCoreShutdownCompatibility.java"
        ).read_text(encoding="utf-8")
        self.assertIn('version = "0.16.0"', mod)
        self.assertLess(
            mod.index("if (configuration.isCompatibilityOnly())"),
            mod.index("if (!configuration.isEnabled())"),
        )
        self.assertIn(
            "MinecraftForge.EVENT_BUS.register(shutdownCompatibility)", mod
        )
        self.assertIn('booleanProperty("compatibility_only")', configuration)
        self.assertIn(
            "capture and compatibility-only modes are mutually exclusive",
            configuration,
        )
        self.assertIn("@SubscribeEvent(priority = EventPriority.HIGH)", bridge)
        self.assertIn("@SubscribeEvent(priority = EventPriority.LOWEST)", bridge)
        self.assertIn("new IdentityHashMap<World, Multimap", bridge)
        self.assertIn("tickets.get(world) == supplied", bridge)

    def test_client_normalization_is_narrow_and_source_bound(self) -> None:
        source = PROBE_SOURCE / (
            "src/main/java/dev/workbench/crucible/runtimegraph/client/"
            "ClientJeiCapture.java"
        )
        value = source.read_text(encoding="utf-8")
        self.assertIn("isExactGtBucketSlotSequence(capturedSlots, type)", value)
        self.assertIn(
            "ItemStack.class.equals(type.type.getIngredientClass())", value
        )
        self.assertIn(
            '"forge".equals(modId) && "bucketfilled".equals(resourceId)', value
        )
        self.assertIn('"minecraft:lava_bucket:lava;".equals(uniqueId)', value)
        self.assertIn("bdsandmCrateRecipe", value)
        self.assertIn(
            "slots(recording.outputSnapshot(), types, gtFluidVein, "
            "bdsandmCrateRecipe)",
            value,
        )
        self.assertIn(
            "slots(recording.inputSnapshot(), types, gtFluidVein, false)",
            value,
        )


if __name__ == "__main__":
    unittest.main()
