package research.orthrus.axiom.materialhost;

import java.security.MessageDigest;
import java.util.*;

/** Original Foundation VM preparation before constructing the native launch classloader. */
public final class NativeFoundationBootstrap {
    public static final String TARGET = "top.outlands.foundation.boot.Foundation";
    public static final String SHA256 = "c8d01527c1834f41141abd417cb59c2633b03c7a66d7ea09f506bb8140fc91ec";
    private NativeFoundationBootstrap() {}
    public static Map<String,Object> prepare(ClassLoader bridge) throws Exception {
        Class<?> launch = Class.forName("net.minecraft.launchwrapper.Launch", false, bridge);
        if (launch.getField("classLoader").get(null) != null)
            throw new IllegalStateException("Original Foundation VM preparation must precede native classloader construction");
        Class<?> foundation = Class.forName(TARGET, false, bridge);
        byte[] bytes;
        try (var stream = foundation.getResourceAsStream("/" + TARGET.replace('.', '/') + ".class")) {
            if (stream == null) throw new IllegalStateException("Original Foundation resource missing");
            bytes = stream.readAllBytes();
        }
        String digest = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
        if (!SHA256.equals(digest) || foundation.getClassLoader() != bridge)
            throw new IllegalStateException("Original Foundation VM preparation identity differs");
        var method = foundation.getDeclaredMethod("breakModuleAndReflection");
        method.setAccessible(true); method.invoke(null);
        return Map.of("method", TARGET + "#breakModuleAndReflection", "inputSha256", digest,
                "codeSource", foundation.getProtectionDomain().getCodeSource().getLocation().toString(),
                "methodReturned", true, "nativeClassLoaderCreated", false);
    }
}
