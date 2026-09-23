"""Saved material checks in the existing workflow; no game-image prerequisite.

Core owns storage and process supervision. Axiom owns native execution and its
domain response. Project Intelligence owns source capture; profiles own context.
"""

import argparse
from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace

from workbench_api import ExecutionContext
from workbench_api.processes import ProcessError, execute_process
from workbench_api.profile_extensions import require_profile_extension, profile_extension_identity
from workbench_core import check_storage as storage
from workbench_core import check_snapshots as snapshots
from workbench_core import check_diagnostics
from workbench_core import check_lifecycle as lifecycle
from workbench_core import check_retention as retention
from workbench_core import process_capture
from workbench_core.check_snapshot_contract import scope_identity
from workbench_core import material_check_setup as setup
from workbench_project_intelligence.working_tree import SourceInputs, capture_source_inputs
from workbench_project_intelligence.saved_candidate import candidate_manifest, stage_candidate
from workbench_pack_program_studio.source_locations import source_location
from .developer_context import observe_developer_context, require_private_storage


AUTHORITY = {"source_mutated": False, "minecraft_launched": False,
             "runtime_image_required": False, "validity_qualified": False,
             "whole_pack_parity": False}
REQUEST = "workbench-material-check-request-v1"
RESULT = "workbench-material-check-result-v1"


def _domain():
    try:
        from workbench_axiom import material_checks
        return material_checks
    except ImportError as exc:
        raise ValueError("material checks require the optional workbench-axiom package") from exc


def _module_identity():
    from workbench_core.modules import discover
    from workbench_core.module_cli import disabled_modules
    from workbench_api.state_paths import default_runtime_state_root
    # Module enablement belongs to Core setup state, not the selected Work
    # Session's independently configurable retained-record directory.
    rows = [row for row in discover(disabled=disabled_modules(default_runtime_state_root())) if row.id == "axiom"]
    if (len(rows) != 1 or rows[0].state != "available" or rows[0].module is None
            or "axiom.material-program" not in {c.id for c in rows[0].module.capabilities}):
        raise ValueError("install and enable Axiom's material-program capability before preparing a material check")
    return rows[0].record()


def _attempt(root, identity):
    if not isinstance(identity, str) or re.fullmatch(r"material-check-[0-9a-f]{32}", identity) is None:
        raise ValueError("select one exact retained material-check attempt")
    return root / ".workbench/check-attempts" / identity


def _reference(path, record):
    return {"owner_id": "axiom", "record_id": record["id"], "record_kind": record["format"],
            "uri": path.as_uri(), "digest": "sha256:" + snapshots.file_content(path)['sha256'],
            "last_verified_state": None, "verified_at": None}


def _sealed(value, kind):
    if storage.seal(kind, {key: row for key, row in value.items() if key != "id"}) != value:
        raise ValueError("retained material check identity changed")
    return value


def _inputs(attempt, candidate, source_dir):
    rows = candidate["files"]
    source = attempt / source_dir
    if storage.tree_manifest(source) != [{**row, "mode": row["mode"] & 0o777} for row in rows]:
        raise ValueError("retained material candidate bytes changed")
    inputs = SourceInputs(json.dumps(candidate["source"], sort_keys=True),
                          tuple((row["path"], (source / row["path"]).read_bytes()) for row in rows),
                          tuple((row["path"], row["mode"]) for row in rows))
    if candidate_manifest(inputs) != candidate:
        raise ValueError("retained material candidate identity changed")
    return inputs


def _program(attempt, candidate, program, program_root, source_dir, archive_name):
    inputs = _inputs(attempt, candidate, source_dir)
    raw, observed = _domain().program_snapshot(inputs.sources, program_root)
    if observed != program or storage.ordinary(attempt / archive_name).read_bytes() != raw:
        raise ValueError("retained material program archive changed")
    return inputs


def _load(root, identity, selection):
    attempt = _attempt(root, identity)
    if root.exists() and not attempt.exists():
        row = next((row for row in lifecycle.history(root)['checks'] if row['attempt_id'] == identity), None)
        if row is not None:
            raise ValueError('retained check details are ' + row['state'] + '; use workbench storage --checks history for recovery')
    request = _sealed(storage.read_json(attempt / "request.json"), "material-check-request")
    if (request.get("format") != REQUEST or request["workspace_uri"] != selection.pack_uri
            or request["selection_id"] != selection.id or request["attempt_id"] != identity
            or request["authority"] != AUTHORITY or request["state"] != "prepared-not-run"):
        raise ValueError("material request differs from selected workspace or authority")
    inputs = _program(attempt, request["candidate"], request["program"], request["program_root"], "source", "program.zip")
    intent = storage.ordinary(attempt / "intent.json").read_bytes()
    expected_intent = b"{}" if request["intent_path"] is None else inputs.sources[request["intent_path"]]
    if intent != expected_intent:
        raise ValueError("retained material intent differs from saved source")
    _domain().intent(intent)
    if request["baseline"] is not None:
        before = request["baseline"]
        _program(attempt, before["candidate"], before["program"], before["program_root"], "baseline-source", "baseline.zip")
    return attempt, request, inputs


