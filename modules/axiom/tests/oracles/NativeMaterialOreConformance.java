package research.orthrus.axiom;

import java.nio.file.Path;
import java.util.*;

public final class NativeMaterialOreConformance {
    public static void main(String[] args) throws Exception {
        NativeRuntime.require(); WorkerIsolation.install();
        var programs = new LinkedHashMap<Path,String>();
        programs.put(Path.of(args[2]), args[3]); programs.put(Path.of(args[4]), args[5]);
        try (var context = NativeVanillaIdentities.openOreProgram(Path.of(args[0]), Path.of(args[1]), programs)) {
            context.initialize();
            Object result = context.runMaterialProgram("NativeMaterialOreProbe", args[6]);
            System.out.println(Json.write(Map.of("result", result, "traceDigest", Json.digest(result),
                    "eventTransformations", context.materialEventTransformations(), "accessTransformations", context.materialAccessTransformations(),
                    "kernelIsolation", true, "minecraftLaunched", false, "wholePackParity", false, "fullRecipeRegistration", false)));
        }
    }
}
