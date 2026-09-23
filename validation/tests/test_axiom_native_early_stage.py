"""Early-stage evidence checks and explicitly provisioned native conformance."""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import axiom_native_root_stage as lane


TARGETS = ('net/minecraft/util/math/Vec3d', 'net/minecraft/util/EnumFacing')
ROOTS = ('net.minecraftforge.fml.relauncher.FMLCorePlugin',
         'net.minecraftforge.classloading.FMLForgePlugin')
CONTAINERS = ['net.minecraftforge.fml.common.FMLContainer',
              'net.minecraftforge.common.ForgeModContainer']
ACCESS_FIELDS = ('field_82609_l', 'field_176754_o')
# Original FMLCorePlugin declaration order; generated wrappers retain these parents.
WRAPPER_PARENTS = [
    'net.minecraftforge.fml.common.asm.transformers.SideTransformer',
    'net.minecraftforge.fml.common.asm.transformers.EventSubscriptionTransformer',
    'net.minecraftforge.fml.common.asm.transformers.EventSubscriberTransformer',
    'net.minecraftforge.fml.common.asm.transformers.SoundEngineFixTransformer',
    'net.minecraftforge.fml.common.asm.transformers.LWJGLTransformer']
# CleanMix installs PREINIT and INIT proxies around original FML transformer setup.
TRANSFORMERS = [
    'org.spongepowered.asm.mixin.transformer.Proxy',
    'net.minecraftforge.fml.common.asm.transformers.PatchingTransformer',
    '$wrapper.net.minecraftforge.fml.common.asm.transformers.SideTransformer',
    '$wrapper.net.minecraftforge.fml.common.asm.transformers.EventSubscriptionTransformer',
    '$wrapper.net.minecraftforge.fml.common.asm.transformers.EventSubscriberTransformer',
    '$wrapper.net.minecraftforge.fml.common.asm.transformers.SoundEngineFixTransformer',
    '$wrapper.net.minecraftforge.fml.common.asm.transformers.LWJGLTransformer',
    'net.minecraftforge.fml.common.asm.transformers.DeobfuscationTransformer',
    'net.minecraftforge.fml.common.asm.transformers.AccessTransformer',
    'net.minecraftforge.fml.common.asm.transformers.ModAccessTransformer',
    'net.minecraftforge.fml.common.asm.transformers.ItemStackTransformer',
    'net.minecraftforge.fml.common.asm.transformers.ItemBlockTransformer',
    'net.minecraftforge.fml.common.asm.transformers.ItemBlockSpecialTransformer',
    'net.minecraftforge.fml.common.asm.transformers.PotionEffectTransformer',
    'org.spongepowered.asm.mixin.transformer.Proxy']


class NativeEarlyStageTests(unittest.TestCase):
    def descriptor(self):
        return {'witnesses': {
            target: {'source': f'raw{i}', 'inputSha256': sha256(f'raw{i}'.encode()).hexdigest()}
            for i, target in enumerate(TARGETS)}}

    def body(self):
        cleanroom = '/fixture/lib/cleanroom-original.jar'
        minecraft = '/fixture/lib/minecraft-server-original.jar'
        return {
            'schema': 'axiom.result.v1', 'operation': 'native-early-stage',
            'scope': 'original-server-early-stage-not-material-initialization',
            'status': 'incomplete', 'diagnostics': [], 'result': {
                'schema': 'axiom.native-early-stage.v1', 'side': 'SERVER',
                'inputStage': 'original-obfuscated-artifacts', 'stage': 'early-stage-ready',
                'executionStage': 'early', 'mixinPhase': 'INIT',
                'earlyPipelineReady': True, 'deobfTransitionReturned': True,
                'loaderInstanceCreated': True, 'nativeOptionsAccepted': True,
                'nativeHomeInitialized': True, 'targetClassesDefined': True,
                'rootStageReady': False, 'materialInitializationComplete': False, 'fullLauncherCompositionQualified': False,
                'minecraftLaunched': False, 'candidateCompilationStarted': False,
                'lateMixinSelectionQualified': False, 'constructionDispatched': False, 'loadControllerDefinedBeforeDefault': False,
                'nativeDiagnostics': [], 'nativeDiagnosticsComplete': True,
                'injectedContainers': CONTAINERS.copy(),
                'transformers': TRANSFORMERS.copy(),
                'mixinProxyStates': [False, True],
                'transformerWrappers': [
                    {'transformer': '$wrapper.' + parent, 'parent': parent, 'coremod': 'FMLCorePlugin', 'codeSource': cleanroom}
                    for parent in WRAPPER_PARENTS],
                'artifactCodeSources': {'cleanroom': cleanroom, 'minecraft': minecraft},
                'rootCallbacks': [
                    {'plugin': plugin, 'setupClass': 'net.minecraftforge.fml.common.asm.FMLSanityChecker' if i == 0 else 'none',
                     'wrapperInjectionReturned': True, 'codeSource': cleanroom}
                    for i, plugin in enumerate(ROOTS)],
                'definitions': {
                    target: {'source': f'raw{i}', 'targetDefined': True,
                             'definitionSha256': sha256(f'defined{i}'.encode()).hexdigest(),
                             'codeSource': minecraft,
                             'definingLoader': 'net.minecraft.launchwrapper.LaunchClassLoader',
                             'fieldAccess': {field: 25 for field in ACCESS_FIELDS} if i == 1 else {},
                             'patchedMethods': lane.VEC3D_METHODS.copy() if i == 0 else []}
                    for i, target in enumerate(TARGETS)}}}

    def check(self, body):
        return lane.check_early_result(body, self.descriptor())

    def test_bounded_definitions_qualify_only_the_incomplete_early_scope(self):
        self.assertEqual([], self.check(self.body()))
        self.assertEqual([], lane.check_early_result(self.body()))

    def test_envelope_cannot_promote_native_or_material_completion(self):
        for field, value in [('schema', 'other'), ('operation', 'material-program'),
                             ('status', 'accepted'), ('scope', 'complete-material-initialization')]:
            body = self.body(); body[field] = value
            with self.subTest(field=field): self.assertTrue(self.check(body))

    def test_wrong_side_stage_schema_or_mixin_phase_refuses(self):
        for field, value in [('schema', 'axiom.native-root-stage.v1'), ('side', 'CLIENT'),
                             ('inputStage', 'prepared-patched-image'), ('stage', 'original-early-launch-loop'),
                             ('executionStage', 'roots'), ('mixinPhase', 'PREINIT'), ('mixinPhase', 'DEFAULT')]:
            body = self.body(); body['result'][field] = value
            with self.subTest(field=field, value=value): self.assertTrue(self.check(body))

    def test_completed_operations_require_actual_true_flags(self):
        for field in ('earlyPipelineReady', 'deobfTransitionReturned', 'loaderInstanceCreated',
                      'nativeOptionsAccepted', 'nativeHomeInitialized', 'targetClassesDefined',
                      'nativeDiagnosticsComplete'):
            for value in (False, 'true', 1, None):
                body = self.body(); body['result'][field] = value
                with self.subTest(field=field, value=value): self.assertTrue(self.check(body))

    def test_deferred_operations_require_explicit_false_flags(self):
        for field in ('rootStageReady', 'materialInitializationComplete', 'fullLauncherCompositionQualified', 'minecraftLaunched',
                      'candidateCompilationStarted', 'lateMixinSelectionQualified', 'constructionDispatched', 'loadControllerDefinedBeforeDefault'):
            for value in (True, 'false', 0, None):
                body = self.body(); body['result'][field] = value
                with self.subTest(field=field, value=value): self.assertTrue(self.check(body))

    def test_native_failure_cannot_be_hidden_by_ready_flags(self):
        for field in ('failure', 'observationFailure'):
            body = self.body()
            body['result'][field] = [{'class': 'java.lang.NoClassDefFoundError', 'message': 'native dependency'}]
            before = deepcopy(body)
            with self.subTest(field=field):
                self.assertTrue(self.check(body))
                self.assertEqual(before, body)

    def diagnostic(self, severity):
        return {'severity': severity, 'logger': 'native.FML', 'message': 'Original native diagnostic',
                'trace': '', 'locationStatus': 'unlocated'}

    def test_native_warnings_are_retained_without_reclassifying_as_errors(self):
        body = self.body()
        body['result']['nativeDiagnostics'] = [self.diagnostic('warning')]
        body['diagnostics'] = deepcopy(body['result']['nativeDiagnostics'])
        before = deepcopy(body)
        self.assertEqual([], self.check(body))
        self.assertEqual(before, body)

    def test_logged_native_error_refuses_even_without_a_thrown_failure(self):
        body = self.body()
        body['result']['nativeDiagnostics'] = [self.diagnostic('warning'), self.diagnostic('error')]
        body['diagnostics'] = deepcopy(body['result']['nativeDiagnostics'])
        before = deepcopy(body)
        self.assertTrue(self.check(body))
        self.assertEqual(before, body)

    def test_omitted_or_changed_native_diagnostics_refuse(self):
        for outer in ([], [self.diagnostic('error')], None):
            body = self.body(); body['result']['nativeDiagnostics'] = [self.diagnostic('warning')]
            body['diagnostics'] = outer
            with self.subTest(outer=outer): self.assertTrue(self.check(body))
        for rows in (None, {}, [None], ['warning'], [{'severity': 'info'}]):
            body = self.body(); body['result']['nativeDiagnostics'] = rows; body['diagnostics'] = deepcopy(rows)
            with self.subTest(rows=rows): self.assertTrue(self.check(body))

    def test_diagnostics_require_preserved_text_fields_and_truthful_location_status(self):
        for field in ('logger', 'message', 'trace', 'locationStatus'):
            for value in (None, 123, [], 'missing-field'):
                row = self.diagnostic('warning')
                if value == 'missing-field': del row[field]
                else: row[field] = value
                body = self.body(); body['result']['nativeDiagnostics'] = [row]; body['diagnostics'] = deepcopy([row])
                with self.subTest(field=field, value=value): self.assertTrue(self.check(body))
        row = self.diagnostic('warning'); row['locationStatus'] = 'located'
        body = self.body(); body['result']['nativeDiagnostics'] = [row]; body['diagnostics'] = deepcopy([row])
        self.assertTrue(self.check(body))

    def test_original_callback_order_and_actual_returns_are_required(self):
        for value in (None, [], {}, [None], self.body()['result']['rootCallbacks'] * 2):
            body = self.body(); body['result']['rootCallbacks'] = value
            with self.subTest(value=value): self.assertTrue(self.check(body))
        body = self.body(); body['result']['rootCallbacks'].reverse()
        self.assertTrue(self.check(body))
        for field, value in [('plugin', 'replacement.Plugin'), ('setupClass', 'none'),
                             ('wrapperInjectionReturned', False), ('wrapperInjectionReturned', 1),
                             ('codeSource', '/fixture/lib/replacement.jar')]:
            body = self.body(); body['result']['rootCallbacks'][0][field] = value
            with self.subTest(field=field, value=value): self.assertTrue(self.check(body))

    def test_injected_native_container_inventory_cannot_be_replaced_by_count(self):
        for value in (None, 2, CONTAINERS[::-1], CONTAINERS + ['fixture.Mod'], ['fixture.One', 'fixture.Two']):
            body = self.body(); body['result']['injectedContainers'] = value
            with self.subTest(value=value): self.assertTrue(self.check(body))

    def test_missing_duplicate_or_malformed_native_transformers_refuse(self):
        for transformer in dict.fromkeys(TRANSFORMERS):
            for change in ('missing', 'duplicate'):
                body = self.body(); queue = body['result']['transformers']
                if change == 'missing': queue.remove(transformer)
                else: queue.append(transformer)
                with self.subTest(transformer=transformer, change=change): self.assertTrue(self.check(body))
        for value in (None, {}, [], TRANSFORMERS[1:-1],
                      self.body()['result']['transformers'] + [None]):
            body = self.body(); body['result']['transformers'] = value
            with self.subTest(value=value): self.assertTrue(self.check(body))

    def test_queue_order_and_wrapped_identity_cannot_be_replaced_by_membership(self):
        for index in range(len(TRANSFORMERS) - 1):
            body = self.body(); queue = body['result']['transformers']
            queue[index], queue[index + 1] = queue[index + 1], queue[index]
            with self.subTest(swapped=index): self.assertTrue(self.check(body))
        body = self.body(); body['result']['transformers'][2] = WRAPPER_PARENTS[0]
        self.assertTrue(self.check(body))
        body = self.body(); body['result']['transformers'].insert(2, 'fixture.UnboundTransformer')
        self.assertTrue(self.check(body))

    def test_original_mixin_proxy_instances_require_only_the_init_proxy_active(self):
        for value in (None, [], [True, False], [True, True], [False, False], [False, True, True],
                      [0, 1], [False, 1], [0, True], ['false', 'true'], (False, True)):
            body = self.body(); body['result']['mixinProxyStates'] = value
            with self.subTest(value=value): self.assertTrue(self.check(body))

    def test_wrappers_require_original_parent_owner_codesource_and_order(self):
        for index in range(len(WRAPPER_PARENTS)):
            for field, value in [('transformer', WRAPPER_PARENTS[index]), ('parent', 'fixture.Replacement'),
                                 ('coremod', 'fixture.Owner'), ('codeSource', '/fixture/other.jar')]:
                body = self.body(); body['result']['transformerWrappers'][index][field] = value
                with self.subTest(index=index, field=field): self.assertTrue(self.check(body))
        for value in (None, {}, [], [None] * 5, self.body()['result']['transformerWrappers'][::-1],
                      self.body()['result']['transformerWrappers'] * 2):
            body = self.body(); body['result']['transformerWrappers'] = value
            with self.subTest(value=value): self.assertTrue(self.check(body))

    def test_vec3d_requires_observed_server_patch_methods(self):
        for method in lane.VEC3D_METHODS:
            body = self.body(); body['result']['definitions'][TARGETS[0]]['patchedMethods'].remove(method)
            with self.subTest(missing=method): self.assertTrue(self.check(body))
        for value in (None, [], {}, lane.VEC3D_METHODS + [lane.VEC3D_METHODS[0]],
                      ['func_189985_c()F', *lane.VEC3D_METHODS[1:]]):
            body = self.body(); body['result']['definitions'][TARGETS[0]]['patchedMethods'] = value
            with self.subTest(value=value): self.assertTrue(self.check(body))

    def test_artifacts_require_distinct_absolute_codesources(self):
        for value in (None, [], {}, {'cleanroom': '/same.jar', 'minecraft': '/same.jar'},
                      {'cleanroom': 'relative.jar', 'minecraft': '/minecraft.jar'},
                      {'cleanroom': '/cleanroom.jar', 'minecraft': None}):
            body = self.body(); body['result']['artifactCodeSources'] = value
            with self.subTest(value=value): self.assertTrue(self.check(body))

    def test_exact_definition_inventory_is_required(self):
        for value in (None, [], {}, {str(i): {} for i in range(2)}, {target: None for target in TARGETS}):
            body = self.body(); body['result']['definitions'] = value
            with self.subTest(value=value): self.assertTrue(self.check(body))
        body = self.body(); body['result']['definitions']['net/minecraft/block/Block'] = {}
        self.assertTrue(self.check(body))

    def test_definition_identity_requires_loader_source_and_real_definition(self):
        for target in TARGETS:
            for field, value in [('source', 'different.raw'), ('source', ''), ('targetDefined', False),
                                 ('targetDefined', 1), ('definingLoader', 'java.net.URLClassLoader'),
                                 ('codeSource', '/fixture/lib/prepared.jar')]:
                body = self.body(); body['result']['definitions'][target][field] = value
                with self.subTest(target=target, field=field): self.assertTrue(self.check(body))

    def test_raw_or_malformed_definition_digests_refuse(self):
        for target in TARGETS:
            raw = self.descriptor()['witnesses'][target]['inputSha256']
            for value in (raw, 'g' * 64, 'A' * 64, '0' * 63, '', None, 123):
                body = self.body(); body['result']['definitions'][target]['definitionSha256'] = value
                with self.subTest(target=target, digest=value): self.assertTrue(self.check(body))

    def test_enumfacing_access_requires_public_integer_modifiers_for_both_fields(self):
        for field in ACCESS_FIELDS:
            for value in (0, 2, 24, True, '25', None):
                body = self.body(); body['result']['definitions'][TARGETS[1]]['fieldAccess'][field] = value
                with self.subTest(field=field, value=value): self.assertTrue(self.check(body))
            body = self.body(); del body['result']['definitions'][TARGETS[1]]['fieldAccess'][field]
            with self.subTest(missing=field): self.assertTrue(self.check(body))
        for value in (None, [], {}, 25):
            body = self.body(); body['result']['definitions'][TARGETS[1]]['fieldAccess'] = value
            with self.subTest(value=value): self.assertTrue(self.check(body))

    def test_missing_or_malformed_result_cannot_qualify(self):
        for result in (None, [], 'ready', {}, {'earlyPipelineReady': True}):
            body = self.body(); body['result'] = result
            with self.subTest(result=result): self.assertTrue(self.check(body))


