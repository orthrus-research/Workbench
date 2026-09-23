package dev.workbench.cleanmixhandler.bootstrap;

import java.util.Properties;
import net.minecraft.launchwrapper.IClassTransformer;

public final class TransformOrderProbe implements IClassTransformer {

    public static final String TRACE_PROPERTY =
        "workbench.cleanmix.handler.observed_transform_order";
    public static final String HEALTH_PROPERTY =
        "workbench.cleanmix.handler.transform_observer_health";
    public static final String ENTRY_PREFIX =
        "WORKBENCH_CLEANMIX_HANDLER_TRANSFORM_ENTRY_V1 ";
    public static final String START_PREFIX =
        "WORKBENCH_CLEANMIX_HANDLER_TRANSFORM_OBSERVER_START_V1 ";

    private static final String PARENT_TARGET =
        "dev.workbench.cleanmixhandler.target.ParentTarget";
    private static final String CHILD_TARGET =
        "dev.workbench.cleanmixhandler.target.ChildTarget";

    public TransformOrderProbe() {
        Properties properties = System.getProperties();
        synchronized (properties) {
            if (properties.getProperty(TRACE_PROPERTY) == null) {
                properties.setProperty(TRACE_PROPERTY, "");
            }
            properties.setProperty(HEALTH_PROPERTY, "active");
        }
        System.out.println(
            START_PREFIX + getClass().getClassLoader().getClass().getName()
        );
    }

    @Override
    public byte[] transform(
        String name,
        String transformedName,
        byte[] basicClass
    ) {
        if (!PARENT_TARGET.equals(transformedName)
            && !CHILD_TARGET.equals(transformedName)) {
            return basicClass;
        }

        Properties properties = System.getProperties();
        String trace;
        int ordinal;
        synchronized (properties) {
            trace = properties.getProperty(TRACE_PROPERTY, "");
            String surrounded = "," + trace + ",";
            if (!surrounded.contains("," + transformedName + ",")) {
                trace = trace.isEmpty()
                    ? transformedName
                    : trace + "," + transformedName;
                properties.setProperty(TRACE_PROPERTY, trace);
            }
            String countProperty = TRACE_PROPERTY + ".entry_count";
            ordinal = Integer.parseInt(properties.getProperty(countProperty, "0")) + 1;
            properties.setProperty(countProperty, String.valueOf(ordinal));
        }
        System.out.println(
            ENTRY_PREFIX + ordinal + " " + transformedName
        );
        return basicClass;
    }
}