def _findings(response, attempt, request):
    if response is None:
        return []
    result = []
    for index, finding in enumerate(_domain().findings(response)):
        baseline = finding["side"] == "baseline"
        owner = request["baseline"] if baseline else request
        if owner is None:
            raise ValueError("native result names an unselected baseline")
        location = finding["nativeLocation"]
        row = {**finding, "id": "diagnostic-" + str(index), "candidate_id": owner["candidate"]["id"],
               "location": None, "location_precision": "unlocated"}
        if location is not None:
            path = owner["program"]["paths"].get(location.get("path"))
            line = location.get("line")
            if path is not None and type(line) is int and line > 0:
                raw = (attempt / ("baseline-source" if baseline else "source") / path).read_bytes()
                # A line anchor is honest for both stack frames and compiler lines.
                # Preserve native columns separately rather than guessing encoding.
                lines = raw.split(b"\n")
                if line <= len(lines):
                    start = sum(len(part) + 1 for part in lines[:line - 1])
                    row["location"] = source_location(raw, path, start, start)
                    row["location_precision"] = "native-line-anchor"
                else:
                    row["location_precision"] = "native-line-out-of-range"
        result.append(row)
    return result


def _verify_response(response, request):
    domain = _domain()
    domain.verify_native_sources(response, request["program"],
                                 None if request["baseline"] is None else request["baseline"]["program"])
    binding = request["inputs"]
    if response.get("invocation", {}).get("installationSha256") != binding["engineManifestSha256"]:
        raise ValueError("native result differs from selected engine installation")
    body = response.get("result", {})
    bodies = [body]
    if "candidate" in body and "baseline" in body:
        bodies += [body[side].get("result", {}) for side in ("candidate", "baseline")]
    for value in bodies:
        for key in ("runtimeManifestSha256", "contextPolicySha256", "admissionPolicySha256", "context"):
            if key in value and value[key] != binding[key]:
                raise ValueError("native result differs from confirmed " + key)


def _verify_record(record, attempt, request, selection):
    _sealed(record, "material-check-result")
    if (record.get("format") != RESULT or record["request_id"] != request["id"]
            or record["attempt_id"] != request["attempt_id"] or record["workspace_uri"] != selection.pack_uri
            or record["selection_id"] != selection.id or record["candidate_id"] != request["candidate"]["id"]
            or record["authority"] != AUTHORITY):
        raise ValueError("retained material result belongs to another request")
    if record["native"] is not None:
        _verify_response(record["native"], request)
    _capture_files(attempt, request, record)
    if record["findings"] != _findings(record["native"], attempt, request):
        raise ValueError("retained material source evidence changed")
    from . import developer_material_delivery as delivery
    delivery.verify(attempt, request, record)


def _capture_files(attempt, request, record=None):
    directory = attempt / 'native-process'
    native = None if record is None else record['native']
    reference = None if native is None else native.get('invocation', {}).get('capture')
    if not directory.exists() and not directory.is_symlink():
        if reference is not None:
            raise ValueError('native response capture is unavailable')
        return {}
    expected_id = None
    if native is not None:
        if (not isinstance(reference, dict) or set(reference) != {'format', 'binding', 'id'}
                or reference['format'] != process_capture.FORMAT or reference['binding'] != request['id']):
            raise ValueError('native response capture belongs to another request')
        expected_id = reference['id']
    return {role: path.relative_to(attempt).as_posix() for role, path in
            process_capture.retained_files(directory, binding=request['id'], expected_id=expected_id,
                                           verify=record is not None).items()}


def reopen(root, identity, selection):
    """Legacy complete response for existing show/execute and owner references.

    R7-03 migrates client response presentation. Routine snapshot/history/detail
    APIs below preserve custody without parsing this full compatibility response.
    """
    attempt, request, _ = _load(root, identity, selection)
    path = attempt / "result.json"
    if not path.exists():
        if (attempt / snapshots.DIRECTORY).exists():
            raise ValueError('complete snapshot retained; legacy result representation is unavailable; use snapshot detail or export access')
        return request, _reference(attempt / "request.json", request)
    record = storage.read_json(path, byte_limit=None)
    _verify_record(record, attempt, request, selection)
    return record, _reference(path, record)


def publish_snapshot(root, identity, selection, *, cancelled=lambda: False):
    """Convert one verified retained result through the production Core writer."""
    from workbench_axiom import retained_snapshots as domain
    from workbench_core import check_snapshot_contract, check_snapshot_index
    attempt, request, _ = _load(root, identity, selection)
    roles = [('intent', 'intent.json'), ('request', 'request.json'), ('saved-program', 'program.zip')]
    if request['baseline'] is not None:
        roles.append(('saved-baseline-program', 'baseline.zip'))
    roles += list(_capture_files(attempt, request).items())
    roles += list(check_diagnostics.retained_files(attempt).items())
    retained = [{'role': role, 'content': {**snapshots.file_content(attempt / path, cancelled),
                 'media_type': ('application/zip' if path.endswith('.zip') else
                                'application/octet-stream' if path.endswith('.raw') else 'application/json')}}
                for role, path in sorted(roles)]
    producer = sha256(storage.canonical({Path(module.__file__).name: sha256(Path(module.__file__).read_bytes()).hexdigest()
        for module in (domain, snapshots, check_snapshot_index, check_snapshot_contract, _domain())})).hexdigest()
    producer = sha256(storage.canonical({'snapshot_components': producer,
                       'shell': sha256(Path(__file__).read_bytes()).hexdigest()})).hexdigest()

    def verify(record):
        _, current, _ = _load(root, identity, selection)
        if current != request:
            raise ValueError('retained material request changed during snapshot publication')
        for (role, path), retained_input in zip(sorted(roles), retained):
            if snapshots.file_content(attempt / path, cancelled) != {
                    key: retained_input['content'][key] for key in ('sha256', 'bytes')}:
                raise ValueError('retained material input bytes changed during snapshot publication: ' + role)
        _verify_record(record, attempt, request, selection)
        domain.verify_references(record)

    manifest = snapshots.publish(attempt, attempt / 'result.json', scope=domain.scope(request), verify=verify,
        describe=lambda record: domain.describe(record, request, retained_inputs=retained, producer_sha256=producer),
        cancelled=cancelled)
    register_snapshot(root, identity, selection)
    return manifest


