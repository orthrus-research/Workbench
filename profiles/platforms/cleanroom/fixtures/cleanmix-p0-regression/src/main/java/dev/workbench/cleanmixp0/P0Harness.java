package dev.workbench.cleanmixp0;

import com.cleanroommc.common.CleanroomEnvironment;
import dev.workbench.cleanmixp0.bootstrap.P0LoadingPlugin;
import dev.workbench.cleanmixp0.bootstrap.P0TransformOrderProbe;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.net.URL;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.spongepowered.asm.mixin.MixinEnvironment;
import org.spongepowered.asm.mixin.Mixins;
import org.spongepowered.asm.service.IMixinService;
import org.spongepowered.asm.service.MixinService;

public final class P0Harness {

    public static final String RESULT_PREFIX = "WORKBENCH_CLEANMIX_P0_RESULT_V1 ";
    private static final String PHASE_PREINIT =
        "dev.workbench.cleanmixp0.PhaseTargets$Preinit";
    private static final String PHASE_INIT =
        "dev.workbench.cleanmixp0.PhaseTargets$Init";
    private static final String PHASE_DEFAULT =
        "dev.workbench.cleanmixp0.PhaseTargets$Default";
    private static final String LATE_TARGET =
        "dev.workbench.cleanmixp0.LateTargets$Target";
    private static final String LATE_TRIGGER =
        "dev.workbench.cleanmixp0.LateTargets$Trigger";
    private static final String LAZY_PARENT =
        "dev.workbench.cleanmixp0.KnownGapTargets$LazyParent";
    private static final String LAZY_CHILD =
        "dev.workbench.cleanmixp0.KnownGapTargets$LazyChild";
    private static final String REENTRANT =
        "dev.workbench.cleanmixp0.KnownGapTargets$Reentrant";
    private static final String THREE_BASE =
        "dev.workbench.cleanmixp0.KnownGapTargets$ThreeBase";
    private static final String THREE_MIDDLE =
        "dev.workbench.cleanmixp0.KnownGapTargets$ThreeMiddle";
    private static final String THREE_LEAF =
        "dev.workbench.cleanmixp0.KnownGapTargets$ThreeLeaf";
    private static final String DETACHED_TWO_PARENT =
        "dev.workbench.cleanmixp0.DetachedTargets$TwoParent";
    private static final String DETACHED_TWO_CHILD =
        "dev.workbench.cleanmixp0.DetachedTargets$TwoChild";
    private static final String DETACHED_THREE_BASE =
        "dev.workbench.cleanmixp0.DetachedTargets$ThreeBase";
    private static final String DETACHED_THREE_MIDDLE =
        "dev.workbench.cleanmixp0.DetachedTargets$ThreeMiddle";
    private static final String DETACHED_THREE_LEAF =
        "dev.workbench.cleanmixp0.DetachedTargets$ThreeLeaf";

    private P0Harness() {
    }

    public static void run() throws Exception {
        String row = System.getProperty(P0LoadingPlugin.ROW_PROPERTY, "");
        if (row.isEmpty()) {
            throw new IllegalArgumentException("P0 row id is required");
        }
        Map<String, Object> result = common(row);
        if (row.startsWith("P0-PHASE-SEQUENCE-")) {
            phase(result);
        } else if (row.equals("P0-LATE-DEFAULT-BEFORE-TARGET-SERVER")) {
            lateBefore(result);
        } else if (row.equals("P0-LATE-DEFAULT-AFTER-TARGET-SERVER")) {
            lateAfter(result);
        } else if (row.equals("P0-XFAIL-LAZY-INHERITANCE-CALLBACKINFO-SERVER")) {
            lazy(result);
        } else if (row.equals("P0-XFAIL-REENTRANT-PENDING-TARGET-SERVER")) {
            reentrant(result);
        } else if (row.equals("P0-XFAIL-THREE-DEEP-CHILD-FIRST-SERVER")) {
            threeDeep(result, true);
        } else if (row.equals("X02-THREE-DEEP-PARENT-FIRST-SERVER")) {
            threeDeep(result, false);
        } else if (row.equals("X02-DETACHED-TWO-CHILD-FIRST-SERVER")) {
            detachedTwo(result, true);
        } else if (row.equals("X02-DETACHED-TWO-PARENT-FIRST-SERVER")) {
            detachedTwo(result, false);
        } else if (row.equals("X02-DETACHED-THREE-CHILD-FIRST-SERVER")) {
            detachedThree(result, true);
        } else if (row.equals("X02-DETACHED-THREE-PARENT-FIRST-SERVER")) {
            detachedThree(result, false);
        } else {
            throw new IllegalArgumentException("unsupported P0 row: " + row);
        }
        result.put(
            "observed_transform_order",
            System.getProperty(P0TransformOrderProbe.TRACE_PROPERTY, "missing")
        );
        result.put(
            "transform_entry_count",
            Integer.valueOf(System.getProperty(
                P0TransformOrderProbe.TRACE_PROPERTY + ".entry_count", "0"
            ))
        );
        result.put(
            "transform_observer_health",
            System.getProperty(P0TransformOrderProbe.HEALTH_PROPERTY, "missing")
        );
        System.out.println(RESULT_PREFIX + json(result));
    }

