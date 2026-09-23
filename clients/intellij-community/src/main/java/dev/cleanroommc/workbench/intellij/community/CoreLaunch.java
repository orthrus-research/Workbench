package dev.cleanroommc.workbench.intellij.community;

import org.jetbrains.annotations.NotNull;
import org.jetbrains.annotations.Nullable;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Exact native or Windows-hosted WSL launch for one installed core. */
public record CoreLaunch(
        @NotNull String configured,
        @NotNull String executable,
        @NotNull List<String> prefixArguments,
        @NotNull String host,
        @Nullable String distribution
) {
    /** Adapter facts consumed by the external non-evidence parity probe. */
    public record InstalledWindowsWslMappingV1(
            @NotNull String launchKind,
            @NotNull String distribution,
            @NotNull String workspaceInput,
            @NotNull String workspaceArgument
    ) {
    }

    private static final Pattern WSL_PATH = Pattern.compile(
            "^//(?:wsl(?:\\.localhost)?|wsl\\$)/([^/]+)(/.*)$",
            Pattern.CASE_INSENSITIVE
    );
    private static final Pattern DISTRIBUTION = Pattern.compile("^[A-Za-z0-9._-]+$");
    private static final Pattern WINDOWS_ROOT = Pattern.compile("^[A-Za-z]:[\\\\/].*$");
    private static final int MAXIMUM_PATH_BYTES = 32 * 1024;
    private static final String ALLOW_AMBIGUOUS_COMMANDS =
            "jdk.lang.Process.allowAmbiguousCommands";

    public CoreLaunch {
        prefixArguments = List.copyOf(prefixArguments);
    }

    public static @NotNull CoreLaunch resolve(@NotNull String configured) {
        boolean windows = System.getProperty("os.name", "")
                .toLowerCase(Locale.ROOT).startsWith("windows");
        if (windows) {
            requireSafeWindowsProcessArguments();
        }
        String systemRoot = System.getenv("SystemRoot");
        if (systemRoot == null) {
            systemRoot = System.getenv("WINDIR");
        }
        return resolve(configured, windows, systemRoot);
    }

    static @NotNull CoreLaunch resolve(
            @NotNull String configured,
            boolean windows,
            @Nullable String systemRoot
    ) {
        String selected = configuredValue(configured, "core executable");
        WslPath wsl = windows ? parseWslPath(selected) : null;
        if (windows && wsl == null && looksLikeWslPath(selected)) {
            throw new IllegalArgumentException("configured WSL core path is invalid");
        }
        if (wsl == null) {
            return new CoreLaunch(selected, selected, List.of(), "native", null);
        }
        String root = configuredValue(systemRoot, "Windows system root").replace('/', '\\');
        if (!WINDOWS_ROOT.matcher(root).matches()) {
            throw new IllegalArgumentException("Windows system root is not one absolute local path");
        }
        while (root.endsWith("\\")) {
            root = root.substring(0, root.length() - 1);
        }
        if (root.length() <= 3) {
            throw new IllegalArgumentException(
                    "Windows system root is not one normalized absolute local path"
            );
        }
        String[] rootParts = root.substring(3).split("\\\\", -1);
        for (String part : rootParts) {
            if (part.isEmpty() || part.equals(".") || part.equals("..")) {
                throw new IllegalArgumentException(
                        "Windows system root is not one normalized absolute local path"
                );
            }
        }
        String parent = wsl.linuxPath().substring(0, wsl.linuxPath().lastIndexOf('/'));
        if (parent.isEmpty()) {
            parent = "/";
        }
        return new CoreLaunch(
                selected,
                root + "\\System32\\wsl.exe",
                List.of(
                        "--distribution", wsl.distribution(),
                        "--cd", parent,
                        "--exec", wsl.linuxPath()
                ),
                "windows-wsl",
                wsl.distribution()
        );
    }

    public @NotNull List<String> command(@NotNull List<String> arguments) {
        List<String> command = new ArrayList<>(1 + prefixArguments.size() + arguments.size());
        command.add(executable);
        command.addAll(prefixArguments);
        for (String value : arguments) {
            command.add(exactValue(value, "core argument", true));
        }
        return List.copyOf(command);
    }

    /**
     * Require OpenJDK's WIN32_SAFE command-line encoder before any Workbench child process.
     * The legacy encoder lets wsl.exe consume embedded JSON quotes as command-line syntax.
     * This process-wide change is intentional: later Windows child launches should retain
     * the safer encoder rather than reintroducing ambiguous command-line parsing.
     */
    static void requireSafeWindowsProcessArguments() {
        try {
            System.setProperty(ALLOW_AMBIGUOUS_COMMANDS, "false");
            if (!"false".equalsIgnoreCase(System.getProperty(ALLOW_AMBIGUOUS_COMMANDS))) {
                throw new IllegalStateException(
                        "safe Windows process argument encoding was not retained"
                );
            }
        } catch (SecurityException error) {
            throw new IllegalStateException(
                    "safe Windows process argument encoding cannot be configured",
                    error
            );
        }
    }

    public @NotNull String commandPath(@NotNull String value, @NotNull String label) {
        String selected = exactValue(value, label, false);
        if (host.equals("native")) {
            return selected;
        }
        WslPath parsed = parseWslPath(selected);
        if (parsed == null || !parsed.distribution().equalsIgnoreCase(distribution)) {
            throw new IllegalArgumentException(
                    label + " does not belong to the configured WSL distribution"
            );
        }
        return parsed.linuxPath();
    }

    public @NotNull InstalledWindowsWslMappingV1 installedWindowsWslMappingV1(
            @NotNull String workspacePath
    ) {
        if (!host.equals("windows-wsl") || distribution == null) {
            throw new IllegalArgumentException(
                    "installed parity host observation requires the Windows/WSL adapter"
            );
        }
        return new InstalledWindowsWslMappingV1(
                "windows-wsl",
                distribution,
                workspacePath,
                commandPath(workspacePath, "installed parity workspace")
        );
    }

    static @Nullable WslPath parseWslPath(@NotNull String value) {
        if (value.indexOf('\0') >= 0 || value.getBytes(java.nio.charset.StandardCharsets.UTF_8).length > MAXIMUM_PATH_BYTES) {
            return null;
        }
        Matcher match = WSL_PATH.matcher(value.replace('\\', '/'));
        if (!match.matches() || !DISTRIBUTION.matcher(match.group(1)).matches()) {
            return null;
        }
        return new WslPath(match.group(1), linuxPath(match.group(2), "WSL path"));
    }

    private static boolean looksLikeWslPath(@NotNull String value) {
        return value.replace('\\', '/').toLowerCase(Locale.ROOT)
                .matches("^//(?:wsl(?:\\.localhost)?|wsl\\$)/.*$");
    }

    private static @NotNull String linuxPath(@NotNull String value, @NotNull String label) {
        String path = exactValue(value, label, false).replace('\\', '/');
        if (!path.startsWith("/") || path.startsWith("//") || path.endsWith("/")) {
            throw new IllegalArgumentException(label + " is not one normalized absolute Linux path");
        }
        String[] parts = path.split("/", -1);
        for (int index = 1; index < parts.length; index++) {
            if (parts[index].isEmpty() || parts[index].equals(".") || parts[index].equals("..")) {
                throw new IllegalArgumentException(label + " is not one normalized absolute Linux path");
            }
        }
        return path;
    }

    private static @NotNull String configuredValue(@Nullable String value, @NotNull String label) {
        if (value == null || value.isBlank() || value.indexOf('\0') >= 0
                || value.getBytes(java.nio.charset.StandardCharsets.UTF_8).length > MAXIMUM_PATH_BYTES) {
            throw new IllegalArgumentException(label + " is invalid");
        }
        return value.trim();
    }

    private static @NotNull String exactValue(
            @Nullable String value,
            @NotNull String label,
            boolean allowEmpty
    ) {
        if (value == null || (!allowEmpty && value.isEmpty()) || value.indexOf('\0') >= 0
                || value.getBytes(java.nio.charset.StandardCharsets.UTF_8).length > MAXIMUM_PATH_BYTES) {
            throw new IllegalArgumentException(label + " is invalid");
        }
        return value;
    }

    record WslPath(@NotNull String distribution, @NotNull String linuxPath) {
    }
}
