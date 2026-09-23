package dev.cleanroommc.workbench.intellij.community;

import org.jetbrains.annotations.NotNull;
import org.jetbrains.annotations.Nullable;

import java.io.IOException;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;

/** No-shell read-only adapter for public diagnosis and capsule inspection routes. */
public final class DiagnoseV1Client {
    private static final Set<String> CAPSULE_READ_OPERATIONS = Set.of("inspect", "verify");

    private DiagnoseV1Client() {
    }

    public static @NotNull List<String> diagnosisArguments(
            @NotNull CoreLaunch launch,
            @NotNull String target,
            @Nullable String stateRoot
    ) {
        String selected = target.equals("latest")
                ? target
                : DiagnoseV1.requireSessionId(target);
        List<String> arguments = new ArrayList<>(List.of("diagnose", selected));
        if (stateRoot != null) {
            arguments.add("--state-root");
            arguments.add(launch.commandPath(stateRoot, "Workbench diagnosis state root"));
        }
        arguments.add("--json");
        return List.copyOf(arguments);
    }

    public static @NotNull List<String> capsuleInspectionArguments(
            @NotNull CoreLaunch launch,
            @NotNull String capsule,
            @NotNull String operation
    ) {
        if (!CAPSULE_READ_OPERATIONS.contains(operation)) {
            throw new IllegalArgumentException(
                    "Workbench capsule read operation is unsupported"
            );
        }
        return List.of(
                "diagnose", "reproduce", operation,
                launch.commandPath(capsule, "Workbench reproduction capsule"),
                "--json"
        );
    }

    public static @NotNull DiagnoseV1.Diagnosis diagnose(
            @NotNull CoreLaunch launch,
            @NotNull String target,
            @Nullable String stateRoot,
            @Nullable String workingDirectory
    ) throws IOException {
        DiagnoseV1.Diagnosis result = DiagnoseV1.parseDiagnosis(CommandProcess.capture(
                launch,
                diagnosisArguments(launch, target, stateRoot),
                DiagnoseV1.MAX_DIAGNOSIS_BYTES,
                120,
                workingDirectory
        ));
        if (result.workSessionId() == null
                || (!target.equals("latest") && !target.equals(result.workSessionId()))) {
            throw new IllegalArgumentException(
                    "Workbench diagnosis changed its selected Work Session identity"
            );
        }
        return result;
    }

    public static @NotNull DiagnoseV1.CapsuleInspection inspectCapsule(
            @NotNull CoreLaunch launch,
            @NotNull String capsule,
            @Nullable String workingDirectory
    ) throws IOException {
        return capsuleRead(launch, capsule, "inspect", workingDirectory);
    }

    public static @NotNull DiagnoseV1.CapsuleInspection verifyCapsule(
            @NotNull CoreLaunch launch,
            @NotNull String capsule,
            @Nullable String workingDirectory
    ) throws IOException {
        return capsuleRead(launch, capsule, "verify", workingDirectory);
    }

    private static @NotNull DiagnoseV1.CapsuleInspection capsuleRead(
            @NotNull CoreLaunch launch,
            @NotNull String capsule,
            @NotNull String operation,
            @Nullable String workingDirectory
    ) throws IOException {
        return DiagnoseV1.parseCapsuleInspection(CommandProcess.capture(
                launch,
                capsuleInspectionArguments(launch, capsule, operation),
                DiagnoseV1.MAX_CAPSULE_INSPECTION_BYTES,
                120,
                workingDirectory
        ));
    }
}
