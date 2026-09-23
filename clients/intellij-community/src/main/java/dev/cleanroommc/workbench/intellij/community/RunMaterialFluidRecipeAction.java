package dev.cleanroommc.workbench.intellij.community;

import com.intellij.ide.util.PropertiesComponent;
import com.intellij.openapi.actionSystem.ActionUpdateThread;
import com.intellij.openapi.actionSystem.AnAction;
import com.intellij.openapi.actionSystem.AnActionEvent;
import com.intellij.openapi.application.ApplicationManager;
import com.intellij.openapi.progress.ProgressIndicator;
import com.intellij.openapi.progress.Task;
import com.intellij.openapi.project.Project;
import com.intellij.openapi.ui.DialogWrapper;
import com.intellij.openapi.ui.Messages;
import com.intellij.openapi.ui.ValidationInfo;
import org.jetbrains.annotations.NotNull;
import org.jetbrains.annotations.Nullable;

import javax.swing.JComboBox;
import javax.swing.JComponent;
import javax.swing.JLabel;
import javax.swing.JPanel;
import javax.swing.JScrollPane;
import javax.swing.JTextArea;
import javax.swing.JTextField;
import java.awt.GridBagConstraints;
import java.awt.GridBagLayout;
import java.awt.Insets;
import java.util.Arrays;
import java.util.List;
import java.util.regex.Pattern;

/** Runs one exact reviewed plan through the installed core in a disposable client. */
public final class RunMaterialFluidRecipeAction extends AnAction {
    private static final String PREFIX = "workbench.feature.materialFluidRecipe.";
    private static final Pattern PLAN_ID = Pattern.compile(
            "workbench-developer-material-fluid-recipe-plan:sha256:[0-9a-f]{64}"
    );

    @Override
    public void actionPerformed(@NotNull AnActionEvent event) {
        Project project = event.getProject();
        if (project == null) {
            return;
        }
        RunDialog dialog = new RunDialog(project, PropertiesComponent.getInstance(project));
        if (!dialog.showAndGet()) {
            return;
        }
        Configuration configuration = dialog.configuration();
        if (Messages.showYesNoDialog(
                project,
                "Run exact reviewed plan\n\n" + configuration.planId()
                        + "\n\nin a disposable Cleanroom client? The source checkout is input-only.",
                "Run Reviewed Material/Fluid Recipe",
                "Run Exact Plan",
                "Cancel",
                Messages.getWarningIcon()
        ) != Messages.YES) {
            return;
        }
        dialog.retain(configuration);
        String executable = CoreLocation.discover(project);
        new Task.Backgroundable(project, "Running reviewed Workbench feature in Cleanroom", false) {
            @Override
            public void run(@NotNull ProgressIndicator indicator) {
                try {
                    DeveloperFeatureClient.Result result = DeveloperFeatureClient.run(
                            executable,
                            configuration.planId(),
                            configuration.options(),
                            project.getBasePath()
                    );
                    ApplicationManager.getApplication().invokeLater(
                            () -> WorkbenchNotifications.developerFeatureFinished(project, result)
                    );
                } catch (Exception error) {
                    ApplicationManager.getApplication().invokeLater(
                            () -> WorkbenchNotifications.developerFeatureFailed(project, error)
                    );
                }
            }
        }.queue();
    }

    @Override
    public void update(@NotNull AnActionEvent event) {
        event.getPresentation().setEnabledAndVisible(event.getProject() != null);
    }

    @Override
    public @NotNull ActionUpdateThread getActionUpdateThread() {
        return ActionUpdateThread.BGT;
    }

    private record Configuration(
            String planId,
            DeveloperFeatureClient.Options options,
            String seedText
    ) {
    }

    private static final class RunDialog extends DialogWrapper {
        private final PropertiesComponent properties;
        private final JTextField plan = new JTextField(72);
        private final JComboBox<String> launcher = new JComboBox<>(new String[]{"prism", "multimc"});
        private final JTextField launcherExecutable = new JTextField(72);
        private final JTextField launcherRoot = new JTextField(72);
        private final JTextField launcherJava = new JTextField(72);
        private final JTextField launcherJavaState = new JTextField(72);
        private final JTextField packwizExecutable = new JTextField(72);
        private final JTextArea seedRoots = new JTextArea(3, 72);
        private final JTextField stateRoot = new JTextField(72);
        private final JTextField timeout = new JTextField(8);
        private final JTextField attachTimeout = new JTextField(8);
        private final JTextField sessionTimeout = new JTextField(8);

        RunDialog(Project project, PropertiesComponent properties) {
            super(project, true);
            this.properties = properties;
            setTitle("Run Reviewed Material/Fluid Recipe");
            plan.setText(value("planId", ""));
            launcher.setSelectedItem(value("launcher", "prism"));
            launcherExecutable.setText(value("launcherExecutable", ""));
            launcherRoot.setText(value("launcherRoot", ""));
            launcherJava.setText(value("launcherJava", ""));
            launcherJavaState.setText(value("launcherJavaState", ""));
            packwizExecutable.setText(value("packwizExecutable", ""));
            seedRoots.setText(value("seedRoots", ""));
            stateRoot.setText(value("stateRoot", ""));
            timeout.setText(value("timeoutSeconds", "600"));
            attachTimeout.setText(value("attachTimeoutSeconds", "120"));
            sessionTimeout.setText(value("sessionTimeoutSeconds", "21600"));
            init();
        }

