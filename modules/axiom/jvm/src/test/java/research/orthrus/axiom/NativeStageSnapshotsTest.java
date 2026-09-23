package research.orthrus.axiom;

import org.junit.jupiter.api.Test;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;

class NativeStageSnapshotsTest {
    @Test void consoleExpansionRetainsMoreThanFormerTwoMiBAllocation() {
        var console=Map.<String,Object>of("head","native µ 😀\n".repeat(200000),"tail","final native cause", "complete",true);
        var value=Map.<String,Object>of("nativeConsole",console);
        assertTrue(Json.write(value).length()>2_097_152);
        assertEquals(value,NativeStageSnapshots.expand(NativeStageSnapshots.compact(value,List.of()),List.of()));
    }

    @Test void selectedNativeValuesShareTheProductionEnvelopeAndRoundTripNullsAndTypedScalars() {
        var material = new LinkedHashMap<String,Object>();
        material.put("name", "gregtech:iron"); material.put("formula", null);
        material.put("propertyValues", Map.of("TOOL", Map.of("attackSpeed", Map.of("type", "float32", "value", "NaN", "rawBits", "7fc00000"))));
        var selected = new LinkedHashMap<String,Object>();
        selected.put("materials", List.of(material)); selected.put("lookups", List.of(Map.of("requested", "gregtech:iron", "exactIdentity", true)));
        selected.put("missingMaterials", List.of("gregtech:missing")); selected.put("vocabulary", Map.of("properties", List.of("TOOL")));
        selected.put("prefixItems", Map.of("phase", "COMPLETE", "forms", List.of(Map.of("eligible", false))));
        selected.put("materialBlocks", Map.of("phase", "COMPLETE", "forms", List.of()));
        selected.put("materialOres", Map.of("phase", "COMPLETE", "forms", List.of(Map.of("generated", List.of(Map.of("ordinaryDrop", Map.of("empty", false)))))));
        selected.put("deferredWork", Map.of("phase", "FROZEN", "prefixProcessing", List.of()));
        selected.put("selectedObservations", Map.of("status", "observed"));
        var execution = new LinkedHashMap<>(selected); execution.put("nativeInitialization", selected); execution.put("diagnostics", List.of());
        String retained = Json.write(execution);
        var compact = NativeStageSnapshots.compactExecution(execution);
        var nativeState = Json.object(compact.get("nativeInitialization"));
        for (String name : selected.keySet()) assertFalse(nativeState.containsKey(name), name);
        assertEquals(retained, Json.write(NativeStageSnapshots.expandExecution(Json.object(Json.parse(Json.write(compact))))));
        assertEquals(retained, Json.write(execution));
        var missing = new LinkedHashMap<>(compact); missing.remove("materialOres");
        assertThrows(IllegalArgumentException.class, () -> NativeStageSnapshots.expandExecution(missing));
    }
    @Test void compressedConsolePreservesTextDigestAndExplicitOverflowWithoutChangingTheSource() {
        for (boolean complete : List.of(true, false)) {
            var console = Map.<String,Object>of("head", "original warning µ 😀\n\t".repeat(5000),
                    "tail", "final compiler cause\n", "complete", complete, "sha256", "a5".repeat(32),
                    "retainedBytes", 125021, "totalBytes", complete ? 125021 : 300000);
            var original = Map.<String,Object>of("nativeConsole", console);
            String retained = Json.write(original);
            var packed = NativeStageSnapshots.compact(original, List.of());
            var encoded = Json.object(packed.get("nativeConsole"));
            assertFalse(encoded.containsKey("head")); assertFalse(encoded.containsKey("tail"));
            assertEquals(complete, encoded.get("complete")); assertEquals(console.get("sha256"), encoded.get("sha256"));
            assertTrue(Json.write(packed).length() < retained.length() / 10);
            assertEquals(retained, Json.write(NativeStageSnapshots.expand(Json.object(Json.parse(Json.write(packed))), List.of())));
            assertEquals(retained, Json.write(original));
        }
    }
    @Test void productionCollectionsAndAdditionalFailureDiagnosticsRoundTripWithoutDuplication() {
        var warning = Map.of("severity", "warning", "message", "original warning");
        var failure = Map.of("channel", "native-bootstrap", "severity", "error", "causes", List.of("native failure"));
        var effects = Map.of("materials", Map.of("entries", Map.of("gregtech:iron", "a5".repeat(32))));
        var custom = Map.of("items", List.of(Map.of("registryName", "susy:meta_item")));
        var nativeState = Map.<String,Object>of("nativeDiagnostics", List.of(warning), "groovyDiagnostics", List.of(),
                "registrationEffects", effects, "customMetaItems", custom, "failure", List.of("native failure"));
        var execution = Map.<String,Object>of("nativeInitialization", nativeState, "diagnostics", List.of(warning, failure),
                "registrationEffects", effects, "customMetaItems", custom);
        String retained = Json.write(execution);
        var compact = NativeStageSnapshots.compactExecution(execution);
        assertFalse(Json.object(compact.get("nativeInitialization")).containsKey("registrationEffects"));
        assertFalse(Json.object(compact.get("nativeInitialization")).containsKey("customMetaItems"));
        assertEquals(retained, Json.write(NativeStageSnapshots.expandExecution(Json.object(Json.parse(Json.write(compact))))));
        assertEquals(retained, Json.write(execution));
        var broken = new LinkedHashMap<>(compact); broken.remove("registrationEffects");
        assertThrows(IllegalArgumentException.class, () -> NativeStageSnapshots.expandExecution(broken));
    }
    @Test void fingerprintEncodingPreservesEveryDigestAndMembershipWithoutChangingTheSource() {
        Map<String,Object> catalog = Map.of("status", "observed", "entries", Map.of("gregtech:iron", "a5".repeat(32), "water", "00".repeat(32)));
        var original = Map.<String,Object>of("registrationEffects", Map.of("materials", catalog, "fluids", catalog,
                "prefixItems", catalog, "materialBlocks", catalog, "oreBlocks", catalog));
        String retained = Json.write(original); var compact = NativeStageSnapshots.compact(original, List.of());
        var entries = Json.object(Json.object(Json.object(compact.get("registrationEffects")).get("materials")).get("entries"));
        assertEquals(43, Json.string(entries.get("gregtech:iron")).length());
        assertEquals(retained, Json.write(NativeStageSnapshots.expand(compact, List.of())));
        assertEquals(retained, Json.write(original));
    }
    @Test void roundTripPreservesEarlierFailuresLocationsNullsAndNestedStages() {
        var warning = Map.of("severity", "warning", "message", "native µ\nmessage 😀",
                "location", Map.of("path", "groovy/preInit/Edit.groovy", "line", 4));
        var failure = Map.of("severity", "error", "message", "later callback failed");
        var early = new LinkedHashMap<String,Object>();
        early.put("nativeDiagnostics", List.of(warning)); early.put("phase", "INIT"); early.put("owner", null);
        var selection = new LinkedHashMap<>(early); selection.put("earlyStage", early); selection.put("phase", "LOADING");
        var original = new LinkedHashMap<>(selection);
        original.put("selectionStage", selection); original.put("phase", "INITIALIZATION");
        original.put("nativeDiagnostics", List.of(warning, failure)); original.put("groovyDiagnostics", List.of());
        original.put("groovyBoundary", Map.of("phase", "PREINITIALIZATION", "nativeDiagnostics", List.of(warning)));
        List<Object> diagnostics = List.of(warning, failure);
        String retained = Json.write(original);
        var encoded = NativeStageSnapshots.compact(original, diagnostics);
        var restored = NativeStageSnapshots.expand(Json.object(Json.parse(Json.write(encoded))),
                Json.array(Json.parse(Json.write(diagnostics))));
        assertEquals(retained, Json.write(restored)); assertEquals(retained, Json.write(original));
        assertFalse(encoded.containsKey("nativeDiagnostics"));
        assertEquals(Json.write(List.of(warning)), Json.write(Json.object(restored.get("groovyBoundary")).get("nativeDiagnostics")));
    }

    @Test void startupFailureWithoutDiagnosticFieldsRetainsTheExactShape() {
        Map<String,Object> original = Map.of("stage", "worker-startup", "failure", List.of(Map.of("message", "cannot start")));
        assertEquals(original, NativeStageSnapshots.expand(NativeStageSnapshots.compact(original, List.of()), List.of()));
    }

    @Test void repeatedStageEvidenceGetsSmallerWithoutDiscardingRows() {
        var rows = new ArrayList<Object>();
        for (int i = 0; i < 100; i++) rows.add(Map.of("message", "original diagnostic " + i, "severity", "warning"));
        var early = Map.<String,Object>of("nativeDiagnostics", rows, "phase", "INIT");
        var original = Map.<String,Object>of("nativeDiagnostics", rows, "groovyDiagnostics", List.of(),
                "earlyStage", early, "selectionStage", Map.of("earlyStage", early, "nativeDiagnostics", rows), "phase", "LOADING");
        var compact = NativeStageSnapshots.compact(original, rows);
        assertTrue(Json.write(compact).length() < Json.write(original).length() / 4);
        assertEquals(original, NativeStageSnapshots.expand(compact, rows));
    }
}
