#!/usr/bin/env python3
"""Build native wheels and check each package in an isolated environment.

This tests native packages only: isolated imports, profile resources, Core
lifecycle, construction, inspection and durable service/cleanup workflows.
It does not launch a game runtime. Build dependencies may be downloaded;
test environments install only from the wheelhouse assembled by this run.
"""

from __future__ import annotations

import argparse
from importlib import metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

from module_packages import check, inventory
from validate_native_artifacts import audit
from native_distribution import current_assembly, verify
from validation_diagnostics import DiagnosticRun, default_directory

ROOT = Path(__file__).resolve().parents[1]
DIAGNOSTICS = None


def run(arguments, *, cwd, environment=None, expected=0):
    if DIAGNOSTICS is not None:
        return DIAGNOSTICS.command(arguments, cwd=cwd, env=environment, timeout=600, expected=expected).stdout
    result = subprocess.run(arguments, cwd=cwd, env=environment, text=True,
                            encoding="utf-8", errors="replace", capture_output=True, timeout=600)
    if result.returncode != expected:
        raise RuntimeError(f"command exited {result.returncode}, expected {expected}: {arguments}\n{result.stdout}\n{result.stderr}")
    return result.stdout


def sample_wheel(root: Path, version: str, *, requires="workbench-api>=0.1,<0.2") -> Path:
    path = root / f"workbench_native_probe-{version}-py3-none-any.whl"
    prefix = f"workbench_native_probe-{version}.dist-info"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("workbench_native_probe.py", (
            "from workbench_api import Module, Capability\n"
            f"def module(): return Module('native-probe', '{version}', (Capability('native-probe.run', ('native-probe',), 'workbench_native_probe:run', 'Conformance probe'),))\n"
            f"def run(argv, *, context):\n context.check_cancelled()\n print('{version}')\n return 0\n"
        ))
        archive.writestr(prefix + "/METADATA", f"Metadata-Version: 2.1\nName: workbench-native-probe\nVersion: {version}\nRequires-Dist: {requires}\n")
        archive.writestr(prefix + "/WHEEL", "Wheel-Version: 1.0\nGenerator: workbench-conformance\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        archive.writestr(prefix + "/entry_points.txt", "[workbench.modules]\nnative-probe = workbench_native_probe:module\n")
        archive.writestr(prefix + "/RECORD", "")
    return path


def validate_atlas_independence(python: Path, *, staging: Path, environment=None):
    """Exercise installed Atlas's declared closure, outside source discovery."""
    return run([str(python), "-I", "-c", r'''
from contextlib import redirect_stdout, redirect_stderr
from importlib import import_module
from importlib.util import find_spec
from io import StringIO
import json
from pathlib import Path
import pkgutil
import sys

from workbench_api import ExecutionContext
import workbench_registration_atlas as registration

for name in ('workbench_pack_program_studio', 'workbench_material_semantics',
             'workbench_shell', 'workbench_profile_supersymmetry'):
    assert find_spec(name) is None, name
for name in ('workbench_atlas', 'workbench_atlas_projection',
             'workbench_atlas_categorical_graph', 'workbench_atlas_recipe_health',
             'workbench_atlas_worldgen'):
    package = import_module(name)
    assert Path(package.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
    for item in pkgutil.walk_packages(package.__path__, name + '.'):
        import_module(item.name)

fixture = Path.cwd() / 'atlas-independent-source'
(fixture / 'groovy').mkdir(parents=True)
source = fixture / 'groovy' / 'test.groovy'
source.write_text('// water and unicode: 水\n', encoding='utf-8')
context = ExecutionContext(fixture, Path.cwd() / 'atlas-state')
module = registration.module()
capability, = (row for row in module.capabilities if row.id == 'atlas.recipes')
package, function = capability.handler.split(':')
handler = getattr(import_module(package), function)

def invoke(*args, expected=0):
    command = ['atlas', 'recipes', *map(str, args)]
    assert tuple(command[:len(capability.command)]) == capability.command
    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = handler(command[len(capability.command):], context=context)
    assert code == expected, (command, code, out.getvalue(), err.getvalue())
    return out.getvalue(), err.getvalue()

record = json.loads(invoke('context', fixture, '--json')[0])
assert record['context_type'] == 'source-only-checkout'
matches = json.loads(invoke('search', fixture, 'water', '--json')[0])['results']
assert len(matches) == 1
selection = matches[0]['selection_id']
record = json.loads(invoke('inspect', fixture, selection, '--json')[0])
assert record['role'] == 'source-occurrence'
source.write_text('// changed water\n', encoding='utf-8')
assert 'stale' in invoke('inspect', fixture, selection, expected=2)[1]
assert 'adapter' in invoke('assess-plan', fixture, 'missing-plan', expected=2)[1]
assert json.loads(invoke('search', fixture, 'water', '--json')[0])['results']
print('PASS installed Atlas imports, source search/inspection, freshness and absent plan adapter without PPS/MS/Shell/profile')
'''], cwd=staging, environment=environment)


def validate_standalone_installation(wheelhouse: Path, *, staging: Path):
    """Exercise shipped files after relocation, outside source import discovery."""
    relocated = staging / "Wheelhouse with spaces 資料"
    shutil.copytree(wheelhouse, relocated)
    for name in ("install_workbench.py", "verify_wheelhouse.py"):
        if (relocated / name).read_bytes() != (ROOT / "tools" / name).read_bytes():
            raise RuntimeError(f"shipped installer input differs from current source: {name}")
    manifest = verify(relocated)
    destination = staging / "Install 資料"
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("PYTHON", "PIP_", "WORKBENCH_"))}
    environment.update(WORKBENCH_STATE_ROOT=str(staging / "Workbench state 資料"),
                       WORKBENCH_CONFIG_HOME=str(staging / "Workbench config 資料"))
    command = [sys.executable, "-E", "-s",
               str((relocated / "install_workbench.py").relative_to(staging)),
               str(relocated.relative_to(staging)), "--destination",
               str(destination.relative_to(staging))]
    run(command, cwd=staging, environment=environment)
    receipt_path = destination / "workbench-install.json"
    receipt = receipt_path.read_bytes()
    if json.loads(receipt)["state"] != "installed":
        raise RuntimeError("shipped installer did not complete")
    # An existing destination must remain usable and keep its original receipt.
    run(command, cwd=staging, environment=environment, expected=1)
    if receipt_path.read_bytes() != receipt:
        raise RuntimeError("rejected reinstall changed the installed receipt")
    relocated.rename(staging / "Archived wheelhouse 資料")
    python = destination / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    executable = destination / ("Scripts/workbench.exe" if os.name == "nt" else "bin/workbench")
    if not executable.is_file():
        raise RuntimeError("shipped installation has no native launcher")

    def invoke(*args, environment=environment):
        return json.loads(run([str(executable), *map(str, args), "--json"],
                              cwd=staging, environment=environment))

    version = invoke("--version")
    if version != {"component_id": "workbench-core", "version": manifest["native_versions"]["workbench-core"]}:
        raise RuntimeError("installed launcher version differs from the selected assembly")
    status = invoke("environment", "status")
    if Path(status["native_core"]["state_root"]) != Path(environment["WORKBENCH_STATE_ROOT"]):
        raise RuntimeError("installed launcher lost the explicit state location")
    invoke("modules", "list")
    invoke("profiles", "list")
    capabilities = invoke("capabilities", "atlas")
    commands = {row["catalog_action"]["command_id"] for row in capabilities["capabilities"]}
    if not {"atlas.recipes-import-capture", "atlas.recipes-routes"} <= commands:
        raise RuntimeError("installed recipe catalog is incomplete")
    # Exercise platform defaults without using this machine's actual user state.
    if sys.platform == "linux" or os.name == "nt":
        defaults = dict(environment)
        defaults.pop("WORKBENCH_STATE_ROOT")
        variable = "LOCALAPPDATA" if os.name == "nt" else "XDG_STATE_HOME"
        defaults[variable] = str(staging / "Platform user state 資料")
        observed = Path(invoke("environment", "status", environment=defaults)["native_core"]["state_root"])
        if not observed.is_relative_to(Path(defaults[variable])):
            raise RuntimeError("platform state discovery escaped the selected user location")
    project = staging / "Project with spaces 資料"
    (project / "groovy").mkdir(parents=True)
    (project / "groovy" / "材料.groovy").write_text("// water and unicode: 水\n", encoding="utf-8")
    invoke("open", project)
    matches = invoke("atlas", "recipes", "search", project, "water")["results"]
    if len(matches) != 1:
        raise RuntimeError("installed Atlas lost its source match in a Unicode path")
    record = invoke("atlas", "recipes", "inspect", project, matches[0]["selection_id"])
    if record["role"] != "source-occurrence":
        raise RuntimeError("installed source evidence acquired an observed-runtime claim")
    run([str(python), "-I", "-c", """
from pathlib import Path
import sys
import workbench_api, workbench_core, workbench_atlas
for module in (workbench_api, workbench_core, workbench_atlas):
    assert Path(module.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()), module.__name__
"""], cwd=staging, environment=environment)
    print("PASS relocated shipped installer, spaces/Unicode paths, no-clobber reinstall, native launcher, user-state discovery, Home and Atlas source search/inspection outside the checkout", flush=True)