        @Override
        protected @Nullable JComponent createCenterPanel() {
            JPanel panel = new JPanel(new GridBagLayout());
            int row = 0;
            GridBagConstraints note = new GridBagConstraints();
            note.gridx = 0;
            note.gridy = row++;
            note.gridwidth = 2;
            note.anchor = GridBagConstraints.WEST;
            note.insets = new Insets(0, 0, 8, 0);
            panel.add(new JLabel(
                    "For a Windows-hosted WSL core, enter paths as matching \\\\wsl.localhost\\DISTRO\\… paths."
            ), note);
            row = add(panel, row, "Reviewed plan ID", plan);
            row = add(panel, row, "Launcher", launcher);
            row = add(panel, row, "Launcher executable", launcherExecutable);
            row = add(panel, row, "Launcher data root", launcherRoot);
            row = add(panel, row, "Java (optional)", launcherJava);
            row = add(panel, row, "Java state root (optional)", launcherJavaState);
            row = add(panel, row, "Packwiz executable (optional)", packwizExecutable);
            row = add(panel, row, "Seed roots (one per line)", new JScrollPane(seedRoots));
            row = add(panel, row, "Feature state root (optional)", stateRoot);
            row = add(panel, row, "FML timeout seconds", timeout);
            row = add(panel, row, "Attach timeout seconds", attachTimeout);
            add(panel, row, "Session timeout seconds", sessionTimeout);
            return panel;
        }

        @Override
        protected @Nullable ValidationInfo doValidate() {
            try {
                configuration();
                return null;
            } catch (IllegalArgumentException error) {
                return new ValidationInfo(error.getMessage(), plan);
            }
        }

        Configuration configuration() {
            String selectedPlan = exact(plan.getText(), "reviewed plan ID");
            if (!PLAN_ID.matcher(selectedPlan).matches()) {
                throw new IllegalArgumentException("Enter one exact reviewed material/fluid recipe plan ID.");
            }
            String launcherName = (String) launcher.getSelectedItem();
            String seeds = seedRoots.getText().trim();
            List<String> selectedSeeds = seeds.isEmpty()
                    ? List.of()
                    : Arrays.stream(seeds.split("\\R"))
                    .map(String::trim)
                    .filter(value -> !value.isEmpty())
                    .map(value -> exact(value, "seed root"))
                    .toList();
            DeveloperFeatureClient.Options options = new DeveloperFeatureClient.Options(
                    launcherName == null ? "prism" : launcherName,
                    exact(launcherExecutable.getText(), "launcher executable"),
                    exact(launcherRoot.getText(), "launcher data root"),
                    optional(launcherJava.getText()),
                    optional(launcherJavaState.getText()),
                    optional(packwizExecutable.getText()),
                    selectedSeeds,
                    optional(stateRoot.getText()),
                    positive(timeout.getText(), "FML timeout"),
                    positive(attachTimeout.getText(), "attach timeout"),
                    positive(sessionTimeout.getText(), "session timeout")
            );
            return new Configuration(selectedPlan, options, seeds);
        }

        void retain(Configuration configuration) {
            DeveloperFeatureClient.Options options = configuration.options();
            set("planId", configuration.planId());
            set("launcher", options.launcher());
            set("launcherExecutable", options.launcherExecutable());
            set("launcherRoot", options.launcherRoot());
            set("launcherJava", options.launcherJava());
            set("launcherJavaState", options.launcherJavaState());
            set("packwizExecutable", options.packwizExecutable());
            set("seedRoots", configuration.seedText());
            set("stateRoot", options.stateRoot());
            set("timeoutSeconds", Double.toString(options.timeoutSeconds()));
            set("attachTimeoutSeconds", Double.toString(options.attachTimeoutSeconds()));
            set("sessionTimeoutSeconds", Double.toString(options.sessionTimeoutSeconds()));
        }

        private String value(String name, String fallback) {
            return properties.getValue(PREFIX + name, fallback);
        }

        private void set(String name, @Nullable String value) {
            properties.setValue(PREFIX + name, value == null ? "" : value);
        }

        private static int add(JPanel panel, int row, String label, JComponent component) {
            GridBagConstraints left = new GridBagConstraints();
            left.gridx = 0;
            left.gridy = row;
            left.anchor = GridBagConstraints.NORTHWEST;
            left.insets = new Insets(4, 0, 4, 12);
            panel.add(new JLabel(label), left);
            GridBagConstraints right = new GridBagConstraints();
            right.gridx = 1;
            right.gridy = row;
            right.weightx = 1;
            right.fill = GridBagConstraints.HORIZONTAL;
            right.insets = new Insets(4, 0, 4, 0);
            panel.add(component, right);
            return row + 1;
        }

        private static String exact(String value, String label) {
            String selected = value == null ? "" : value.trim();
            if (selected.isEmpty() || selected.indexOf('\0') >= 0) {
                throw new IllegalArgumentException(label + " is required and must contain no NUL.");
            }
            return selected;
        }

        private static @Nullable String optional(String value) {
            if (value == null || value.isBlank()) {
                return null;
            }
            return exact(value, "path");
        }

        private static double positive(String value, String label) {
            try {
                double result = Double.parseDouble(value.trim());
                if (!Double.isFinite(result) || result <= 0) {
                    throw new NumberFormatException();
                }
                return result;
            } catch (RuntimeException error) {
                throw new IllegalArgumentException(label + " must be a positive number.", error);
            }
        }
    }
}
