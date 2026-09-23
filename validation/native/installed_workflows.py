"""Run real owner operations using installed wheels, never checkout imports.

Invoked with an isolated Python interpreter by validate_native_packages.py.
All mutation targets are disposable directories created by this harness.
This proves construction and service custody, not a launched game runtime.
"""
from __future__ import annotations

from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import threading
import time


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def shared_developer_workflow(root, *, source_only=False):
    """One installed, source-only Supersymmetry vertical with retained results."""
    from hashlib import sha256
    from urllib.parse import urlparse
    from urllib.request import url2pathname
    from workbench_api.resources import repository_root
    from workbench_shell.developer_context import selected_context, observe_developer_context
    from workbench_shell.work_session import WorkSessionStore
    import workbench_shell

    pack = root / "developer-pack"
    pack.mkdir()
    files = {
        "groovy/material/Water.groovy": "Water = new Material.Builder(20001, SuSyUtility.susyId('water')).liquid().build()\n",
        "config/betterquesting/DefaultQuests/Quests/test/1.json": '{"questID:3":1,"properties:10":{"betterquesting:10":{"name:8":"Water quest"}},"tasks:9":{"0:10":{"taskID:8":"bq_standard:retrieval","requiredFluids:9":{"0:10":{"FluidName:8":"water","Amount:3":1000}}}}}',
        "pack.toml": 'name = "Supersymmetry"\nauthor = "SymmetricDevs"\nversion = "test"\npack-format = "packwiz:1.1.0"\n[index]\nfile = "index.toml"\nhash-format = "sha256"\nhash = "' + sha256(b"").hexdigest() + '"\n[versions]\nforge = "14.23.5.2860"\nminecraft = "1.12.2"\n',
        "index.toml": "",
        "groovy/prePostInit/Recipemaps.groovy": "package prePostInit\nclass Recipemaps {\n    static final def MIXER = recipemap('mixer')\n    static final def BR = recipemap('batch_reactor')\n}\n",
        "groovy/postInit/chemistry/Probe.groovy": "import static prePostInit.Recipemaps.*\nimport static gregtech.api.GTValues.*\nMIXER.recipeBuilder()\n    .fluidInputs(fluid('water') * 1000)\n    .fluidOutputs(fluid('distilled_water') * 1000)\n    .duration(20)\n    .EUt(VA[LV])\n    .buildAndRegister()\n",
        "groovy/runConfig.json": '{"packName":"Supersymmetry","packId":"supersymmetry","version":"fixture","debug":false,"loaders":{"preInit":["classes/","material/","preInit/"],"postInit":["prePostInit/","postInit/"]}}\n',
    }
    for name, text in files.items():
        path = pack / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    (pack / "mods").mkdir()
    (pack / "config").mkdir(exist_ok=True)
    for arguments in (("init", "-q"), ("config", "user.name", "Workbench Fixture"),
                      ("config", "user.email", "fixture@workbench.invalid"), ("add", "."),
                      ("-c", "commit.gpgsign=false", "commit", "-qm", "fixture")):
        subprocess.run(["git", "-C", str(pack), *arguments], check=True, capture_output=True)
    state = root / "developer-state"
    environment = {key: value for key, value in os.environ.items() if not key.startswith(("PYTHON", "PIP_", "WORKBENCH_"))}
    environment.update(WORKBENCH_STATE_ROOT=str(root / "developer-host-state"), WORKBENCH_CONFIG_HOME=str(root / "developer-config"))

    def call(*arguments):
        completed = subprocess.run([sys.executable, "-I", "-m", "workbench_core", "context", "--state-root", str(state), *arguments],
                                   cwd=root, env=environment, check=False, capture_output=True, text=True, timeout=120)
        require(completed.returncode == 0, "installed context failed: " + completed.stdout + completed.stderr)
        return json.loads(completed.stdout)

    selection = call("select", str(pack), "--pack-profile", "supersymmetry", "--platform-profile", "cleanroom", "--variant", "cleanroom-provisional")
    session = selection["session_id"]
    source_before = {name: (pack / name).read_bytes() for name in files}
    navigation = call("run", session, "--", "source", "search", "water", "--kind", "material")
    selected_source = navigation["result"]["results"][0]["selection_id"]
    related = call("run", session, "--", "source", "related", selected_source)
    require({"recipe", "quest", "material"} <= {row["kind"] for row in related["result"]["nodes"]}, "installed source navigation lost typed joins")
    location = call("run", session, "--", "source", "location", selected_source)
    require(location["result"]["location"]["path"] == "groovy/material/Water.groovy", "installed source location changed")
    require(related["result"]["context"]["authority"]["runtime_authority"] == "none", "source navigation claimed runtime evidence")
    if source_only:
        from importlib.util import find_spec
        require(find_spec("workbench_blueprints") is None, "source-only installation pulled in Blueprints")
        script = pack / "groovy/postInit/chemistry/Probe.groovy"
        script.write_text(script.read_text().replace("1000", "1250"))
        saved = {name: (pack / name).read_bytes() for name in files}
        index = (pack / ".git/index").read_bytes()
        review = call("run", session, "--", "review", "local", "--baseline-ref=HEAD")["result"]
        require(review["checks"][0]["state"] == "completed", "installed source review did not interpret the change")
        require(review["changes"] and review["files"], "installed local review missed developer edits")
        require(not review["authority"]["construction_authorized"], "review promoted developer edits to construction authority")
        require(saved == {name: (pack / name).read_bytes() for name in files}, "local review modified source")
        require(index == (pack / ".git/index").read_bytes(), "local review modified the index")
        call("run", session, "--", "review", "local", "--baseline-ref=" + review["baseline"]["revision"], "--expect-review", review["review_id"])
        require(not call("show", session)["owner_record_refs"], "read-only review created an owner journal")
        require(not (pack / ".workbench").exists(), "source review wrote into the pack")
        print("PASS installed IDE-first saved-source review with Blueprints absent: exact changes, no source/index mutation, no game launch", flush=True)
        installed_saved_checks(root, state, pack, session, call)
        return
    options = call("run", session, "--", "feature", "options", "recipe-change")
    require(options["context"]["selection_id"] == selection["selection_id"], "options lost selection")
    review = call("run", session, "--", "review", "recipes", "--baseline-ref", "HEAD")
    require(review["context"]["selection_id"] == selection["selection_id"], "review lost selection")
    planned = call("run", session, "--", "feature", "plan", "recipe-change", "--recipe-script", "groovy/postInit/chemistry/Probe.groovy",
                   "--recipe-map", "batch_reactor", "--fluid-input", '{"name":"water","amount":1000}',
                   "--fluid-output", '{"name":"steam","amount":1000}', "--duration", "100", "--voltage-tier", "LV")
    reference = planned["owner_record_ref"]
    plan_path = Path(url2pathname(urlparse(reference["uri"]).path))
    require("diff" in json.dumps(planned["result"]), "recipe plan lacks exact review")
    prepared = call("prepare", session, str(plan_path))
    require(prepared["preparation"]["state"] == "prepared-not-run", "preparation claimed runtime execution")
    require(prepared["preparation"]["plan_id"] == planned["result"]["id"], "preparation lost reviewed plan")
    require(prepared["preparation"]["selection_id"] == selection["selection_id"], "preparation lost selection")
    reopened = call("show", session)
    require({row["record_id"] for row in reopened["owner_record_refs"]} == {planned["result"]["id"], prepared["preparation"]["id"]}, "session lost owner references")
    require(source_before == {name: (pack / name).read_bytes() for name in files}, "developer workflow mutated source")
    require(not (pack / ".workbench").exists(), "developer workflow wrote source-local state")
    require(all(row["state"] == "available" for row in selection["observation"]["owners"]), "installed native owner unavailable")
    # Reopen through the same service-callable selection model, not a CLI cache.
    selected = selected_context(state, session)
    require(observe_developer_context(selected).selection.id == selection["selection_id"], "service context differs from CLI")
    print("PASS installed shared context: source search/typed joins/exact locations, recipe review, exact plan, prepared pair, Work Session reopen; no game launch", flush=True)


