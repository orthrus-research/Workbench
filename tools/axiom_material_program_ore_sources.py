"""Native GT ore forms for the original-identity material program context.

This selects source members and owners, never reconstructs material/stone state
or invents generation decisions. Susy addon producers remain separately scoped.
"""
import json
import re

import axiom_material_ore_sources as ores
import axiom_material_item_sources as members

GT = members.GT
PATHS = {name:path for name,(repo,path) in ores.PATHS.items() if repo=='gtceu'}
HOST = 'research.orthrus.axiom.materialhost'
IMPORTS = '''
import gregtech.api.GregTechAPI;
import gregtech.api.unification.material.Material;
import gregtech.api.unification.material.Materials;
import gregtech.api.unification.material.info.MaterialFlags;
import gregtech.api.unification.material.properties.PropertyKey;
import gregtech.api.unification.material.registry.MaterialRegistry;
import gregtech.api.unification.ore.*;
import gregtech.api.unification.OreDictUnifier;
import gregtech.api.util.GTUtility;
import gregtech.api.util.IBlockOre;
import gregtech.common.blocks.*;
import research.orthrus.axiom.materialhost.NativeOreDeclarations;
import research.orthrus.axiom.materialhost.NativeOreHostBlocks;
'''


def bind(name, text, mapping, annotations, registry_binding):
    text=annotations(text)
    text=re.sub(r'^import (?:gregtech\.client|gregtech\.integration\.jei|net\.minecraftforge\.client)\.[^;]+;\n', '', text, flags=re.M)
    for unused in ('gregtech.api.util.Mods','gregtech.common.blocks.MetaBlocks'):
        text=text.replace('import '+unused+';','')
    text=text.replace('GregTechAPI.oreBlockTable','NativeOreDeclarations.oreBlockTable')
    text=text.replace('MetaBlocks.ORES','NativeOreDeclarations.ORES').replace('MetaBlocks.STONE_BLOCKS','NativeOreHostBlocks.STONE_BLOCKS')
    text=re.sub(r'new Failure\("incomplete", "ore\.[^"\n]+", "([^"\n]+)"\)',
                lambda m: HOST+'.NativeBoundary.unsupported("ore.'+m[1]+'")',text)
    # These classes import Minecraft Material, not the GT material with that name.
    imports=IMPORTS.replace('import gregtech.api.unification.material.Material;','') if name=='VariantBlock' else IMPORTS
    end=text.index(';')+1
    text=text[:end]+'\n'+imports+text[end:]
    text=registry_binding(text,mapping,'STONE_TYPE_REGISTRY')
    return ores.bind_native_symbols(name,text,mapping)


def shell(name,body):
    return 'package '+HOST+';\n'+IMPORTS+'''
import java.util.*;
import java.util.stream.*;
import java.util.function.Function;
import net.minecraft.block.Block;
import net.minecraft.block.state.IBlockState;
import net.minecraft.item.*;
import net.minecraft.util.ResourceLocation;
import net.minecraftforge.registries.IForgeRegistry;
import org.apache.commons.lang3.ArrayUtils;
public final class '''+name+' {\n'+body+'\n}\n'


def assemble(originals,mapping,annotations,registry_binding):
    result={}
    for name,path in PATHS.items():
        text=ores.project(name,originals[path[len(GT):-5]])
        result['gregtech.'+path[len(GT):-5].replace('/','.')]=bind(name,text,mapping,annotations,registry_binding)
    proxy=originals['common/CommonProxy']; meta=originals['common/blocks/MetaBlocks']; api=originals['api/GregTechAPI']
    body=[members.field(meta,'public static final List<BlockOre> ORES'),
          members.field(api,'public static final Map<Material, Map<StoneType, IBlockOre>> oreBlockTable')]
    body += [members.block(proxy,m) for m in ('private static void createOreBlock(Material material)',
        'private static <T> T[] copyNotNull(', 'private static void createOreBlock(Material material, StoneType[]',
        'private static <T extends Block> ItemBlock createItemBlock(')]
    callback=members.block(proxy,'public static void registerBlocks(')
    condition=members.block(callback,'if (material.hasProperty(PropertyKey.ORE)')
    body.append('static void generate() {\nStoneType.init();\nfor (MaterialRegistry materialRegistry : GregTechAPI.materialManager.getRegistries()) {\nfor (Material material : materialRegistry) {\n'+condition+'\n}}\n}')
    registration='for (BlockOre block : ORES) registry.register(block);'
    if callback.count(registration)!=1:raise ValueError('Native ore registration anchor differs')
    body.append('static void registerBlocks(IForgeRegistry<Block> registry) { '+registration+' }')
    body.append('static void registerItems(IForgeRegistry<Item> registry) {\n'+members.block(
        members.block(proxy,'public static void registerItems('),'for (BlockOre block : ORES)')+'\n}')
    body.append('static void registerOres() {\n'+members.block(
        members.block(meta,'public static void registerOreDict()'),'for (BlockOre blockOre : ORES)')+'\n}')
    result[HOST+'.NativeOreDeclarations']=bind('NativeOreDeclarations',shell('NativeOreDeclarations','\n'.join(body)),mapping,annotations,registry_binding)
    hosts=members.field(meta,'public static final EnumMap<StoneVariantBlock.StoneVariant, StoneVariantBlock> STONE_BLOCKS')
    result[HOST+'.NativeOreHostBlocks']=shell('NativeOreHostBlocks',hosts)
    lookup=members.block(originals['api/worldgen/config/OreConfigUtils'],'public static Map<StoneType, IBlockState> getOreForMaterial(')
    text='package gregtech.api.worldgen.config;\nimport java.util.*;\nimport java.util.stream.*;\nimport net.minecraft.block.state.IBlockState;\npublic class OreConfigUtils {\n'+lookup+'\n}\n'
    result['gregtech.api.worldgen.config.OreConfigUtils']=bind('OreConfigUtils',text,mapping,annotations,registry_binding)
    rules=[line for line in originals['resource:src/main/resources/gregtech_at.cfg'].splitlines()
           if line.split('#')[0].strip().split()[1:2]==['net.minecraft.block.Block']]
    if len(rules)!=2:raise ValueError('GT Block access-rule closure differs')
    result[HOST+'.OreAccessRules']='package '+HOST+';\npublic final class OreAccessRules { public static final String RULES = '+json.dumps('\n'.join(rules)+'\n')+'; }\n'
    return result