# Independent original callback/transform declarations for the required stack.
PACK_ARTIFACTS = [
    ('ae2-extended-life', 'ae2-uel-v0.56.6.jar'),
    ('alet', 'A_Little_Extra_Tiles-1.1.0-pre034.jar'),
    ('architecturecraft-tridev', 'architecturecraft-1.12-3.108.jar'),
    ('athenaeum', 'athenaeum-1.12.2-1.19.6.jar'),
    ('autoreglib', 'AutoRegLib-1.3-32.jar'),
    ('barrels-drums-storage-more', 'BarrelsDrumsStorageAndMore-0.0.24.jar'),
    ('better-builders-wands', 'BetterBuildersWands-1.12.2-0.13.2.271+5997513.jar'),
    ('biomes-o-plenty', 'BiomesOPlenty-1.12.2-7.0.1.2445-universal.jar'),
    ('bubbles-a-baubles-fork', 'Bubbles-2.4.10.jar'),
    ('building-gadgets', 'BuildingGadgets-2.8.4.jar'),
    ('catwalks-4', 'catwalks-1.12.2-4.0.45.jar'),
    ('cb-multipart', 'ForgeMultipart-1.12.2-2.6.2.83-universal.jar'),
    ('cd4017be-library', 'CD4017BE_lib-1.12.2-6.5.1.jar'),
    ('chameleon', 'Chameleon-1.12-4.1.3.jar'),
    ('chest-transporter', 'ChestTransporter-1.12.2-2.8.8.jar'),
    ('chisel', 'Chisel-MC1.12.2-1.0.2.45.jar'),
    ('codechicken-lib-1-8', 'CodeChickenLib-1.12.2-3.2.3.358-universal.jar'),
    ('commons0815', 'Commons0815-1.12.2-1.4.0.jar'),
    ('creativecore', 'CreativeCore_v1.10.71_mc1.12.2.jar'),
    ('dropt', 'dropt-1.12.2-1.19.4.jar'),
    ('engineers-decor', 'engineersdecor-1.12.2-1.1.5.jar'),
    ('forgelin-continuous', 'Forgelin-Continuous-2.4.10.0.jar'),
    ('fugue', '+Fugue-0.24.3.jar'),
    ('gaspunk', 'gaspunk-1.12.2-1.4.8.jar'),
    ('gaspunk-inhaler', 'Gaspunk-Inhaler-1.12.2-1.0.1.jar'),
    ('geckolib', 'geckolib-forge-1.12.2-3.0.31.jar'),
    ('gregicality-multiblocks', 'GregicalityMultiblocks-1.2.11.jar'),
    ('gregtech-ce-unofficial', 'gregtech-1.12.2-2.8.10-beta.jar'),
    ('gregtech-food-option', 'gregtechfoodoption-1.12.2-1.12.10.jar'),
    ('groovyscript', 'groovyscript-1.4.3.jar'),
    ('had-enough-items', 'HadEnoughItems_1.12.2-4.32.0.jar'),
    ('icbm', 'ICBM-classic-1.12.2-6.5.5.jar'),
    ('ichunutil', 'iChunUtil-1.12.2-7.2.2.jar'),
    ('immersive-railroading', 'ImmersiveRailroading-1.12.2-forge-1.10.0.jar'),
    ('industrial-renewal', 'IndustrialRenewal_1.12.2-0.21.8.jar'),
    ('ivtoolkit', 'IvToolkit-1.3.3-1.12.jar'),
    ('just-enough-calculation', 'JustEnoughCalculation-1.12.2-3.2.7.jar'),
    ('lazy-ae2', 'lazy-ae2-1.12.2-1.1.26.jar'),
    ('libnine', 'libnine-1.12.2-1.2.2.jar'),
    ('littletiles', 'LittleTiles_v1.5.87_mc1.12.2.jar'),
    ('mcjtylib-refilmed', 'mcjtylib-refilmed-3.5.5.jar'),
    ('modernmarkings', 'AGS ModernMarkings Mod-0.4.1.jar'),
    ('modularui', 'modularui-3.1.6.jar'),
    ('mrtjpcore', 'MrTJPCore-1.12.2-2.1.4.43-universal.jar'),
    ('multistorage', 'multistorage-1.12.0-1.4.14.jar'),
    ('nae2', 'nae2-1.6.4.jar'),
    ('natures-compass', 'NaturesCompass-1.12.2-1.8.5.jar'),
    ('new-tardis-mod', 'tardis-0.1.4A.jar'),
    ('omlib', 'omlib-1.12.2-3.1.5-256.jar'),
    ('ompd', 'ompd-1.12.2-3.1.1-76.jar'),
    ('openblocks-elevator', 'ElevatorMod-1.12.2-1.3.14.jar'),
    ('opencomputers', 'OpenComputers-MC1.12.2-1.8.9a+8ca336f.jar'),
    ('openglasses2', 'OpenGlasses-MC1.12.2-2.2-53.jar'),
    ('openmodularturrets', 'openmodularturrets-1.12.2-3.1.14-382.jar'),
    ('opensecurity', 'OpenSecurity-1.12.2-1.0-93.jar'),
    ('packagedauto', 'PackagedAuto-1.12.2-1.0.24.73.jar'),
    ('portal-gun', 'PortalGun-1.12.2-7.1.0.jar'),
    ('project-red-core', 'ProjectRed-1.12.2-4.9.4.120-Base.jar'),
    ('project-red-illumination', 'ProjectRed-1.12.2-4.9.4.120-lighting.jar'),
    ('project-red-integration', 'ProjectRed-1.12.2-4.9.4.120-integration.jar'),
    ('pyrotech', 'pyrotech-1.12.2-1.6.19.jar'),
    ('quark-rotn-edition', 'QuarkRotN-r1.6-192.jar'),
    ('redstone-control', 'RedstoneControl-1.12.2-0.3.1.3.jar'),
    ('redstone-gauges-and-switches', 'rsgauges-1.12.2-1.2.8.jar'),
    ('refinedtools', 'refinedtools-7.78.jar'),
    ('retro-sophisticated-backpacks', 'Retro-Sophisticated-Backpacks-1.1.4.jar'),
    ('scalar-legacy', 'Scalar Legacy-1.0.1.jar'),
    ('scaling-health', 'ScalingHealth-1.12.2-1.3.42+147.jar'),
    ('scape-and-run-parasites', 'SRParasites-1.12.2v1.9.21.jar'),
    ('shetiphiancore', 'shetiphiancore-1.12.0-3.5.9.jar'),
    ('silent-lib', 'SilentLib-1.12.2-3.0.14+168.jar'),
    ('stargate-network', 'SGCraft-2.0.5.jar'),
    ('storage-drawers', 'StorageDrawers-1.12.2-5.5.3.jar'),
    ('supercritical', 'Supercritical-0.2.7.jar'),
    ('sussypatches', 'SussyPatches-1.11.6.jar'),
    ('susycore', 'Susy-Core-0.1.118.jar'),
    ('techguns', 'techguns-1.12.2-2.0.2.0_pre3.2.jar'),
    ('tool-belt', 'ToolBelt-1.12.2-1.9.14.jar'),
    ('torchmaster', 'torchmaster_1.12.2-1.8.5.0.jar'),
    ('track-api', 'TrackAPI-1.2.jar'),
    ('travelers-backpack', 'TravelersBackpack-1.12.2-1.0.35.jar'),
    ('universal-mod-core', 'UniversalModCore-1.12.2-forge-1.2.2-3d12767.jar'),
    ('universal-tweaks', 'UniversalTweaks-1.12.2-1.20.1.jar'),
    ('vertically-stacked-dimensions', 'VerticallyStackedDimensions-1.12.2-0.1.9.jar'),
    ('weeping-angels-mod', 'weeping-angels-46.jar'),
    ('xtones', 'Xtones-1.2.2.jar'),
    ('ynet-an-xnet-fork', 'xnet-1.12-1.8.4-ynet.jar'),
]
PACK_COREMODS = [
    ('mod.acgaming.universaltweaks.core.UTLoadingPlugin', 'universal-tweaks'),
    ('com.cleanroommc.groovyscript.core.GroovyScriptCore', 'groovyscript'),
    (ROOTS[0], 'cleanroom'), (ROOTS[1], 'cleanroom'),
    ('com.cleanroommc.fugue.common.FugueLoadingPlugin', 'fugue'),
    ('com.creativemd.creativecore.core.CreativePatchingLoader', 'creativecore'),
    ('io.github.chaosunity.forgelin.preloader.ForgelinPlugin', 'forgelin-continuous'),
    ('gregtechfoodoption.mixins.GTFOEarlyMixinPlugin', 'gregtech-food-option'),
    ('ivorius.ivtoolkit.IvToolkitLoadingPlugin', 'ivtoolkit'),
    ('com.creativemd.littletiles.LittlePatchingLoader', 'littletiles'),
    ('co.neeve.nae2.NAE2MixinPlugin', 'nae2'),
    ('pcl.opensecurity.util.SoundUnpack', 'opensecurity'),
    ('com.cleanroommc.retrosophisticatedbackpacks.preloader.RSBPlugin', 'retro-sophisticated-backpacks'),
    ('com.cleanroommc.scalar.ScalarLoadingPlugin', 'scalar-legacy'),
    ('dev.tianmi.sussypatches.core.LoadingPlugin', 'sussypatches'),
    ('baubles.core.BubblesCore', 'bubbles-a-baubles-fork'),
    ('appeng.core.AE2ELCore', 'ae2-extended-life'),
    ('gregtech.asm.GregTechLoadingPlugin', 'gregtech-ce-unofficial'),
    ('li.cil.oc.common.launch.TransformerLoader', 'opencomputers'),
    ('vazkii.quark.base.asm.LoadingPlugin', 'quark-rotn-edition'),
    ('shetiphian.asm.TweakPlugin', 'contained:shetiphian-asm'),
    ('com.cleanroommc.modularui.core.ModularUICore', 'modularui'),
    ('cd4017be.dimstack.asm.CorePlugin', 'vertically-stacked-dimensions'),
    ('supersymmetry.asm.SusyLoadingPlugin', 'susycore'),
    ('techguns.core.TechgunsFMLPlugin', 'techguns')]