def installed_saved_checks(root, state, pack, session, call):
    """Actual installed owners and native process fixture, not a game qualification."""
    from contextlib import redirect_stdout
    from io import StringIO
    from unittest.mock import patch
    from workbench_core import check_storage
    from workbench_project_intelligence.working_tree import capture_source_inputs
    from workbench_profile_supersymmetry import developer_checks as policy
    from workbench_profile_supersymmetry import recipe_lifecycle
    from workbench_shell.developer_context_cli import main

    catalog = call("run", session, "--", "checks", "catalog")["result"]
    observer = Path(recipe_lifecycle.__file__).with_name("observer")
    require(observer.resolve().is_relative_to(Path(sys.prefix).resolve()), "observer resources escaped installed environment")
    require(all((observer / name).is_file() for name in ("RecipeAgent.java", "RecipeTrace.java", "RecipeDecisionHooks.java", "RecipeDecisions.java")), "installed profile lacks observer source resources")
    require(catalog["checks"][0]["id"] == "supersymmetry.client-cold-start", "installed native check provider missing")
    if sys.platform != "linux":
        print("PASS installed check catalog; native Linux execution not claimed on this host", flush=True)
        return
    template = root / "check-fixture"
    template.mkdir()
    (template / "fixture.py").write_text(
        "import json, pathlib, re, time\n"
        "root = pathlib.Path('.')\n"
        "nonce = re.search(r'nonce: \"([0-9a-f]+)\"', (root/'groovy/workbenchChecks/Observe.groovy').read_text()).group(1)\n"
        "(root/'logs').mkdir()\n"
        "marker = '[WORKBENCH-SAVED-CHECK]' + json.dumps(dict(nonce=nonce, side='CLIENT', items=1, fluids=1))\n"
        "(root/'logs/latest.log').write_text('Forge Mod Loader has successfully loaded 1 mods\\n' + marker + '\\n')\n"
        "(root/'logs/groovy.log').write_text('fixture\\n')\n"
        "probe = root/'groovy/workbenchChecks/RecipeCapture.groovy'\n"
        "if probe.exists():\n"
        "    line = next(line for line in probe.read_text().splitlines() if line.startswith('def spec ='))\n"
        "    spec = json.loads(json.loads(line.split('gson.fromJson(',1)[1].rsplit(', Map)',1)[0]))\n"
        "    source = (root/'groovy/postInit/chemistry/Probe.groovy').read_text()\n"
        "    actual = dict(map='mixer',duration=int(re.search(r'duration\\((\\d+)\\)',source).group(1)),eut=30,item_inputs=[],item_outputs=[],fluid_inputs=[dict(name='water',amount=1250)],fluid_outputs=[dict(name='distilled_water',amount=1250)])\n"
        "    resolution = {json.dumps(row,sort_keys=True,separators=(',',':')):row['name'] for row in spec['identities']}\n"
        "    captured = dict(format='workbench-recipe-capture-v4',lifecycle=None,decisions=None,nonce=nonce,expectation_id=spec['expectation_id'],state='complete',phase='render-tick-after-load-complete',resolution=resolution,map='mixer',error=None,records=[dict(id='r0',recipe=actual,lookup_active=True,category_present=True,unsupported_reason=None)],queries=[dict(id='q0',accepting=['r0'],winner_id='r0',items=[],fluids=[dict(name='water',amount=1250)],voltage_limit=2147483647,winner=actual,unsupported=False)])\n"
        "    with (root/'logs/latest.log').open('a') as stream: stream.write('[WORKBENCH-RECIPE-CAPTURE]'+json.dumps(captured)+'\\n')\n"
        "time.sleep(30)\n"
    )
    before = capture_source_inputs(pack)
    image = check_storage.import_image(state / "developer-checks", template, Path(sys.executable).resolve(), ["fixture.py"], policy.binding(before), excluded_roots=policy.descriptor()["excluded_roots"])

    def check(*args):
        output = StringIO()
        # Only game installation admission is replaced. CLI, native provider
        # admission, Work Session linking, process custody and recovery are real.
        fixture_provenance = {"pack": {"name": "fixture", "version": "test"}, "platform": {"id": "fixture", "version": "1"}, "dependencies": []}
        with patch.object(policy, "validate_image", return_value=image["binding"]), patch.object(policy, "provenance", return_value=fixture_provenance), redirect_stdout(output):
            code = main(["--state-root", str(state), "run", session, "--", "checks", *args])
        require(code == 0, "installed saved check failed: " + output.getvalue())
        return json.loads(output.getvalue())

    request = check("prepare", "--image", image["id"], "--timeout", "10")["result"]
    require(request["state"] == "prepared-not-run", "preparation claimed execution")
    result = check("execute", request["attempt_id"], "--confirm", request["id"])["result"]
    require(result["state"] == "completed" and result["cleanup"]["state"] == "trashed", "installed fixture did not close and clean up")
    require(check("show", request["attempt_id"])["result"] == result, "installed check reopen changed retained evidence")
    require(capture_source_inputs(pack) == before, "installed execution changed source or index")
    require({row["record_id"] for row in call("show", session)["owner_record_refs"]} == {request["id"], result["id"]}, "check owner references were not linked to the Work Session")
    repeated_request = check("prepare", "--image", image["id"], "--timeout", "10")["result"]
    repeated = check("execute", repeated_request["attempt_id"], "--confirm", repeated_request["id"])["result"]
    comparison = check("compare", repeated["attempt_id"], "--reference", result["attempt_id"])["result"]
    require(comparison["state"] == "compared" and not comparison["authority"]["outcomes_promoted"], "installed comparison changed run outcomes")
    require(comparison["id"] in {row["record_id"] for row in call("show", session)["owner_record_refs"]}, "comparison was not verified and linked to the existing Work Session")
    require(len(check("history")["result"]["runs"]) == 2, "installed history lost a retained run")
    require(check("compare", repeated["attempt_id"], "--reference", result["attempt_id"])["result"] == comparison, "installed comparison is not repeatable")
    print("PASS installed saved-candidate check without Blueprints: explicit prepare/execute, real supervised fixture, logs, closure, recoverable cleanup and Work Session reopen; no game qualification", flush=True)
    recipes = check("recipes", "--path", "groovy/postInit/chemistry/Probe.groovy")["result"]
    require(len(recipes["recipes"]) == 1 and recipes["recipes"][0]["support"] == "supported", "installed saved recipe catalog failed")
    request = check("prepare", "--no-trace", "--image", image["id"], "--recipe", recipes["recipes"][0]["id"], "--timeout", "10")["result"]
    result = check("execute", request["attempt_id"], "--confirm", request["id"])["result"]
    require(result["assertions"]["state"] == "matched", "installed resource/recipe assertion did not match independent fixture values")
    require(check("show", request["attempt_id"])["result"] == result, "installed recipe assertion did not reopen")
    script = pack / "groovy/postInit/chemistry/Probe.groovy"
    original = script.read_bytes()
    try:
        script.write_bytes(original.replace(b"duration(20)", b"duration(40)"))
        wrong_request = check("prepare", "--no-trace", "--image", image["id"], "--recipe", recipes["recipes"][0]["id"], "--recipe-reference", request["attempt_id"], "--timeout", "10")["result"]
        wrong = check("execute", wrong_request["attempt_id"], "--confirm", wrong_request["id"])["result"]
        require(wrong["assertions"]["state"] == "mismatched", "installed fixture echoed the old expectation instead of observing saved bytes")
        explained = check("explain", wrong_request["attempt_id"])["result"]
        require("Duration (ticks): expected 20; observed 40" in explained["text"], "installed explanation lost observed property differences")
        require(explained["result_id"] == wrong["id"], "installed explanation changed result identity")
        require(any(source["basis"] == "literal-candidate" for section in explained["explanation"]["sections"] for source in section["sources"]), "installed source candidate navigation is absent")
    finally:
        script.write_bytes(original)
    require(capture_source_inputs(pack) == before, "installed recipe execution changed source/index")
    print("PASS installed recipe catalog, packaged observer, source-bound expectations, independent match/mismatch and retained reopen with Blueprints absent; fixture only, no game qualification", flush=True)


