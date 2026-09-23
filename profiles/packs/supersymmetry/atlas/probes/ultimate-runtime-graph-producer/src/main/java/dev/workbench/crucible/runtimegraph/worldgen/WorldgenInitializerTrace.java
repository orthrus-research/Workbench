package dev.workbench.crucible.runtimegraph.worldgen;

import com.google.gson.JsonObject;

import dev.workbench.crucible.runtimegraph.CanonicalJson;

import java.util.ArrayList;
import java.util.Collections;
import java.util.IdentityHashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Retains the accepted GT worldgen initializer JSON that final runtime predicates erase.
 *
 * <p>The trace is opened and sealed around GT's natural reload method. It therefore rejects
 * stale definitions from an earlier epoch and proves every final registry object came from one
 * accepted initializer in the sealed epoch.</p>
 */
public final class WorldgenInitializerTrace {
    private static final WorldgenInitializerTrace GLOBAL = new WorldgenInitializerTrace();

    private Phase phase = Phase.NOT_STARTED;
    private Thread epochThread;
    private Attempt active;
    private final List<Attempt> attempts = new ArrayList<Attempt>();
    private Snapshot sealed;

    public static WorldgenInitializerTrace global() { return GLOBAL; }

    public synchronized void beginEpoch(Object receiver, Object expectedReceiver) {
        if (phase == Phase.OPEN) fail("nested GT worldgen initialization epoch");
        if (receiver == null || receiver != expectedReceiver) {
            fail("GT worldgen epoch receiver is not the registry singleton");
        }
        phase = Phase.OPEN;
        epochThread = Thread.currentThread();
        active = null;
        attempts.clear();
        sealed = null;
    }

    public synchronized void initializerEntered(
        Scope scope,
        Object receiver,
        JsonObject initializer
    ) {
        requireOpen("initializer entry");
        if (scope == null || receiver == null || initializer == null) {
            fail("GT worldgen initializer entry contains null");
        }
        if (active != null) fail("nested GT worldgen initializer");
        active = new Attempt(
            scope,
            receiver,
            initializer.deepCopy(),
            attempts.size()
        );
        attempts.add(active);
    }

    public synchronized void initializerReturned(
        Scope scope,
        Object receiver,
        boolean accepted
    ) {
        requireOpen("initializer return");
        if (active == null || active.scope != scope || active.identity != receiver) {
            fail("GT worldgen initializer return does not match entry");
        }
        active.accepted = Boolean.valueOf(accepted);
        active = null;
    }

    public synchronized void sealEpoch(
        Object receiver,
        Object expectedReceiver,
        List<DefinitionRef> ores,
        List<DefinitionRef> fluids,
        Map<Integer, String> dimensions
    ) {
        requireOpen("epoch return");
        if (receiver == null || receiver != expectedReceiver) {
            fail("GT worldgen sealed receiver is not the registry singleton");
        }
        if (active != null) fail("GT worldgen epoch returned with an active initializer");
        List<DefinitionRecord> oreRows = correlate(Scope.ORE, ores);
        List<DefinitionRecord> fluidRows = correlate(Scope.BEDROCK_FLUID, fluids);
        for (Attempt attempt : attempts) {
            if (attempt.accepted == null) fail("GT worldgen initializer lacks a return result");
            if (attempt.accepted.booleanValue() && !attempt.registered) {
                fail("accepted GT worldgen initializer is absent from the final registry");
            }
        }
        List<DimensionRecord> dimensionRows = dimensions(dimensions);
        sealed = new Snapshot(oreRows, fluidRows, dimensionRows);
        phase = Phase.SEALED;
        epochThread = null;
    }

    public synchronized Snapshot requireFinalSnapshot(
        List<DefinitionRef> ores,
        List<DefinitionRef> fluids,
        Map<Integer, String> dimensions
    ) {
        if (phase != Phase.SEALED || sealed == null) {
            fail("GT worldgen initializer trace is not sealed");
        }
        verify(sealed.oreDefinitions, ores, Scope.ORE);
        verify(sealed.fluidDefinitions, fluids, Scope.BEDROCK_FLUID);
        List<DimensionRecord> currentDimensions = dimensions(dimensions);
        if (!sealed.dimensions.equals(currentDimensions)) {
            fail("GT named dimensions changed after the sealed worldgen epoch");
        }
        return sealed.copy();
    }

    private List<DefinitionRecord> correlate(Scope scope, List<DefinitionRef> definitions) {
        if (definitions == null) fail("final GT worldgen definition list is null");
        IdentityHashMap<Object, Boolean> identities = new IdentityHashMap<Object, Boolean>();
        Map<String, Integer> occurrences = new LinkedHashMap<String, Integer>();
        List<DefinitionRecord> result = new ArrayList<DefinitionRecord>();
        for (int ordinal = 0; ordinal < definitions.size(); ordinal++) {
            DefinitionRef definition = definitions.get(ordinal);
            if (definition == null || definition.identity == null || !validName(definition.name)) {
                fail("final GT worldgen definition identity is invalid");
            }
            if (identities.put(definition.identity, Boolean.TRUE) != null) {
                fail("same GT worldgen object occurs twice in final registry");
            }
            Attempt matched = null;
            for (Attempt attempt : attempts) {
                if (attempt.scope == scope && attempt.identity == definition.identity) {
                    if (matched != null) fail("GT worldgen object has multiple initializers");
                    matched = attempt;
                }
            }
            if (matched == null || !Boolean.TRUE.equals(matched.accepted)) {
                fail("final GT worldgen object lacks an accepted initializer");
            }
            matched.registered = true;
            int occurrence = occurrences.containsKey(definition.name)
                ? occurrences.get(definition.name).intValue()
                : 0;
            occurrences.put(definition.name, Integer.valueOf(occurrence + 1));
            result.add(new DefinitionRecord(
                scope,
                ordinal,
                occurrence,
                definition.identity,
                definition.name,
                matched.initializer,
                CanonicalJson.sha256(matched.initializer),
                matched.attemptOrdinal
            ));
        }
        return Collections.unmodifiableList(result);
    }