    private static Map<String, Object> common(String row) {
        IMixinService service = MixinService.getService();
        if (!"CleanMix".equals(service.getName())) {
            throw new IllegalStateException("unexpected Mixin service: " + service.getName());
        }
        URL mixinSource = MixinEnvironment.class.getProtectionDomain()
            .getCodeSource().getLocation();
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("row_id", row);
        result.put("physical_side", CleanroomEnvironment.side().name());
        result.put("mixin_service", service.getName());
        result.put("mixin_source", mixinSource.toExternalForm());
        result.put(
            "mixin_phase",
            String.valueOf(MixinEnvironment.getCurrentEnvironment().getPhase())
        );
        result.put(
            "context_class_loader",
            Thread.currentThread().getContextClassLoader().getClass().getName()
        );
        return result;
    }

    private static void phase(Map<String, Object> result) throws Exception {
        result.put("preinit_value", invokeInt(PHASE_PREINIT));
        result.put("init_value", invokeInt(PHASE_INIT));
        result.put("default_value", invokeInt(PHASE_DEFAULT));
        result.put("preinit_marker_count", P0Counters.phasePreinit);
        result.put("init_marker_count", P0Counters.phaseInit);
        result.put("default_marker_count", P0Counters.phaseDefault);
        result.put("original_body_count", P0Counters.originalBodies);
    }

    private static void lateBefore(Map<String, Object> result) throws Exception {
        IMixinService service = MixinService.getService();
        boolean before = service.getClassTracker().isClassLoaded(LATE_TARGET);
        Mixins.addConfiguration("mixins.workbench.cleanmix-p0.late.json");
        boolean afterRegistration = service.getClassTracker().isClassLoaded(LATE_TARGET);
        result.put("loaded_before_registration", before);
        result.put("loaded_after_registration", afterRegistration);
        result.put("value", invokeInt(LATE_TARGET));
        result.put("late_marker_count", P0Counters.lateMixin);
        result.put("original_body_count", P0Counters.originalBodies);
    }

    private static void lateAfter(Map<String, Object> result) throws Exception {
        Class<?> target = load(LATE_TARGET);
        Object instance = target.getDeclaredConstructor().newInstance();
        int baseline = (Integer) target.getMethod("value").invoke(instance);
        String identity = identity(target);
        boolean before = MixinService.getService().getClassTracker()
            .isClassLoaded(LATE_TARGET);
        String failureClass = null;
        String failureMessage = null;
        Mixins.addConfiguration("mixins.workbench.cleanmix-p0.late.json");
        try {
            invokeInt(LATE_TRIGGER);
        } catch (Throwable failure) {
            Throwable root = unwrap(failure);
            failureClass = root.getClass().getName();
            failureMessage = String.valueOf(root.getMessage());
        }
        int after = (Integer) target.getMethod("value").invoke(instance);
        result.put("loaded_before_registration", before);
        result.put("baseline_value", baseline);
        result.put("after_value", after);
        result.put("class_identity_before", identity);
        result.put("class_identity_after", identity(target));
        result.put("trigger_failure_class", failureClass);
        result.put("trigger_failure_message", failureMessage);
        result.put("late_marker_count", P0Counters.lateMixin);
    }

