package dev.workbench.worldgenprototype.compat.groovy;

import com.cleanroommc.groovyscript.compat.mods.GroovyPropertyContainer;
import dev.workbench.worldgenprototype.diagnostics.PrototypeDiagnostics;
import dev.workbench.worldgenprototype.world.plan.WorldStudioPlan;
import dev.workbench.worldgenprototype.world.plan.WorldStudioPlans;

/** Configuration-time facade; no Groovy object enters the generation hot path. */
public final class WorldStudioPropertyContainer extends GroovyPropertyContainer {

    private WorldStudioPlan.Builder builder = WorldStudioPlan.defaultsBuilder();

    public synchronized void defaults() {
        builder = WorldStudioPlan.defaultsBuilder();
    }

    public synchronized void profile(String profileId) {
        builder.profile(profileId);
    }

    public synchronized void megaRegionScale(double scale) {
        builder.megaRegionScale(scale);
    }

    public synchronized void field(
            String id,
            double scale,
            int octaves,
            double lacunarity,
            double gain
    ) {
        builder.field(id, scale, octaves, lacunarity, gain);
    }

    public synchronized void clearMegaRegions() {
        builder.clearMegaRegions();
    }

    public synchronized void lithology(
            String id,
            double permeability,
            double erodibility
    ) {
        builder.lithology(id, permeability, erodibility);
    }

    public synchronized void megaRegion(
            String id,
            double weight,
            double baseHeight,
            double relief,
            double temperatureBias,
            double moistureBias,
            String lithology,
            String... biomeIds
    ) {
        builder.megaRegion(
                id,
                weight,
                baseHeight,
                relief,
                temperatureBias,
                moistureBias,
                lithology,
                biomeIds
        );
    }

    public synchronized void watershedGrid(
            int cellSizeBlocks,
            int tileSizeCells,
            int haloCells
    ) {
        builder.watershedGrid(cellSizeBlocks, tileSizeCells, haloCells);
    }

    public synchronized void watershedRunoff(
            double baseRunoff,
            double rainfallScale,
            double permeabilityInfluence,
            double streamDischarge,
            double riverDischarge
    ) {
        builder.watershedRunoff(
                baseRunoff,
                rainfallScale,
                permeabilityInfluence,
                streamDischarge,
                riverDischarge
        );
    }

    public synchronized void watershedChannels(
            double streamHalfWidthBlocks,
            double streamDepthBlocks,
            double riverHalfWidthBlocks,
            double riverDepthBlocks,
            double bankBlendBlocks,
            double lakeMinimumFillDepth
    ) {
        builder.watershedChannels(
                streamHalfWidthBlocks,
                streamDepthBlocks,
                riverHalfWidthBlocks,
                riverDepthBlocks,
                bankBlendBlocks,
                lakeMinimumFillDepth
        );
    }

    public synchronized void carvers(boolean cavesEnabled, boolean ravinesEnabled) {
        builder.carvers(cavesEnabled, ravinesEnabled);
    }

    public synchronized String publish() {
        WorldStudioPlans.Snapshot previous = WorldStudioPlans.current();
        WorldStudioPlans.Snapshot snapshot = WorldStudioPlans.publish(builder.build());
        if (snapshot != previous) {
            PrototypeDiagnostics.planPublished(snapshot);
        }
        return snapshot.hash();
    }

    public String currentPlanHash() {
        return WorldStudioPlans.current().hash();
    }

    public long currentPlanVersion() {
        return WorldStudioPlans.current().version();
    }
}
