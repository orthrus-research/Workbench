#!/usr/bin/env python3
"""Saved early-platform configuration and native containers, not loader parity."""
import argparse
from hashlib import sha256
from pathlib import Path

import axiom_pack_configuration_conformance as configuration

CONFIG = 'config/forge_early.cfg'
SCOPE = 'original-constructors-and-ivtoolkit-plugin-contract-not-loader-activation'
CUSTOM = b'''general {
    B:CUSTOM_BUILT_IN_MOD_VERSION=true
    S:MIXIN_BOOTER_VERSION=12.34
    S:CONFIG_ANY_TIME_VERSION=7.8
}
'''
BLACKLIST = b'''general {
    S:LOADING_PLUGIN_BLACKLIST <
        developer.ExamplePlugin
     >
}
'''


def corpus(config):
    base = configuration.corpus(config)[0]['files']
    result = []
    for name, early, expected in (
            ('default', None, {}),
            ('custom-versions', CUSTOM, {'custom': True, 'mixin': '12.34', 'anytime': '7.8'}),
            ('custom-disabled', CUSTOM.replace(b'=true', b'=false'), {'mixin': '12.34', 'anytime': '7.8'}),
            ('invalid-switch', CUSTOM.replace(b'=true', b'=not_a_boolean'), {'mixin': '12.34', 'anytime': '7.8'}),
            ('saved-blacklist', BLACKLIST, {'blacklist': ['developer.ExamplePlugin']}),
            ('default-again', None, {})):
        files = dict(base)
        if early is not None: files[CONFIG] = early
        result.append({'name': name, 'files': files, 'platform': expected})
    error = configuration.corpus(config)[-1]
    result.append({**error, 'platform': {}})
    result.append({'name': 'developer-correction', 'files': dict(base), 'platform': {}})
    return result


def check_platform(case, body):
    failures = []
    platform = body.get('bootstrap', {}).get('platformInitialization', {})
    expected = case.get('platform', {}); raw = case['files'].get(CONFIG)
    if (platform.get('status') != 'returned' or platform.get('side') != 'SERVER'
            or platform.get('method') != 'original FMLInjectionData.build -> ConfigManager.register; Loader.injectData'
            or platform.get('minecraftVersion') != '1.12.2' or platform.get('mcpVersion') != '9.42'
            or platform.get('forgeVersion') != '14.23.5.2864'
            or platform.get('inputPresent') is not (raw is not None)
            or raw is not None and platform.get('inputSha256') != sha256(raw).hexdigest()
            or raw is None and 'inputSha256' in platform
            or len(platform.get('workerFileSha256', '')) != 64):
        failures.append('native early configuration/version/input observation differs')
    if (platform.get('customBuiltInVersions') is not expected.get('custom', False)
            or platform.get('mixinBooterVersion') != expected.get('mixin', '11.8')
            or platform.get('configAnytimeVersion') != expected.get('anytime', '3.0')):
        failures.append('saved native early values differ')
    blacklist = platform.get('loadingPluginBlacklist', [])
    if ('blacklist' in expected and blacklist != expected['blacklist']
            or 'blacklist' not in expected and 'zone.rong.mixinbooter.MixinBooterPlugin' not in blacklist):
        failures.append('saved native plugin blacklist differs')
    if (platform.get('launcherCompositionQualified') is not False
            or platform.get('fullCoremodInjectionExecuted') is not False or platform.get('containerScope') != SCOPE):
        failures.append('container prefix promoted to loader qualification')
    rows = platform.get('builtInContainers', [])
    containers = {row.get('id'): row for row in rows}
    versions = {'cleanroom': ('com.cleanroommc.common.CleanroomContainer', '0.6.12-alpha'),
                'mixinbooter': ('zone.rong.mixinbooter.MixinBooterModContainer',
                               expected.get('mixin', '11.8') if expected.get('custom') else '11.8'),
                'configanytime': ('com.cleanroommc.common.ConfigAnytimeContainer',
                                 expected.get('anytime', '3.0') if expected.get('custom') else '3.0')}
    if len(rows) != 3 or set(containers) != set(versions):
        failures.append('original built-in container set differs')
    for name, (owner, version) in versions.items():
        row = containers.get(name, {})
        if (row.get('class') != owner or row.get('version') != version
                or row.get('processedRange') != ('any' if name == 'configanytime' else version)
                or row.get('processedVersion') != ('unknown' if name == 'configanytime' else version)):
            failures.append('original container/version semantics differ: ' + name)
    iv = platform.get('ivToolkit', {}); container = iv.get('container', {})
    if (iv.get('plugin') != 'ivorius.ivtoolkit.IvToolkitLoadingPlugin'
            or iv.get('asmTransformers') != [] or iv.get('nativeJavaCheckReturned') is not True
            or any(iv.get(key) is not False for key in ('setupClassPresent', 'accessTransformerPresent',
                                                       'injectDataExecuted', 'registeredWithCoreModManager'))
            or container.get('class') != 'ivorius.ivtoolkit.IvToolkitCoreContainer'
            or container.get('id') != 'ivtoolkit' or container.get('version') != '1.3.3-1.12'):
        failures.append('original IVToolkit contract absent or full injection claimed')
    linkage = body.get('bootstrap', {}).get('packMaterialLinkage', {})
    if (linkage.get('elementMixinObserved') is not True or linkage.get('orePrefixMixinObserved') is not True
            or linkage.get('wholeInstalledMixinSet') is not False):
        failures.append('existing native material transformations lost or overclaimed')
    return failures


def check_result(case, response, exit_code):
    return configuration.check_result(case, response, exit_code) + check_platform(case, response.get('result', {}))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('java-home', 'engine-home', 'runtime-home', 'pack', 'report'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--whole-pack', action='store_true')
    args = parser.parse_args(argv)
    configuration.run(args.java_home, args.engine_home, args.runtime_home, args.pack, args.report,
                      whole_pack=args.whole_pack, case_selector=corpus, result_checker=check_result,
                      receipt_schema='axiom.pack-platform-observations.v1')
    return 0


if __name__ == '__main__': raise SystemExit(main())
