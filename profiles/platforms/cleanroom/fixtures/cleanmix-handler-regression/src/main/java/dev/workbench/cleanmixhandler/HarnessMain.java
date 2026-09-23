package dev.workbench.cleanmixhandler;

import com.cleanroommc.common.CleanroomEnvironment;
import dev.workbench.cleanmixhandler.bootstrap.TransformOrderProbe;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.net.URL;
import org.spongepowered.asm.mixin.MixinEnvironment;
import org.spongepowered.asm.service.IMixinService;
import org.spongepowered.asm.service.MixinService;

public final class HarnessMain {

    public static final String ORDER_PROPERTY =
        "workbench.cleanmix.handler.target_order";
    public static final String ROW_PROPERTY =
        "workbench.cleanmix.handler.row_id";
    public static final String RESULT_PREFIX =
        "WORKBENCH_CLEANMIX_HANDLER_RESULT_V1 ";

    private static final String PARENT_TARGET =
        "dev.workbench.cleanmixhandler.target.ParentTarget";
    private static final String CHILD_TARGET =
        "dev.workbench.cleanmixhandler.target.ChildTarget";

    private HarnessMain() {
    }

    public static void main(String[] arguments) throws Exception {
        String order = System.getProperty(ORDER_PROPERTY);
        String rowId = System.getProperty(ROW_PROPERTY);
        if (!"parent_first".equals(order) && !"child_first".equals(order)) {
            throw new IllegalArgumentException("unsupported target order: " + order);
        }
        if (rowId == null || rowId.isEmpty()) {
            throw new IllegalArgumentException("row id is required");
        }

        ClassLoader loader = Thread.currentThread().getContextClassLoader();
        String requestMethod;
        Class<?> childClass;
        if ("parent_first".equals(order)) {
            Class.forName(PARENT_TARGET, true, loader);
            childClass = Class.forName(CHILD_TARGET, true, loader);
            requestMethod = "class_for_name";
        } else {
            childClass = findClassDirectly(loader, CHILD_TARGET);
            requestMethod = "launch_class_loader_find_class";
        }
        Class<?> parentClass = Class.forName(PARENT_TARGET, true, loader);
        String expectedTransformOrder = "parent_first".equals(order)
            ? PARENT_TARGET + "," + CHILD_TARGET
            : CHILD_TARGET + "," + PARENT_TARGET;
        String observedTransformOrder = System.getProperty(
            TransformOrderProbe.TRACE_PROPERTY,
            "observer_not_started"
        );
        String observerHealth = System.getProperty(
            TransformOrderProbe.HEALTH_PROPERTY,
            "missing"
        );
        boolean transformOrderMatches = expectedTransformOrder.equals(
            observedTransformOrder
        );
        Object parent = parentClass.getDeclaredConstructor().newInstance();
        Object child = childClass.getDeclaredConstructor().newInstance();
        Method parentExercise = parentClass.getMethod("exercise");
        Method parentControl = parentClass.getMethod("control");
        Method childExercise = childClass.getMethod("exercise");
        Method childControl = childClass.getMethod("control");
        parentExercise.invoke(parent);
        parentControl.invoke(parent);
        childExercise.invoke(child);
        childControl.invoke(child);

        IMixinService service = MixinService.getService();
        if (!"CleanMix".equals(service.getName())) {
            throw new IllegalStateException(
                "unexpected Mixin service: " + service.getName()
            );
        }
        URL mixinSource = MixinEnvironment.class
            .getProtectionDomain()
            .getCodeSource()
            .getLocation();
        String result = "{"
            + field("row_id", rowId) + ","
            + field("target_order", order) + ","
            + field("target_request_method", requestMethod) + ","
            + field("expected_transform_order", expectedTransformOrder) + ","
            + field("observed_transform_order", observedTransformOrder) + ","
            + field("transform_observer_health", observerHealth) + ","
            + bool("transform_order_oracle", transformOrderMatches) + ","
            + field("physical_side", CleanroomEnvironment.side().name()) + ","
            + field("mixin_service", service.getName()) + ","
            + field("mixin_source", mixinSource.toExternalForm()) + ","
            + field("target_class_loader", childClass.getClassLoader().getClass().getName()) + ","
            + field("context_class_loader", loader.getClass().getName()) + ","
            + number("parent_handler_count", HarnessCounters.parentHandlerCount) + ","
            + number("child_handler_count", HarnessCounters.childHandlerCount) + ","
            + number("original_body_count", HarnessCounters.originalBodyCount) + ","
            + number("parent_control_count", HarnessCounters.parentControlCount) + ","
            + number("child_control_count", HarnessCounters.childControlCount)
            + "}";
        System.out.println(RESULT_PREFIX + result);

        if (!"active".equals(observerHealth) || !transformOrderMatches) {
            throw new IllegalStateException(
                "native transform order oracle failed: expected="
                    + expectedTransformOrder
                    + " observed="
                    + observedTransformOrder
                    + " observer_health="
                    + observerHealth
            );
        }

        if (HarnessCounters.originalBodyCount != 2
            || HarnessCounters.parentHandlerCount
                + HarnessCounters.childHandlerCount != 2
            || HarnessCounters.parentControlCount != 1
            || HarnessCounters.childControlCount != 1) {
            throw new IllegalStateException("fixture control counters are invalid");
        }
    }

    private static Class<?> findClassDirectly(
        ClassLoader loader,
        String className
    ) throws Exception {
        Method findClass = loader.getClass().getMethod("findClass", String.class);
        try {
            return (Class<?>) findClass.invoke(loader, className);
        } catch (InvocationTargetException failure) {
            Throwable cause = failure.getCause();
            if (cause instanceof Exception) {
                throw (Exception) cause;
            }
            if (cause instanceof Error) {
                throw (Error) cause;
            }
            throw failure;
        }
    }

    private static String field(String name, String value) {
        return quote(name) + ":" + quote(value);
    }

    private static String number(String name, int value) {
        return quote(name) + ":" + value;
    }

    private static String bool(String name, boolean value) {
        return quote(name) + ":" + String.valueOf(value);
    }

    private static String quote(String value) {
        StringBuilder result = new StringBuilder(value.length() + 2);
        result.append('"');
        for (int index = 0; index < value.length(); index++) {
            char character = value.charAt(index);
            if (character == '"' || character == '\\') {
                result.append('\\');
            }
            result.append(character);
        }
        return result.append('"').toString();
    }
}
