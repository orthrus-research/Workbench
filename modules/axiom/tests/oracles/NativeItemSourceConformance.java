package research.orthrus.axiom;

import java.net.URL;
import java.nio.file.Path;
import java.util.*;

/** Independent source-compiled native dependency execution; never an installed engine input API. */
public final class NativeItemSourceConformance {
    public static void main(String[] args) throws Exception {
        NativeRuntime.require(); WorkerIsolation.install();
        var urls=new URL[args.length-1]; for(int i=1;i<args.length;i++) urls[i-1]=Path.of(args[i]).toUri().toURL();
        try(var loader=new NativeMaterialClassLoader(urls,Set.of("research/orthrus/axiom/nativeconstruction/MaterialEvent.class"),false)) {
            Thread.currentThread().setContextClassLoader(loader);
            Class.forName("net.minecraft.init.Bootstrap",true,loader).getMethod("axiom$materialIdentities").invoke(null);
            Class.forName("net.minecraft.init.Enchantments",true,loader);
            Class.forName("net.minecraftforge.fluids.FluidRegistry",true,loader);
            Object result=Class.forName("research.orthrus.axiom.nativeconstruction.NativeItemProbe",true,loader).getMethod("run",String.class).invoke(null,args[0]);
            System.out.println(Json.write(Map.of("result",result,"traceDigest",Json.digest(result),"kernelIsolation",true,
                    "minecraftLaunched",false,"wholePackParity",false,"generatedGTContent",false)));
        }
    }
}