def register_snapshot(root, identity, selection):
    attempt, request, _ = _load(root, identity, selection)
    inputs = {'intent': 'intent.json', 'request': 'request.json', 'saved-program': 'program.zip'}
    inputs.update(_capture_files(attempt, request))
    inputs.update(check_diagnostics.retained_files(attempt))
    sources = ['source']
    if request['baseline'] is not None:
        inputs['saved-baseline-program'] = 'baseline.zip'
        sources.append('baseline-source')
    return lifecycle.register(root, attempt, inputs=inputs, source_directories=sources,
        context={'selection_id': request['selection_id'], 'workspace_uri': request['workspace_uri'],
                 'context_id': request['inputs']['context']['id'], 'owner': 'axiom'},
        reproduction=[{'role': role, 'path': value} for role, value in request['paths'].items()])


@contextmanager
def open_snapshot(root, identity, selection, *, cancelled=lambda: False):
    """Owner-authorized read lease with current retained-source verification."""
    from workbench_axiom import retained_snapshots as domain
    attempt, request, _ = _load(root, identity, selection)
    expected = {'attempt_id': identity, 'request_id': request['id'], 'bindings': domain.bindings(request)}
    with snapshots.Snapshot(attempt, scope=lambda manifest: domain.resolve_scope(request, manifest, scope_identity=scope_identity), expected=expected,
                            supported_schemas=domain.SUPPORTED_SCHEMAS, cancelled=cancelled) as opened:
        yield opened


def snapshot_query(root, identity, selection, query, *, cancelled=lambda: False):
    with open_snapshot(root, identity, selection, cancelled=cancelled) as opened:
        return opened.query(query)


def rebuild_snapshot(root, identity, selection, *, cancelled=lambda: False):
    from workbench_axiom import retained_snapshots as domain
    attempt, request, _ = _load(root, identity, selection)
    return snapshots.rebuild(attempt, scope=lambda manifest: domain.resolve_scope(request, manifest, scope_identity=scope_identity),
        expected={'attempt_id': identity, 'request_id': request['id'], 'bindings': domain.bindings(request)}, cancelled=cancelled)


def _history_record(root, identity, selection):
    if (_attempt(root, identity) / snapshots.DIRECTORY).exists():
        with open_snapshot(root, identity, selection) as opened:
            summary = opened.publication['summary']
            from workbench_axiom import retained_snapshots as domain
            from workbench_core.check_snapshot_evolution import interpretation
            return {'attempt_id': identity, 'state': summary['state'], 'record_id': opened.manifest['result_id'],
                    'interpretation': interpretation(opened.manifest, domain.SUPPORTED_SCHEMAS, scope_supported=opened.scope_supported)}
    result, _ = reopen(root, identity, selection)
    return {'attempt_id': identity, 'state': result['state'], 'record_id': result['id']}


def _snapshot_finding(opened, diagnostic_id, attempt, request):
    """Reconcile one indexed finding with its original native diagnostic/source.

    This runs the existing projection over one retained diagnostic, preserving
    its original side, pointers and source anchoring without loading all results.
    """
    match = re.fullmatch(r'diagnostic-([0-9]+)', diagnostic_id or '')
    if match is None:
        raise ValueError('select an exact located retained material diagnostic')
    finding = opened.read_record('findings', 'item:' + str(int(match.group(1))))
    side = finding.get('side')
    if side not in {'baseline', 'candidate'} or side == 'baseline' and request['baseline'] is None:
        raise ValueError('retained diagnostic names an unselected side')
    paired = request['baseline'] is not None
    prefix = '/result/' + side if paired else ''
    admission = finding.get('channel') == 'source-admission'
    diagnostic_prefix = prefix + ('/result/sourceAdmission/findings/' if admission else '/result/execution/diagnostics/')
    pointer = finding.get('pointer', '')
    if not pointer.startswith(diagnostic_prefix) or not re.fullmatch('[0-9]+', pointer[len(diagnostic_prefix):]):
        raise ValueError('retained native diagnostic pointer differs')
    ordinal = int(pointer[len(diagnostic_prefix):])
    section = (side + '-' if paired else '') + ('admission-findings' if admission else 'diagnostics')
    diagnostic = opened.read_record(section, 'item:' + str(ordinal))
    response = {'result': {'sourceAdmission': {'findings': [diagnostic]}}} if admission else {
        'result': {'execution': {'diagnostics': [diagnostic]}}}
    if paired:
        response = {'result': {side: response, ('candidate' if side == 'baseline' else 'baseline'):
                              {'result': {'execution': {'diagnostics': []}}}}}

    def restore_pointer(value, field=''):
        if isinstance(value, str) and field.lower().endswith('pointer') and value.startswith(diagnostic_prefix + '0'):
            return pointer + value[len(diagnostic_prefix + '0'):]
        if isinstance(value, list):
            return [restore_pointer(item, field) for item in value]
        if isinstance(value, dict):
            return {key: restore_pointer(item, key) for key, item in value.items()}
        return value

    expected = [dict(restore_pointer(row), id=diagnostic_id) for row in _findings(response, attempt, request)]
    if finding not in expected or finding.get('id') != diagnostic_id or finding.get('location') is None:
        raise ValueError('retained material source evidence changed or is unlocated')
    return finding