PACK_TRANSFORMERS = [*TRANSFORMERS[:7], '$wrapper.com.creativemd.littletiles.LittleTilesTransformer',
                     'codechicken.asm.internal.SnifferTransformer',
                     *TRANSFORMERS[7:9], 'baubles.core.BubblesTransformer',
                     'io.github.chaosunity.forgelin.transformer.ForgelinTransformer',
                     'com.cleanroommc.groovyscript.core.GroovyScriptTransformer',
                     'com.creativemd.littletiles.LittleTilesAfterTransformer',
                     *TRANSFORMERS[9:], '$wrapper.appeng.core.transformer.AE2ELTransformer', '$wrapper.gregtech.asm.GregTechTransformer',
                     '$wrapper.li.cil.oc.common.asm.ClassTransformer',
                     '$wrapper.vazkii.quark.base.asm.ClassTransformer',
                     '$wrapper.shetiphian.asm.ClassTransformer',
                     '$wrapper.com.cleanroommc.modularui.core.ClassTransformer',
                     '$wrapper.cd4017be.dimstack.asm.ChunkPrimerTransformer',
                     '$wrapper.cd4017be.dimstack.asm.BlockPortalTransformer',
                     '$wrapper.supersymmetry.asm.SusyTransformer', '$wrapper.techguns.core.TechgunsASMTransformer']
GROOVY_TARGET = 'org/codehaus/groovy/reflection/CachedClass$1'
GROOVY_HOOK = ('com/cleanroommc/groovyscript/sandbox/transformer/GroovyCodeFactory.makeFieldsHook'
               '(Lorg/codehaus/groovy/reflection/CachedClass;)Ljava/security/PrivilegedAction;')