    private static void lazy(Map<String, Object> result) throws Exception {
        load(LAZY_PARENT);
        Class<?> child = load(LAZY_CHILD);
        Object instance = child.getDeclaredConstructor().newInstance();
        child.getMethod("exercise").invoke(instance);
        result.put("parent_handler_count", P0Counters.lazyParent);
        result.put("child_handler_count", P0Counters.lazyChild);
        result.put("callback_info_null_count", P0Counters.lazyCallbackInfoNull);
        result.put("original_body_count", P0Counters.originalBodies);
        result.put("parent_handler_names", handlerNames(load(LAZY_PARENT), "lazyHandler"));
        result.put("child_handler_names", handlerNames(child, "lazyHandler"));
    }

    private static void reentrant(Map<String, Object> result) throws Exception {
        result.put(
            "reentrant_action",
            System.getProperty(ReentrantConfigPlugin.ACTION_PROPERTY, "missing")
        );
        result.put("value", invokeInt(REENTRANT));
        result.put("reentrant_marker_count", P0Counters.reentrantMixin);
        result.put("original_body_count", P0Counters.originalBodies);
    }

    private static void threeDeep(
        Map<String, Object> result, boolean childFirst
    ) throws Exception {
        ClassLoader loader = Thread.currentThread().getContextClassLoader();
        Class<?> base;
        Class<?> middle;
        Class<?> leaf;
        if (childFirst) {
            leaf = findClassDirectly(loader, THREE_LEAF);
            middle = load(THREE_MIDDLE);
            base = load(THREE_BASE);
        } else {
            base = load(THREE_BASE);
            middle = load(THREE_MIDDLE);
            leaf = load(THREE_LEAF);
        }
        invokeVoid(base, "exercise");
        invokeVoid(middle, "exercise");
        invokeVoid(leaf, "exercise");
        invokeVoid(base, "control");
        invokeVoid(middle, "control");
        invokeVoid(leaf, "control");
        result.put("base_handler_count", P0Counters.threeBase);
        result.put("middle_handler_count", P0Counters.threeMiddle);
        result.put("leaf_handler_count", P0Counters.threeLeaf);
        result.put("original_body_count", P0Counters.originalBodies);
        result.put("base_control_count", P0Counters.baseControl);
        result.put("middle_control_count", P0Counters.middleControl);
        result.put("leaf_control_count", P0Counters.leafControl);
        result.put("base_handler_names", handlerNames(base, "threeHandler"));
        result.put("middle_handler_names", handlerNames(middle, "threeHandler"));
        result.put("leaf_handler_names", handlerNames(leaf, "threeHandler"));
    }

    private static void detachedTwo(
        Map<String, Object> result, boolean childFirst
    ) throws Exception {
        ClassLoader loader = Thread.currentThread().getContextClassLoader();
        Class<?> parent;
        Class<?> child;
        if (childFirst) {
            child = findClassDirectly(loader, DETACHED_TWO_CHILD);
            parent = load(DETACHED_TWO_PARENT);
        } else {
            parent = load(DETACHED_TWO_PARENT);
            child = load(DETACHED_TWO_CHILD);
        }
        invokeVoid(parent, "exercise");
        invokeVoid(child, "exercise");
        invokeVoid(parent, "control");
        invokeVoid(child, "control");
        result.put("parent_handler_count", P0Counters.detachedTwoParent);
        result.put("child_handler_count", P0Counters.detachedTwoChild);
        result.put("parent_control_count", P0Counters.detachedTwoParentControl);
        result.put("child_control_count", P0Counters.detachedTwoChildControl);
        result.put("original_body_count", P0Counters.originalBodies);
        result.put(
            "parent_handler_names", handlerNames(parent, "detachedTwoHandler")
        );
        result.put(
            "child_handler_names", handlerNames(child, "detachedTwoHandler")
        );
    }

