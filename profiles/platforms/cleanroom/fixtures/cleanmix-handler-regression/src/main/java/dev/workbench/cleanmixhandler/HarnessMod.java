package dev.workbench.cleanmixhandler;

import net.minecraftforge.fml.common.Mod;
import net.minecraftforge.fml.common.event.FMLPreInitializationEvent;

@Mod(
    modid = HarnessMod.MOD_ID,
    name = "Workbench CleanMix Handler Regression Fixture",
    version = "0.1.0",
    acceptableRemoteVersions = "*"
)
public final class HarnessMod {

    public static final String MOD_ID = "workbench_cleanmix_handler_regression";
    public static final String FAILURE_PREFIX =
        "WORKBENCH_CLEANMIX_HANDLER_FAILURE_V1 ";

    @Mod.EventHandler
    public void preInit(FMLPreInitializationEvent event) {
        int exitCode = 0;
        try {
            HarnessMain.main(new String[0]);
        } catch (Throwable failure) {
            exitCode = 70;
            System.err.println(
                FAILURE_PREFIX
                    + failure.getClass().getName()
                    + ": "
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