class NativePackEarlyStageTests(unittest.TestCase):
    descriptor = NativeEarlyStageTests.descriptor

    def artifacts(self):
        return [{'descriptor': 'mods/' + descriptor + '.pw.toml', 'outputPath': 'mods/' + filename,
                 'size': 1000 + index, 'sha256': sha256(filename.encode()).hexdigest()}
                for index, (descriptor, filename) in enumerate(PACK_ARTIFACTS)]

    def context(self):
        context = json.loads(lane.PACK_PROFILE.read_bytes())
        context['artifacts'] = self.artifacts()
        context['definitionWitness']['inputSha256'] = sha256(b'original CachedClass$1').hexdigest()
        return context

    def source_program(self, config=b'general { B:enabled=true }\r\n'):
        from axiom_material_program_cases import source_acknowledgement
        return source_acknowledgement({'groovy/runConfig.json': b'{}\r\n',
            'groovy/preInit/Startup.groovy': b'// saved startup\n', 'config/forge_early.cfg': config})

    def body(self):
        body = NativeEarlyStageTests.body(self)
        result = body['result']
        result['nativeContext'] = 'supersymmetry:required-early'
        result['nativeArtifactPlacement'] = 'links-to-verified-read-only-inputs'
        result['queuedAccessTransformers'] = self.context()['accessTransformers'].copy()
        result['sourceProgram'] = self.source_program()
        result['nativeArtifacts'] = {row['descriptor']: {
            'codeSource': '/fixture/native-home/' + row['outputPath'], 'size': row['size'], 'sha256': row['sha256']}
            for row in self.artifacts()}
        sources = {name: row['codeSource'] for name, row in result['nativeArtifacts'].items()}
        sources['cleanroom'] = result['artifactCodeSources']['cleanroom']
        result['containedArtifactRoot']='/fixture/native-worker'
        result['nativeContainedArtifacts']={pin['id']: {
            'parentArtifact':pin['parentArtifact'],'parentCodeSource':sources[pin['parentArtifact']],
            'entry':pin['entry'],'codeSource':str(Path(result['containedArtifactRoot'])/pin['outputPath']),
            'sha256':pin['sha256'],'size':pin['size'],'extractedByObserver':False}
            for pin in self.context().get('containedArtifacts',[])}
        sources.update({name:row['codeSource'] for name,row in result['nativeContainedArtifacts'].items()})
        result['coremodCallbacks'] = [
            {'plugin': plugin, 'setupClass': {ROOTS[0]: 'net.minecraftforge.fml.common.asm.FMLSanityChecker',
                            'io.github.chaosunity.forgelin.preloader.ForgelinPlugin': 'io.github.chaosunity.forgelin.preloader.ForgelinSetup',
                            'pcl.opensecurity.util.SoundUnpack': 'pcl.opensecurity.util.SoundUnpack'}.get(plugin, 'none'),
             'wrapperInjectionReturned': True,
             'codeSource': sources[artifact if artifact == 'cleanroom' or artifact.startswith('contained:') else 'mods/' + artifact + '.pw.toml']}
            for plugin, artifact in PACK_COREMODS]
        result['injectedContainers'] = ['com.cleanroommc.groovyscript.sandbox.ScriptModContainer',
            *CONTAINERS, 'com.creativemd.creativecore.core.CreativeCoreDummy',
            'ivorius.ivtoolkit.IvToolkitCoreContainer', 'com.creativemd.littletiles.LittleTilesCore',
            'com.cleanroommc.scalar.ScalarModContainer', 'li.cil.oc.common.launch.CoreModContainer', 'techguns.core.TechgunsCore']
        result['transformers'] = PACK_TRANSFORMERS.copy()
        for parent, owner, artifact in [
            ('com.creativemd.littletiles.LittleTilesTransformer', 'LittlePatchingLoader', 'littletiles'),
            ('appeng.core.transformer.AE2ELTransformer', 'AE2ELCore', 'ae2-extended-life'),
            ('gregtech.asm.GregTechTransformer', 'GregTechLoadingPlugin', 'gregtech-ce-unofficial'),
            ('li.cil.oc.common.asm.ClassTransformer', 'TransformerLoader', 'opencomputers'),
            ('vazkii.quark.base.asm.ClassTransformer', 'Quark Plugin', 'quark-rotn-edition'),
            ('shetiphian.asm.ClassTransformer', 'ShetiPhian-ASM', 'contained:shetiphian-asm'),
            ('com.cleanroommc.modularui.core.ClassTransformer', 'ModularUI-Core', 'modularui'),
            ('cd4017be.dimstack.asm.ChunkPrimerTransformer', 'Vertically Stacked Dimensions ASM', 'vertically-stacked-dimensions'),
            ('cd4017be.dimstack.asm.BlockPortalTransformer', 'Vertically Stacked Dimensions ASM', 'vertically-stacked-dimensions'),
            ('supersymmetry.asm.SusyTransformer', 'SusyLoadingPlugin', 'susycore'),
            ('techguns.core.TechgunsASMTransformer', 'Techguns Core', 'techguns')]:
            result['transformerWrappers'].append({'transformer': '$wrapper.' + parent, 'parent': parent,
                'coremod': owner, 'codeSource': sources[artifact if artifact.startswith('contained:') else 'mods/' + artifact + '.pw.toml']})
        result['definitions'][GROOVY_TARGET] = {
            'source': GROOVY_TARGET, 'targetDefined': True,
            'definitionSha256': sha256(b'defined CachedClass$1').hexdigest(),
            'inputSha256': sha256(b'original CachedClass$1').hexdigest(),
            'codeSource': sources['mods/groovyscript.pw.toml'],
            'definingLoader': 'net.minecraft.launchwrapper.LaunchClassLoader',
            'observedHook': GROOVY_HOOK, 'initializationRequested': False}
        return body

    def check(self, body, source_program=None):
        return lane.check_pack_early_result(body, self.descriptor(), self.context(),
                                           self.source_program() if source_program is None else source_program)

    def test_contained_artifact_requires_exact_parent_bytes_original_extraction_and_codesource(self):
        for field,value in [('parentArtifact','mods/other.pw.toml'),('parentCodeSource','/other/parent.jar'),
                ('entry','other.jar'),('sha256','0'*64),('size',True),('codeSource','/other/child.jar'),
                ('extractedByObserver',True)]:
            body=self.body();body['result']['nativeContainedArtifacts']['contained:shetiphian-asm'][field]=value
            with self.subTest(field=field):self.assertTrue(self.check(body))
        for field in ('nativeContainedArtifacts','containedArtifactRoot'):
            body=self.body();del body['result'][field]
            with self.subTest(missing=field):self.assertTrue(self.check(body))

    def test_complete_required_early_evidence_preserves_the_deferred_material_scope(self):
        body = self.body(); before = deepcopy(body)
        self.assertEqual([], self.check(body))
        self.assertEqual(before, body)
        self.assertTrue(lane.check_early_result(body, self.descriptor()))
        self.assertEqual('org.spongepowered.asm.mixin.transformer.Proxy', body['result']['transformers'][-11])

    def test_missing_native_context_or_original_callback_remains_incomplete(self):
        for field in ('nativeContext', 'coremodCallbacks', 'nativeArtifactPlacement', 'queuedAccessTransformers'):
            body = self.body(); del body['result'][field]
            with self.subTest(field=field): self.assertTrue(self.check(body))
        body = self.body(); body['result']['nativeContext'] = 'root-only'
        self.assertTrue(self.check(body))
        body = self.body(); body['result']['coremodCallbacks'].pop(0)
        self.assertTrue(self.check(body))

    def test_saved_configuration_bytes_and_every_acknowledgement_field_are_bound(self):
        body = self.body()
        for field in self.source_program():
            changed = deepcopy(body); del changed['result']['sourceProgram'][field]
            with self.subTest(field=field): self.assertTrue(self.check(changed))
        self.assertTrue(self.check(body, self.source_program(b'general { B:enabled=false }\r\n')))
        body['result']['sourceProgram']['scope'] = 'groovy-only'
        self.assertTrue(self.check(body))

    def test_coremod_callbacks_require_original_order_return_and_artifact_source(self):
        for index in range(len(PACK_COREMODS)):
            for field, value in [('wrapperInjectionReturned', False), ('setupClass', 'missing.Setup'),
                                 ('codeSource', '/fixture/native-home/mods/other.jar')]:
                body = self.body(); body['result']['coremodCallbacks'][index][field] = value
                with self.subTest(index=index, field=field): self.assertTrue(self.check(body))
        body = self.body(); body['result']['coremodCallbacks'][0:2] = body['result']['coremodCallbacks'][1::-1]
        self.assertTrue(self.check(body))

    def test_original_artifact_bytes_and_native_filenames_are_bound(self):
        for descriptor, _ in PACK_ARTIFACTS:
            key = 'mods/' + descriptor + '.pw.toml'
            for field, value in [('sha256', '0' * 64), ('size', 0), ('codeSource', '/fixture/native-home/mods/renamed.jar')]:
                body = self.body(); body['result']['nativeArtifacts'][key][field] = value
                with self.subTest(artifact=key, field=field): self.assertTrue(self.check(body))
            body = self.body(); del body['result']['nativeArtifacts'][key]
            with self.subTest(missing=key): self.assertTrue(self.check(body))

    def test_required_transformers_wrappers_and_containers_keep_native_order(self):
        body = self.body(); queue = body['result']['transformers']; queue[8], queue[9] = queue[9], queue[8]
        self.assertTrue(self.check(body))
        for field in ('transformers', 'transformerWrappers', 'injectedContainers'):
            body = self.body(); body['result'][field].pop()
            with self.subTest(field=field): self.assertTrue(self.check(body))
        body = self.body(); body['result']['transformerWrappers'][-1]['codeSource'] = body['result']['artifactCodeSources']['cleanroom']
        self.assertTrue(self.check(body))
        body = self.body(); body['result']['mixinProxyStates'] = [True, True]
        self.assertTrue(self.check(body))

    def test_cachedclass_requires_actual_unchanged_initialization_boundary_and_field_hook(self):
        for field, value in [('source', 'other/Class'), ('inputSha256', '0' * 64),
                             ('definitionSha256', sha256(b'original CachedClass$1').hexdigest()),
                             ('observedHook', GROOVY_HOOK.replace('makeFieldsHook', 'otherHook')),
                             ('initializationRequested', True), ('targetDefined', False),
                             ('definingLoader', 'java.net.URLClassLoader'), ('codeSource', '/fixture/prepared.jar')]:
            body = self.body(); body['result']['definitions'][GROOVY_TARGET][field] = value
            with self.subTest(field=field): self.assertTrue(self.check(body))
        body = self.body(); del body['result']['definitions'][GROOVY_TARGET]
        self.assertTrue(self.check(body))

    def test_native_warnings_errors_and_deferred_claims_retain_shared_meaning(self):
        body = self.body(); warning = NativeEarlyStageTests.diagnostic(self, 'warning')
        body['result']['nativeDiagnostics'] = [warning]; body['diagnostics'] = deepcopy([warning])
        self.assertEqual([], self.check(body))
        body['result']['nativeDiagnostics'][0]['severity'] = 'error'
        body['diagnostics'] = deepcopy(body['result']['nativeDiagnostics'])
        before = deepcopy(body)
        self.assertTrue(self.check(body)); self.assertEqual(before, body)
        for field in ('materialInitializationComplete', 'lateMixinSelectionQualified', 'constructionDispatched'):
            body = self.body(); body['result'][field] = True
            with self.subTest(field=field): self.assertTrue(self.check(body))


