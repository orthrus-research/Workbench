package dev.workbench.dailyloop;

import net.minecraft.command.CommandBase;
import net.minecraft.command.CommandException;
import net.minecraft.command.ICommandSender;
import net.minecraft.server.MinecraftServer;
import net.minecraft.util.text.TextComponentString;

public final class DailyLoopProbeCommand extends CommandBase {
    @Override
    public String getName() {
        return "workbench_daily_loop_probe";
    }

    @Override
    public String getUsage(ICommandSender sender) {
        return "/" + getName();
    }

    @Override
    public int getRequiredPermissionLevel() {
        return 0;
    }

    @Override
    public void execute(
        MinecraftServer server,
        ICommandSender sender,
        String[] arguments
    ) throws CommandException {
        DailyLoopProbe.assertCommonRegistry();
        sender.sendMessage(new TextComponentString(
            DailyLoopProbe.markerFor(DailyLoopContent.PROBE_BLOCK_ID.toString())
                + " mixin_observations=" + DailyLoopProbe.mixinObservationCount()
        ));
    }
}
