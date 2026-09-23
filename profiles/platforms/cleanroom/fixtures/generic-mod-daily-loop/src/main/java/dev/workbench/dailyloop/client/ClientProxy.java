package dev.workbench.dailyloop.client;

import dev.workbench.dailyloop.DailyLoopProbe;
import dev.workbench.dailyloop.proxy.CommonProxy;
import net.minecraft.client.resources.I18n;
import org.apache.logging.log4j.LogManager;

public final class ClientProxy extends CommonProxy {
    private static final String LOCALIZATION_KEY =
        "tile.workbench_daily_loop.probe_block.name";

    @Override
    public void assertClientLocalization() {
        String localized = I18n.format(LOCALIZATION_KEY);
        if (LOCALIZATION_KEY.equals(localized)) {
            throw new IllegalStateException(
                "daily-loop client localization is absent: " + LOCALIZATION_KEY
            );
        }
        LogManager.getLogger("workbench_daily_loop").info(
            "{} key={} value={}",
            DailyLoopProbe.CLIENT_READY_MARKER,
            LOCALIZATION_KEY,
            localized
        );
    }
}