    private static void detachedThree(
        Map<String, Object> result, boolean childFirst
    ) throws Exception {
        ClassLoader loader = Thread.currentThread().getContextClassLoader();
        Class<?> base;
        Class<?> middle;
        Class<?> leaf;
        if (childFirst) {
            leaf = findClassDirectly(loader, DETACHED_THREE_LEAF);
            middle = load(DETACHED_THREE_MIDDLE);
            base = load(DETACHED_THREE_BASE);
        } else {
            base = load(DETACHED_THREE_BASE);
            middle = load(DETACHED_THREE_MIDDLE);
            leaf = load(DETACHED_THREE_LEAF);
        }
        invokeVoid(base, "exercise");
        invokeVoid(middle, "exercise");
        invokeVoid(leaf, "exercise");
        invokeVoid(base, "control");
        invokeVoid(middle, "control");
        invokeVoid(leaf, "control");
        result.put("base_handler_count", P0Counters.detachedThreeBase);
        result.put("middle_handler_count", P0Counters.detachedThreeMiddle);
        result.put("leaf_handler_count", P0Counters.detachedThreeLeaf);
        result.put("base_control_count", P0Counters.detachedThreeBaseControl);
        result.put("middle_control_count", P0Counters.detachedThreeMiddleControl);
        result.put("leaf_control_count", P0Counters.detachedThreeLeafControl);
        result.put("original_body_count", P0Counters.originalBodies);
        result.put(
            "base_handler_names", handlerNames(base, "detachedThreeHandler")
        );
        result.put(
            "middle_handler_names", handlerNames(middle, "detachedThreeHandler")
        );
        result.put(
            "leaf_handler_names", handlerNames(leaf, "detachedThreeHandler")
        );
    }

    private static Class<?> load(String name) throws ClassNotFoundException {
        return Class.forName(
            name, true, Thread.currentThread().getContextClassLoader()
        );
    }

    private static int invokeInt(String className) throws Exception {
        Class<?> type = load(className);
        Object instance = type.getDeclaredConstructor().newInstance();
        return (Integer) type.getMethod("value").invoke(instance);
    }

    private static void invokeVoid(Class<?> type, String method) throws Exception {
        Object instance = type.getDeclaredConstructor().newInstance();
        type.getMethod(method).invoke(instance);
    }

    private static Class<?> findClassDirectly(
        ClassLoader loader, String className
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

    private static List<String> handlerNames(Class<?> type, String fragment) {
        List<String> result = new ArrayList<>();
        for (Method method : type.getDeclaredMethods()) {
            if (method.getName().contains(fragment)) {
                result.add(method.getName());
            }
        }
        Collections.sort(result);
        return result;
    }

    private static Throwable unwrap(Throwable failure) {
        Throwable current = failure;
        while (current instanceof InvocationTargetException
                && ((InvocationTargetException) current).getCause() != null) {
            current = ((InvocationTargetException) current).getCause();
        }
        return current;
    }

    private static String identity(Object value) {
        return value.getClass().getName() + "@"
            + Integer.toHexString(System.identityHashCode(value));
    }

    private static String json(Object value) {
        if (value == null) {
            return "null";
        }
        if (value instanceof Boolean || value instanceof Number) {
            return String.valueOf(value);
        }
        if (value instanceof Map) {
            StringBuilder result = new StringBuilder("{");
            boolean first = true;
            for (Object raw : ((Map<?, ?>) value).entrySet()) {
                Map.Entry<?, ?> entry = (Map.Entry<?, ?>) raw;
                if (!first) {
                    result.append(',');
                }
                first = false;
                result.append(json(String.valueOf(entry.getKey())));
                result.append(':').append(json(entry.getValue()));
            }
            return result.append('}').toString();
        }
        if (value instanceof Iterable) {
            StringBuilder result = new StringBuilder("[");
            boolean first = true;
            for (Object item : (Iterable<?>) value) {
                if (!first) {
                    result.append(',');
                }
                first = false;
                result.append(json(item));
            }
            return result.append(']').toString();
        }
        String text = String.valueOf(value);
        StringBuilder result = new StringBuilder(text.length() + 2).append('"');
        for (int index = 0; index < text.length(); index++) {
            char character = text.charAt(index);
            if (character == '"' || character == '\\') {
                result.append('\\');
            }
            result.append(character);
        }
        return result.append('"').toString();
    }
}
