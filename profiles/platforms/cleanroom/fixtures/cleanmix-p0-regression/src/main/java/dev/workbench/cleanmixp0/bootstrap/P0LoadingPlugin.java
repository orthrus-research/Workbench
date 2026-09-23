package dev.workbench.cleanmixp0.bootstrap;

import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import net.minecraftforge.common.ForgeVersion;
import net.minecraftforge.fml.relauncher.IFMLLoadingPlugin;
import zone.rong.mixinbooter.IEarlyMixinLoader;

@IFMLLoadingPlugin.Name("WorkbenchCleanMixP0Regression")
@IFMLLoadingPlugin.MCVersion(ForgeVersion.mcVersion)
@IFMLLoadingPlugin.SortingIndex(Integer.MIN_VALUE)
@IFMLLoadingPlugin.TransformerExclusions({
    "dev.workbench.cleanmixp0.bootstrap"
})
public final class P0LoadingPlugin
    implements IFMLLoadingPlugin, IEarlyMixinLoader {

    public static final String ROW_PROPERTY = "workbench.cleanmix.p0.row_id";

    @Override
    public List<String> getMixinConfigs() {
        String row = System.getProperty(ROW_PROPERTY, "");
        if (row.startsWith("P0-PHASE-SEQUENCE-")) {
            return Arrays.asList(
                "mixins.workbench.cleanmix-p0.phase-preinit.json",
                "mixins.workbench.cleanmix-p0.phase-init.json",
                "mixins.workbench.cleanmix-p0.phase-default.json"
            );
        }
        if (row.equals("P0-XFAIL-LAZY-INHERITANCE-CALLBACKINFO-SERVER")) {
            return Collections.singletonList(
                "mixins.workbench.cleanmix-p0.lazy.json"
            );
        }
        if (row.equals("P0-XFAIL-REENTRANT-PENDING-TARGET-SERVER")) {
            return Collections.singletonList(
                "mixins.workbench.cleanmix-p0.reentrant.json"
            );
        }
        if (row.equals("P0-XFAIL-THREE-DEEP-CHILD-FIRST-SERVER")
                || row.equals("X02-THREE-DEEP-PARENT-FIRST-SERVER")) {
            return Collections.singletonList(
                "mixins.workbench.cleanmix-p0.three.json"
            );
        }
        if (row.startsWith("X02-DETACHED-TWO-")) {
            return Collections.singletonList(
                "mixins.workbench.cleanmix-p0.detached-two.json"
            );
        }
        if (row.startsWith("X02-DETACHED-THREE-")) {
            return Collections.singletonList(
                "mixins.workbench.cleanmix-p0.detached-three.json"
            );
        }
        return Collections.emptyList();
    }

    @Override
    public String[] getASMTransformerClass() {
        return new String[] {P0TransformOrderProbe.class.getName()};
    }

    @Override
    public String getModContainerClass() {
        return null;
    }

    @Override
    public String getSetupClass() {
        return null;
    }

    @Override
    public void injectData(Map<String, Object> data) {
    }

    @Override
    public String getAccessTransformerClass() {
        return null;
    }
}