def _complete_live_program(selection, program):
    # Ignored source or configuration cannot silently disappear from a check.
    paths = program["paths"]
    config = Path(paths["groovy/runConfig.json"])
    root = selection.workspace / config.parent.parent
    observed = {}
    for directory in _domain().PROGRAM_DIRECTORIES:
        target = root / directory
        if directory == "config" and not target.exists() and not target.is_symlink():
            continue  # A bounded program may have no configuration directory.
        for row in storage.tree_manifest(target):
            observed[directory + "/" + row["path"]] = (row["size"], row["sha256"])
    expected = {row["path"]: (row["size"], row["sha256"]) for row in program["files"]}
    if observed != expected:
        raise ValueError("complete selected groovy/config directories differ from captured Git source (including ignored files)")


def _binding(selection, paths, context_id):
    module = _module_identity()
    engine = storage.ordinary(Path(paths["engine_home"]), directory=True)
    runtime = storage.ordinary(Path(paths["runtime_home"]), directory=True)
    storage.ordinary(runtime / "runtime.json")
    java = storage.ordinary(Path(paths["java"]))
    from workbench_axiom.java_runtime import java_tool
    if java != java_tool(java.parent.parent) or not os.access(java, os.X_OK):
        raise ValueError("select the explicit Cleanroom JDK Java executable for this host")
    value = _domain().inputs_identity(engine, runtime, selection.pack_profile, context_id, selection.platform_profile)
    # Read actual runtime bytes on every resolution. A saved path or unchanged
    # manifest alone cannot hide a replaced class, library or candidate artifact.
    runtime_manifest = storage.read_json(runtime / "runtime.json")
    declared = runtime_manifest.get("files")
    observed = [{key: row[key] for key in ("path", "size", "sha256")}
                for row in storage.tree_manifest(runtime, exclude=("runtime.json",))]
    if (not isinstance(declared, list) or any(not isinstance(row, dict) or set(row) != {"path", "size", "sha256"}
                                            for row in declared)
            or sorted(declared, key=lambda row: row["path"]) != observed):
        raise ValueError("selected Axiom runtime files differ from their manifest; prepare verified inputs again")
    toolchain = storage.tree_manifest(java.parent.parent, contained_file_links=True)
    platform = require_profile_extension("workbench.axiom_targets", selection.platform_profile).jvm_policy()
    policy = platform["policy"]
    expected_jvm = policy["runtimeFiles"] + policy["compilerFiles"]
    actual_jvm = {row["path"]: {key: row[key] for key in ("path", "size", "sha256")} for row in toolchain}
    if (platform["profile"] != selection.platform_profile or policy.get("profile") != selection.platform_profile
            or policy.get("schema") != "axiom.jvm-runtime.v1"
            or runtime_manifest.get("runtimeInputs") != expected_jvm
            or any(actual_jvm.get(row["path"]) != row for row in expected_jvm)):
        raise ValueError("selected runtime and Java files differ from the installed platform JVM policy")
    return {**value, "module": module, "toolchainFiles": toolchain,
            "platformJvmPolicySha256": platform["sha256"], "jvmSelectionStatus": policy["selectionStatus"],
            "platformOwner": profile_extension_identity("workbench.axiom_targets", selection.platform_profile),
            "inputIdentityScope": "before-and-after-invocation; native worker verifies runtime files"}


def _preparation_requirements(selection, context_id):
    catalog = _domain().contexts(selection.pack_profile)
    context = next((row for row in catalog["policy"]["contexts"] if row["id"] == context_id), None)
    if (context is None or context["platformProfile"] != selection.platform_profile
            or context["side"] != "server"):
        raise ValueError("native input preparation requires a selected SERVER context for this platform")
    platform = require_profile_extension("workbench.axiom_targets", selection.platform_profile)
    if not callable(getattr(platform, "preparation_inputs", None)):
        raise ValueError("selected platform does not declare native input preparation")
    requirements = platform.preparation_inputs()
    if requirements["profile"] != selection.platform_profile:
        raise ValueError("native input preparation differs from the installed platform policy")
    pack = require_profile_extension("workbench.axiom_targets", selection.pack_profile)
    selected = pack.preparation_inputs(context_id) if callable(getattr(pack, "preparation_inputs", None)) else None
    if selected is not None:
        if (selected["profile"] != selection.pack_profile
                or any(selected[key] != requirements[key] for key in ("schema", "side", "inputStage"))):
            raise ValueError("native input preparation differs from the installed pack policy")
        requirements = {**requirements, "profile": selection.pack_profile,
            "policySha256": {**{selection.platform_profile + "/" + key: value for key, value in requirements["policySha256"].items()},
                             **{selection.pack_profile + "/" + key: value for key, value in selected["policySha256"].items()}},
            "artifacts": [*requirements["artifacts"], *selected["artifacts"]]}
    return requirements, catalog, context


def _preparation_binding(selection, requirements, context_id):
    module = _module_identity()
    expected, catalog, context = _preparation_requirements(selection, context_id)
    if requirements != expected:
        raise ValueError("native input preparation differs from the installed profile policies")
    return {"context": context, "contextPolicySha256": catalog["sha256"], "profileOwner": catalog["owner"],
            "platformOwner": profile_extension_identity("workbench.axiom_targets", selection.platform_profile),
            "module": module}


