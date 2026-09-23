package research.orthrus.axiom.materialhost;

import com.google.common.io.CharSource;
import net.minecraftforge.fml.common.asm.transformers.AccessTransformer;

/** Original Cleanroom transformer with the selected GT Block rules, no new ASM. */
public final class NativeMaterialBlockAccess {
    private NativeMaterialBlockAccess() {}
    public static AccessTransformer create() throws ReflectiveOperationException {
        var constructor = AccessTransformer.class.getDeclaredConstructor(Class.class);
        constructor.setAccessible(true);
        var transformer = constructor.newInstance(AccessTransformer.class);
        var process = AccessTransformer.class.getDeclaredMethod("processATFile", CharSource.class);
        process.setAccessible(true);
        process.invoke(transformer, CharSource.wrap(OreAccessRules.RULES));
        return transformer;
    }
}
