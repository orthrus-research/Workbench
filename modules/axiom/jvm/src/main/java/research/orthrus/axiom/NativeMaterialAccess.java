package research.orthrus.axiom;

import java.lang.reflect.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;

/** The original Cleanroom access transformer, with the pinned GT Block rules.
 * No ASM/access-flag implementation is duplicated here. Also used by javac's
 * disposable classpath projection so compile and execution see identical bytes.
 */
final class NativeMaterialAccess {
    static final String BLOCK = "net.minecraft.block.Block";
    static byte[] transform(ClassLoader loader, byte[] raw, String rules) throws ReflectiveOperationException {
        Class<?> type = Class.forName("net.minecraftforge.fml.common.asm.transformers.AccessTransformer", true, loader);
        Constructor<?> constructor = type.getDeclaredConstructor(Class.class); constructor.setAccessible(true);
        Object transformer = constructor.newInstance(type);
        Class<?> chars = Class.forName("com.google.common.io.CharSource", true, loader);
        Object source = chars.getMethod("wrap", CharSequence.class).invoke(null, rules);
        Method process = type.getDeclaredMethod("processATFile", chars); process.setAccessible(true);
        process.invoke(transformer, source);
        return (byte[])type.getMethod("transform", String.class, String.class, byte[].class)
                .invoke(transformer, BLOCK, BLOCK, raw);
    }
    public static void main(String[] args) throws Exception {
        URL[] urls = new URL[args.length - 1];
        for (int i = 1; i < args.length; i++) urls[i - 1] = Path.of(args[i]).toUri().toURL();
        try (var loader = new URLClassLoader(urls, ClassLoader.getPlatformClassLoader());
             var input = loader.getResourceAsStream(BLOCK.replace('.', '/') + ".class")) {
            if (input == null) throw new IllegalArgumentException("Missing native Block image");
            String rules = new String(Base64.getDecoder().decode(args[0]), StandardCharsets.UTF_8);
            System.out.println(Base64.getEncoder().encodeToString(transform(loader, input.readAllBytes(), rules)));
        }
    }
}
