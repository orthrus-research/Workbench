package research.orthrus.axiom;

import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.Map;

/** Developer-only fresh-process qualification entry point. */
public final class NativeCatalogConformance {
    public static void main(String[] args) throws Exception {
        NativeRuntime.require(); WorkerIsolation.install();
        var programs = new LinkedHashMap<Path,String>();
        programs.put(Path.of(args[2]), args[3]); programs.put(Path.of(args[4]), args[5]);
        try (var context = NativeVanillaIdentities.openProgram(Path.of(args[0]), Path.of(args[1]), programs)) {
            context.initialize();
            Object result = context.runMaterialProgram("NativeCatalogProbe", args[6]);
            try { context.runMaterialProgram("NativeCatalogProbe", args[6]); throw new AssertionError("Repeated catalog evaluation admitted"); }
            catch (IllegalStateException expected) { if (!expected.getMessage().contains("already attempted")) throw expected; }
            System.out.println(Json.write(Map.of("result", result, "traceDigest", Json.digest(result),
                    "eventTransformations", context.materialEventTransformations(), "kernelIsolation", true,
                    "wholePackParity", false, "minecraftLaunched", false, "generatedContent", false)));
        }
    }
}