def installed_shell_service(root):
    from workbench_api.resources import repository_root
    import workbench_shell
    from workbench_shell.feature_studio_registry import build_feature_studio_registry_v3
    from workbench_shell.installed_service import build_installed_discovery_registry_v3, probe_installed_service_v3
    from workbench_shell.service_control_registry import build_service_control_registry_v3
    from workbench_shell.world_studio_registry import build_world_studio_registry_v3

    resources = repository_root(workbench_shell.__file__)
    require(resources.is_relative_to(Path(sys.prefix).resolve()), "service resources escaped installed environment")
    require(not (resources / "pixi.toml").exists(), "native runtime acquired source-only Pixi authority")
    for builder in (build_feature_studio_registry_v3, build_service_control_registry_v3, build_world_studio_registry_v3):
        bundle = builder(resources)
        contract = bundle.dependency_lock_manifest["installed_runtime_contract"]
        require(contract["execution_scope"] == "installed-native-runtime", "service registry misreported installed runtime")
        require(not contract["source_pixi_qualification"], "installed registry claimed source Pixi qualification")
    build_installed_discovery_registry_v3(resources)
    if os.name == "nt":
        # Windows process termination is not graceful POSIX SIGTERM. Exercise
        # the real endpoint/composition without claiming that lifecycle signal.
        from workbench_core.service.host import LocalServiceEndpointV3
        from workbench_shell.feature_studio_service import compose_feature_studio_service_v3
        composition = compose_feature_studio_service_v3(resources, root / "shell-service")
        endpoint = root / "service.sock"
        try:
            with LocalServiceEndpointV3(composition.host, endpoint_path=endpoint):
                probe = probe_installed_service_v3(resources, endpoint_path=endpoint, credential_path=composition.authenticator.path)
                require(probe["state"] == "ready" and probe["authenticated"], "installed Shell endpoint probe failed")
        finally:
            composition.close()
        require(not endpoint.exists(), "installed Shell endpoint survived stop")
        print("PASS installed Shell registries, endpoint startup, authenticated probe and stop (in-process; no Windows signal claim)", flush=True)
        return

    service = root / "shell-service"
    endpoint = service / "run/service.sock"
    ready = root / "shell-ready.json"
    environment = {key: value for key, value in os.environ.items() if not key.startswith(("PYTHON", "PIP_", "WORKBENCH_"))}
    environment.update(WORKBENCH_STATE_ROOT=str(root / "shell-state"), WORKBENCH_CONFIG_HOME=str(root / "shell-config"))
    with (root / "shell-service.log").open("w+") as output:
        process = subprocess.Popen([sys.executable, "-I", "-m", "workbench_core", "service-host-v3",
            "--service-root", str(service), "--endpoint", str(endpoint), "--ready-file", str(ready),
            "--process-nonce", "service-process-nonce:" + "c" * 32], cwd=root, env=environment, stdout=output, stderr=output)
        try:
            deadline = time.monotonic() + 30
            while not ready.is_file() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.025)
            output.flush()
            output.seek(0)
            require(ready.is_file() and process.poll() is None, "installed Shell failed startup: " + output.read())
            record = json.loads(ready.read_text())
            require(record["pid"] == process.pid, "installed service ready receipt belongs to another process")
            probe = probe_installed_service_v3(resources, endpoint_path=endpoint, credential_path=Path(record["credential_path"]))
            require(probe["state"] == "ready" and probe["authenticated"], "installed Shell probe failed")
            require(probe["claims"]["feature_handlers_registered"], "real Shell Feature Studio handlers were not registered")
            process.terminate()
            process.wait(15)
            require(process.returncode == 0 and not endpoint.exists(), "installed Shell did not stop cleanly")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(5)
    print("PASS installed Shell registries and real Core CLI service startup, authenticated probe and graceful stop", flush=True)