def _assemble_prepared_runtime(selection, root, context_id, prepared, engine, java, cancelled):
    from workbench_axiom import native_assembly
    engine, java = Path(engine).absolute(), Path(java).absolute()
    from workbench_axiom.java_runtime import java_tool
    if java != java_tool(java.parent.parent):
        raise ValueError("select the explicit Cleanroom JDK Java executable for this host")
    java_home = java.parent.parent

    def observe():
        return {"preparation": _preparation_binding(selection, prepared["requirements"], context_id),
                "assembly": native_assembly.assembly_inputs(engine, java_home, selection.pack_profile,
                                                             selection.platform_profile, context_id)}

    def build(artifact_root, output, work):
        logs = work / "logs"
        logs.mkdir(mode=0o700)
        sequence = 0

        def run(label, argv, cwd):
            nonlocal sequence
            sequence += 1
            name = str(sequence).zfill(2) + "-" + label
            command = list(map(str, argv))
            storage.write_json(logs / (name + "-invocation.json"), {"command": command, "cwd": str(cwd),
                               "timeout_seconds": None, "output_limit": None})
            result = execute_process(command, cwd=cwd, stdin=b"", environment={"LANG": "C.UTF-8"},
                cancelled=SimpleNamespace(is_set=cancelled), timeout_seconds=None, output_limit=None, input_limit=None)
            (logs / (name + ".stdout")).write_bytes(result.stdout)
            (logs / (name + ".stderr")).write_bytes(result.stderr)
            storage.write_json(logs / (name + "-result.json"), {"exit_code": result.exit_code})
            if result.exit_code:
                raise ValueError("Original native assembly step " + label + " exited " + str(result.exit_code)
                                 + "; retained output: " + str(logs / (name + ".stderr")))

        native_assembly.assemble_runtime(engine, java_home, artifact_root, output, work,
                                         selection.pack_profile, selection.platform_profile, context_id, run)

    runtime = setup.prepare_runtime(root, selection.id, context_id, prepared, observe, build, cancelled=cancelled)
    if cancelled():
        raise ValueError("native runtime preparation cancelled before setup publication")
    paths = {"engine_home": str(engine), "runtime_home": str(runtime), "java": str(java)}
    return setup.configure(root, selection.id, context_id, paths, ".",
                           lambda selected, context: _binding(selection, selected, context))


def _explicit_paths(args):
    values = {name: getattr(args, name, None) for name in ("engine_home", "runtime_home", "java")}
    supplied = [value is not None for value in values.values()]
    if any(supplied) and not all(supplied):
        raise ValueError("provide engine-home, runtime-home and java together, or omit all three to use saved setup")
    return {name: str(value.absolute()) for name, value in values.items()} if all(supplied) else None


def _selected_inputs(selection, root, args):
    paths = _explicit_paths(args)
    if paths is not None:
        return paths, _binding(selection, paths, args.context), args.program_root or ".", None
    configured = setup.resolve(root, selection.id, args.context,
                               lambda selected, context: _binding(selection, selected, context))
    return (configured["paths"], configured["inputs"],
            args.program_root or configured["program_root"], configured["id"])


def _prepare(selection, root, state, args):
    if selection.mod_uri is not None:
        raise ValueError("material preflight selects a pack program, not a constituent-mod build")
    operation = observe_developer_context(selection)
    paths, binding, program_root, setup_id = _selected_inputs(selection, root, args)
    captured = capture_source_inputs(selection.workspace)
    if captured.observation != operation.as_dict()["source"]:
        raise ValueError("source changed while preparing material check")
    domain = _domain()
    raw, program = domain.program_snapshot(captured.sources, program_root)
    _complete_live_program(selection, program)
    intent_path = None if args.request is None else str(domain.portable(args.request))
    if intent_path is not None and intent_path not in captured.sources:
        raise ValueError("material intent must be a saved, nonignored file in the selected checkout")
    intent = b"{}" if intent_path is None else captured.sources[intent_path]
    domain.intent(intent)
    names = set(program["paths"].values())
    if intent_path is not None:
        names.add(intent_path)
    inputs = SourceInputs(captured.observation_json, tuple((name, captured.sources[name]) for name in sorted(names)),
                          tuple((name, mode) for name, mode in captured.modes if name in names))
    baseline, before_inputs, before_zip = None, None, None
    if args.baseline is not None:
        before_dir, previous, before_inputs = _load(root, args.baseline, selection)
        if previous["inputs"]["context"] != binding["context"]:
            raise ValueError("baseline belongs to another material context")
        baseline = {key: previous[key] for key in ("attempt_id", "program_root", "program", "candidate")}
        baseline["request_id"] = previous["id"]
        baseline["expectation_policy"] = "current-request-intent-evaluated-for-both-fresh-workers"
        before_zip = (before_dir / "program.zip").read_bytes()
    operation.require_fresh()
    attempt = storage.allocate_attempt(root, "material-check")
    candidate = stage_candidate(inputs, attempt / "source")
    storage.write_bytes(attempt / "program.zip", raw)
    storage.write_bytes(attempt / "intent.json", intent)
    if baseline is not None:
        stage_candidate(before_inputs, attempt / "baseline-source")
        storage.write_bytes(attempt / "baseline.zip", before_zip)
    operation.require_fresh()
    request = storage.seal("material-check-request", {
        "format": REQUEST, "state": "prepared-not-run", "attempt_id": attempt.name,
        "diagnostic_delivery_format": "workbench-material-diagnostic-view-v1",
        "workspace_uri": selection.pack_uri, "selection_id": selection.id,
        "operation_id": operation.id, "candidate": candidate,
        "program_root": program_root, "program": program, "intent_path": intent_path,
        "baseline": baseline, "paths": paths, "inputs": binding, "setup_id": setup_id, "authority": AUTHORITY,
    })
    storage.write_json(attempt / "request.json", request)
    return request, _reference(attempt / "request.json", request)


class _Cancellation:
    def __init__(self, attempt, request, outer):
        self.attempt, self.request, self.outer = attempt, request, outer

    def is_set(self):
        if self.outer():
            return True
        path = self.attempt / "cancel.json"
        if not path.exists():
            return False
        if storage.read_json(path) != {"request_id": self.request["id"]}:
            raise ValueError("material cancellation belongs to another request")
        return True


