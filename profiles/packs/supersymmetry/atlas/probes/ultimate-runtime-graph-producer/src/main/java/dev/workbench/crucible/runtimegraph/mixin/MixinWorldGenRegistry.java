package dev.workbench.crucible.runtimegraph.mixin;

import dev.workbench.crucible.runtimegraph.worldgen.WorldgenInitializerTrace;

import gregtech.api.worldgen.config.BedrockFluidDepositDefinition;
import gregtech.api.worldgen.config.OreDepositDefinition;
import gregtech.api.worldgen.config.WorldGenRegistry;

import it.unimi.dsi.fastutil.ints.Int2ObjectMap;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@Mixin(value = WorldGenRegistry.class, remap = false)
public abstract class MixinWorldGenRegistry {
    @Inject(method = "reinitializeRegisteredVeins()V", at = @At("HEAD"), require = 1, remap = false)
    private void workbench$beginWorldgenEpoch(CallbackInfo callback) {
        WorldgenInitializerTrace.global().beginEpoch(this, WorldGenRegistry.INSTANCE);
    }

    @Inject(method = "reinitializeRegisteredVeins()V", at = @At("RETURN"), require = 1, remap = false)
    private void workbench$sealWorldgenEpoch(CallbackInfo callback) {
        WorldgenInitializerTrace.global().sealEpoch(
            this,
            WorldGenRegistry.INSTANCE,
            ores(),
            fluids(),
            dimensions()
        );
    }

    private static List<WorldgenInitializerTrace.DefinitionRef> ores() {
        List<WorldgenInitializerTrace.DefinitionRef> result =
            new ArrayList<WorldgenInitializerTrace.DefinitionRef>();
        for (OreDepositDefinition definition : WorldGenRegistry.getOreDeposits()) {
            result.add(new WorldgenInitializerTrace.DefinitionRef(
                definition, definition.getDepositName()
            ));
        }
        return result;
    }

    private static List<WorldgenInitializerTrace.DefinitionRef> fluids() {
        List<WorldgenInitializerTrace.DefinitionRef> result =
            new ArrayList<WorldgenInitializerTrace.DefinitionRef>();
        for (BedrockFluidDepositDefinition definition : WorldGenRegistry.getBedrockVeinDeposits()) {
            result.add(new WorldgenInitializerTrace.DefinitionRef(
                definition, definition.getDepositName()
            ));
        }
        return result;
    }

    private static Map<Integer, String> dimensions() {
        Map<Integer, String> result = new LinkedHashMap<Integer, String>();
        for (Int2ObjectMap.Entry<String> entry
            : WorldGenRegistry.getNamedDimensions().int2ObjectEntrySet()) {
            if (result.put(Integer.valueOf(entry.getIntKey()), entry.getValue()) != null) {
                throw new IllegalStateException("duplicate GT named dimension ID");
            }
        }
        return result;
    }
}
