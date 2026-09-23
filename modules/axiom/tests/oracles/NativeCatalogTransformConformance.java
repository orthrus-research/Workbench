package research.orthrus.axiom;

import java.net.URL;
import java.nio.file.Path;
import java.util.Set;

/** Source-compiled transformer ahead of native libraries; never part of the shipped program. */
public final class NativeCatalogTransformConformance {
    public static void main(String[] args) throws Exception {
        NativeRuntime.require(); WorkerIsolation.install();
        var names=Set.of(args[0].split(","));
        if (!NativeMaterialClassLoader.EVENTS.containsAll(names)) throw new IllegalArgumentException("Unqualified event transformation");
        var urls=new URL[args.length-1]; for (int i=1;i<args.length;i++) urls[i-1]=Path.of(args[i]).toUri().toURL();
        try (var loader=new NativeMaterialClassLoader(urls,Set.of("research/orthrus/axiom/nativeconstruction/MaterialEvent.class"),false)) {
            for (String name : names) Class.forName(name,true,loader);
            System.out.println(Json.write(loader.transformations()));
        }
    }
}