def installed_world_presenter(root):
    from workbench_api.canonical import content_id
    from workbench_api.resources import repository_root
    from workbench_core.service.host import local_service_physical_lease_ports
    from workbench_core.service.runtime import ServiceRuntimeV3
    from workbench_crucible_service import DurableJobStore
    from workbench_crucible_worldgen.view import WorldStudioProvingViewHandler
    from workbench_runtime_explorer.graph_query import EmbeddedGraphQueryPresenterV2
    import workbench_shell
    from workbench_shell.world_studio_registry import build_world_studio_presenter_binding, build_world_studio_registry_v3

    def unavailable(*_arguments):
        raise AssertionError("installed composition check must not dispatch a world query")

    handler = WorldStudioProvingViewHandler(query_resolver=unavailable,
        proof_index_resolver=unavailable, action_gate_resolver=unavailable)
    binding = build_world_studio_presenter_binding(handler)
    current = build_world_studio_registry_v3(repository_root(workbench_shell.__file__))
    require(binding.composition == current, "installed World presenter does not bind the actual producer")
    with ServiceRuntimeV3(root / "world-service", registrations=(binding.registration,),
        physical_leases=local_service_physical_lease_ports(),
        store_factory=lambda path, leases: DurableJobStore(path, physical_leases=leases,
            context_publication_validator=lambda _context, _binding: False)) as runtime:
        presenter = EmbeddedGraphQueryPresenterV2(runtime, binding)
        manifest = presenter.manifest
        route = dict(manifest["route"])
        route_id = route.pop("route_id")
        require(route_id == manifest["route_id"] == content_id("runtime-explorer-graph-query-route", route),
            "installed World presenter route is not content addressed")
        for key, expected in (("registry_id", current.registry["registry_id"]),
            ("source_tree_id", current.source_tree_manifest["id"]),
            ("service_distribution_id", current.service_distribution["id"]),
            ("capability_id", binding.registration.capability_id),
            ("handler_id", binding.registration.handler_id),
            ("implementation_id", binding.registration.implementation_id)):
            require(route[key] == expected, "installed World presenter route mismatch: " + key)
    print("PASS installed World producer, Core registration and Explorer presenter identity parity (no game query claim)", flush=True)