    private static List<DimensionRecord> dimensions(Map<Integer, String> values) {
        if (values == null) fail("GT named dimensions are null");
        List<Integer> ids = new ArrayList<Integer>(values.keySet());
        Collections.sort(ids);
        List<DimensionRecord> result = new ArrayList<DimensionRecord>();
        for (Integer id : ids) {
            String name = values.get(id);
            if (id == null || !validName(name)) fail("GT named dimension row is invalid");
            result.add(new DimensionRecord(id.intValue(), name));
        }
        return Collections.unmodifiableList(result);
    }

    private static void verify(
        List<DefinitionRecord> retained,
        List<DefinitionRef> current,
        Scope scope
    ) {
        if (current == null || retained.size() != current.size()) {
            fail("GT worldgen final-list size changed for " + scope.id);
        }
        for (int index = 0; index < retained.size(); index++) {
            DefinitionRecord left = retained.get(index);
            DefinitionRef right = current.get(index);
            if (right == null || left.identity != right.identity || !left.name.equals(right.name)) {
                fail("GT worldgen final-list identity changed for " + scope.id);
            }
        }
    }

    private void requireOpen(String operation) {
        if (phase != Phase.OPEN || Thread.currentThread() != epochThread) {
            fail("GT worldgen " + operation + " occurred outside its initialization epoch");
        }
    }

    private static boolean validName(String value) {
        return value != null && !value.isEmpty()
            && value.indexOf('\r') < 0 && value.indexOf('\n') < 0 && value.indexOf('\0') < 0;
    }

    private static void fail(String message) { throw new IllegalStateException(message); }

    public enum Scope {
        ORE("ore"),
        BEDROCK_FLUID("bedrock-fluid");

        public final String id;
        Scope(String id) { this.id = id; }
    }

    public static final class DefinitionRef {
        public final Object identity;
        public final String name;

        public DefinitionRef(Object identity, String name) {
            this.identity = identity;
            this.name = name;
        }
    }

    public static final class DefinitionRecord {
        public final Scope scope;
        public final int registryOrdinal;
        public final int nameOccurrenceOrdinal;
        public final Object identity;
        public final String name;
        public final JsonObject initializer;
        public final String initializerSha256;
        public final int initializerAttemptOrdinal;

        private DefinitionRecord(
            Scope scope,
            int registryOrdinal,
            int nameOccurrenceOrdinal,
            Object identity,
            String name,
            JsonObject initializer,
            String initializerSha256,
            int initializerAttemptOrdinal
        ) {
            this.scope = scope;
            this.registryOrdinal = registryOrdinal;
            this.nameOccurrenceOrdinal = nameOccurrenceOrdinal;
            this.identity = identity;
            this.name = name;
            this.initializer = initializer.deepCopy();
            this.initializerSha256 = initializerSha256;
            this.initializerAttemptOrdinal = initializerAttemptOrdinal;
        }

        private DefinitionRecord copy() {
            return new DefinitionRecord(
                scope, registryOrdinal, nameOccurrenceOrdinal, identity, name,
                initializer, initializerSha256, initializerAttemptOrdinal
            );
        }
    }

    public static final class DimensionRecord {
        public final int dimensionId;
        public final String name;

        private DimensionRecord(int dimensionId, String name) {
            this.dimensionId = dimensionId;
            this.name = name;
        }

        @Override public boolean equals(Object value) {
            if (!(value instanceof DimensionRecord)) return false;
            DimensionRecord other = (DimensionRecord) value;
            return dimensionId == other.dimensionId && name.equals(other.name);
        }

        @Override public int hashCode() { return 31 * dimensionId + name.hashCode(); }
    }

    public static final class Snapshot {
        public final List<DefinitionRecord> oreDefinitions;
        public final List<DefinitionRecord> fluidDefinitions;
        public final List<DimensionRecord> dimensions;

        private Snapshot(
            List<DefinitionRecord> oreDefinitions,
            List<DefinitionRecord> fluidDefinitions,
            List<DimensionRecord> dimensions
        ) {
            this.oreDefinitions = oreDefinitions;
            this.fluidDefinitions = fluidDefinitions;
            this.dimensions = dimensions;
        }

        private Snapshot copy() {
            List<DefinitionRecord> ores = new ArrayList<DefinitionRecord>();
            for (DefinitionRecord row : oreDefinitions) ores.add(row.copy());
            List<DefinitionRecord> fluids = new ArrayList<DefinitionRecord>();
            for (DefinitionRecord row : fluidDefinitions) fluids.add(row.copy());
            return new Snapshot(
                Collections.unmodifiableList(ores),
                Collections.unmodifiableList(fluids),
                dimensions
            );
        }
    }

    private enum Phase { NOT_STARTED, OPEN, SEALED }

    private static final class Attempt {
        private final Scope scope;
        private final Object identity;
        private final JsonObject initializer;
        private final int attemptOrdinal;
        private Boolean accepted;
        private boolean registered;

        private Attempt(Scope scope, Object identity, JsonObject initializer, int attemptOrdinal) {
            this.scope = scope;
            this.identity = identity;
            this.initializer = initializer;
            this.attemptOrdinal = attemptOrdinal;
        }
    }
}
