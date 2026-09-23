package dev.cleanroommc.workbench.intellij.community;

import org.jetbrains.annotations.NotNull;
import org.jetbrains.annotations.Nullable;

import java.io.IOException;
import java.util.List;

/** No-shell client for the additive public Workspace Home V2 response. */
final class WorkspaceHomeV2Client {
    private WorkspaceHomeV2Client() {
    }

    static @NotNull List<String> arguments(
            @NotNull CoreLaunch launch, @NotNull String workspace
    ) {
        return List.of("open", launch.commandPath(workspace, "workspace"), "--json");
    }

    static @NotNull WorkspaceHomeV2 load(
            @NotNull CoreLaunch launch,
            @NotNull String workspace,
            @Nullable String workingDirectory
    ) throws IOException {
        String output = CommandProcess.capture(
                launch,
                arguments(launch, workspace),
                WorkspaceHomeV2.MAX_BYTES,
                120,
                workingDirectory
        );
        return WorkspaceHomeV2.parse(output);
    }
}
