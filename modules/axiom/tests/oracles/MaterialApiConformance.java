package research.orthrus.axiom;

import java.io.*;
import java.net.*;
import java.nio.file.*;
import java.util.*;

/** Trusted Java linkage witnesses only; never admits or executes candidate source. */
public final class MaterialApiConformance {
    public static void main(String[] args) throws Exception {
        NativeRuntime.require();
        if (!args[0].equals("worker")) {
            List<String> arguments = new ArrayList<>(List.of(args));
            arguments.set(0, "worker");
            var inputs = List.of(Path.of(args[1]), Path.of(args[2]), Path.of(args[3]), Path.of(args[5]));
            var command = Main.sandboxCommand(inputs,
                    Arrays.asList(System.getProperty("java.class.path").split(File.pathSeparator)),
                    MaterialApiConformance.class.getName(), arguments);
            Process process = new ProcessBuilder(command).start();
            System.out.println(Json.write(Main.observe(process, new byte[0], 8 << 20, 2 << 20, 30000)));
            return;
        }
        WorkerIsolation.install();
        PrintStream protocol = System.out;
        System.setOut(System.err);
        Path images = Path.of(args[1]), libraries = Path.of(args[2]);
        var policy = Json.object(Json.parse(Target.resource("/axiom/native-identity-runtime.json")));
        var urls = new ArrayList<URL>();
        for (String key : List.of("images", "libraries")) {
            Path root = key.equals("images") ? images : libraries;
            var rows = Json.array(policy.get(key)); NativeRuntime.verifyFiles(root, rows);
            for (Object row : rows) urls.add(root.resolve(Json.string(Json.object(row).get("path"))).toUri().toURL());
        }
        // Digests here are supplied by the trusted qualification runner after an
        // exact source rebuild, never an authoring request or arbitrary candidate.
        for (int index : List.of(3, 5)) {
            Path jar = Path.of(args[index]);
            if (!Json.bytesDigest(Files.readAllBytes(jar)).equals(args[index + 1]))
                throw new IllegalArgumentException("API witness input digest differs");
            urls.add(jar.toUri().toURL());
        }
        try (var loader = new NativeMaterialClassLoader(urls.toArray(URL[]::new), Set.of(), false)) {
            Thread.currentThread().setContextClassLoader(loader);
            Class.forName("net.minecraft.init.Bootstrap", true, loader)
                    .getMethod("axiom$materialIdentities").invoke(null);
            Class.forName("net.minecraft.init.Enchantments", true, loader);
            Class.forName("net.minecraftforge.fluids.FluidRegistry", true, loader);
            Object result = Class.forName("research.orthrus.axiom.materialtest.MaterialApiProbe", true, loader)
                    .getMethod("run", String.class).invoke(null, args[7]);
            protocol.println(Json.write(Map.of("schema", "axiom.result.v1", "result", result,
                    "eventTransformations", loader.transformations(), "kernelIsolation", true,
                    "namespaceIsolation", true, "groovyExecutionQualified", false, "minecraftLaunched", false)));
        }
    }
}