def construction_and_inspection(root):
    from workbench_api.resources import repository_root
    from workbench_cleanroom_new_project import construction
    from workbench_project_intelligence.workspace_doctor import new_report

    resources = repository_root(construction.__file__)
    require(resources.is_relative_to(Path(sys.prefix).resolve()), "resources escaped installed environment")
    target = root / "constructed-project"
    request = construction.build_cleanroom_mod_request(target, output_mode="direct-apply", allow_direct_apply=True)
    preview = construction.preview_cleanroom_mod_construction(resources, request)
    require(preview["state"] == "ready" and not target.exists(), "preview mutated its target")
    plan = preview["plan"]
    try:
        construction.apply_cleanroom_mod_construction(resources, plan, root / "rejected", consent_plan_id="wrong")
    except ValueError:
        pass
    else:
        raise AssertionError("construction accepted unrelated consent")
    require(not target.exists(), "rejected construction mutated its target")
    result = construction.apply_cleanroom_mod_construction(resources, plan, root / "construction-state", consent_plan_id=plan["id"])
    require(result["state"] == "applied", "construction did not apply")
    require(construction._verify_constructed_target(target, plan), "constructed bytes differ from reviewed plan")
    before = {p.relative_to(target).as_posix(): p.read_bytes() for p in target.rglob("*") if p.is_file()}
    report = new_report(target)
    require(report["read_only"] is True, "inspection did not declare read-only semantics")
    require(report["target"]["workspace"]["kind"] == "gradle-project", "installed inspector did not recognize constructed project")
    after = {p.relative_to(target).as_posix(): p.read_bytes() for p in target.rglob("*") if p.is_file()}
    require(before == after, "inspection mutated the project")
    print("PASS installed construction consent, exact output and read-only inspection", flush=True)


