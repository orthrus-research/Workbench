"""Native cleanroom profile registration; no implicit target selection."""
from workbench_api.profiles import Profile
from workbench_api.resources import repository_root


def profile():
    return Profile('cleanroom', 'platform', repository_root(__file__) / 'profiles/platforms/cleanroom', {'profile': 'provisional.yaml', 'construction': 'new-project-kinds/cleanroom-mod-construction-owner-v2.json', 'mixin-policy': 'mixins/cleanroom-mixin-doctor-policy-v1.json', 'runtime-toolchain': 'runtime-toolchain.json', 'registry-runtime': 'registry-runtime.json', 'axiom-jvm': 'jvm-runtime.json', 'axiom-jvm-windows-x64': 'jvm-runtime-windows-x64.json', 'axiom-native-root': 'native-root-class-space.json', 'axiom-native-libraries': 'native-identity-runtime.json'})
