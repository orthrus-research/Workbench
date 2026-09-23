package dev.workbench.cleanmixp0.bootstrap;

import java.util.Arrays;
import java.util.LinkedHashSet;
import java.util.Properties;
import java.util.Set;
import net.minecraft.launchwrapper.IClassTransformer;

public final class P0TransformOrderProbe implements IClassTransformer {

    public static final String TRACE_PROPERTY =
        "workbench.cleanmix.p0.observed_transform_order";
    public static final String HEALTH_PROPERTY =
        "workbench.cleanmix.p0.transform_observer_health";
    public static final String ENTRY_PREFIX =
        "WORKBENCH_CLEANMIX_P0_TRANSFORM_ENTRY_V1 ";
    private static final Set<String> TARGETS = new LinkedHashSet<>(Arrays.asList(
        "dev.workbench.cleanmixp0.PhaseTargets$Preinit",
        "dev.workbench.cleanmixp0.PhaseTargets$Init",
        "dev.workbench.cleanmixp0.PhaseTargets$Default",
        "dev.workbench.cleanmixp0.LateTargets$Target",
        "dev.workbench.cleanmixp0.LateTargets$Trigger",
        "dev.workbench.cleanmixp0.KnownGapTargets$LazyParent",
        "dev.workbench.cleanmixp0.KnownGapTargets$LazyChild",
        "dev.workbench.cleanmixp0.KnownGapTargets$Reentrant",
        "dev.workbench.cleanmixp0.KnownGapTargets$ThreeBase",
        "dev.workbench.cleanmixp0.KnownGapTargets$ThreeMiddle",
        "dev.workbench.cleanmixp0.KnownGapTargets$ThreeLeaf",
        "dev.workbench.cleanmixp0.DetachedTargets$TwoParent",
        "dev.workbench.cleanmixp0.DetachedTargets$TwoChild",
        "dev.workbench.cleanmixp0.DetachedTargets$ThreeBase",
        "dev.workbench.cleanmixp0.DetachedTargets$ThreeMiddle",
        "dev.workbench.cleanmixp0.DetachedTargets$ThreeLeaf"
    ));

    public P0TransformOrderProbe() {
        Properties properties = System.getProperties();
        synchronized (properties) {
            properties.setProperty(TRACE_PROPERTY, "");
            properties.setProperty(HEALTH_PROPERTY, "active");
            properties.setProperty(TRACE_PROPERTY + ".entry_count", "0");
        }
        System.out.println(
            "WORKBENCH_CLEANMIX_P0_TRANSFORM_OBSERVER_START_V1 "
                + getClass().getClassLoader().getClass().getName()
        );
    }

    @Override
    public byte[] transform(
        String name, String transformedName, byte[] basicClass
    ) {
        if (!TARGETS.contains(transformedName)) {
            return basicClass;
        }
        Properties properties = System.getProperties();
        int ordinal;
        synchronized (properties) {
            String trace = properties.getProperty(TRACE_PROPERTY, "");
            String surrounded = "," + trace + ",";
            if (!surrounded.contains("," + transformedName + ",")) {
                properties.setProperty(
                    TRACE_PROPERTY,
                    trace.isEmpty() ? transformedName : trace + "," + transformedName
                );
            }
            String countProperty = TRACE_PROPERTY + ".entry_count";
            ordinal = Integer.parseInt(properties.getProperty(countProperty, "0")) + 1;
            properties.setProperty(countProperty, String.valueOf(ordinal));
        }
        System.out.println(ENTRY_PREFIX + ordinal + transformedName);
        return basicClass;
    }
}