def cleanup_and_recovery(root):
    from workbench_core.storage import inventory_storage, plan_cleanup, execute_cleanup, plan_restore_trash, execute_restore_trash

    workspace = root / "cleanup-workspace"
    cache = workspace / ".workbench/cache/disposable"
    cache.mkdir(parents=True)
    (cache / "payload").write_bytes(b"recoverable cache bytes")
    evidence = workspace / ".workbench/evidence/retained"
    evidence.mkdir(parents=True)
    (evidence / "receipt").write_bytes(b"user evidence retained")
    inventory = inventory_storage(workspace)
    item = next(row for row in inventory["items"] if Path(row["path"]) == cache)
    plan = plan_cleanup(workspace, selector=item["item_id"])
    require(plan["status"] == "ready" and cache.exists(), "cleanup preview is not read-only")
    require(execute_cleanup(workspace, plan)["status"] == "complete", "cleanup did not finish")
    require(not cache.exists(), "cleanup left live cache")
    trash = next(row for row in inventory_storage(workspace)["items"] if row["kind"] == "trash")
    restore = plan_restore_trash(workspace, selector=trash["item_id"])
    require(execute_restore_trash(workspace, restore)["status"] == "complete", "restore did not finish")
    require((cache / "payload").read_bytes() == b"recoverable cache bytes", "restore changed bytes")
    require((evidence / "receipt").read_bytes() == b"user evidence retained", "cleanup changed evidence")
    retained = next(row for row in inventory_storage(workspace)["items"] if Path(row["path"]) == evidence / "receipt")
    require(plan_cleanup(workspace, selector=retained["item_id"])["status"] == "blocked", "authoritative evidence is eligible for cleanup")
    print("PASS installed cleanup, recoverable trash and evidence protection", flush=True)


