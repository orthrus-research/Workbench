package research.orthrus.axiom;

import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.Map;

/** Fresh-process boundary; producer fixtures execute in the native class space. */
public final class NativeMaterialConformance {
    public static void main(String[] args) throws Exception {
        NativeRuntime.require(); WorkerIsolation.install();
        var programs = new LinkedHashMap<Path, String>();
        programs.put(Path.of(args[2]), args[3]);
        programs.put(Path.of(args[4]), args[5]);
        try (var context = NativeVanillaIdentities.openProgram(Path.of(args[0]), Path.of(args[1]), programs)) {
            context.initialize();
            Object result = context.runMaterialProgram("NativeProducerConformance", args[6]);
            try { context.runMaterialProgram("NativeProducerConformance", args[6]); throw new AssertionError("Repeated material evaluation admitted"); }
            catch (IllegalStateException expected) {
                if (!expected.getMessage().contains("already attempted")) throw expected;
            }
            System.out.println(Json.write(Map.of("result", result, "traceDigest", Json.digest(result),
                    "minecraftLaunched", false, "wholePackParity", false, "kernelIsolation", true)));
        }
    }
}