class NativeStageExecutionCase(unittest.TestCase):
    """Shared original-input conformance using the existing supervised probe."""
    execution_stage = 'early'
    environment_prefix = 'AXIOM_EARLY'

    @classmethod
    def check_response(cls, response, descriptor, program):
        check = lane.check_selection_result if cls.execution_stage == 'selection' else lane.check_pack_early_result
        return check(response, descriptor, program['nativeContext'], program['sourceProgram'])

    @classmethod
    def verify_baseline(cls):
        if (cls.build['status'] != 'passed-bounded-native-' + cls.execution_stage + '-stage'
                or cls.build['failures'] or cls.baseline_failures):
            raise ValueError('Native conformance requires a previously checked positive candidate for its stage')

    @classmethod
    def setUpClass(cls):
        names = tuple(cls.environment_prefix + '_' + suffix for suffix in
                      ('NATIVE_CANDIDATE', 'ENGINE_HOME', 'JAVA_HOME', 'REPORT_ROOT'))
        selected = {name: os.environ.get(name) for name in names}
        if not any(selected.values()):
            raise unittest.SkipTest('Explicit native ' + cls.execution_stage + ' candidate, engine, JVM and new report root were not supplied')
        if not all(selected.values()):
            raise ValueError('Native early execution requires all four explicit input/report paths')
        cls.candidate, cls.engine, cls.java = [Path(selected[name]).resolve(strict=True) for name in names[:3]]
        cls.report = Path(selected[names[3]]).absolute()
        cls.report.mkdir(parents=True, exist_ok=False)
        cls.jvm = lane.verify_runtime(cls.java, compiler=True)
        engine_raw, _, cls.jars = lane.engine_inputs(cls.engine, lane.ENGINE_LOCK.read_bytes())
        cls.engine_digest = sha256(engine_raw).hexdigest()
        cls.build = json.loads((cls.candidate / 'build.json').read_bytes())
        cls.package = cls.candidate / 'runtime'
        cls.probe = cls.candidate / 'root-stage-probe.jar'
        cls.probe_digest = sha256(cls.probe.read_bytes()).hexdigest()
        cls.program = json.loads((cls.package / 'program.json').read_bytes())
        if (cls.program['engineSha256'] != cls.engine_digest or cls.build['engineSha256'] != cls.engine_digest
                or cls.build['jvm'] != cls.jvm
                or cls.program['nativeContext']['id'] != 'supersymmetry:required-early'):
            raise ValueError('Native candidate differs from the explicit required-context engine')
        for name, digest in cls.build['sourceInputs'].items():
            lane.checked_path(lane.ROOT, {'path': name, 'sha256': digest})
        for row in cls.program['files']:
            lane.checked_path(cls.package, row)
        cls.descriptor = json.loads((cls.package / 'root-class-space.json').read_bytes())
        cls.baseline_failures = cls.check_response(cls.build['response'], cls.descriptor, cls.program)
        cls.verify_baseline()
        (cls.report / 'inputs.json').write_text(json.dumps({
            'candidate': str(cls.candidate), 'engineHome': str(cls.engine), 'javaHome': str(cls.java),
            'engineManifestSha256': cls.engine_digest, 'probeSha256': cls.probe_digest,
            'candidateBuildSha256': sha256((cls.candidate / 'build.json').read_bytes()).hexdigest(),
            'testSourceSha256': sha256(Path(__file__).read_bytes()).hexdigest(),
            'sourceInputs': cls.build['sourceInputs'], 'jvm': cls.jvm,
            'scope': 'required original ' + cls.execution_stage + ' stage; not material initialization',
            'baselineStatus': cls.build['status'], 'baselineCheckerFailures': cls.baseline_failures,
        }, indent=2) + '\n')

    def run_case(self, name, *, addition=None, replacement=None, package_change=None):
        from axiom_native_early_inputs import archive_inventory, source_acknowledgement

        destination = self.report / name
        package = destination / 'runtime'
        shutil.copytree(self.package, package)
        program = deepcopy(self.program)
        changes = {}
        if addition is not None or replacement is not None:
            self.assertFalse(addition is not None and replacement is not None)
            path, data = addition if addition is not None else replacement
            archive_path = package / program['programArchive']
            with zipfile.ZipFile(archive_path) as archive:
                entries = {name: archive.read(name) for name in archive.namelist()}
            if addition is not None: self.assertNotIn(path, entries)
            else: self.assertIn(path, entries)
            with zipfile.ZipFile(archive_path, 'w', compression=zipfile.ZIP_STORED) as archive:
                for entry, raw in sorted({**entries, path: data}.items()):
                    info = zipfile.ZipInfo(entry, date_time=(1980, 1, 1, 0, 0, 0))
                    info.external_attr = 0o100600 << 16
                    archive.writestr(info, raw)
            inventory = archive_inventory(archive_path)
            program['sourceProgram'] = source_acknowledgement(inventory)
            pin = {'size': archive_path.stat().st_size, 'sha256': sha256(archive_path.read_bytes()).hexdigest()}
            program['inputBindings']['programArchive'] = pin
            next(row for row in program['files'] if row['path'] == program['programArchive']).update(pin)
            changes = {'addedFile' if addition is not None else 'replacedFile': path,
                       'newBytes': data.decode(), 'unchangedBaselineFiles': len(entries) - (replacement is not None),
                       'previousSha256': None if addition is not None else sha256(entries[path]).hexdigest()}
        if package_change is not None:
            changes = package_change(program, package)
        (package / 'program.json').write_text(json.dumps(program, indent=2) + '\n')
        command = [str(self.java / 'bin/java'), '-cp',
                   os.pathsep.join(map(str, [self.probe, *self.jars])),
                   'research.orthrus.axiom.NativeRootStageProbe', str(package)]
        receipt = {'command': command, 'changes': changes,
                   'programSha256': sha256((package / 'program.json').read_bytes()).hexdigest(),
                   'sourceProgram': program['sourceProgram'], 'freshWorkerRequiredIfStarted': True}
        (destination / 'invocation.json').write_text(json.dumps(receipt, indent=2) + '\n')
        started = time.monotonic()
        try:
            process = subprocess.run(command, cwd=destination, env={'LANG': 'C.UTF-8', 'PATH': os.environ.get('PATH', '/usr/bin:/bin')},
                                     capture_output=True)
        except subprocess.TimeoutExpired as failure:
            (destination / 'stdout.json').write_bytes(failure.stdout or b'')
            (destination / 'stderr.log').write_bytes(failure.stderr or b'')
            receipt.update(timedOut=True, seconds=time.monotonic() - started)
            (destination / 'execution.json').write_text(json.dumps(receipt, indent=2) + '\n')
            raise
        (destination / 'stdout.json').write_bytes(process.stdout)
        (destination / 'stderr.log').write_bytes(process.stderr)
        receipt.update(exitCode=process.returncode, seconds=time.monotonic() - started)
        (destination / 'execution.json').write_text(json.dumps(receipt, indent=2) + '\n')
        self.assertEqual(0, process.returncode, process.stderr.decode(errors='replace'))
        response = json.loads(process.stdout)
        self.assertEqual('incomplete', response['status'])
        body = response['result']
        for field in ('minecraftLaunched', 'materialInitializationComplete'):
            self.assertIs(body[field], False)
        if self.execution_stage != 'groovy':
            self.assertIs(body['candidateCompilationStarted'], False)
            self.assertIsNot(body.get('constructionDispatched'), True)
        else:
            self.assertIsInstance(body['candidateCompilationStarted'], bool)
            self.assertIs(body.get('preInitializationDispatched'), False)
        if addition is not None or replacement is not None:
            self.assertEqual(program['sourceProgram'], body['sourceProgram'])
        for row in self.program['files']:
            lane.checked_path(self.package, row)
        self.assertEqual(self.probe_digest, sha256(self.probe.read_bytes()).hexdigest())
        return response, program