def validate(wheelhouse_input: Path | None = None) -> int:
    failures = check(ROOT)
    if failures:
        raise RuntimeError("\n".join(failures))
    rows = inventory(ROOT)
    with tempfile.TemporaryDirectory(prefix="wb-") as temporary:
        staging = Path(temporary)
        wheelhouse = staging / "wheels"
        wheelhouse.mkdir()
        pip = [sys.executable, "-m", "pip"]
        if wheelhouse_input is not None:
            manifest = current_assembly(wheelhouse_input)
            expected = {row["distribution"]: row["version"] for row in rows}
            if manifest["native_versions"] != expected:
                raise RuntimeError("native conformance requires the complete current package inventory")
            private = staging / "assembly"
            (private / "wheels").mkdir(parents=True)
            for name in ("wheelhouse.json", "requirements.lock"):
                shutil.copyfile(wheelhouse_input / name, private / name)
            for row in manifest["wheels"]:
                shutil.copyfile(wheelhouse_input / "wheels" / row["filename"], private / "wheels" / row["filename"])
            if verify(private) != manifest:
                raise RuntimeError("native assembly changed during private admission")
            wheelhouse = private / "wheels"
            if DIAGNOSTICS is not None:
                DIAGNOSTICS.document["metadata"].update(source_sha256=manifest["source_sha256"], wheels=manifest["wheels"], target=manifest["target"])
        else:
            # Reused setuptools build directories can silently retain deleted code.
            # Build from a new source copy containing only repository inputs.
            source_copy = staging / "source"
            owners = tuple(Path(row["path"]).parent for row in rows)
            # This is an input inventory, so never read it through bounded
            # diagnostic tails. NUL separation also preserves unusual names.
            files = subprocess.check_output(
                ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                cwd=ROOT,
            ).split(b"\0")
            for name in sorted(set(files)):
                if not name:
                    continue
                relative = Path(os.fsdecode(name))
                source = ROOT / relative
                if source.is_file() and any(relative.is_relative_to(owner) for owner in owners):
                    if source.is_symlink():
                        raise RuntimeError(f"native source input is a symlink: {relative}")
                    destination = source_copy / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
            sources = [str(source_copy / owner) for owner in owners]
            print("Building API, Core and module wheels", flush=True)
            run([*pip, "wheel", "--wheel-dir", str(wheelhouse), *sources], cwd=staging)
            run([*pip, "download", "--only-binary=:all:", "--dest", str(wheelhouse), f"pip=={metadata.version('pip')}"], cwd=staging)
        for wheel in wheelhouse.glob("workbench_*.whl"):
            failures = audit(wheel)
            if failures:
                raise RuntimeError(f"native artifact audit failed: {wheel.name}: {failures}")
        print("PASS native artifact contents, licenses, notices and complete RECORD hashes", flush=True)

        environment_count = 0

        def environment(name, distribution):
            nonlocal environment_count
            directory = staging / f"e{environment_count}"
            environment_count += 1
            if DIAGNOSTICS is not None:
                DIAGNOSTICS.document["metadata"].setdefault("environments", []).append(
                    {"purpose": name, "path": str(directory)})
            run([sys.executable, "-m", "venv", "--without-pip", str(directory)], cwd=staging)
            python = directory / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            packages = [distribution] if isinstance(distribution, str) else distribution
            run([*pip, "--python", str(python), "install", "--no-index", "--find-links", str(wheelhouse), "pip", *packages], cwd=staging)
            return python

        api = environment("api-only", "workbench-api")
        run([str(api), "-I", "-c", "from importlib.util import find_spec; import workbench_api.sessions; import workbench_api.host_filesystem; assert find_spec('workbench_core') is None; assert not hasattr(workbench_api.sessions, 'RetainedSession')"], cwd=staging)
        run([str(api), "-I", "-c", "from workbench_api.events import EventNormalizer; assert not EventNormalizer().normalize('BUILD FAILED in 1s').outcome_failure"], cwd=staging)
        print("PASS API imports without Core", flush=True)
        run([str(api), "-I", "-c", "from workbench_api.packwiz import client_dependencies; from workbench_api.prism import encode_launch_script, parse_launch_script; fields = dict(mainClass='example.Main', launcher='standard', param=['path with spaces']); assert parse_launch_script(encode_launch_script(fields)) == fields"], cwd=staging)

        core = environment("core-only", "workbench-core")
        selected = dict(os.environ, WORKBENCH_STATE_ROOT=str(staging / "state"), WORKBENCH_CONFIG_HOME=str(staging / "config"), XDG_CACHE_HOME=str(staging / "cache"))
        selected.pop("PYTHONPATH", None)
        selected.pop("WORKBENCH_PACKAGED_SUITE_ROOT", None)
        (staging / "state").mkdir()

        def core_run(args, expected=0):
            return run([str(core), "-I", "-m", "workbench_core", *args], cwd=staging, environment=selected, expected=expected)

        assert json.loads(core_run(["modules", "list", "--json"])) == []
        lease_checks = staging / "installed_file_leases.py"
        shutil.copyfile(ROOT / "core/tests/test_host_file_leases.py", lease_checks)
        run([str(core), "-I", str(lease_checks)], cwd=staging, environment=selected)
        run([str(core), "-I", "-c", "from workbench_api.retained_snapshots import retained_snapshot_provider; reader = retained_snapshot_provider('core', 'workbench-core>=0.1.4,<0.2.0'); assert callable(reader.open_snapshot)"], cwd=staging)
        run([str(core), "-I", "-c", "from workbench_core.environment_preparation import plan, execute, recover; from workbench_core.prism_capture import normalize_handoff; from workbench_core.check_execution import CheckProgress, read_progress; from workbench_api.checks import observation; assert observation('running', 'starting', 'Starting')['state'] == 'running'; from importlib.util import find_spec; assert find_spec('workbench_shell') is None; assert find_spec('workbench_profile_supersymmetry') is None"], cwd=staging)
        if DIAGNOSTICS is not None:
            DIAGNOSTICS.document["metadata"]["environment_preparation"] = (
                "linux-process-execution" if sys.platform == "linux" else "unsupported-host-refusal")
        if sys.platform == "linux":
            run([str(core), "-I", "-c", """
import sys, tempfile
from pathlib import Path
from workbench_core import check_storage as storage
from workbench_core.environment_preparation import _command
from workbench_core.host_services import install_local_host_services
install_local_host_services()
with tempfile.TemporaryDirectory(prefix='installed-preparation-') as temporary:
    root = storage.initialize(Path(temporary))
    attempt = root / '.workbench/environment-attempts/environment-probe'
    attempt.mkdir(parents=True)
    storage.write_json(attempt / 'request.json', {'id': 'installed-preparation-request'})
    work = root / '.workbench/tmp/preparation-probe'
    work.mkdir()
    for label in ('refresh', 'install', 'prism'):
        result = _command(root, attempt, work, label, [sys.executable, '-c', 'pass'], cwd=work, environment={}, cancelled=lambda: False, capture=False)
        assert result['state'] == 'closed' and result['stop_reason'] == 'process-exited'
        assert result['observations'][0]['request_id'] == 'installed-preparation-request'
"""], cwd=staging)
        else:
            run([str(core), "-I", "-c", """
from pathlib import Path
from workbench_core.environment_preparation import plan
root = Path.cwd() / 'unsupported-preparation'
assert not root.exists()
try:
    plan(root, source=None, source_rows=None, candidate_id=None, binding=None,
         requirements=None, dependencies=None, tools=None, accounts=None,
         seeds=None, context=None, provider=None, excluded_roots=None)
except ValueError as error:
    assert 'requires Linux executables' in str(error), str(error)
else:
    raise AssertionError('environment preparation accepted an unsupported host')
assert not root.exists()
print('PASS installed environment preparation refuses this host before side effects; Linux execution not claimed')
"""], cwd=staging)
        core_run(["--help"])
        core_run(["version", "--json"])
        core_run(["storage", "list", "--json"])
        run([str(core), "-I", "-c", "from workbench_core.service.runtime import ServiceRuntimeV3, LocalServiceAuthenticator; from workbench_core.service.host import ServiceHostV3; from workbench_api.service import ServiceHandlerRegistration; from importlib.util import find_spec; assert find_spec('workbench_crucible') is None; assert find_spec('workbench_shell') is None"], cwd=staging)
        core_run(["runtime", "create", "--profile", "absent", "--label", "probe", "--json"], 2)
        rejected = sample_wheel(staging, "9.0.0", requires="workbench-native-deliberately-missing>=1")
        core_run(["modules", "install", str(rejected)], 2)
        assert json.loads(core_run(["modules", "list", "--json"])) == []
        for version in ("0.1.0", "0.2.0"):
            path = sample_wheel(staging, version)
            core_run(["modules", "install" if version == "0.1.0" else "update", str(path)])
            assert core_run(["native-probe"]).strip() == version
        retained = staging / "state/retained-data.txt"
        retained.parent.mkdir(parents=True, exist_ok=True)
        retained.write_text("retain me", encoding="utf-8")
        core_run(["modules", "disable", "native-probe"])
        core_run(["native-probe"], 2)
        core_run(["storage", "list", "--json"])
        core_run(["modules", "enable", "native-probe"])
        core_run(["native-probe"])
        core_run(["modules", "remove", "native-probe"])
        assert retained.read_text(encoding="utf-8") == "retain me"
        assert json.loads(core_run(["modules", "list", "--json"])) == []
        print("PASS Core-only lifecycle, dependency rejection and retained-data protection", flush=True)

        assert json.loads(core_run(["profiles", "list", "--json"])) == []
        for version in ("0.1.0", "0.2.0"):
            path = staging / f"workbench_native_profile_probe-{version}-py3-none-any.whl"
            prefix = f"workbench_native_profile_probe-{version}.dist-info"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("workbench_native_profile_probe.py", "from pathlib import Path\nfrom workbench_api.profiles import Profile\ndef profile(): return Profile('native-profile-probe', 'pack', Path(__file__).parent, {})\n")
                archive.writestr(prefix + "/METADATA", f"Metadata-Version: 2.1\nName: workbench-native-profile-probe\nVersion: {version}\nRequires-Dist: workbench-api>=0.1,<0.2\n")
                archive.writestr(prefix + "/WHEEL", "Wheel-Version: 1.0\nGenerator: workbench-conformance\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
                archive.writestr(prefix + "/entry_points.txt", "[workbench.profiles]\nnative-profile-probe = workbench_native_profile_probe:profile\n")
                archive.writestr(prefix + "/RECORD", "")
            core_run(["profiles", "install" if version == "0.1.0" else "update", str(path)])
            observed, = json.loads(core_run(["profiles", "list", "--json"]))
            assert observed["state"] == "available" and observed["version"] == version
        core_run(["profiles", "disable", "native-profile-probe"])
        observed, = json.loads(core_run(["profiles", "list", "--json"]))
        assert observed["state"] == "disabled"
        core_run(["profiles", "enable", "native-profile-probe"])
        observed, = json.loads(core_run(["profiles", "list", "--json"]))
        assert observed["state"] == "available"
        core_run(["profiles", "remove", "native-profile-probe"])
        assert json.loads(core_run(["profiles", "list", "--json"])) == []
        assert retained.read_text(encoding="utf-8") == "retain me"
        print("PASS installed profile install/update/disable/enable/remove and retained-data protection", flush=True)

        for row in rows:
            if not row["module_id"]:
                continue
            package = row["distribution"] + ("[construction]" if "construction" in row["optional_dependencies"] else "")
            python = environment(row["module_id"], package)
            script = """
from importlib import import_module, metadata
from pathlib import Path
import sys
from workbench_api import ExecutionContext
from workbench_api.profiles import profiles
entry, = metadata.entry_points(group='workbench.modules', name=sys.argv[1])
module = entry.load()()
assert module.id == sys.argv[1]
assert module.version == entry.dist.version
registration = import_module(entry.value.split(':')[0])
assert Path(registration.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
available_profiles = {profile.id for profile in profiles()}
for capability in module.capabilities:
    if not set(capability.requires_profiles) <= available_profiles:
        continue
    package, name = capability.handler.split(':')
    handler = getattr(import_module(package), name)
    try:
        code = handler(['--help'], context=ExecutionContext(Path.cwd(), Path.cwd() / 'state'))
    except SystemExit as exc:
        code = exc.code
    assert code in (None, 0), (capability.id, code)
"""
            run([str(python), "-I", "-c", script, row["module_id"]], cwd=staging, environment=selected)
            if row["module_id"] == "atlas":
                print(validate_atlas_independence(
                    python, staging=staging, environment=selected
                ).strip(), flush=True)
                route_tests = staging / "atlas-route-tests"
                route_tests.mkdir()
                for name in ("test_captured_recipe_routes.py", "test_recipe_routes_cli.py", "test_recipe_browse.py", "test_recipe_session.py"):
                    shutil.copyfile(ROOT / "modules/atlas/tests" / name, route_tests / name)
                run([str(python), "-I", "-m", "unittest", "discover", "-s", str(route_tests),
                     "-p", "test_*.py"], cwd=staging, environment=selected)
                print("PASS installed independent Atlas routes, paged neighborhoods and native JSONL sessions; captured values, unknown craftability, cycles and graph drift", flush=True)
            print(f"PASS isolated {row['module_id']} entry points with declared dependencies", flush=True)
        for row in rows:
            if not row["path"].startswith("profiles/"):
                continue
            package = row["distribution"] + ("[construction]" if "construction" in row["optional_dependencies"] else "")
            python = environment(row["distribution"], package)
            run([str(python), "-I", "-c", """
from importlib import metadata
from pathlib import Path
import sys
from workbench_api.profiles import profiles, profile_scope
from workbench_api.runtime import runtime_provider
from workbench_api.profile_extensions import event_classifiers
from workbench_api.events import EventNormalizer
from workbench_api.console_policy import console_policies
selected = profiles()
assert selected
if any(profile.id == 'cleanroom' for profile in selected):
    assert EventNormalizer(classifiers=event_classifiers()).normalize('BUILD FAILED in 1s').outcome_failure
    with profile_scope(disabled=('cleanroom',)):
        assert not event_classifiers()
        assert not any(profile.id == 'supersymmetry' for profile in profiles())
if any(profile.id == 'supersymmetry' for profile in selected):
    policy, = console_policies()
    assert policy.profile_id == 'supersymmetry' and policy.examples
    from workbench_api.profile_extensions import require_profile_extension, profile_extension_identity
    from workbench_blueprints.profile_construction import recipe_change_authority, quest_change_authority
    recipe_change_authority('supersymmetry')
    quest_change_authority('supersymmetry')
    for group in ('workbench.recipe_observers', 'workbench.material_fluid_observers', 'workbench.material_recipe_observers', 'workbench.material_fluid_policies', 'workbench.runtime_pairs', 'workbench.source_interpreters'):
        require_profile_extension(group, 'supersymmetry')
        assert profile_extension_identity(group, 'supersymmetry')['package_source_sha256']
    from workbench_profile_supersymmetry import installed_material_fluid_runtime
    from workbench_crucible.developer_checks import provider, validate_interpretation
    from types import SimpleNamespace
    checks, identity = provider('supersymmetry')
    logs = {'logs/latest.log': {'state': 'captured', 'text': ''}, 'logs/groovy.log': {'state': 'captured', 'text': '[CLIENT/ERROR] groovy/preInit/Test.groovy: 2: compilation failed\\n'}}
    observed = checks.interpret(SimpleNamespace(sources={'groovy/preInit/Test.groovy': b'def x = (\\n'}), logs, 'nonce', runtime_root=Path('/private/run'))
    validate_interpretation(observed)
    assert observed['outcome'] == 'failed' and observed['observation']['state'] == 'failure'
    assert observed['findings'][0]['location']['start'] == {'line': 2, 'column': 1}
    assert not any(name == 'workbench_shell' or name.startswith('workbench_shell.') for name in sys.modules)
    assert not any(name == 'workbench_core' or name.startswith('workbench_core.') for name in sys.modules)
for profile in selected:
    assert profile.root.resolve().is_relative_to(Path(sys.prefix).resolve())
    for role in profile.resources:
        assert profile.resource(role).is_file()
    if 'worldgen' in profile.resources:
        provider = runtime_provider(profile.id)
        path = provider.resolve_profile_path(Path.cwd(), profile.id)
        assert path == profile.resource('worldgen')
        loaded = provider.load_profile(path, Path.cwd())
        assert loaded['profile_id']
"""], cwd=staging, environment=selected)
            print(f"PASS isolated {row['distribution']} owned resources and providers", flush=True)
        suite = environment("installed-workflows", [row["distribution"] for row in rows])
        local_paths = staging / "local_paths.py"
        shutil.copyfile(ROOT / "validation/native/local_paths.py", local_paths)
        print(run([str(suite), "-I", str(local_paths)], cwd=staging, environment=selected).strip(), flush=True)
        binary_files = staging / "binary_files.py"
        shutil.copyfile(ROOT / "validation/native/binary_files.py", binary_files)
        print(run([str(suite), "-I", str(binary_files)], cwd=staging, environment=selected).strip(), flush=True)
        run([str(suite), "-I", "-c", """
import os, subprocess, sys
from pathlib import Path
from workbench_core.host_services import install_local_host_services
from workbench_shell.feature_change_workspace import _exclusive_record_lock
install_local_host_services()
path = Path.cwd() / 'shell-writer.lock'
with _exclusive_record_lock(path, 'installed Shell writer') as descriptor:
    os.lseek(descriptor, 0, os.SEEK_SET)
    assert os.read(descriptor, 64).decode('ascii').strip() == str(os.getpid())
    script = '''
import os, sys
from workbench_api.host_filesystem import file_lease
from workbench_core.host_services import install_local_host_services
install_local_host_services()
descriptor = os.open(sys.argv[1], os.O_RDWR)
try:
    try:
        with file_lease(descriptor, exclusive=True):
            raise AssertionError('Shell writer did not hold the host lease')
    except BlockingIOError:
        pass
finally:
    os.close(descriptor)
'''
    subprocess.run([sys.executable, '-I', '-c', script, str(path)], check=True, timeout=15)
with _exclusive_record_lock(path, 'released Shell writer'):
    pass
print('PASS installed Shell writer uses the API host lease and releases it')
"""], cwd=staging, environment=selected)
        for row in rows:
            if row["module_id"]:
                run([str(suite), "-I", "-c", script, row["module_id"]], cwd=staging, environment=selected)
        harness = staging / "installed_workflows.py"
        shutil.copy2(ROOT / "validation/native/installed_workflows.py", harness)
        source_only = environment("source-review-without-blueprints", ["workbench-shell", "workbench-profile-supersymmetry"])
        print(run([str(source_only), "-I", str(harness), "--source-only"], cwd=staging, environment=selected), end="", flush=True)
        print(run([str(suite), "-I", str(harness)], cwd=staging, environment=selected), end="", flush=True)
        if wheelhouse_input is not None:
            validate_standalone_installation(wheelhouse_input, staging=staging)
        if wheelhouse_input is not None and current_assembly(wheelhouse_input) != manifest:
            raise RuntimeError("native assembly or source changed during conformance")
    return 0


def main(argv=None) -> int:
    global DIAGNOSTICS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheelhouse", type=Path, help="reuse a verified full native assembly built from this source")
    parser.add_argument("--diagnostics", type=Path, help="new directory for bounded validation logs")
    args = parser.parse_args(argv)
    try:
        with DiagnosticRun(args.diagnostics or default_directory(ROOT, "native-conformance"), "native-conformance", ("conformance",)) as diagnostics:
            DIAGNOSTICS = diagnostics
            with diagnostics.phase("conformance"):
                return validate(args.wheelhouse)
    finally:
        DIAGNOSTICS = None


if __name__ == "__main__":
    raise SystemExit(main())
