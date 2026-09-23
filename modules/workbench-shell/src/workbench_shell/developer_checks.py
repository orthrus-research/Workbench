"""Compose exact saved-candidate checks; Core owns every physical side effect."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
from uuid import uuid4

from workbench_core import check_execution, check_storage as storage
from workbench_core import check_attachments
from workbench_api.profile_extensions import require_profile_extension
from workbench_crucible.developer_checks import (
    provider,
    seal_result,
    validate_result,
    outcome,
)
from workbench_project_intelligence.saved_candidate import (
    candidate_manifest,
    stage_candidate,
)
from workbench_project_intelligence.working_tree import (
    SourceInputs,
    capture_source_inputs,
)
from .developer_context import observe_developer_context, require_private_storage
from workbench_project_intelligence.working_tree import revision_labels
from workbench_crucible.check_comparison import compare_results, group_findings
from workbench_crucible.check_assertions import evaluate as evaluate_assertions, validate_expectation


def _root(selection, state):
    return require_private_storage(selection, state) / "developer-checks"


def _attempt(root, identity):
    if re.fullmatch(r"check-[0-9a-f]{32}", identity or "") is None:
        raise ValueError("select an exact check attempt")
    return root / ".workbench/check-attempts" / identity


def _request(root, identity, selection):
    attempt = _attempt(root, identity)
    value = storage.read_json(attempt / "request.json")
    if value.get("format") != "workbench-saved-check-request-v4":
        raise ValueError("unsupported saved-check request; prepare a new check with current provenance")
    validate_expectation(value.get("expectation"))
    check_attachments.validate(value["attachment"])
    if value["provenance"]["runtime"].get("check_attachment") != check_attachments.comparison_identity(value["attachment"]):
        raise ValueError("check attachment differs from request provenance")
    if "expectation" not in value or value["expectation"] is not None and value["expectation"]["candidate_id"] != value["candidate"]["id"]:
        raise ValueError("check expectation candidate binding changed")
    body = {key: row for key, row in value.items() if key != "id"}
    if (
        storage.seal("saved-check-request", body) != value
        or value["workspace_uri"] != selection.pack_uri
        or value["selection_id"] != selection.id
        or value["attempt_id"] != identity
    ):
        raise ValueError("check request identity or selected workspace changed")
    return attempt, value


def _inputs(attempt, request):
    rows = request["candidate"]["files"]
    source = attempt / "source"
    expected = [{**row, "mode": row["mode"] & 0o777} for row in rows]
    if storage.tree_manifest(source) != expected:
        raise ValueError("retained saved candidate changed")
    return SourceInputs(
        json.dumps(request["candidate"]["source"], sort_keys=True),
        tuple((row["path"], (source / row["path"]).read_bytes()) for row in rows),
        tuple((row["path"], row["mode"]) for row in rows),
    )


def _reference(path, value):
    return {
        "owner_id": "crucible",
        "record_id": value["id"],
        "record_kind": value["format"],
        "uri": path.as_uri(),
        "digest": "sha256:" + sha256(path.read_bytes()).hexdigest(),
        "last_verified_state": None,
        "verified_at": None,
    }


def _recovery(attempt, request):
    path = attempt / "recovery.json"
    if not path.exists():
        return None
    value = storage.read_json(path)
    if (
        storage.seal(
            "check-recovery", {key: row for key, row in value.items() if key != "id"}
        )
        != value
        or value["request_id"] != request["id"]
        or value["attempt_id"] != request["attempt_id"]
    ):
        raise ValueError("check recovery identity changed")
    return value


def _reopen(root, identity, selection):
    attempt, request = _request(root, identity, selection)
    inputs = _inputs(attempt, request)
    expected_inputs = _expectation_source(root, request, inputs, selection)
    path = attempt / "result.json"
    if path.exists():
        result = validate_result(storage.read_json(path))
        if (result["request_id"] != request["id"] or result["attempt_id"] != identity
                or result["candidate_id"] != request["candidate"]["id"]
                or result["candidate"] != request["candidate"]["source"]
                or result["provenance"] != request["provenance"] or result["provider"] != request["provider"]
                or result["workspace_uri"] != selection.pack_uri or result["selection_id"] != selection.id):
            raise ValueError("check result belongs to another request")
        if (None if result["assertions"] is None else result["assertions"]["expectation"]) != request["expectation"]:
            raise ValueError("check assertion expectation differs from its confirmed request")
        line_counts, evidence_text = {}, {}
        for evidence in result["evidence"]:
            raw = storage.ordinary(
                attempt / storage.safe_path(evidence["path"])
            ).read_bytes()
            if (
                sha256(raw).hexdigest() != evidence["sha256"]
                or len(raw) != evidence["size"]
            ):
                raise ValueError("retained check evidence changed")
            line_counts[evidence["path"]] = len(raw.decode("utf-8").splitlines())
            evidence_text[evidence["path"]] = raw.decode("utf-8").splitlines()
        interpretation = result.get("interpretation")
        if interpretation:
            references = [*interpretation["observation"]["evidence"],
                          *(ref for row in interpretation["findings"] for ref in row["evidence"])]
            if result["assertions"] is not None:
                references.extend(result["assertions"]["observation"]["evidence"])
            if any(ref["log"] not in line_counts or not 1 <= ref["line"] <= line_counts[ref["log"]] for ref in references):
                raise ValueError("retained diagnostic points outside its captured evidence")
            if interpretation["complete_logs"] and not set(request["check"]["log_paths"]).issubset(line_counts):
                raise ValueError("complete interpretation lacks required retained logs")
            if result["assertions"] is not None:
                observed = result["assertions"]["observation"]
                detail = observed["details"]
                if "explanation" in detail:
                    from workbench_pack_program_studio.source_locations import verify_source_location
                    source_sets = {candidate_manifest(value)["id"]: value.sources for value in (inputs, expected_inputs)}
                    for section in detail["explanation"]["sections"]:
                        for source in section["sources"]:
                            location = source["location"]
                            try:
                                verify_source_location(source_sets[source["candidate_id"]][location["path"]], location)
                            except (ValueError, KeyError) as exc:
                                raise ValueError("explanation source differs from retained candidate bytes") from exc
                    for event in detail.get("lifecycle", {}).get("events", []):
                        location = event.get("location")
                        if location is not None:
                            verify_source_location(inputs.sources[location["path"]], location)
                if "capture" in detail:
                    prefix = detail.get("capture_prefix")
                    if not isinstance(prefix, str) or not 1 <= len(prefix) <= 128 or len(observed["evidence"]) != 1:
                        raise ValueError("assertion capture lacks one exact evidence binding")
                    ref = observed["evidence"][0]
                    line = evidence_text[ref["log"]][ref["line"] - 1]
                    if prefix not in line or json.loads(line.split(prefix, 1)[1]) != detail["capture"]:
                        raise ValueError("assertion capture differs from retained log evidence")
        return result, _reference(path, result)
    state = (
        "needs-attention" if (attempt / "started.json").exists() else "prepared-not-run"
    )
    if state == "needs-attention":
        try:
            with storage.execution_lock(attempt):
                pass
        except storage.CheckStorageError:
            state = "running"
    if _recovery(attempt, request):
        state = "recovered-incomplete"
    return {
        "format": "workbench-saved-check-status-v1",
        "attempt_id": identity,
        "state": state,
        "request": request,
        "attempt_uri": attempt.as_uri(),
    }, None


def _recover(root, identity, selection, consent):
    attempt, request = _request(root, identity, selection)
    if consent != request["id"]:
        raise ValueError("recovery requires confirmation of the exact check request")
    with storage.execution_lock(attempt):
        path = attempt / "recovery.json"
        if path.exists():
            return _recovery(attempt, request)
        projection = root / ".workbench/tmp" / identity
        process = check_execution.recover_execution(root, attempt, projection)
        evidence = {}
        if projection.exists():
            # Preserve available logs before recoverable cleanup, even if the
            # original writer died before it could seal a check result.
            evidence = check_execution.capture_logs(
                projection, request["check"]["log_paths"],
                optional_globs=request["check"]["optional_log_globs"],
            )
            console = attempt / "console.json"
            if console.exists():
                evidence.update(
                    check_execution.console_logs(
                        root, storage.read_json(console)["session_id"]
                    )
                )
            storage.write_json(
                attempt / ("recovered-logs-" + uuid4().hex + ".json"), evidence
            )
            receipt = storage.cleanup_projection(root, projection)
            cleanup = {"state": "trashed", "receipt": receipt}
        else:
            cleanup = {"state": "not-present"}
        result = storage.seal(
            "check-recovery",
            {
                "format": "workbench-check-recovery-v1",
                "attempt_id": identity,
                "request_id": request["id"],
                "state": "recovered",
                "process": process,
                "cleanup": cleanup,
                "meaning": "Process/storage recovery only; the original check result is not promoted or rewritten",
            },
        )
        storage.write_json(path, result)
        return result


def _expectation_source(root, request, inputs, selection):
    expectation = request["expectation"]
    if expectation is None:
        return inputs
    reference = expectation["source"]["reference"]
    if reference is not None:
        directory, original = _request(root, reference["attempt_id"], selection)
        result = validate_result(storage.read_json(directory / "result.json"))
        if result["id"] != reference["result_id"] or result["request_id"] != original["id"]:
            raise ValueError("expected recipe source reference changed")
        inputs = _inputs(directory, original)
    if candidate_manifest(inputs)["id"] != expectation["source"]["candidate_id"]:
        raise ValueError("expected recipe source candidate differs")
    from workbench_pack_program_studio.source_locations import verify_source_location
    location = expectation["subject"]["location"]
    verify_source_location(inputs.sources[location["path"]], location)
    return inputs


def _prepare(selection, root, owner, owner_identity, image_id, timeout, *, recipe_id=None, recipe_reference=None, absent=False, trace=True):
    operation = observe_developer_context(selection)
    inputs = capture_source_inputs(selection.workspace)
    if inputs.observation != operation.as_dict()["source"]:
        raise ValueError("saved source changed before check preparation")
    expectation = None
    if recipe_id:
        source_inputs, reference = inputs, None
        if recipe_reference:
            result, _ = _reopen(root, recipe_reference, selection)
            directory, previous = _request(root, recipe_reference, selection)
            source_inputs = _inputs(directory, previous)
            reference = {"attempt_id": recipe_reference, "result_id": result["id"]}
        expectation = owner.recipe_expectation(inputs, source_inputs, recipe_id,
                                              mode="absent" if absent else "present", reference=reference)
    elif absent or recipe_reference:
        raise ValueError("Reference or absence requires one explicit --recipe identity")
    description = owner.descriptor()
    image = storage.load_image(root, image_id)
    owner.validate_image(
        inputs,
        storage.image_path(root, image_id) / "payload",
        storage.image_executable(root, image),
        image["arguments"],
    )
    if image["binding"] != owner.binding(inputs):
        raise ValueError(
            "runtime image dependency binding differs from saved source; prepare a matching environment"
        )
    operation.require_fresh()
    provenance = {
        "format": "workbench-check-provenance-v1",
        "source_labels": revision_labels(selection.workspace, inputs.observation["revision"]),
        "runtime": owner.provenance(inputs, storage.image_executable(root, image)),
        "image": {"id": image["id"], "binding": image["binding"], "executable": image["executable"]},
        "host": check_execution.host_observation(), "check": description,
        "timeout_seconds": timeout,
    }
    storage.initialize(root)
    attempt_id = "check-" + uuid4().hex
    attempt = _attempt(root, attempt_id)
    attempt.mkdir(mode=0o700)
    candidate = stage_candidate(inputs, attempt / "source")
    nonce = uuid4().hex
    specification = owner.attachment(inputs, nonce, expectation=expectation) if trace else None
    platform_owner = require_profile_extension("workbench.check_platforms", description["platform"])
    if specification is not None:
        platform_owner.validate_check_attachment(specification, storage.image_executable(root, image))
        owner.validate_attachment_image(storage.image_path(root, image_id) / "payload")
    attachment = check_attachments.build(attempt / "attachment", storage.image_executable(root, image), specification)
    provenance["runtime"]["check_attachment"] = check_attachments.comparison_identity(attachment)
    overlays = owner.overlays(inputs, nonce, expectation=expectation, trace=attachment is not None)
    overlay_rows = []
    (attempt / "overlays").mkdir(mode=0o700)
    for name, raw in sorted(overlays.items()):
        target = attempt / "overlays" / storage.safe_path(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(raw)
        overlay_rows.append(
            {
                "path": name,
                "size": len(raw),
                "sha256": sha256(raw).hexdigest(),
                "mode": 0o644,
            }
        )
    operation.require_fresh()
    request = storage.seal(
        "saved-check-request",
        {
            "format": "workbench-saved-check-request-v4",
            "attachment": attachment,
            "provenance": provenance,
            "expectation": expectation,
            "state": "prepared-not-run",
            "workspace_uri": selection.pack_uri,
            "selection_id": selection.id,
            "attempt_id": attempt_id,
            "candidate": candidate,
            "context": operation.as_dict(),
            "image_id": image_id,
            "provider": owner_identity,
            "check": description,
            "timeout_seconds": timeout,
            "nonce": nonce,
            "overlays": overlay_rows,
            "effects": [
                "Create a private disposable runtime copy",
                "Execute the selected native client and observation-only probe",
                *(["Attach the sealed current-stack registration observer; record executed stages and verified source origins"] if attachment is not None else []),
                "Retain diagnostics and logs; move disposable files into recoverable trash after verified process closure",
            ],
            "downloads": [],
            "authority": {
                "runtime_launched": False,
                "execution_authorized": False,
                "source_mutated": False,
            },
        },
    )
    storage.write_json(attempt / "request.json", request)
    return request, _reference(attempt / "request.json", request), operation.as_dict()


def _execute(selection, root, identity, consent, cancelled):
    attempt, request = _request(root, identity, selection)
    if consent != request["id"]:
        raise ValueError(
            "execution requires explicit confirmation of this exact request"
        )
    with storage.execution_lock(attempt):
        if (attempt / "started.json").exists() or (attempt / "recovery.json").exists():
            raise ValueError(
                "attempt was already started; reopen it or prepare a new check"
            )
        owner, owner_identity = provider(selection.pack_profile)
        if owner_identity != request["provider"]:
            raise ValueError("check provider changed after preparation")
        if check_execution.host_observation() != request["provenance"]["host"]:
            raise ValueError("check host or admitted environment changed; prepare again")
        operation = observe_developer_context(selection)
        if operation.as_dict() != request["context"]:
            raise ValueError(
                "source or selected dependencies changed; prepare a fresh check"
            )
        inputs = _inputs(attempt, request)
        source_inputs = _expectation_source(root, request, inputs, selection)
        expectation = request["expectation"]
        if expectation is not None:
            if expectation["support"] != "supported":
                raise ValueError("unsupported recipe expectation; no runtime execution is needed")
            if owner.recipe_expectation(inputs, source_inputs, expectation["subject"]["id"], mode=expectation["mode"], reference=expectation["source"]["reference"]) != expectation:
                raise ValueError("confirmed recipe expectation changed")
        image = storage.load_image(root, request["image_id"])
        owner.validate_image(
            inputs,
            storage.image_path(root, image["id"]) / "payload",
            storage.image_executable(root, image),
            image["arguments"],
        )
        if image["binding"] != owner.binding(inputs):
            raise ValueError("image binding differs from candidate")
        if storage.tree_manifest(attempt / "overlays") != request["overlays"]:
            raise ValueError("check instrumentation changed")
        if request["attachment"] is not None:
            owner.validate_attachment_image(storage.image_path(root, image["id"]) / "payload")
            specification = owner.attachment(inputs, request["nonce"], expectation=expectation)
            require_profile_extension("workbench.check_platforms", request["check"]["platform"]).validate_check_attachment(
                specification, storage.image_executable(root, image))
            if (storage.tree_manifest(attempt / "attachment/source") != request["attachment"]["source_files"]
                    or specification["policy"] != request["attachment"]["policy"]):
                raise ValueError("check observer policy or retained source changed")
        storage.write_json(attempt / "started.json", {"request_id": request["id"]})
        projection = root / ".workbench/tmp" / identity
        evidence, execution, error = [], {"state": "not-started"}, None
        logs, interpretation = {}, None
        cleanup = {"state": "not-needed"}
        try:
            storage.provision_projection(
                root,
                identity,
                image,
                candidate_files=inputs.sources,
                candidate_rows=request["candidate"]["files"],
                source_roots=request["check"]["source_roots"],
                overlay_root=attempt / "overlays",
                overlay_rows=request["overlays"],
            )
            check_attachments.materialize(projection, attempt / "attachment", request["attachment"])
            storage.write_json(
                attempt / "projection.json",
                {
                    "files": storage.tree_manifest(projection),
                    "candidate_id": request["candidate"]["id"],
                    "image_id": image["id"],
                    "overlays": request["overlays"],
                },
            )
            operation.require_fresh()
            if (attempt / "cancel.json").exists() or cancelled():
                execution = {"state": "not-started", "stop_reason": "cancelled"}
            else:
                # After this boundary edits are allowed; evidence remains bound
                # to the staged candidate rather than following the live tree.
                execution = check_execution.execute(
                    root,
                    attempt,
                    projection,
                    image,
                    request_id=request["id"],
                    log_paths=request["check"]["log_paths"],
                    optional_log_globs=request["check"]["optional_log_globs"],
                    observe=lambda records: owner.observe(
                        inputs, records, request["nonce"], runtime_root=projection, expectation=request["expectation"]
                    ),
                    timeout=request["timeout_seconds"],
                    cancelled=cancelled,
                    expected_host=request["provenance"]["host"],
                    attachment=request["attachment"],
                )
            logs = check_execution.capture_logs(
                projection, request["check"]["log_paths"],
                optional_globs=request["check"]["optional_log_globs"],
            )
            if execution.get("console"):
                logs.update(
                    check_execution.console_logs(
                        root, execution["console"]["session"]["session_id"]
                    )
                )
            (attempt / "logs").mkdir(mode=0o700)
            for name, record in logs.items():
                if record["state"] == "captured":
                    target = attempt / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(record["text"].encode())
                    evidence.append(
                        {
                            "path": name,
                            "sha256": record["sha256"],
                            "size": record["size"],
                        }
                    )
            interpretation = owner.interpret(inputs, logs, request["nonce"], runtime_root=projection)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"[:4000]
            if (attempt / "console.json").exists() and not (
                attempt / "execution.json"
            ).exists():
                execution = {"state": "closure-unverified"}
        finally:
            if projection.exists():
                if execution["state"] == "closure-unverified":
                    cleanup = {
                        "state": "blocked",
                        "reason": "process closure unverified; runtime retained",
                    }
                else:
                    try:
                        receipt = storage.cleanup_projection(root, projection)
                        storage.write_json(attempt / "cleanup.json", receipt)
                        cleanup = {
                            "state": "trashed",
                            "receipt_uri": (attempt / "cleanup.json").as_uri(),
                        }
                    except Exception as exc:
                        cleanup = {
                            "state": "blocked",
                            "reason": str(exc)[:2000],
                            "runtime_uri": projection.as_uri(),
                        }
        try:
            assertion_observation = owner.assertion_observation(inputs, logs, request["nonce"], request["expectation"])
            assertions = evaluate_assertions(request["expectation"], assertion_observation, execution, interpretation, error)
        except Exception as exc:
            error = (error or "Assertion interpretation failed: " + str(exc))[:4000]
            observation = None if request["expectation"] is None else {"state":"incomplete", "facts":{}, "evidence":[], "details":{}, "reasons":[error]}
            assertions = evaluate_assertions(request["expectation"], observation, execution, interpretation, error)
        state = outcome(execution, interpretation, error)
        result = seal_result(
            {
                "format": "workbench-saved-check-result-v4",
                "provenance": request["provenance"],
                "diagnostics": group_findings(interpretation),
                "assertions": assertions,
                "state": state,
                "workspace_uri": selection.pack_uri,
                "selection_id": selection.id,
                "attempt_id": identity,
                "request_id": request["id"],
                "candidate_id": request["candidate"]["id"],
                "candidate": request["candidate"]["source"],
                "image_id": image["id"],
                "provider": owner_identity,
                "execution": execution,
                "cleanup": cleanup,
                "error": error,
                "interpretation": interpretation,
                "evidence": evidence,
                "attempt_uri": attempt.as_uri(),
                "limitations": request["check"]["limitations"],
                "authority": {
                    "source_mutated": False,
                    "construction_authorized": False,
                    "qualification_granted": False,
                },
            }
        )
        storage.write_json(attempt / "result.json", result)
        return result, _reference(attempt / "result.json", result)


def _compare(root, selection, reference_id, candidate_id):
    reference, _ = _reopen(root, reference_id, selection)
    candidate, _ = _reopen(root, candidate_id, selection)
    result = compare_results(reference, candidate)
    directory = _attempt(root, candidate_id) / "comparisons"
    directory.mkdir(mode=0o700, exist_ok=True)
    storage.ordinary(directory, directory=True)
    path = directory / (result["id"].split(":")[-1] + ".json")
    if path.exists():
        if storage.read_json(path) != result:
            raise ValueError("retained comparison changed")
    else:
        storage.write_json(path, result)
    return result, _reference(path, result)


def _history(root, selection):
    directory = root / ".workbench/check-attempts"
    rows, unsupported = [], 0
    paths = sorted(directory.glob("check-*/result.json")) if directory.exists() else []
    if len(paths) > 2000:
        raise ValueError("history exceeds its bound; select exact attempt IDs")
    for path in paths:
        value = storage.read_json(path)
        if value.get("workspace_uri") != selection.pack_uri or value.get("selection_id") != selection.id:
            continue
        if value.get("format") != "workbench-saved-check-result-v4":
            unsupported += 1
            continue
        validate_result(value)
        if value["attempt_id"] != path.parent.name:
            raise ValueError("history attempt identity changed")
        rows.append({**{key: value[key] for key in ("id", "attempt_id", "state", "candidate_id", "image_id")},
                     "source": value["candidate"], "pack": value["provenance"]["runtime"]["pack"],
                     "platform": value["provenance"]["runtime"]["platform"],
                     "started_at": value["execution"].get("console", {}).get("session", {}).get("started_at", "")})
    rows.sort(key=lambda row: (row["started_at"], row["attempt_id"]), reverse=True)
    return {"format": "workbench-check-history-v1", "runs": rows[:100], "omitted_runs": max(0, len(rows) - 100), "unsupported_records": unsupported,
            "meaning": "Sealed metadata preview; comparison revalidates both runs and retained evidence. Older records need fresh checks, not implicit migration."}


def run_checks(selection, argv, *, state_root, cancelled=lambda: False):
    if argv[:1] == ["materials"]:
        from .developer_material_checks import run
        return run(selection, argv[1:], state_root=state_root, cancelled=cancelled)
    parser = argparse.ArgumentParser(prog="context run checks")
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("catalog")
    actions.add_parser("images")
    actions.add_parser("history")
    recipes = actions.add_parser("recipes", help="Inspect source-bound recipe candidates without launching")
    recipes.add_argument("--path", help="Exact groovy/postInit source path")
    recipes.add_argument("--reference", help="Inspect captured source from an exact retained run")
    comparison = actions.add_parser("compare")
    comparison.add_argument("attempt", help="candidate run")
    comparison.add_argument("--reference", required=True, help="explicit reference run")
    from . import developer_environment

    developer_environment.register(actions)
    importer = actions.add_parser("import-image")
    importer.add_argument("--runtime-root", type=Path, required=True)
    importer.add_argument("--java", type=Path, required=True)
    importer.add_argument(
        "--arguments-json",
        required=True,
        help="exact direct Java argument array; no shell interpolation",
    )
    prepare = actions.add_parser("prepare")
    prepare.add_argument("--image", required=True)
    prepare.add_argument("--timeout", type=int, default=600)
    prepare.add_argument("--recipe", help="Exact saved recipe ID from checks recipes")
    prepare.add_argument("--recipe-reference", help="Select expected recipe from a retained run's captured source")
    prepare.add_argument("--absent", action="store_true", help="Expect the referenced exact recipe to be absent")
    prepare.add_argument("--no-trace", action="store_true", help="Run the final-state recipe snapshot without a startup lifecycle observer")
    for action in ("execute", "show", "explain", "source", "progress", "cancel", "log", "recover"):
        command = actions.add_parser(action)
        command.add_argument("attempt")
        if action in {"execute", "recover"}:
            command.add_argument("--confirm", required=True)
        elif action == "log":
            command.add_argument("--path", required=True)
        elif action == "source":
            command.add_argument("--source", required=True, help="Exact explanation section ID and zero-based source index, separated by a colon")
    args = parser.parse_args(argv)
    root = _root(selection, state_root)
    reference, context = (
        None,
        {"selection": {"pack_uri": selection.pack_uri}, "selection_id": selection.id},
    )
    if args.action == "history":
        result = _history(root, selection)
    elif args.action == "compare":
        result, reference = _compare(root, selection, args.reference, args.attempt)
    elif args.action in {"environment-show", "environment-cancel", "environment-recover"}:
        result, reference = developer_environment.run(
            selection, root, None, None, args, cancelled
        )
    elif args.action in {"show", "explain", "source", "progress", "cancel", "execute", "log", "recover"}:
        if args.action == "show":
            result, reference = _reopen(root, args.attempt, selection)
        elif args.action == "source":
            record, _ = _reopen(root, args.attempt, selection)
            if re.fullmatch(r"[a-z0-9-]{1,64}:[0-9]{1,2}", args.source) is None:
                raise ValueError("select one exact retained explanation source")
            section_id, index = args.source.split(":")
            sections = record["assertions"]["observation"]["details"]["explanation"]["sections"]
            section = next((row for row in sections if row["id"] == section_id), None)
            if section is None or int(index) >= len(section["sources"]):
                raise ValueError("retained explanation source does not exist")
            source = section["sources"][int(index)]
            directory, request = _request(root, args.attempt, selection)
            inputs = _inputs(directory, request)
            if source["candidate_id"] != request["candidate"]["id"]:
                inputs = _expectation_source(root, request, inputs, selection)
            raw = inputs.sources[source["location"]["path"]]
            if len(raw) > 1024**2:
                raise ValueError("retained source exceeds the read-only view bound")
            result = {"format": "workbench-check-source-view-v1", "attempt_id": args.attempt,
                      "result_id": record["id"], "source": source, "text": raw.decode("utf-8"), "read_only": True}
        elif args.action == "explain":
            record, _ = _reopen(root, args.attempt, selection)
            assertion = record.get("assertions")
            if assertion is None:
                raise ValueError("This attempt has no retained recipe assertion")
            explanation = assertion["observation"]["details"].get("explanation")
            if explanation is None:
                raise ValueError("No explanation was retained; create a fresh check, not an upgrade to historical evidence")
            result = {"format": "workbench-check-explanation-view-v1", "attempt_id": args.attempt,
                      "result_id": record["id"], "state": record["state"], "assertion_state": assertion["state"],
                      "explanation": explanation,
                      "text": f"Recipe assertion: {assertion['state']}\nStartup: {record['state']}\n" + "\n".join(assertion["reasons"]) + "\n\n" + explanation["text"]}
        elif args.action == "progress":
            attempt, request = _request(root, args.attempt, selection)
            result = check_execution.read_progress(attempt, request["id"])
        elif args.action == "recover":
            result = _recover(root, args.attempt, selection, args.confirm)
        elif args.action == "log":
            record, _ = _reopen(root, args.attempt, selection)
            if args.path not in {row["path"] for row in record.get("evidence", [])}:
                raise ValueError("select one exact retained evidence log")
            path = _attempt(root, args.attempt) / storage.safe_path(args.path)
            result = {
                "format": "workbench-check-log-v1",
                "attempt_id": args.attempt,
                "path": args.path,
                "text": path.read_text(),
                "sha256": sha256(path.read_bytes()).hexdigest(),
            }
        elif args.action == "cancel":
            attempt, request = _request(root, args.attempt, selection)
            if (
                not (attempt / "result.json").exists()
                and not (attempt / "cancel.json").exists()
            ):
                storage.write_json(
                    attempt / "cancel.json", {"request_id": request["id"]}
                )
            result = {
                "format": "workbench-check-cancellation-v1",
                "state": "requested",
                "attempt_id": args.attempt,
            }
        else:
            result, reference = _execute(
                selection, root, args.attempt, args.confirm, cancelled
            )
    else:
        owner, owner_identity = provider(selection.pack_profile)
        description = owner.descriptor()
        if (
            selection.platform_profile != description["platform"]
            or selection.variant != description["variant"]
            or selection.mod_uri
        ):
            raise ValueError(
                "check requires the declared pack/platform variant without a constituent-mod build"
            )
        if "environment" in args.action:
            result, reference = developer_environment.run(
                selection, root, owner, owner_identity, args, cancelled
            )
        elif args.action == "catalog":
            result = {
                "format": "workbench-developer-check-catalog-v1",
                "checks": [description],
                "provider": owner_identity,
            }
        elif args.action == "images":
            result = {
                "format": "workbench-check-images-v1",
                "images": storage.image_summaries(root),
            }
        elif args.action == "recipes":
            if args.reference:
                _reopen(root, args.reference, selection)
                directory, request = _request(root, args.reference, selection)
                inputs = _inputs(directory, request)
            else:
                inputs = capture_source_inputs(selection.workspace)
            result = owner.recipe_catalog(inputs, args.path)
            result["reference"] = args.reference
        elif args.action == "import-image":
            inputs = capture_source_inputs(selection.workspace)
            arguments = json.loads(args.arguments_json)
            runtime = storage.ordinary(args.runtime_root, directory=True)
            if runtime.is_relative_to(
                selection.workspace
            ) or selection.workspace.is_relative_to(runtime):
                raise ValueError("installed runtime must be separate from source")
            binding = owner.validate_image(inputs, runtime, args.java, arguments)
            image = storage.import_image(
                root,
                runtime,
                args.java,
                arguments,
                binding,
                excluded_roots=description["excluded_roots"],
                toolchain_root=args.java.parent.parent,
            )
            result = {
                "format": "workbench-runtime-image-import-v1",
                "id": image["id"],
                "file_count": len(image["files"]),
                "toolchain_file_count": len(image["toolchain"]["files"]),
                "binding": image["binding"],
                "runtime_launched": False,
            }
        else:
            if not 1 <= args.timeout <= 3600:
                raise ValueError("timeout must be between 1 and 3600 seconds")
            result, reference, context = _prepare(
                selection, root, owner, owner_identity, args.image, args.timeout,
                recipe_id=args.recipe, recipe_reference=args.recipe_reference, absent=args.absent, trace=not args.no_trace,
            )
    presentation = {}
    if args.action == "show":
        attempt, request = _request(root, args.attempt, selection)
        presentation["recovery"] = _recovery(attempt, request)
        presentation["request_id"] = request["id"]
    if result.get("format") == "workbench-saved-check-result-v4":
        try:
            _, current_provider = provider(selection.pack_profile)
            presentation["source_current"] = (
                capture_source_inputs(selection.workspace).observation
                == result["candidate"]
                and current_provider == result["provider"]
            )
        except (ValueError, OSError):
            presentation["source_current"] = False
    return {
        "format": "workbench-developer-action-v1",
        "exit_code": 0,
        "context": context,
        "result": result,
        "presentation": presentation,
        "owner_record_ref": reference,
        "diagnostics": "",
    }
