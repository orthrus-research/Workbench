package research.orthrus.axiom.materialhost;

import net.minecraft.launchwrapper.IClassTransformer;

/** Native runtime-generated traits use a separate loader from compiled scripts. */
public final class NativeTraitDefinitionTransformer implements IClassTransformer {
    @Override public byte[] transform(String name,String transformedName,byte[] input) {
        if(input==null||!MaterialTraitLoaderHook.TARGET.equals(transformedName))return input;
        return MaterialTraitLoaderHook.apply(input,"research/orthrus/axiom/materialhost/MaterialTraitClasses");
    }
}