class NativeSelectionStageTests(unittest.TestCase):
    """Selection verdicts need native definitions and state, beyond queue entries."""
    def context(self):
        context = NativePackEarlyStageTests().context()
        for witness in context['selection']['definitionWitnesses']:
            witness['inputSha256'] = sha256(witness['target'].encode()).hexdigest()
        return context

    def body(self):
        early = NativePackEarlyStageTests().body()
        early['result']['executedTweaks'] = ['native.Tweaker']
        body = deepcopy(early); result = body['result']; context = self.context()
        body.update(operation='native-selection-stage', scope='original-server-selection-stage-not-material-initialization')
        result.update(schema='axiom.native-selection-stage.v1', executionStage='selection', mixinPhase='DEFAULT',
                      stage='selection-prefix-completed', loaderState='LOADING', earlyStage=deepcopy(early['result']),
                      launchArgumentCallbacks=['native.Tweaker'], serverStartupConfirmationPerformed=False)
        result['nativeEffectiveSide']='SERVER'
        result['nativeServerOwner']={'mode':'original-dedicated-server-construction',
            'serverClass':'net.minecraft.server.dedicated.DedicatedServer',
            'dataFixerClass':'net.minecraftforge.common.util.CompoundDataFixer','threadGroup':'SERVER',
            'constructorReturned':True,'nativeDefiningLoader':True,'originalOwnershipPrefixReturned':True,
            'gameThreadAbsent':True,'snooperStarted':False,'worldCount':0,
            'networkEndpointCount':0,'networkConnectionCount':0,
            'serverArtifact':result['artifactCodeSources']['minecraft'],
            'dataFixerArtifact':result['artifactCodeSources']['cleanroom']}
        for key in ('selectionReady', 'selectionPrefixCompleted', 'constructionBoundaryReached', 'selectionDefinitionsVerified',
                    'lateMixinSelectionQualified', 'launchArgumentsReturned', 'vanillaRegistrationReturned',
                    'nativeServerHandlerInitialized'):
            result[key] = True
        result['activeMods'] = context['selection']['requiredMods'].copy()
        result['selectedMods'] = [{'id': mod, 'container': 'net.minecraftforge.fml.common.FMLModContainer',
                                  'version': '1.0', 'source': '/fixture/' + mod + '.jar', 'state': 'UNLOADED',
                                  'nativeLoaded': True, 'instanceCreated': False,
                                  'requirements': [], 'dependencies': [], 'dependants': []}
                                 for mod in result['activeMods']]
        result['mixinConfigurations'] = context['selection']['requiredMixinConfigurations'].copy()
        result['selectionDefinitions'] = {}
        for witness in context['selection']['definitionWitnesses']:
            target = witness['target']; source = (result['artifactCodeSources']['cleanroom'] if witness['artifact'] == 'cleanroom'
                else result['nativeArtifacts'][witness['artifact']]['codeSource'])
            method = {'name': witness.get('methodName', 'handler$native'), 'descriptor': witness['methodDescriptor'],
                      'mixin': witness['mixin'], 'calls': [witness['hook']]}
            methods = [method]
            if 'dispatchMethod' in witness:
                methods.append({'name': witness['dispatchMethod'], 'descriptor': witness['dispatchDescriptor'], 'mixin': '',
                                'calls': [target.replace('.', '/') + '.' + method['name'] + method['descriptor']]})
            result['selectionDefinitions'][target] = {
                'inputCodeSource': source, 'codeSource': source, 'definingLoader': 'net.minecraft.launchwrapper.LaunchClassLoader',
                'targetDefined': True, 'definedBeforeObservation': False, 'definitionCount': 1, 'mixinPhase': 'DEFAULT',
                'inputSha256': witness['inputSha256'], 'definitionSha256': sha256(('defined ' + target).encode()).hexdigest(),
                'methods': methods, 'requiredMixinApplied': True}
        return body

    def check(self, body):
        fixture = NativePackEarlyStageTests()
        return lane.check_selection_result(body, fixture.descriptor(), self.context(), fixture.source_program())

    def test_definitions_and_original_state_qualify_only_pre_construction(self):
        body = self.body(); before = deepcopy(body)
        self.assertEqual([], self.check(body)); self.assertEqual(before, body)
        self.assertEqual('incomplete', body['status'])

    def test_server_owner_requires_original_identity_side_and_unstarted_state(self):
        baseline=self.body()
        for key in baseline['result']['nativeServerOwner']:
            for value in (None, 'unobserved'):
                body=deepcopy(baseline);body['result']['nativeServerOwner'][key]=value
                with self.subTest(key=key,value=value):self.assertTrue(self.check(body))
        for key in ('worldCount','networkEndpointCount','networkConnectionCount'):
            body=deepcopy(baseline);body['result']['nativeServerOwner'][key]=1
            with self.subTest(key=key):self.assertTrue(self.check(body))
        body=deepcopy(baseline);body['result']['nativeEffectiveSide']='CLIENT'
        self.assertTrue(self.check(body))
        body=deepcopy(baseline);del body['result']['nativeServerOwner']
        self.assertTrue(self.check(body))

    def test_queue_without_actual_definition_or_hook_remains_incomplete(self):
        for witness in self.context()['selection']['definitionWitnesses']:
            for missing in ('definition', 'mixin', 'calls', 'raw-bytes'):
                body = self.body(); rows = body['result']['selectionDefinitions']; row = rows[witness['target']]
                if missing == 'definition': del rows[witness['target']]
                elif missing == 'raw-bytes': row['definitionSha256'] = row['inputSha256']
                else: row['methods'][0][missing] = '' if missing == 'mixin' else []
                with self.subTest(target=witness['target'], missing=missing): self.assertTrue(self.check(body))

    def test_merged_hook_without_actual_dispatch_is_incomplete(self):
        body = self.body(); target = self.context()['selection']['definitionWitnesses'][0]['target']
        body['result']['selectionDefinitions'][target]['methods'][1]['calls'] = []
        self.assertTrue(self.check(body))

    def test_preloaded_wrong_source_or_wrong_phase_definition_refuses(self):
        target = self.context()['selection']['definitionWitnesses'][1]['target']
        for key, value in [('definedBeforeObservation', True), ('definitionCount', 2), ('mixinPhase', 'INIT'),
                           ('codeSource', '/fixture/other.jar'), ('inputSha256', '0' * 64), ('observationFailure', 'read failed')]:
            body = self.body(); body['result']['selectionDefinitions'][target][key] = value
            with self.subTest(key=key): self.assertTrue(self.check(body))

    def test_missing_inactive_or_constructed_required_container_refuses(self):
        for change in ('missing', 'inactive', 'constructed', 'order'):
            body = self.body(); result = body['result']
            if change == 'missing': result['activeMods'].remove('codechickenlib')
            elif change == 'inactive': result['selectedMods'][0]['nativeLoaded'] = False
            elif change == 'constructed': result['selectedMods'][0]['instanceCreated'] = True
            else: result['activeMods'].reverse()
            with self.subTest(change=change): self.assertTrue(self.check(body))

    def test_logged_or_thrown_failure_cannot_be_hidden_by_readiness(self):
        for change in ('logged', 'thrown', 'diagnostics-missing'):
            body = self.body()
            if change == 'thrown': body['result']['failure'] = [{'class': 'native.Failure'}]
            else:
                body['result']['nativeDiagnostics'] = [{'severity': 'error', 'logger': 'native', 'message': 'failure',
                                                       'trace': '', 'locationStatus': 'unlocated'}]
                if change == 'logged': body['diagnostics'] = deepcopy(body['result']['nativeDiagnostics'])
            with self.subTest(change=change): self.assertTrue(self.check(body))

    def test_construction_or_missing_continuation_flags_refuse(self):
        for key, value in [('constructionDispatched', True), ('loaderState', 'CONSTRUCTING'), ('selectionReady', False),
                           ('selectionDefinitionsVerified', False), ('nativeServerHandlerInitialized', False),
                           ('launchArgumentCallbacks', []), ('mixinConfigurations', []), ('earlyStage', None)]:
            body = self.body(); body['result'][key] = value
            with self.subTest(key=key): self.assertTrue(self.check(body))


class NativeConstructionStageTests(unittest.TestCase):
    """Partial construction must preserve prerequisites and cannot close R3."""
    def body(self):
        selection = NativeSelectionStageTests().body()
        result = deepcopy(selection['result'])
        result['selectionStage'] = deepcopy(selection['result'])
        result.update(schema='axiom.native-construction-stage.v1', executionStage='construction',
                      stage='original-loader-construction', loaderState='CONSTRUCTING',
                      constructionDispatched=True, constructionDispatchStarted=True, constructionReady=False)
        return {**selection, 'operation': 'native-construction-stage',
                'scope': 'original-server-construction-stage-not-material-initialization', 'result': result}

    def completed_body(self):
        body=self.body();result=body['result'];context=NativeSelectionStageTests().context()
        result.update(constructionReady=True,constructionMethodReturned=True,nativeGroovySandboxCreated=True,
                      loaderState='PREINITIALIZATION',preInitializationDispatched=False)
        result['constructedMods']=[]
        for selected in result['selectedMods']:
            selected['source']=result['artifactCodeSources']['cleanroom']
            result['constructedMods'].append({'id':selected['id'],'container':selected['container'],'state':'CONSTRUCTED',
                'instanceCreated':True,'instanceClass':'original.'+selected['id'],
                'instanceCodeSource':result['artifactCodeSources']['cleanroom']})
        result['nativeGroovyConstruction']={'sandboxClass':'com.cleanroommc.groovyscript.sandbox.GroovyScriptSandbox',
            'engineClass':'com.cleanroommc.groovyscript.sandbox.CustomGroovyScriptEngine',
            'modSupportFrozen':False,'scriptOwnerAssigned':False,
            'bindings':{'Mods':'com.cleanroommc.groovyscript.compat.mods.ModSupport',
                'Log':'com.cleanroommc.groovyscript.sandbox.GroovyLogImpl',
                'EventManager':'com.cleanroommc.groovyscript.event.GroovyEventManager'},
            'externalPlugins':[{'class':row['class'],'codeSource':result['nativeArtifacts'][row['artifact']]['codeSource']}
                for row in context['construction']['groovyPlugins']]}
        result['nativeSusyConstruction']={'irInstanceClass':'cam72cam.immersiverailroading.ImmersiveRailroading',
            'irStockLoaderKeys':context['construction']['susyStockLoaders'].copy(),'supercriticalMaterialModifications':False}
        return body

    def check(self, body):
        return lane.check_construction_result(body, NativeEarlyStageTests().descriptor(),
            NativeSelectionStageTests().context(), NativePackEarlyStageTests().body()['result']['sourceProgram'])

    def test_returned_construction_alone_cannot_qualify_groovy_and_production_integration(self):
        body = self.body()
        body['result'].update(constructionReady=True, constructionMethodReturned=True,
                              nativeGroovySandboxCreated=True, loaderState='PREINITIALIZATION')
        self.assertEqual(['Construction stage alone does not establish Groovy-stage acceptance'], self.check(body))

    def test_native_failure_and_earlier_selection_evidence_are_preserved(self):
        body = self.body()
        body['result']['failure'] = [{'class': 'java.lang.OutOfMemoryError', 'message': 'Java heap space',
                                     'frames': ['native.Original.construct(Original.java:12)']}]
        before = deepcopy(body)
        self.assertIn('Original native construction failed', self.check(body))
        self.assertEqual(before, body)

    def test_actual_saved_acknowledgement_cannot_be_replaced_by_expected_input(self):
        body = self.body(); body['result']['sourceProgram']['sha256'] = 'f' * 64
        self.assertIn('Complete saved Groovy/configuration acknowledgement differs', self.check(body))

    def test_construction_cannot_hide_a_missing_selection_definition(self):
        body = self.body(); body['result']['selectionStage']['selectionDefinitions'] = {}
        self.assertIn('Actual selection definition witnesses are missing', self.check(body))


