package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.intellij.testFramework.fixtures.BasePlatformTestCase;
import java.awt.Component;
import java.awt.Container;
import java.util.ArrayList;
import java.util.List;
import javax.swing.JButton;
import javax.swing.JLabel;
import javax.swing.JList;

/** Real Swing result selection, readiness and freshness guards. */
public final class MaterialResultsPlatformTest extends BasePlatformTestCase {
    private static List<Component> descendants(Component component) {
        var result = new ArrayList<Component>(); result.add(component);
        if (component instanceof Container container) for (var child : container.getComponents()) result.addAll(descendants(child));
        return result;
    }
    private static JButton button(MaterialResultsPanel panel, String name) {
        return (JButton) descendants(panel).stream().filter(c -> c instanceof JButton b && b.getText().equals(name)).findFirst().orElseThrow();
    }
    public void testReadinessUpdateKeepsSelectedEvidenceAndSourceEditsReachActions() {
        var value = JsonParser.parseString("""
            {"diagnostic_id":"revision", "request_id":"request", "native_outcome":"native-failed", "coverage":"complete",
             "detail_state":"preparing", "findings_count":1, "finding_counts":{"error-located":1,"error-unlocated":0,"warning-located":0,"warning-unlocated":0,"information-located":0,"information-unlocated":0},
             "findings":[{"id":"diagnostic-0","side":"candidate","severity":"error","message":"Original native error","location":{"path":"groovy/Test.groovy","start":{"line":4}}}]}
            """).getAsJsonObject();
        var observed = new ArrayList<Boolean>(); var panel = new MaterialResultsPanel(getProject());
        try {
            panel.show(value, () -> true, new MaterialResultsPanel.Owner() {
                public JsonObject read(String action, JsonObject options) { throw new AssertionError("Complete cached group must not be fetched again"); }
                public void open(String action, JsonObject finding, boolean fresh) { observed.add(fresh); }
                public void annotations(JsonObject view) { }
                public void recipes(JsonObject snapshot) { }
                public void actions() { }
            });
            var list = (JList<?>) descendants(panel).stream().filter(c -> "Axiom findings".equals(c.getName())).findFirst().orElseThrow();
            assertEquals(1, list.getModel().getSize()); assertFalse(button(panel, "Recipe Details").isEnabled());
            assertTrue(descendants(panel).stream().anyMatch(c -> c instanceof JLabel label && label.getText().contains("1 errors")));
            button(panel, "Open Source").doClick(); assertEquals(List.of(true), observed);
            var selected = list.getSelectedValue(); value.addProperty("detail_state", "ready"); panel.update(value);
            assertTrue(button(panel, "Recipe Details").isEnabled()); assertSame(selected, list.getSelectedValue());
            panel.invalidateSource(); button(panel, "Open Source").doClick(); assertEquals(List.of(true, false), observed);
            assertTrue(button(panel, "Captured Source").isEnabled());
            panel.clearResult(); assertEquals(0, list.getModel().getSize()); assertFalse(button(panel, "Open Source").isEnabled()); assertFalse(button(panel, "Recipe Details").isEnabled());
        } finally { panel.dispose(); }
    }
}
