#!/usr/bin/env python3
"""Original IVToolkit injection and native bus observations, not launcher parity."""
import argparse
from pathlib import Path

import axiom_pack_configuration_conformance as configuration

CONFIG = 'config/forge_early.cfg'
PLUGIN = 'ivorius.ivtoolkit.IvToolkitLoadingPlugin'
CONTAINER = 'ivorius.ivtoolkit.IvToolkitCoreContainer'
SCOPE = 'original-ivtoolkit-coremod-injection-not-complete-launcher'
BUS_SCOPE = 'original-injected-ivtoolkit-container-only-not-complete-pack-activation'
CLASS_SHA256 = '89e20ba1a6592f695a0f7d1c0513cff24ffc07feed09b41acb4bd9d888dd5d30'
CUSTOM = b'''general {
    B:CUSTOM_BUILT_IN_MOD_VERSION=true
    S:MIXIN_BOOTER_VERSION=12.34
    S:CONFIG_ANY_TIME_VERSION=7.8
}
'''
BLACKLIST = b'''general {
    S:LOADING_PLUGIN_BLACKLIST <
        ivorius.ivtoolkit.IvToolkitLoadingPlugin
     >
}
'''


def corpus(config):
    native = configuration.corpus(config)
    base = native[0]['files']
    return [
        {'name': 'default', 'files': dict(base)},
        {'name': 'custom-versions', 'files': {**base, CONFIG: CUSTOM}},
        {'name': 'ivtoolkit-blacklisted', 'files': {**base, CONFIG: BLACKLIST}, 'coremodDeferred': True},
        {'name': 'default-again', 'files': dict(base)},
        dict(native[-1]),
        {'name': 'developer-correction', 'files': dict(base)},
    ]


def check_coremod(case, body):
    failures = []
    coremod = body.get('bootstrap', {}).get('platformInitialization', {}).get('coremodComposition', {})
    activation = body.get('execution', {}).get('addonDiscovery', {}).get('nativeInjectedCoremodActivation', {})
    if (coremod.get('scope') != SCOPE or coremod.get('launcherCompositionQualified') is not False
            or coremod.get('fullCoremodInjectionExecuted') is not False):
        failures.append('bounded coremod injection was absent or promoted to launcher qualification')
    if case.get('coremodDeferred'):
        if (coremod.get('status') != 'not-admitted'
                or coremod.get('reason') != 'saved-blacklist-requires-native-discovery-decision'
                or coremod.get('nativePluginRegistered') is not False or coremod.get('injectDataExecuted') is not False
                or 'container' in coremod or 'initializationPrefix' in coremod
                or activation.get('status') != 'not-executed' or activation.get('packActivationQualified') is not False
                or 'material-context.ivtoolkit-coremod-blacklisted' not in body.get('execution', {}).get('coverageGaps', [])):
            failures.append('blacklisted plugin was injected or its native-discovery gap was hidden')
        return failures
    if (coremod.get('status') != 'injected' or coremod.get('nativePluginRegistered') is not True
            or coremod.get('injectDataExecuted') is not True or coremod.get('classBytesMatchedOriginalArtifact') is not True
            or coremod.get('artifact') != 'mods/IvToolkit-1.3.3-1.12.jar'
            or coremod.get('artifactSha256') != 'ffb745111790e27cb7810a2e03d5270265dee7ebd3ef98fde897c409d58b1e59'
            or coremod.get('injectedContainerNames') != [CONTAINER]
            or coremod.get('nativeCandidateSelectionExecuted') is not False):
        failures.append('original bound IVToolkit registration/injection effects differ')
    container = coremod.get('container', {})
    if (container.get('class') != CONTAINER or container.get('id') != 'ivtoolkit'
            or container.get('wrapper') != 'net.minecraftforge.fml.common.InjectedModContainer'
            or container.get('version') != '1.3.3-1.12' or container.get('source') != 'minecraft.jar'
            or container.get('nativeNullSourceFallback') is not True):
        failures.append('original injected-container wrapping differs')
    prefix = coremod.get('initializationPrefix', {})
    if (prefix.get('owner') != 'net.minecraftforge.fml.relauncher.CoreModManager'
            or prefix.get('method') != 'axiom$initializeCoremodEnvironment'
            or prefix.get('originalClassSha256') != CLASS_SHA256
            or prefix.get('boundary') != 'after-original-loadPlugins-write-before-root-plugin-loop'
            or type(prefix.get('nativeDeobfuscatedEnvironment')) is not bool
            or prefix.get('side') != 'SERVER' or prefix.get('patchingTransformerRegistered') is not True
            or prefix.get('rootPluginSetupExecuted') is not False
            or prefix.get('queuedTweaks') != ['net.minecraftforge.fml.common.launcher.FMLInjectionAndSortingTweaker',
                                               'org.spongepowered.asm.launch.MixinTweaker']):
        failures.append('original closed initialization prefix or its native side effects differ')
    if (activation.get('scope') != BUS_SCOPE
            or activation.get('method') != 'original LoadController transition(LOADING) and FMLLoadEvent dispatch'
            or activation.get('loaderState') != 'LOADING' or activation.get('containerStates') != {'ivtoolkit': 'LOADED'}
            or activation.get('presentCandidateQueries') != {'ivtoolkit': True}
            or activation.get('activeCandidateOrder') != ['ivtoolkit']
            or activation.get('hostLoaderStateRestored') is not True
            or activation.get('packActivationQualified') is not False
            or activation.get('modInstancesConstructed') is not False
            or activation.get('dependencyOrder', {}).get('versionRequirementsChecked') is not False):
        failures.append('original injected-container bus transition absent or overqualified')
    return failures


def check_result(case, response, exit_code):
    return configuration.check_result(case, response, exit_code) + check_coremod(case, response.get('result', {}))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('java-home', 'engine-home', 'runtime-home', 'pack', 'report'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--whole-pack', action='store_true')
    args = parser.parse_args(argv)
    configuration.run(args.java_home, args.engine_home, args.runtime_home, args.pack, args.report,
                      whole_pack=args.whole_pack, case_selector=corpus, result_checker=check_result,
                      receipt_schema='axiom.pack-coremod-observations.v1')
    return 0


if __name__ == '__main__': raise SystemExit(main())