class NativeGroovyStageTests(unittest.TestCase):
    def body(self):
        prior=NativeConstructionStageTests().completed_body()
        prior['result'].update(constructionReady=True,constructionMethodReturned=True,loaderState='PREINITIALIZATION',
                               preInitializationDispatched=False)
        result=deepcopy(prior['result'])
        result.update(constructionStage=deepcopy(prior['result']),schema='axiom.native-groovy-stage.v1',executionStage='groovy',
            groovyInitializationReady=True,groovyInitializationReturned=True,nativeMapperAdmissionBound=True,
            candidateCompilationStarted=True,foundationBootstrap={'method':'top.outlands.foundation.boot.Foundation#breakModuleAndReflection',
                'methodReturned':True,'nativeClassLoaderCreated':False},
            nativeGroovyInitialization={'modSupportFrozen':True,'scriptOwnerIsOriginalContainer':True,'scriptOwner':'supersymmetry',
                'scriptOwnerAssigned':True,'scriptOwnerClass':'net.minecraftforge.fml.common.InjectedModContainer'},
            nativeGroovyErrors=[],candidateAdmissionViolations=[],candidateResourceFailure=False,candidateLinkageFailure=False,
            nativeConsole={'complete':True},groovyDiagnostics=[],groovyCompilationFailure=False,groovyLoggedError=False,
            nativeScriptIndex=[{'path':'preInit/Original.groovy','classDefined':True,'preprocessorCheckFailed':False}])
        return {**prior,'operation':'native-groovy-stage','scope':'original-server-groovy-stage-not-material-initialization','result':result}
    def check(self, body):
        return lane.check_groovy_result(body,NativeEarlyStageTests().descriptor(),NativeSelectionStageTests().context(),
            NativePackEarlyStageTests().body()['result']['sourceProgram'])
    def test_complete_native_evidence_qualifies_groovy_boundary_but_not_material_initialization(self):
        body=self.body();before=deepcopy(body)
        self.assertEqual([],self.check(body))
        self.assertEqual(before,body)
        self.assertEqual('incomplete',body['status'])
        self.assertIs(body['result']['materialInitializationComplete'],False)
    def test_native_logged_failure_and_console_overflow_are_explicit(self):
        body=self.body();body['result']['nativeConsole']['complete']=False
        body['result']['nativeDiagnostics'].append({'severity':'error','message':'unable to resolve class original.Dependency'})
        body['diagnostics']=body['result']['nativeDiagnostics']
        failures=self.check(body)
        self.assertIn('Original native console evidence exceeded its retained bound',failures)
        self.assertIn('Original Groovy initialization logged native errors',failures)
    def test_ownership_boot_order_and_construction_prerequisites_remain_required(self):
        for key in ('scriptOwnerIsOriginalContainer','modSupportFrozen'):
            body=self.body();body['result']['nativeGroovyInitialization'][key]=False
            self.assertIn('Original Groovy compatibility or script ownership differs',self.check(body))
        body=self.body();body['result']['foundationBootstrap']['nativeClassLoaderCreated']=True
        self.assertIn('Original Foundation VM preparation did not precede native classloader construction',self.check(body))
        body=self.body();body['result']['constructionStage']['constructionMethodReturned']=False
        self.assertIn('Original construction did not return before Groovy initialization',self.check(body))

    def test_file_only_diagnostic_channel_and_index_cannot_be_omitted(self):
        for key in ('groovyDiagnostics','groovyCompilationFailure','groovyLoggedError','nativeScriptIndex'):
            body=self.body();del body['result'][key]
            self.assertTrue(self.check(body),key)
        body=self.body();body['result']['groovyDiagnostics']=[{'severity':'error','message':'Native file-only error'}]
        self.assertIn('Original Groovy diagnostic channels were not preserved in the envelope',self.check(body))
        body['diagnostics']+=body['result']['groovyDiagnostics']
        self.assertIn('Original Groovy diagnostic channels are incomplete or contain native errors',self.check(body))
        body=self.body();body['result']['nativeScriptIndex'][0]['preprocessorCheckFailed']=True
        self.assertIn('Original Groovy indexed script coverage is incomplete',self.check(body))

    def test_construction_flags_do_not_replace_native_instances_plugins_and_effects(self):
        changes=[('constructedMods',[]),('nativeGroovyConstruction',{}),('nativeSusyConstruction',{}),
                 ('constructionEffectsObservationFailure','native observation failed')]
        for field,value in changes:
            body=self.body();body['result']['constructionStage'][field]=value
            self.assertTrue(self.check(body),field)
        body=self.body();body['result']['constructionStage']['constructedMods'][-1]['state']='ERRORED'
        self.assertTrue(self.check(body))
        body=self.body();body['result']['constructionStage']['constructedMods'][-1]['instanceCodeSource']='/different/input.jar'
        self.assertTrue(self.check(body))
        body=self.body();body['result']['constructionStage']['nativeGroovyConstruction']['externalPlugins'].pop()
        self.assertTrue(self.check(body))


class NativePreInitStageTests(unittest.TestCase):
    def test_compressed_console_restores_exact_text_and_preserves_incompleteness(self):
        import base64
        import gzip
        for complete in (True,False):
            text={'head':'original warning µ 😀\n\t'*5000,'tail':'final compiler cause\n'}
            console={'complete':complete,'sha256':'a5'*32,'totalBytes':300000,'retainedBytes':125021,
                'textGzipBase64':base64.b64encode(gzip.compress(json.dumps(text).encode())).decode()}
            response={'diagnostics':[],'result':{'nativeConsole':console,'snapshotEncoding':{
                'schema':'axiom.native-stage-snapshots.v1','diagnosticsPointer':'/diagnostics',
                'nativeDiagnosticCount':0,'groovyDiagnosticCount':0,'consoleEncoding':'gzip-base64-json'}}}
            before=deepcopy(response); restored=lane.expand_stage_evidence(response)['result']['nativeConsole']
            self.assertEqual({**{k:v for k,v in console.items() if k!='textGzipBase64'},**text},restored)
            self.assertEqual(before,response)

    def test_production_collections_restore_with_trailing_bootstrap_failure(self):
        warning={'severity':'warning','message':'original warning'}
        failure={'severity':'error','message':'native bootstrap failure'}
        execution={'diagnostics':[warning,failure],
            'registrationEffects':{'materials':{'entries':{'gregtech:iron':'A'*43}}},
            'customMetaItems':{'items':[{'registryName':'susy:meta_item'}]},
            'nativeInitialization':{'failure':['original cause'],'snapshotEncoding':{
                'schema':'axiom.native-stage-snapshots.v1','diagnosticsPointer':'/execution/diagnostics',
                'nativeDiagnosticCount':1,'groovyDiagnosticCount':0,'nativeDiagnosticsPresent':True,
                'groovyDiagnosticsPresent':True,'diagnosticEnvelopeCount':2,'fingerprintEncoding':'base64url-sha256',
                'executionFields':['registrationEffects','customMetaItems']}}}
        retained=deepcopy(execution);restored=lane.expand_execution_evidence(execution)
        self.assertEqual(retained,execution)
        self.assertEqual([warning],restored['nativeInitialization']['nativeDiagnostics'])
        self.assertEqual([],restored['nativeInitialization']['groovyDiagnostics'])
        self.assertEqual([warning,failure],restored['diagnostics'])
        self.assertEqual('00'*32,restored['registrationEffects']['materials']['entries']['gregtech:iron'])
        self.assertEqual(restored['registrationEffects'],restored['nativeInitialization']['registrationEffects'])
        self.assertEqual(restored['customMetaItems'],restored['nativeInitialization']['customMetaItems'])
        del execution['registrationEffects']
        with self.assertRaises(ValueError):lane.expand_execution_evidence(execution)

    def test_selected_native_values_restore_from_the_production_envelope(self):
        selected={'materials':[{'name':'gregtech:iron','formula':None,'propertyValues':{'TOOL':{'speed':{'type':'float32','value':'NaN'}}}}],
                  'lookups':[{'requested':'gregtech:iron','exactIdentity':True}],'missingMaterials':['gregtech:missing'],
                  'vocabulary':{'properties':['TOOL']},'prefixItems':{'forms':[{'eligible':False}]},
                  'materialBlocks':{'forms':[]},'materialOres':{'forms':[{'generated':[{'ordinaryDrop':{'empty':False}}]}]},
                  'deferredWork':{'phase':'FROZEN'},'selectedObservations':{'status':'observed'}}
        execution={**selected,'diagnostics':[],'nativeInitialization':{'snapshotEncoding':{
            'schema':'axiom.native-stage-snapshots.v1','diagnosticsPointer':'/execution/diagnostics',
            'nativeDiagnosticCount':0,'groovyDiagnosticCount':0,'nativeDiagnosticsPresent':False,
            'groovyDiagnosticsPresent':False,'executionFields':list(selected)}}}
        retained=deepcopy(execution);restored=lane.expand_execution_evidence(execution)
        self.assertEqual(retained,execution)
        for name,value in selected.items():self.assertEqual(value,restored['nativeInitialization'][name])
        del execution['materialOres']
        with self.assertRaises(ValueError):lane.expand_execution_evidence(execution)

    def test_catalog_wire_digest_restores_original_identity_and_metadata(self):
        response={'diagnostics':[],'result':{'registrationEffects':{'materials':{
            'status':'observed','inventoryComplete':True,'entries':{'gregtech:iron':'A'*43}}},
            'snapshotEncoding':{'schema':'axiom.native-stage-snapshots.v1','diagnosticsPointer':'/diagnostics',
                'nativeDiagnosticCount':0,'groovyDiagnosticCount':0,'nativeDiagnosticsPresent':False,
                'groovyDiagnosticsPresent':False,'fingerprintEncoding':'base64url-sha256'}}}
        before=deepcopy(response);expanded=lane.expand_stage_evidence(response)
        self.assertEqual(before,response)
        self.assertEqual({'status':'observed','inventoryComplete':True,'entries':{'gregtech:iron':'00'*32}},
                         expanded['result']['registrationEffects']['materials'])

    def test_stage_references_preserve_earlier_diagnostics_and_do_not_modify_receipt(self):
        warning={'severity':'warning','message':'original warning','location':{'path':'groovy/preInit/Edit.groovy','line':4}}
        error={'severity':'error','message':'later callback failed'}
        receipt={'diagnostics':[warning,error],'result':{'phase':'INITIALIZATION',
            'earlyStage':{'inheritedKeys':[],'values':{'phase':'INIT','nativeDiagnostics':[warning]}},
            'selectionStage':{'inheritedKeys':['earlyStage'],'values':{'phase':'LOADING'}},
            'groovyBoundary':{'inheritedKeys':[],'values':{'nativeDiagnostics':[warning]}},
            'snapshotEncoding':{'schema':'axiom.native-stage-snapshots.v1','diagnosticsPointer':'/diagnostics',
                'nativeDiagnosticCount':1,'groovyDiagnosticCount':1,'nativeDiagnosticsPresent':True,'groovyDiagnosticsPresent':True}}}
        before=deepcopy(receipt);expanded=lane.expand_stage_evidence(receipt)['result']
        self.assertEqual(before,receipt)
        self.assertEqual([warning],expanded['nativeDiagnostics']);self.assertEqual([error],expanded['groovyDiagnostics'])
        self.assertEqual(expanded['earlyStage'],expanded['selectionStage']['earlyStage'])
        self.assertEqual([warning],expanded['groovyBoundary']['nativeDiagnostics'])
        self.assertNotIn('snapshotEncoding',expanded)

    def body(self):
        body=NativeGroovyStageTests().body(); result=body['result']
        boundary={key:deepcopy(value) for key,value in result.items() if key.startswith(('groovy','nativeGroovy','candidate'))
                  or key in ('nativeScriptIndex','nativeMapperAdmissionBound','nativeDiagnostics','nativeDiagnosticsComplete',
                             'preInitializationDispatched','materialInitializationComplete','loaderState')}
        result.update(groovyBoundary=boundary,executionStage='preinit',schema='axiom.native-preinit-stage.v1',
            preInitializationReady=True,preInitializationReturned=True,preInitializationDispatchStarted=True,
            preInitializationDispatched=True,preInitializationDispatchReturned=True,nonRecipeRegistryEventsReturned=True,
            modPreInitDispatchCount=1,loaderState='INITIALIZATION',
            preInitializedMods=[{'id':row['id'],'state':'PREINITIALIZED'} for row in result['selectedMods']])
        result['nativeBiomes']=NativeBiomeObservationTests().body()['nativeBiomes']
        result['nativeBiomes']['biomes']['biomesoplenty:mountain']['codeSource']=result['nativeArtifacts']['mods/biomes-o-plenty.pw.toml']['codeSource']
        return {**body,'operation':'native-preinit-stage','scope':'original-server-preinit-stage-not-material-initialization'}
    def check(self, body):
        return lane.check_preinit_result(body,NativeEarlyStageTests().descriptor(),NativeSelectionStageTests().context(),
            NativePackEarlyStageTests().body()['result']['sourceProgram'])
    def test_complete_preinit_return_does_not_replace_material_effect_observations(self):
        body=self.body();before=deepcopy(body)
        self.assertEqual(['Native preInit effect inventory is unavailable'],self.check(body))
        self.assertEqual(before,body)
    def effects(self):
        catalogs={
            'materials':('native-material-registry','native-material-observation-selected-property-values-v2'),
            'fluids':('native-forge-fluid-registry','native-fluid-default-scalars-v1'),
            'prefixItems':('native-meta-prefix-item-collections','original-prefix-item-variants-and-registry-identities-v1'),
            'materialBlocks':('native-material-block-collections','original-block-item-state-property-and-unifier-identities-v1'),
            'oreBlocks':('native-ore-block-collection','original-ore-stone-variants-and-block-item-registry-identities-v1')}
        effects={'schema':'axiom.native-registration-effects.v1','phase':'FROZEN',
            'customItems':{'status':'reference','sourcePointer':'/result/customMetaItems'},
            'materialFluidBindings':{'status':'observed','bindingsComplete':True,'affectingGaps':[],
                                    'materialsWithFluidProperty':1,'fingerprintedIn':'materials'}}
        for name,(membership,scope) in catalogs.items():
            effects[name]={'status':'observed','inventoryComplete':True,'membership':membership,'stateScope':scope,
                           'entries':{'native:example':'a'*64}}
            if name in ('prefixItems','materialBlocks','oreBlocks'):
                effects[name]['witnesses']=[{'material':'gregtech:iron','generated':[{'empty':False,
                    'registryIdentity':True,'unifierIdentity':False,'materialIdentity':True}]}]
        return {'registrationEffects':effects,
            'nativeMaterialRegistries':{'status':'observed','phase':'FROZEN','totalRegisteredMaterials':1,
                                       'registries':[{'registeredMaterials':1}]},
            'customMetaItems':{'status':'observed','items':[{'registryName':'susy:meta_item','forgeRegistered':True,
                'nativeClassSpace':True,'variants':[{'name':'example','meta':1,'ownerIdentity':True,'nameLookupIdentity':False}]}]},
            'nativeMaterialWitnesses':[{'name':name,'registryIdentity':True,
                'nativePropertyState':{'schema':'axiom.native-material-property-state.v1'}}
                for name in ('gregtech:iron','gregtech:diamond')]}
    def test_current_native_effects_allow_stage_acceptance_without_a_completed_product_claim(self):
        body=self.body();body['result'].update(self.effects());before=deepcopy(body)
        self.assertEqual([],self.check(body));self.assertEqual(before,body)
        self.assertFalse(body['result']['materialInitializationComplete'])
    def test_partial_catalogs_and_wrong_state_scopes_cannot_qualify_preinit_effects(self):
        for family in ('materials','fluids','prefixItems','materialBlocks','oreBlocks'):
            for field,value in [('status','unavailable'),('inventoryComplete',False),('entries',{}),
                                ('entries',{'native:example':'not-a-digest'}),('stateScope','different-scope')]:
                state=self.effects();state['registrationEffects'][family][field]=value
                self.assertTrue(lane.check_preinit_effects(state),(family,field))
    def test_registry_binding_and_custom_owner_gaps_remain_affecting(self):
        state=self.effects();state['nativeMaterialRegistries']['totalRegisteredMaterials']=2
        self.assertTrue(lane.check_preinit_effects(state))
        for key,value in [('bindingsComplete',False),('affectingGaps',['native:example']),('materialsWithFluidProperty',True)]:
            state=self.effects();state['registrationEffects']['materialFluidBindings'][key]=value
            self.assertTrue(lane.check_preinit_effects(state),key)
        for key,value in [('forgeRegistered',False),('nativeClassSpace',False),('variants',[{'meta':1}])]:
            state=self.effects();state['customMetaItems']['items'][0][key]=value
            self.assertTrue(lane.check_preinit_effects(state),key)
    def test_selected_generated_native_identities_are_required_but_unifier_absence_is_preserved(self):
        state=self.effects();self.assertEqual([],lane.check_preinit_effects(state))
        for family in ('prefixItems','materialBlocks','oreBlocks'):
            state=self.effects();state['registrationEffects'][family]['witnesses'][0]['generated'][0]['registryIdentity']=False
            self.assertTrue(lane.check_preinit_effects(state),family)
        state=self.effects();state['nativeMaterialWitnesses'][0]['registryIdentity']=False
        self.assertTrue(lane.check_preinit_effects(state))
    def test_missing_dispatch_registry_events_and_native_mod_states_are_visible(self):
        for key in ('groovyBoundary','preInitializationDispatched','nonRecipeRegistryEventsReturned','preInitializedMods'):
            body=self.body();del body['result'][key]
            self.assertGreater(len(self.check(body)),1,key)
        body=self.body();body['result']['modPreInitDispatchCount']=2
        self.assertIn('Original preInit dispatch count or final state differs',self.check(body))
    def test_later_native_error_is_retained_separately_from_clean_groovy_boundary(self):
        body=self.body();body['result']['failure']=[{'class':'native.Failure','message':'Original callback failed'}]
        body['result']['groovyDiagnostics']=[{'severity':'error','message':'Original callback failed'}]
        body['diagnostics']=body['result']['groovyDiagnostics'];before=deepcopy(body)
        failures=self.check(body)
        self.assertIn('Original preInit failed or its observations are incomplete',failures)
        self.assertIn('Original preInit diagnostics contain errors or are incomplete',failures)
        self.assertNotIn('Original Groovy initialization failed or its observation is incomplete',failures)
        self.assertEqual(before,body)


