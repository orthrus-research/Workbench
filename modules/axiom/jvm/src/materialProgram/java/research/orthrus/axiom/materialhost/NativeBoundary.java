package research.orthrus.axiom.materialhost;

import java.util.ArrayList;
import java.util.List;

/** Coverage is owned outside candidate exception control. One fresh worker only. */
public final class NativeBoundary {
    private static final List<String> GAPS = new ArrayList<>();
    private NativeBoundary() {}
    public static synchronized UnsupportedOperationException unsupported(String key) {
        GAPS.add(key);
        return new UnsupportedOperationException("Unqualified native material dependency: " + key);
    }
    public static synchronized List<String> gaps() { return List.copyOf(GAPS); }

    public static String fluidTooltipTranslation(String key, Object... arguments) {
        throw unsupported("fluid.tooltip-localization");
    }
    public static net.minecraft.item.ItemStack itemLogo() {
        throw unsupported("item.custom-logo");
    }
}
