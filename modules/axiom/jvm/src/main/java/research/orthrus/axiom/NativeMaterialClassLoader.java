package research.orthrus.axiom;

import java.io.IOException;
import java.lang.reflect.*;
import java.net.*;
import java.security.CodeSource;
import java.security.cert.Certificate;
import java.util.*;

/** Reached event transformations only, using the original transformer in this same class space. */
final class NativeMaterialClassLoader extends URLClassLoader {
    private static final String PREFIX = "research.orthrus.axiom.nativeconstruction.";
    static final Set<String> EVENTS = Set.of("net.minecraftforge.fml.common.eventhandler.GenericEvent",
            "net.minecraftforge.event.AttachCapabilitiesEvent", "net.minecraftforge.oredict.OreDictionary$OreRegisterEvent",
            "net.minecraftforge.event.RegistryEvent", "net.minecraftforge.event.RegistryEvent$Register",
            PREFIX + "MaterialRegistryEvent", PREFIX + "MaterialEvent", PREFIX + "PostMaterialEvent");
    private final boolean catalog;
    private final boolean oreAccess;
    private final Map<String,Object> transforms = new TreeMap<>();
    private final Map<String,Object> accessTransforms = new TreeMap<>();
    private Object transformer;
    NativeMaterialClassLoader(URL[] urls, Set<String> entries, boolean oreAccess) {
        super(urls, ClassLoader.getPlatformClassLoader());
        catalog = entries.contains(PREFIX.replace('.', '/') + "MaterialEvent.class");
        if (oreAccess && !entries.contains(PREFIX.replace('.', '/') + "OreAccessRules.class"))
            throw new IllegalArgumentException("Ore context requires its source-qualified access rules");
        this.oreAccess = oreAccess;
    }
    @Override protected Class<?> findClass(String name) throws ClassNotFoundException {
        if (oreAccess && name.equals(NativeMaterialAccess.BLOCK)) {
            URL resource = findResource(name.replace('.', '/') + ".class");
            if (resource == null) throw new ClassNotFoundException(name);
            try (var input = resource.openStream()) {
                byte[] raw = input.readAllBytes();
                String rules = (String)Class.forName(PREFIX + "OreAccessRules", true, this).getField("RULES").get(null);
                byte[] transformed = NativeMaterialAccess.transform(this, raw, rules);
                if (Arrays.equals(raw, transformed)) throw new ClassNotFoundException("Required GT Block access transformation did not apply");
                accessTransforms.put(name, Map.of("inputSha256", Json.bytesDigest(raw), "outputSha256", Json.bytesDigest(transformed),
                        "rulesSha256", Json.bytesDigest(rules.getBytes(java.nio.charset.StandardCharsets.UTF_8))));
                return defineClass(name, transformed, 0, transformed.length, new CodeSource(resource, (Certificate[])null));
            } catch (IOException | ReflectiveOperationException failure) { throw new ClassNotFoundException("GT Block access transformation failed", failure); }
        }
        if (!EVENTS.contains(name) || (!catalog && name.startsWith(PREFIX))) return super.findClass(name);
        URL resource = findResource(name.replace('.', '/') + ".class");
        if (resource == null) throw new ClassNotFoundException(name);
        try (var input = resource.openStream()) {
            byte[] raw = input.readAllBytes();
            if (transformer == null) transformer = Class.forName("net.minecraftforge.fml.common.asm.transformers.EventSubscriptionTransformer", true, this)
                    .getConstructor().newInstance();
            byte[] transformed = (byte[])transformer.getClass().getMethod("transform", String.class, String.class, byte[].class)
                    .invoke(transformer, name, name, raw);
            if (Arrays.equals(raw, transformed)) throw new ClassNotFoundException("Required native material event transformation did not apply: " + name);
            transforms.put(name, Map.of("inputSha256", Json.bytesDigest(raw), "outputSha256", Json.bytesDigest(transformed)));
            return defineClass(name, transformed, 0, transformed.length, new CodeSource(resource, (Certificate[])null));
        } catch (IOException | ReflectiveOperationException failure) {
            throw new ClassNotFoundException("Native material event transformation failed: " + name, failure);
        }
    }
    Map<String,Object> transformations() { return Map.copyOf(transforms); }
    Map<String,Object> accessTransformations() { return Map.copyOf(accessTransforms); }
}
