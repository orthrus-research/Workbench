package research.orthrus.axiom.materialhost;

import com.google.gson.Gson;
import net.minecraft.util.text.translation.LanguageMap;
import net.minecraftforge.fml.common.*;
import net.minecraftforge.fml.common.event.FMLLoadEvent;
import java.lang.reflect.Field;
import java.io.OutputStream;
import java.io.OutputStreamWriter;
import java.nio.charset.StandardCharsets;
import java.security.DigestOutputStream;
import java.security.MessageDigest;
import java.util.*;

/** Original candidate bus activation, not a complete-composition loaded-mod oracle. */
public final class NativeAddonActivation {
    private NativeAddonActivation() {}

    public static Map<String,Object> inspect(Map<String,ModContainer> candidates, NativeInitializationTrace trace) throws Exception {
        return inspect(candidates, trace, "addon-candidate-bus", "discovered-jar-candidates-only-not-complete-pack-activation");
    }

    public static Map<String,Object> inspectInjectedCoremod(ModContainer container, NativeInitializationTrace trace) throws Exception {
        if (!(container instanceof InjectedModContainer) || !"ivtoolkit".equals(container.getModId()))
            throw new IllegalArgumentException("Original injected IVToolkit container required");
        return inspect(Map.of(container.getModId(), container), trace, "native-injected-coremod-bus",
                "original-injected-ivtoolkit-container-only-not-complete-pack-activation");
    }

    private static Map<String,Object> inspect(Map<String,ModContainer> candidates, NativeInitializationTrace trace,
            String traceId, String scope) throws Exception {
        trace.declare(traceId, "hook", "LoadController.distributeStateMessage(FMLLoadEvent) -> buildModList -> registerBus");
        trace.begin(traceId);
        var loader = Loader.instance();
        var fields = new LinkedHashMap<Field,Object>();
        for (String name : List.of("mods", "namedMods", "modController")) {
            Field field = Loader.class.getDeclaredField(name); field.setAccessible(true);
            fields.put(field, field.get(loader));
        }
        var result = new LinkedHashMap<String,Object>();
        try {
            var controller = new LoadController(loader);
            for (Field field : fields.keySet()) field.set(loader, switch (field.getName()) {
                case "mods" -> new ArrayList<>(candidates.values());
                case "namedMods" -> candidates;
                case "modController" -> controller;
                default -> throw new IllegalStateException("Unexpected native loader field");
            });
            controller.transition(LoaderState.LOADING, false);
            // Do not replace registerBus with an enabled flag: this includes the
            // complete original FMLServerHandler language-resource injection.
            controller.distributeStateMessage(FMLLoadEvent.class);
            var states = new TreeMap<String,String>();
            var presentQueries = new TreeMap<String,Boolean>();
            var unprovided = new TreeSet<String>();
            for (var entry : candidates.entrySet()) {
                ModContainer container = entry.getValue();
                if (container.getMod() != null) throw new IllegalStateException("Candidate bus constructed a mod instance");
                states.put(entry.getKey(), controller.getModState(container).name());
                // Only witnessed candidate IDs. Never infer absence for IDs not
                // in this incomplete composition or expose answers to callbacks.
                presentQueries.put(entry.getKey(), Loader.isModLoaded(entry.getKey()));
                for (var requirement : container.getRequirements())
                    if (!candidates.containsKey(requirement.getLabel())) unprovided.add(requirement.getLabel());
            }
            result.put("scope", scope);
            result.put("method", "original LoadController transition(LOADING) and FMLLoadEvent dispatch");
            result.put("loaderState", loader.getLoaderState().name());
            result.put("containerStates", states);
            result.put("presentCandidateQueries", presentQueries);
            result.put("activeCandidateOrder", controller.getActiveModList().stream().map(ModContainer::getModId).toList());
            result.put("language", language());
            result.put("modInstancesConstructed", false);
            result.put("packActivationQualified", false);
            result.put("dependencyOrder", Map.of("status", "deferred", "versionRequirementsChecked", false,
                    "requiredIdsOutsideCandidateSet", new ArrayList<>(unprovided),
                    "reason", "Complete Cleanroom selection, built-in/injected containers and API providers are not composed"));
        } finally {
            for (var entry : fields.entrySet()) entry.getKey().set(loader, entry.getValue());
            for (var entry : fields.entrySet())
                if (entry.getKey().get(loader) != entry.getValue()) throw new IllegalStateException("Native host loader state not restored");
        }
        result.put("hostLoaderStateRestored", true);
        trace.returned(traceId);
        return result;
    }

    @SuppressWarnings("unchecked")
    private static Map<String,Object> language() throws Exception {
        // Read-only observation of the selected native image's actual map.
        Field singleton = LanguageMap.class.getDeclaredField("field_74817_a"); singleton.setAccessible(true);
        Field strings = LanguageMap.class.getDeclaredField("field_74816_c"); strings.setAccessible(true);
        var values = new TreeMap<>((Map<String,String>) strings.get(singleton.get(null)));
        // Same canonical JSON/UTF-8 bytes, without retaining a second full
        // language string and byte array beside the original native map.
        var hasher = MessageDigest.getInstance("SHA-256");
        try (var writer = new OutputStreamWriter(new DigestOutputStream(OutputStream.nullOutputStream(),hasher),StandardCharsets.UTF_8)) {
            new Gson().toJson(values,writer);
        }
        var digest = hasher.digest();
        return Map.of("method", "original FMLServerHandler.addModAsResource -> LanguageMap.inject",
                "entries", values.size(), "sha256", HexFormat.of().formatHex(digest));
    }
}
