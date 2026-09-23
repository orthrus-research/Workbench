package com.cleanroommc.workbench.cleanmixhandler;

import com.cleanroommc.common.CleanroomEnvironment;
import dev.workbench.cleanmixhandler.HarnessMain;
import java.io.File;
import java.util.HashMap;
import java.util.List;
import net.minecraft.launchwrapper.ITweaker;
import net.minecraft.launchwrapper.Launch;
import net.minecraft.launchwrapper.LaunchClassLoader;
import net.minecraftforge.fml.common.asm.FMLSanityChecker;
import net.minecraftforge.fml.relauncher.Side;
import net.minecraftforge.fml.relauncher.libraries.LibraryManager;

public final class HarnessTweaker implements ITweaker {

    public static final String CLEANMIX_ARTIFACT_PROPERTY =
        "workbench.cleanmix.handler.cleanmix_artifact";

    public HarnessTweaker() {
        String artifactValue = System.getProperty(CLEANMIX_ARTIFACT_PROPERTY);
        if (artifactValue == null || artifactValue.isEmpty()) {
            throw new IllegalArgumentException("CleanMix artifact path is required");
        }
        File artifact = new File(artifactValue).getAbsoluteFile();
        if (!artifact.isFile()) {
            throw new IllegalArgumentException(
                "CleanMix artifact is not a file: " + artifact
            );
        }
        FMLSanityChecker.fmlLocation = artifact;
        if (!Launch.blackboard.containsKey("forgeLaunchArgs")) {
            Launch.blackboard.put(
                "forgeLaunchArgs",
                new HashMap<String, String>()
            );
        }
        LibraryManager.setup(Launch.minecraftHome);
        CleanroomEnvironment.setSide(Side.SERVER);
    }

    @Override
    public void acceptOptions(
        List<String> arguments,
        File gameDirectory,
        File assetsDirectory,
        String profile
    ) {
    }

    @Override
    public void injectIntoClassLoader(LaunchClassLoader classLoader) {
    }

    @Override
    public String getLaunchTarget() {
        return HarnessMain.class.getName();
    }

    @Override
    public String[] getLaunchArguments() {
        return new String[0];
    }
}