def _execute(selection, root, state, identity, confirmation, cancelled):
    attempt, request, _ = _load(root, identity, selection)
    if confirmation != request["id"]:
        raise ValueError("confirm the exact prepared material request ID")
    with lifecycle.lease(root), storage.execution_lock(attempt):
        if (attempt / "started.json").exists():
            raise ValueError("material attempt was already started; prepare a new check")
        operation = observe_developer_context(selection)
        if (operation.id != request["operation_id"]
                or capture_source_inputs(selection.workspace).observation != request["candidate"]["source"]):
            raise ValueError("source or developer context changed; prepare a fresh material check")
        _complete_live_program(selection, request["program"])
        if _binding(selection, request["paths"], request["inputs"]["context"]["id"]) != request["inputs"]:
            raise ValueError("selected Axiom inputs changed; prepare a fresh material check")
        operation.require_fresh()
        storage.write_json(attempt / "started.json", {"request_id": request["id"]})
        token = _Cancellation(attempt, request, cancelled)
        native, failure, code = None, None, None
        try:
            from workbench_axiom.cli import invoke
            args = SimpleNamespace(**{key: Path(value) for key, value in request["paths"].items()},
                                   program=attempt / "program.zip", request=attempt / "intent.json",
                                   baseline_program=None if request["baseline"] is None else attempt / "baseline.zip",
                                   profile=selection.pack_profile, context=request["inputs"]["context"]["id"])
            response, code = invoke("material-program", args, ExecutionContext(attempt, state, token),
                                    capture_directory=attempt / 'native-process', capture_binding=request['id'])
            _verify_response(response, request)
            native = response
            if _binding(selection, request["paths"], args.context) != request["inputs"]:
                raise ValueError("selected Axiom inputs changed during invocation; result is not qualified")
            if token.is_set():
                raise ProcessError("material check cancelled before result retention")
        except (OSError, ValueError, TypeError, KeyError, ProcessError) as exc:
            failure = {"type": type(exc).__name__, "message": str(exc), "meaning": "incomplete-invocation-not-source-invalidity"}
        record = {
            "format": RESULT, "state": "completed" if failure is None else "incomplete",
            "attempt_id": identity, "request_id": request["id"], "workspace_uri": selection.pack_uri,
            "selection_id": selection.id, "candidate_id": request["candidate"]["id"],
            "native": native, "native_exit_code": code, "failure": failure,
            "findings": _findings(native, attempt, request), "authority": AUTHORITY,
            "process_owner": "core", "temporary_storage_owner": "core-and-isolated-axiom-worker",
        }
        from . import developer_material_delivery as delivery
        delivered = delivery.publish(attempt, request, record)
        record['diagnostic_delivery'] = delivered['id']
        record = storage.seal("material-check-result", record)
        # Complete native evidence follows the suspended MVP output policy
        # through retention and reopening, including source/history consumers.
        storage.write_json(attempt / "result.json", record, byte_limit=None)
        if not token.is_set():
            try:
                publish_snapshot(root, identity, selection, cancelled=token.is_set)
            except snapshots.SnapshotCancelled:
                # Original evidence is already durable; cancelled derived work
                # remains journaled and may be retried without native execution.
                pass
        return record, _reference(attempt / 'result.json', record)


def run(selection, argv, *, state_root, cancelled=lambda: False):
    root = require_private_storage(selection, state_root) / 'developer-checks'
    if argv and argv[0] == 'retention':
        return _run(selection, argv, state_root=state_root, cancelled=cancelled)
    if root.exists():
        with lifecycle.lease(root):
            result = _run(selection, argv, state_root=state_root, cancelled=cancelled)
    else:
        result = _run(selection, argv, state_root=state_root, cancelled=cancelled)
    if argv and argv[0] == 'contexts':
        try:
            result['result']['storage'] = retention.status(root)
        except (OSError, ValueError) as exc:
            result['result']['storage'] = {'before_work_notice': 'Storage policy is unavailable; automatic maintenance cannot run. ' + str(exc)}
    if argv and argv[0] in {'run', 'execute'} and root.exists():
        try:
            result['presentation']['retention'] = retention.maintain(root, protect_attempts=[result['result'].get('attempt_id')])
        except (OSError, ValueError) as exc:
            result['presentation']['retention'] = {'state': 'deferred', 'notice': str(exc)}
    return result


