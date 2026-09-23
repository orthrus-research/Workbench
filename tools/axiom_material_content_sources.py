"""Original-identity prefix item family for native material-program execution.

Share member extraction with the earlier item qualification, not its relocated
runtime or material carriers. Source-owned decisions and original registry
objects remain in the same class space as the developer's Groovy program.
"""
import re

import axiom_item_sources as unifier
import axiom_material_item_sources as items
import axiom_material_block_sources as blocks
import axiom_material_program_ore_sources as ores

GT = 'src/main/java/gregtech/'
CLASSES = tuple(p[len(GT):-5] for p in (
    *items.PATHS.values(), *blocks.PATHS.values(), *ores.PATHS.values(), *(p for n, p in unifier.PATHS.items() if n != 'ItemConstants')))
SUPPORT = ('common/items/MetaItems', 'common/CommonProxy', 'common/blocks/MetaBlocks',
           'api/recipes/ModHandler', 'api/items/toolitem/ToolClasses', 'api/worldgen/config/OreConfigUtils')
HOST = 'research.orthrus.axiom.materialhost'

IMPORTS = '''
import gregtech.api.GregTechAPI;
import gregtech.api.GTValues;
import gregtech.api.util.GTLog;
import gregtech.api.unification.OreDictUnifier;
import gregtech.api.unification.material.Material;
import gregtech.api.unification.material.Materials;
import gregtech.api.unification.material.registry.MaterialRegistry;
import gregtech.api.unification.material.properties.*;
import gregtech.api.unification.ore.OrePrefix;
import gregtech.api.unification.stack.*;
import gregtech.api.items.metaitem.*;
import gregtech.api.items.metaitem.stats.*;
import gregtech.api.capability.impl.CombinedCapabilityProvider;
'''


def bind_unifier(text, mappings):
    unifier.validate_bindings(mappings)
    for method, srg in unifier.METHODS.items():
        if method == 'copy':
            # MaterialStack.copy must remain the original GT operation.
            for before in ('event.getOre().copy()', 'itemStacks.get(0).copy()', 'ItemStack::copy'):
                text = text.replace(before, before.replace('copy', srg))
        elif method == 'isEmpty':
            text = text.replace('itemStack.isEmpty()', 'itemStack.' + srg + '()')
        else:
            text = text.replace('.' + method + '()', '.' + srg + '()')
    return bind_registry(text, mappings).replace('ItemStack.EMPTY', 'ItemStack.field_190927_a').replace(
        'this.item.getTranslationKey(toItemStack())', 'this.item.func_77667_c(toItemStack())')


def bind_registry(text, mappings, receiver='registry'):
    _, methods = unifier.native_symbols(mappings)
    for name, owner in (('getObject', 'RegistrySimple'), ('getObjectById', 'RegistryNamespaced'),
                        ('getIDForObject', 'RegistryNamespaced')):
        choices = methods['net/minecraft/util/registry/' + owner, name]
        if len(choices) != 1:
            raise ValueError('Ambiguous material registry native binding: ' + name)
        text = text.replace(receiver + '.' + name + '(', receiver + '.' + next(iter(choices)) + '(')
    return text


def assemble(originals, mappings, annotations):
    result = {}
    for name, path in items.PATHS.items():
        if name in ('MetaItem', 'StandardMetaItem'):
            # Preserve complete selected binary owners and nested classes. Both
            # custom and generated prefix items share the original item list,
            # component dispatch and value objects, not a reduced carrier.
            continue
        original = originals[path[len(GT):-5]]
        if name == 'MetaPrefixItem':
            package = path[len(GT):-5].rsplit('/', 1)[0].replace('/', '.')
            text = 'package gregtech.' + package + ';\n' + items.IMPORTS + IMPORTS
            text += items.project_prefix(original)
            text = re.sub(r'new Failure\("incomplete", "item.behavior", "Unqualified material item method: (\w+)"\)',
                          lambda m: HOST + '.NativeBoundary.unsupported("item.behavior.' + m[1] + '")', text)
        else:
            text = original
        result['gregtech.' + path[len(GT):-5].replace('/', '.')] = bind_registry(
            items.bind_native_symbols(annotations(text), mappings), mappings)
    for name, path in unifier.PATHS.items():
        if name != 'ItemConstants':
            result['gregtech.' + path[len(GT):-5].replace('/', '.')] = bind_unifier(
                annotations(originals[path[len(GT):-5]]), mappings)

    # Explicit bounded callbacks, not a claim to retain all of MetaItems.init or
    # CommonProxy.registerItems. The original shared item loop includes declared
    # StandardMetaItems; tools, addon producers and recipes remain excluded.
    source = originals['common/items/MetaItems']
    prefix_field = items.field(source, 'private static final List<OrePrefix> orePrefixes')
    prefix_block = items.block(source, '    static {')
    construction = items.block(source, '        for (OrePrefix prefix : orePrefixes)')
    registration = items.block(originals['common/CommonProxy'], '        for (MetaItem<?> item : MetaItems.ITEMS)')
    ore = items.block(items.block(source, 'public static void registerOreDict()'), '        for (MetaItem<?> item : ITEMS)')
    text = 'package ' + HOST + ';\n' + IMPORTS + '''
import java.util.*;
import com.google.common.base.CaseFormat;
import net.minecraft.item.Item;
import net.minecraftforge.registries.IForgeRegistry;
import gregtech.api.items.materialitem.MetaPrefixItem;
final class NativePrefixItemDeclarations {
'''
    text += prefix_field + '\n' + prefix_block
    text += '\nstatic void construct() {\n' + construction + '\n}\n'
    text += '\nstatic void register(IForgeRegistry<Item> registry) {\n' + registration.replace('MetaItems.ITEMS', 'MetaItem.getMetaItems()') + '\n}\n'
    text += '\nstatic void registerOres() {\n' + ore.replace(' : ITEMS)', ' : MetaItem.getMetaItems())') + '\n}\n}\n'
    result[HOST + '.NativePrefixItemDeclarations'] = text
    result.update(assemble_blocks(originals, mappings, annotations))
    result.update(ores.assemble(originals, mappings, annotations, bind_registry))
    return result