def durable_service(root):
    from workbench_api.canonical import content_id
    from workbench_api.service import ServiceHandlerRegistration
    from workbench_core.service.host import local_service_physical_lease_ports
    from workbench_core.service.runtime import ServiceRuntimeV3
    from workbench_crucible_jobs.synthetic import build_synthetic_job_publication
    from workbench_crucible_service import DurableJobStore

    publication = build_synthetic_job_publication()
    started = threading.Event()
    release = threading.Event()

    def handler(context, arguments):
        context.progress("execute", 0, 1, message="installed owner started")
        started.set()
        while not release.wait(0.01):
            context.checkpoint("installed.wait")
        context.checkpoint("installed.completed")
        return {"value": arguments["value"]}

    registration = ServiceHandlerRegistration(
        method="probe/materialize", capability_id=content_id("capability", {"probe": "installed"}),
        capability_version="1.0.0", handler_id=content_id("handler", {"probe": "installed"}),
        implementation_id=content_id("implementation", {"probe": "installed"}),
        mutation_boundary="reference-update", asynchronous=True, maximum_concurrency=1, handler=handler,
    )
    service_root = root / "service"

    def runtime():
        host = ServiceRuntimeV3(service_root, registrations=(registration,), physical_leases=local_service_physical_lease_ports(),
            store_factory=lambda path, leases: DurableJobStore(path, physical_leases=leases, context_publication_validator=lambda _context, _binding: True))
        try:
            host.store.register_context(publication.context_ref.canonical_bytes, publication.input_binding.canonical_bytes)
        except BaseException:
            host.close()
            raise
        return host

    request = {"method": registration.method, "capability_id": registration.capability_id,
        "arguments": {"value": "installed"}, "context_ref_id": publication.context_ref.id,
        "input_binding_id": publication.input_binding.id, "idempotency_key": "installed", "request_id": "installed.request"}
    with runtime() as host:
        accepted = host.dispatch(request)
        job_id = accepted["job"]["job_id"]
        try:
            require(started.wait(5), "installed handler did not start")
            require(host.dispatch(request)["job"]["job_id"] == job_id, "idempotent dispatch created another job")
            head = host.store.handle(job_id)
            host.cancel_job(job_id, expected_event_id=head.latest_event_id, expected_event_ordinal=head.latest_event_ordinal, reason="installed cancellation")
            deadline = time.monotonic() + 10
            while host.store.handle(job_id).terminal_seal_id is None and time.monotonic() < deadline:
                time.sleep(0.01)
            require(host.store.handle(job_id).terminal_outcome == "cancelled-before-mutation", "cancellation lost mutation truth")
            host.store.validate_complete_job(job_id)
        finally:
            release.set()
    with runtime() as reopened:
        require(reopened.store.handle(job_id).terminal_outcome == "cancelled-before-mutation", "restart lost terminal record")
        queued, created = reopened.store.create_job(registration, context_ref_id=publication.context_ref.id,
            input_binding_id=publication.input_binding.id, arguments={"value": "recover"},
            idempotency_key="recover", actor_id=reopened.actor_id)
        require(created, "recovery probe was not queued")
    with runtime() as recovered:
        require(queued.job_id in recovered.recovered_job_ids, "unstarted job was not recovered")
        recovered.store.validate_complete_job(queued.job_id)
        require(recovered.store.handle(queued.job_id).mutation_state == "not-started", "recovery invented mutation")
    print("PASS installed durable dispatch, idempotence, cancellation, restart and recovery", flush=True)


def main():
    from workbench_core.host_services import install_local_host_services
    from workbench_api.host_filesystem import secure_private_path
    install_local_host_services()
    with tempfile.TemporaryDirectory(prefix="workbench-installed-workflows-") as temporary:
        root = Path(temporary).resolve()
        secure_private_path(root, directory=True)
        if sys.argv[1:] == ["--source-only"]:
            shared_developer_workflow(root, source_only=True)
            return
        construction_and_inspection(root)
        shared_developer_workflow(root)
        cleanup_and_recovery(root)
        durable_service(root)
        installed_shell_service(root)
        installed_world_presenter(root)


if __name__ == "__main__":
    main()
