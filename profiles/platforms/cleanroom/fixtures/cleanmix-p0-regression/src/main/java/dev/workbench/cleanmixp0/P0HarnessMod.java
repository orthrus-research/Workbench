package dev.workbench.cleanmixp0;

import net.minecraftforge.fml.common.Mod;
import net.minecraftforge.fml.common.event.FMLPreInitializationEvent;

@Mod(
    modid = P0HarnessMod.MOD_ID,
    name = "Workbench CleanMix P0 Regression Fixture",
    version = "0.1.0",
    acceptableRemoteVersions = "*"
)
public final class P0HarnessMod {

    public static final String MOD_ID = "workbench_cleanmix_p0_regression";
    public static final String FAILURE_PREFIX = "WORKBENCH_CLEANMIX_P0_FAILURE_V1 ";

    @Mod.EventHandler
    public void preInit(FMLPreInitializationEvent event) {
        int exitCode = 0;
        try {
            P0Harness.run();
        } catch (Throwable failure) {
            exitCode = 70;
            System.err.println(
                FAILURE_PREFIX + failure.getClass().getName() + ": "
                    + String.valueOf(failure.getMessage())
            );
            failure.printStackTrace(System.err);
        } finally {
            System.out.flush();
            System.err.flush();
            System.exit(exitCode);
        }
    }
}
