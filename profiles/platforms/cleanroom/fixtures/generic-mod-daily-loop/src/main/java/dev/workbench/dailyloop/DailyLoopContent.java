package dev.workbench.dailyloop;

import net.minecraft.block.Block;
import net.minecraft.block.material.Material;
import net.minecraft.creativetab.CreativeTabs;
import net.minecraft.item.Item;
import net.minecraft.item.ItemBlock;
import net.minecraft.util.ResourceLocation;
import net.minecraftforge.event.RegistryEvent;
import net.minecraftforge.fml.common.Mod;
import net.minecraftforge.fml.common.eventhandler.SubscribeEvent;

@Mod.EventBusSubscriber(modid = DailyLoopMod.MOD_ID)
public final class DailyLoopContent {
    public static final ResourceLocation PROBE_BLOCK_ID =
        new ResourceLocation(DailyLoopMod.MOD_ID, "probe_block");

    public static final Block PROBE_BLOCK = new Block(Material.ROCK)
        .setHardness(1.5F)
        .setResistance(10.0F)
        .setCreativeTab(CreativeTabs.BUILDING_BLOCKS)
        .setTranslationKey(DailyLoopMod.MOD_ID + ".probe_block")
        .setRegistryName(PROBE_BLOCK_ID);

    private DailyLoopContent() {
    }

    @SubscribeEvent
    public static void registerBlocks(RegistryEvent.Register<Block> event) {
        event.getRegistry().register(PROBE_BLOCK);
    }

    @SubscribeEvent
    public static void registerItems(RegistryEvent.Register<Item> event) {
        event.getRegistry().register(
            new ItemBlock(PROBE_BLOCK)
                .setRegistryName(PROBE_BLOCK_ID)
                .setTranslationKey(DailyLoopMod.MOD_ID + ".probe_block")
        );
    }
}
