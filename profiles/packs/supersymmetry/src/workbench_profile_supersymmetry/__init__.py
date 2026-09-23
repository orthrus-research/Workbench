"""Native supersymmetry profile registration; no implicit target selection."""
from workbench_api.profiles import Profile
from workbench_api.resources import repository_root


def profile():
    return Profile('supersymmetry', 'pack', repository_root(__file__) / 'profiles/packs/supersymmetry', {
        'profile': 'profile.yaml',
        'native-manifest': 'pyproject.toml',
        'registration-source': 'src/workbench_profile_supersymmetry/__init__.py',
        'acquisition': 'acquisition-v1.json',
        'manual-artifacts': 'runtime/manual-artifacts-v1.json',
        'worldgen': 'worldgen/worldgen-iteration-profile-v2.json',
        'registration-catalog': 'registration/catalog-v1.json',
        'source-lock': 'source-locks/legacy-forge/source-lock.json',
        'groovy-program': 'groovy/groovy-program-profile-v1.json',
        'susy-reccomplex-arg3': 'compatibility/recurrent-complex-1.4.8.6-structure-context-arg3-v1.json',
        'susy-reccomplex-susycore-0112-flag': 'compatibility/susycore-0.1.112-reccomplex-flag-v1.json',
    })