def _run(selection, argv, *, state_root, cancelled=lambda: False):
    from . import developer_material_snapshots as presentation_api
    parser = argparse.ArgumentParser(prog="workbench context run -- checks materials")
    actions = parser.add_subparsers(dest="action", required=True)
    for name in ("contexts", "history"):
        actions.add_parser(name)
    policy_action = actions.add_parser('retention', help='Core-owned history preferences and cleanup status')
    policy_action.add_argument('operation', choices=['status', 'preview', 'configure', 'maintain'])
    policy_action.add_argument('--settings', type=json.loads)
    policy_action.add_argument('--confirm')
    configure = actions.add_parser("setup", help="Validate and retain this selection's native inputs for routine reruns")
    for name in ("engine-home", "runtime-home"):
        configure.add_argument("--" + name, type=Path)
    configure.add_argument("--engine-archive", type=Path, help="Independent Axiom engine ZIP to prepare in Core state")
    configure.add_argument("--prepare", action="store_true", help="Prepare selected engine/native inputs and assemble the runtime")
    configure.add_argument("--java", type=Path, help="Explicit Java executable; defaults to Core's saved Java selection")
    configure.add_argument("--context", required=True)
    configure.add_argument("--program-root", default=".")
    status = actions.add_parser("setup-status", help="Revalidate saved setup without running native initialization")
    status.add_argument("--context", required=True)
    for action in ("prepare", "run"):
        prepare = actions.add_parser(action)
        for name in ("engine-home", "runtime-home", "java"):
            prepare.add_argument("--" + name, type=Path, help="Advanced invocation override; supply all three paths together")
        prepare.add_argument("--program-root", help="Checkout-relative program directory; defaults to saved setup or the live pack root")
        prepare.add_argument("--request", help="Optional checkout-relative saved JSON expectations; omit for native diagnostics")
        prepare.add_argument("--context", required=True)
        prepare.add_argument("--baseline", help="Retained material attempt whose complete program is rerun in a fresh worker")
    for name in ("execute", "show", "source", "cancel", "register", "delivery", "diagnostics", "diagnostic"):
        command = actions.add_parser(name)
        command.add_argument("attempt")
        if name == "execute":
            command.add_argument("--confirm", required=True)
        if name in {"source", "diagnostic"}:
            command.add_argument("--diagnostic", required=True)
        if name in {"source", "diagnostics", "diagnostic"}:
            command.add_argument("--revision", required=name == "diagnostic")
        if name == "diagnostics":
            command.add_argument("--offset", type=int, default=0)
            command.add_argument("--group", help="Exact severity/location group from diagnostic summary")
    query = actions.add_parser('query', help='Read one snapshot-bound section, record or value chunk')
    query.add_argument('attempt')
    query.add_argument('--query', required=True, type=json.loads)
    compare = actions.add_parser('compare', help='Compare retained observation contracts and content without native execution')
    compare.add_argument('before')
    compare.add_argument('after')
    compare.add_argument('--crafting-key', help='Optional exact crafting record key for a stored graph comparison')
    export = actions.add_parser('export', help='Write verified complete JSON to a new selected local file')
    export.add_argument('attempt')
    export.add_argument('--snapshot', required=True)
    export.add_argument('--destination', required=True, type=Path)
    export.add_argument('--section')
    export.add_argument('--key')
    export.add_argument('--sha256')
    args = parser.parse_args(argv)
    root = require_private_storage(selection, state_root) / "developer-checks"
    reference, presentation = None, {}
    if args.action in {'delivery', 'diagnostics', 'diagnostic'} or args.action == 'source' and args.revision:
        from . import developer_material_delivery as delivery
        if args.action == 'delivery':
            result = delivery.status(root, args.attempt, selection)
        elif args.action == 'diagnostics':
            result, labels = delivery.view(root, args.attempt, selection, revision=args.revision, offset=args.offset, group=args.group)
            presentation['finding_labels'] = labels
            _, request = delivery.request(root, args.attempt, selection)
            try:
                _complete_live_program(selection, request['program'])
                presentation['source_current'] = capture_source_inputs(selection.workspace).observation == request['candidate']['source']
            except (OSError, ValueError):
                presentation['source_current'] = False
        elif args.action == 'diagnostic':
            result = delivery.diagnostic(root, args.attempt, selection, args.revision, args.diagnostic)
        else:
            result = delivery.source(root, args.attempt, selection, args.revision, args.diagnostic)
    elif args.action == 'retention':
        result = retention.dispatch(root, args.operation, settings=args.settings, confirmation=args.confirm)
    elif args.action == "contexts":
        module = _module_identity()
        result = {"format": "workbench-material-contexts-v1", **_domain().contexts(selection.pack_profile),
                  "module": module, "authority": AUTHORITY}
    elif args.action in {"setup", "setup-status"}:
        if selection.mod_uri is not None:
            raise ValueError("material preflight selects a pack program, not a constituent-mod build")
        verify = lambda selected, context: _binding(selection, selected, context)
        if args.action == "setup":
            if args.prepare:
                if args.runtime_home is not None or args.program_root != "." or args.engine_home is not None and args.engine_archive is not None:
                    parser.error("setup --prepare owns runtime assembly; omit runtime-home/program-root and select only one engine input")
                requirements, _, _ = _preparation_requirements(selection, args.context)
                engine = args.engine_home
                if args.engine_archive is not None:
                    from workbench_axiom.cli import distribution
                    engine = setup.prepare_engine(root, args.engine_archive, distribution, cancelled=cancelled)
                if args.java is not None and engine is None:
                    parser.error("setup --prepare requires an engine archive or engine home when selecting java")
                result = setup.prepare_inputs(root, selection.id, args.context, requirements,
                    lambda requirements, context: _preparation_binding(selection, requirements, context), cancelled=cancelled)
                if engine is not None:
                    java = args.java if args.java is not None else setup.selected_java()
                    result = _assemble_prepared_runtime(selection, root, args.context, result, engine, java, cancelled)
            else:
                if args.engine_archive is not None:
                    parser.error("engine-archive requires setup --prepare")
                if args.engine_home is None or args.runtime_home is None:
                    parser.error("setup requires engine-home and runtime-home, or --prepare to acquire raw inputs")
                if args.java is None:
                    args.java = setup.selected_java()
                result = setup.configure(root, selection.id, args.context, _explicit_paths(args), args.program_root, verify)
        else:
            result = setup.status(root, selection.id, args.context, verify)
    elif args.action in {"prepare", "run"}:
        result, reference = _prepare(selection, root, state_root, args)
        if args.action == "run":
            args.attempt = result["attempt_id"]
            result, reference = _execute(selection, root, state_root, args.attempt, result["id"], cancelled)
    elif args.action == "register":
        result = register_snapshot(root, args.attempt, selection)
    elif args.action == "history":
        rows = []
        directory = root / ".workbench/check-attempts"
        if directory.exists():
            storage.ordinary(directory, directory=True)
            for path in sorted(directory.glob("material-check-*")):
                try:
                    rows.append(_history_record(root, path.name, selection))
                except (OSError, ValueError, TypeError, KeyError) as exc:
                    rows.append({"attempt_id": path.name, "state": "unavailable", "reason": str(exc)})
        registered = lifecycle.history(root)['checks'] if root.exists() else []
        for row in registered:
            if row['state'] != 'retained' and row['context'].get('selection_id') == selection.id:
                rows.append({'attempt_id': row['attempt_id'], 'state': row['state'],
                             'snapshot_id': row['snapshot_id'], 'original_summary': row['original_summary'],
                             'detail_state': row['state'], 'storage_command': 'workbench storage --checks history'})
        result = {"format": "workbench-material-check-history-v1", "attempts": rows}
    elif args.action == "execute":
        result, reference = _execute(selection, root, state_root, args.attempt, args.confirm, cancelled)
    elif args.action == 'query':
        result, labels = presentation_api.query(root, args.attempt, selection, args.query, cancelled=cancelled)
        presentation['finding_labels'] = labels
    elif args.action == 'compare':
        result = presentation_api.compare(root, args.before, args.after, selection,
                                          crafting_key=args.crafting_key, cancelled=cancelled)
    elif args.action == 'export':
        if any(value is not None for value in (args.section, args.key, args.sha256)) and not all(
                value is not None for value in (args.section, args.key, args.sha256)):
            parser.error('record export requires section, key and sha256 together')
        if not args.destination.is_absolute():
            parser.error('export requires an absolute selected destination')
        result = presentation_api.export(root, args.attempt, selection, args.snapshot, args.destination,
            section=args.section, key=args.key, digest=args.sha256, cancelled=cancelled)
    elif args.action == "cancel":
        attempt, request, _ = _load(root, args.attempt, selection)
        complete = (attempt / snapshots.DIRECTORY / 'publication.json').exists() or (
            (attempt / "result.json").exists() and not storage.execution_active(attempt))
        if not complete and not (attempt / "cancel.json").exists():
            storage.write_json(attempt / "cancel.json", {"request_id": request["id"]})
        result = {"format": "workbench-material-check-cancellation-v1", "attempt_id": args.attempt,
                  "state": "already-completed" if complete else "requested"}
    elif args.action == "source" and (_attempt(root, args.attempt) / snapshots.DIRECTORY).exists():
        attempt, request, _ = _load(root, args.attempt, selection)
        with open_snapshot(root, args.attempt, selection) as opened:
            finding = _snapshot_finding(opened, args.diagnostic, attempt, request)
            result_id = opened.manifest['result_id']
            reference = _reference(attempt / snapshots.DIRECTORY / 'publication.json', opened.publication)
        attempt, request, _ = _load(root, args.attempt, selection)
        source_dir = 'baseline-source' if finding['side'] == 'baseline' else 'source'
        raw = (attempt / source_dir / finding['location']['path']).read_bytes()
        result = {'format': 'workbench-material-source-view-v1', 'attempt_id': args.attempt,
                  'result_id': result_id, 'source': finding, 'text': raw.decode('utf-8'), 'read_only': True}
    elif args.action == 'show' and (_attempt(root, args.attempt) / check_diagnostics.DIRECTORY / 'publication.json').exists() and not (_attempt(root, args.attempt) / 'snapshot/publication.json').exists():
        from . import developer_material_delivery as delivery
        result, labels = delivery.view(root, args.attempt, selection)
        presentation['finding_labels'] = labels
    elif args.action == 'show':
        result, reference, labels = presentation_api.view(root, args.attempt, selection, cancelled=cancelled)
        presentation['finding_labels'] = labels
    else:
        result, reference = reopen(root, args.attempt, selection)
        if args.action == "source":
            finding = next((row for row in result.get("findings", []) if row["id"] == args.diagnostic), None)
            if finding is None or finding["location"] is None:
                raise ValueError("select an exact located retained material diagnostic")
            attempt, request, _ = _load(root, args.attempt, selection)
            source_dir = "baseline-source" if finding["side"] == "baseline" else "source"
            raw = (attempt / source_dir / finding["location"]["path"]).read_bytes()
            result = {"format": "workbench-material-source-view-v1", "attempt_id": args.attempt,
                      "result_id": result["id"], "source": finding, "text": raw.decode("utf-8"), "read_only": True}
    if args.action in {"show", "execute", "run"}:
        attempt, request, _ = _load(root, args.attempt, selection)
        if args.action != 'show':
            if (cancelled() or (attempt / 'cancel.json').exists()) and not (
                    attempt / snapshots.DIRECTORY / 'publication.json').exists():
                result, reference = presentation_api.unpublished(result, request), None
            else:
                result, reference, labels = presentation_api.view(root, args.attempt, selection, cancelled=cancelled)
                presentation['finding_labels'] = labels
        presentation["request_id"] = request["id"]
        presentation["attempt_state"] = result["state"]
        if result["format"] == REQUEST and (attempt / "started.json").exists():
            # A marker is not proof that a worker is still alive; never auto-rerun.
            presentation["attempt_state"] = "started-without-retained-result"
        try:
            _complete_live_program(selection, request["program"])
            presentation["source_current"] = capture_source_inputs(selection.workspace).observation == request["candidate"]["source"]
        except (OSError, ValueError):
            presentation["source_current"] = False
    code = (result.get("native_exit_code") if not result.get("failure_present", result.get("failure")) else 1) if args.action == "run" else 0
    return {"format": "workbench-developer-action-v1", "exit_code": code if type(code) is int else 1,
            "context": {"selection": {"pack_uri": selection.pack_uri}, "selection_id": selection.id},
            "result": result, "presentation": presentation, "owner_record_ref": reference, "diagnostics": ""}