BLOCK_IMPORTS = '''
import gregtech.api.GregTechAPI;
import gregtech.api.items.toolitem.ToolClasses;
import gregtech.api.recipes.ModHandler;
import gregtech.api.unification.material.Material;
import gregtech.api.unification.material.Materials;
import gregtech.api.unification.material.info.MaterialFlags;
import gregtech.api.unification.material.properties.PropertyKey;
import gregtech.api.unification.material.registry.MaterialRegistry;
import gregtech.api.unification.OreDictUnifier;
import gregtech.api.unification.ore.OrePrefix;
import gregtech.api.util.GTUtility;
import gregtech.api.util.function.TriConsumer;
import gregtech.common.blocks.*;
import gregtech.common.blocks.properties.PropertyMaterial;
import static gregtech.api.unification.material.info.MaterialFlags.*;
'''


def assemble_blocks(originals, mappings, annotations):
    result = {}
    for name, path in blocks.PATHS.items():
        text = annotations(blocks.project(name, originals[path[len(GT):-5]]))
        end = text.index(';') + 1
        text = text[:end] + '\n' + BLOCK_IMPORTS + text[end:]
        text = re.sub(r'new Failure\("incomplete", "block.behavior", "([^"\n]+)"\)',
                      lambda m: HOST + '.NativeBoundary.unsupported("block.behavior.' + m[1] + '")', text)
        result['gregtech.' + path[len(GT):-5].replace('/', '.')] = blocks.bind_native_symbols(name, text, mappings)
    original_view = {('gtceu', GT + n + '.java'): raw for n, raw in originals.items()}
    declarations = blocks.family_declarations(original_view)
    text = 'package ' + HOST + ';\n' + BLOCK_IMPORTS + '''
import java.util.*;
import java.util.Map.Entry;
import java.util.function.*;
import it.unimi.dsi.fastutil.ints.*;
import it.unimi.dsi.fastutil.objects.*;
import net.minecraft.block.Block;
import net.minecraft.block.state.IBlockState;
import net.minecraft.item.*;
import net.minecraft.util.ResourceLocation;
import net.minecraftforge.registries.IForgeRegistry;
final class NativeMaterialBlockDeclarations {
'''
    text += '\n'.join(declarations).replace('MetaBlocks::', 'NativeMaterialBlockDeclarations::') + '\n}\n'
    result[HOST + '.NativeMaterialBlockDeclarations'] = blocks.bind_native_symbols('NativeMaterialBlockDeclarations', text, mappings)
    result['gregtech.api.recipes.ModHandler'] = ('package gregtech.api.recipes;\n' + BLOCK_IMPORTS +
        'public class ModHandler {\n' + annotations(items.block(originals['api/recipes/ModHandler'],
            'public static boolean isMaterialWood(')) + '\n}\n')
    result['gregtech.api.items.toolitem.ToolClasses'] = ('package gregtech.api.items.toolitem;\npublic class ToolClasses {\n' +
        '\n'.join(items.field(originals['api/items/toolitem/ToolClasses'], 'public static final String ' + n + ' =')
                  for n in ('AXE', 'PICKAXE', 'SHOVEL', 'WRENCH')) + '\n}\n')
    return result