class NativeBiomeObservationTests(unittest.TestCase):
    def body(self):
        return {'nativeArtifacts':{'mods/biomes-o-plenty.pw.toml':{'codeSource':'/fixture/original-bop.jar'}},
            'nativeBiomes':{'schema':'axiom.native-biome-registrations.v1','status':'observed','observationsComplete':True,
                'affectingGaps':[],'configurationDirectoryIdentity':True,'configurationPath':'config/biomesoplenty/biome_ids.json',
                'serverProxy':'biomesoplenty.core.CommonProxy','modState':'PREINITIALIZED',
                'configuredIds':{'mountain':71,'mystic_grove':-1},'disabledBiomes':['biomesoplenty:mystic_grove'],
                'biomes':{'biomesoplenty:mountain':{'class':'biomesoplenty.common.biome.overworld.BiomeGenMountain',
                    'codeSource':'/fixture/original-bop.jar','nativeClassLoaderIdentity':True,
                    'nativeForgeRegistryIdentity':True,'nativePresentBiomeIdentity':True,'dictionaryTypes':['MOUNTAIN']}}}}
    def test_configured_disabled_biomes_are_preserved_without_inventing_registry_members(self):
        body=self.body();before=deepcopy(body);self.assertEqual([],lane.check_biome_registrations(body));self.assertEqual(before,body)
        body['nativeBiomes']['biomes']['biomesoplenty:mystic_grove']=deepcopy(body['nativeBiomes']['biomes']['biomesoplenty:mountain'])
        self.assertTrue(lane.check_biome_registrations(body))
    def test_counts_and_returned_callbacks_cannot_replace_native_identity_or_config_coverage(self):
        for key,value in [('status','incomplete'),('configurationDirectoryIdentity',False),('serverProxy','fixture.Proxy'),
                          ('modState','CONSTRUCTED'),('configuredIds',{'mountain':71,'missing':72}),('disabledBiomes',[])]:
            body=self.body();body['nativeBiomes'][key]=value
            with self.subTest(key=key):self.assertTrue(lane.check_biome_registrations(body))
        for key,value in [('codeSource','/fixture/other.jar'),('nativeClassLoaderIdentity',False),
                          ('nativeForgeRegistryIdentity',False),('nativePresentBiomeIdentity',False)]:
            body=self.body();body['nativeBiomes']['biomes']['biomesoplenty:mountain'][key]=value
            with self.subTest(key=key):self.assertTrue(lane.check_biome_registrations(body))
    def test_native_worldgen_functions_must_match_independent_saved_keys_weights_and_membership(self):
        path='config/gregtech/worldgen/vein/overworld/platinum_vein.json';name='biomesoplenty:mountain';expected={path:{name:100}}
        original={'nativeWorldgenBiomeBindings':{'schema':'axiom.native-worldgen-biome-bindings.v1','status':'observed',
            'observationsComplete':True,'affectingGaps':[],'definitions':{path:{'nativeDefinitionMembership':True,
                'biomeWeights':{name:{'nativeForgeRegistryIdentity':True,'configuredWeight':100,'nativeWeight':100}}}}}}
        before=deepcopy(original);self.assertEqual([],lane.check_worldgen_biome_bindings(original,expected));self.assertEqual(before,original)
        for change in ('missing-definition','missing-biome','unregistered','wrong-weight','different-saved-weight','incomplete'):
            body=deepcopy(original);row=body['nativeWorldgenBiomeBindings']['definitions'][path]
            if change=='missing-definition':body['nativeWorldgenBiomeBindings']['definitions'].clear()
            elif change=='missing-biome':row['biomeWeights'].clear()
            elif change=='unregistered':row['nativeDefinitionMembership']=False
            elif change=='wrong-weight':row['biomeWeights'][name]['nativeWeight']=0
            elif change=='different-saved-weight':row['biomeWeights'][name].update(configuredWeight=50,nativeWeight=50)
            else:body['nativeWorldgenBiomeBindings']['observationsComplete']=False
            with self.subTest(change=change):self.assertTrue(lane.check_worldgen_biome_bindings(body,expected))






if __name__ == '__main__': unittest.main()
