"""Translate invocation only. All recipe and machine decisions belong to Java."""

import argparse
from hashlib import sha256
from importlib.resources import files
import json
import os
from pathlib import Path
import sys

from packaging.specifiers import SpecifierSet
from workbench_api.processes import ProcessError, execute_process, capture_process, open_process_output
from workbench_api.filesystem_paths import native_path
from workbench_api.profile_extensions import require_profile_extension, profile_extension_identity


ENGINE_NOTICES = (
    "LICENSE", "NOTICE.md", "UPSTREAM-NOTICE.md", "third-party/fastutil/LICENSE",
    "third-party/groovy-and-tomlj/LICENSE", "third-party/groovy-and-tomlj/NOTICE",
    "third-party/checker-qual/LICENSE.txt", "third-party/forge/LGPL-2.1.txt",
    "third-party/asm.txt", "third-party/guava-failureaccess-jspecify/LICENSE",
    "third-party/log4j-api/LICENSE", "third-party/log4j-api/NOTICE",
    "third-party/log4j-core/LICENSE", "third-party/log4j-core/NOTICE",
    "third-party/commons-lang3/LICENSE.txt", "third-party/commons-lang3/NOTICE.txt",
)


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key: " + key)
        value[key] = item
    return value


def _json(raw):
    return json.loads(raw, object_pairs_hook=_pairs, parse_constant=lambda value: (_ for _ in ()).throw(ValueError("nonfinite JSON")))


def _response(value):
    output = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    return output


def _ordinary(path):
    if native_path(path).is_symlink() or not native_path(path).is_file():
        raise ValueError("expected an ordinary installed file: " + str(path))
    return path


def installation(home):
    contract = _json(files("workbench_axiom").joinpath("engine-contract.json").read_bytes())
    home = Path(home).absolute()
    if native_path(home).is_symlink() or not native_path(home).is_dir() or native_path(home / "lib").is_symlink():
        raise ValueError("expected an ordinary Axiom installation directory")
    with native_path(_ordinary(home / "engine-manifest.json")).open("rb") as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError("engine manifest exceeds byte bound")
    manifest = _json(raw)
    if not isinstance(manifest, dict) or set(manifest) != {"schema", "component", "version", "mainClass", "jars"}:
        raise ValueError("invalid engine installation manifest fields")
    if manifest["schema"] != contract["installationSchema"] or manifest["component"] != contract["engineComponent"]:
        raise ValueError("not an Axiom engine installation")
    if manifest["version"] not in SpecifierSet(contract["engineVersions"]) or manifest["mainClass"] != contract["mainClass"]:
        raise ValueError("incompatible Axiom engine version or entrypoint")
    if not isinstance(manifest["jars"], dict) or not 1 <= len(manifest["jars"]) <= 16:
        raise ValueError("invalid engine library inventory")
    actual = {path.name for path in native_path(home / "lib").iterdir()}
    if actual != set(manifest["jars"]):
        raise ValueError("installed engine library inventory differs from its manifest")
    jars = []
    for name, digest in sorted(manifest["jars"].items()):
        if Path(name).name != name or not name.endswith(".jar"):
            raise ValueError("unsafe engine library filename")
        path = _ordinary(home / "lib" / name)
        hasher = sha256()
        with native_path(path).open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                hasher.update(chunk)
        if hasher.hexdigest() != digest:
            raise ValueError("installed engine library digest differs: " + name)
        jars.append(str(path))
    return manifest, jars, contract, sha256(raw).hexdigest()


def distribution(home):
    """Verify the independent engine distribution and retained original notices."""
    home = Path(home)
    manifest, _, _, digest = installation(home)
    if home.name != manifest["component"] + "-" + manifest["version"]:
        raise ValueError("engine distribution folder differs from its component/version")
    return {"component": manifest["component"], "version": manifest["version"],
            "manifestSha256": digest,
            "notices": {name: sha256(native_path(_ordinary(home / name)).read_bytes()).hexdigest()
                        for name in ENGINE_NOTICES}}


def invoke(operation, args, context, *, capture_directory=None, capture_binding=None):
    """Return the native domain response; callers own presentation and retention."""
    manifest, jars, contract, installation_digest = installation(args.engine_home)
    # Preserve Core's Windows execution spelling (including a verified 8.3 alias).
    java = args.java.absolute()
    if not java.is_file() or not os.access(java, os.X_OK):
        raise ValueError("Java executable is unavailable")
    request = b""
    profile_identity = None
    target_policy = None
    platform_identity = platform_policy = None
    material_identity = material_policy = None
    material_program = baseline_program = None
    extra = []
    if operation in {"target", "platform"}:
        profile_identity = profile_extension_identity("workbench.axiom_targets", args.profile)
        target_policy = require_profile_extension("workbench.axiom_targets", args.profile).target_policy()
        if target_policy.get("profile") != args.profile:
            raise ValueError("source target policy belongs to another profile")
        extra = ["--" + operation, str(_ordinary(getattr(args, operation)).absolute())]
        if operation == "target":
            if args.artifacts is not None:
                extra += ["--artifacts", str(_ordinary(args.artifacts).absolute())]
            if (args.platform is None) != (args.platform_profile is None):
                raise ValueError("--platform and --platform-profile must be supplied together")
            if args.platform is not None:
                platform_identity = profile_extension_identity("workbench.axiom_targets", args.platform_profile)
                platform_policy = require_profile_extension("workbench.axiom_targets", args.platform_profile).target_policy()
                if platform_policy.get("profile") != args.platform_profile:
                    raise ValueError("platform policy belongs to another profile")
                extra += ["--platform", str(_ordinary(args.platform).absolute())]
    if operation != "coverage" and args.request is not None:
        with _ordinary(args.request).open("rb") as stream:
            request = stream.read()
    if operation == "material-program":
        material_identity = profile_extension_identity("workbench.axiom_targets", args.profile)
        material_policy = require_profile_extension("workbench.axiom_targets", args.profile).material_admission(args.context)
        if material_policy.get("profile") != args.profile or material_policy.get("context") != args.context:
            raise ValueError("material context belongs to another profile")
        from .material_checks import intent, archive_inventory
        value = intent(b"{}" if args.request is None else request)
        value.update(context=args.context, contextPolicySha256=material_policy["contextPolicySha256"],
                     admissionPolicySha256=material_policy["sha256"])
        request = json.dumps(value, sort_keys=True).encode()
        runtime_home = args.runtime_home.absolute()
        if runtime_home.is_symlink() or not runtime_home.is_dir():
            raise ValueError("expected an ordinary native runtime directory")
        extra = ["--runtime-home", str(runtime_home), "--program", str(_ordinary(args.program).absolute())]
        material_program = archive_inventory(args.program)
        if args.baseline_program is not None:
            extra += ["--baseline-program", str(_ordinary(args.baseline_program).absolute())]
            baseline_program = archive_inventory(args.baseline_program)
    process = execute_process if capture_directory is None else capture_process
    capture = {} if capture_directory is None else {"directory": capture_directory, "binding": capture_binding}
    from .java_runtime import vm_arguments, process_isolation
    isolated_inputs = [Path(p) for p in jars] + [Path(extra[i]) for i in range(1, len(extra), 2)]
    result = process(
        [str(java), *vm_arguments(), "-cp", os.pathsep.join(jars), contract["mainClass"], operation, *extra],
        cwd=context.workspace, stdin=request, environment={"LANG": "C.UTF-8"},
        cancelled=context.cancelled, timeout_seconds=None, output_limit=None, input_limit=None,
        **capture, **process_isolation(java, isolated_inputs, source_evaluation=operation != "coverage"),
    )
    if capture_directory is None:
        value = _json(result.stdout)
    else:
        with open_process_output(result.stdout) as stream:
            value = _json(stream.read())
    if not isinstance(value, dict) or value.get("schema") != contract["resultSchema"] or value.get("engineVersion") != manifest["version"] or value.get("operation") != operation:
        raise ValueError("engine response is incompatible with this invocation")
    expected = {"accepted": 0, "rejected": 1, "source-error": 1, "request-error": 2, "unsupported": 3, "requires-context": 3,
                "incomplete": 4, "execution-error": 4}.get(value.get("status"))
    if expected != result.exit_code:
        raise ValueError("engine result and exit status disagree")
    if profile_identity is not None:
        if profile_extension_identity("workbench.axiom_targets", args.profile) != profile_identity:
            raise ValueError("source target profile changed during evaluation")
        if require_profile_extension("workbench.axiom_targets", args.profile).target_policy() != target_policy:
            raise ValueError("target policy changed during evaluation")
        if value.get("status") == "accepted" and (value["result"].get("profile") != args.profile
                or value["result"].get("policySha256") != target_policy["sha256"]):
            raise ValueError("source target differs from the explicitly selected installed profile policy")
    if platform_identity is not None:
        if profile_extension_identity("workbench.axiom_targets", args.platform_profile) != platform_identity or \
                require_profile_extension("workbench.axiom_targets", args.platform_profile).target_policy() != platform_policy:
            raise ValueError("platform profile or policy changed during evaluation")
        if value.get("status") == "accepted":
            selected = value["result"].get("platformInspection", {})
            if selected.get("profile") != args.platform_profile or selected.get("policySha256") != platform_policy["sha256"]:
                raise ValueError("platform differs from the explicitly selected installed profile policy")
    if material_identity is not None:
        if profile_extension_identity("workbench.axiom_targets", args.profile) != material_identity or \
                require_profile_extension("workbench.axiom_targets", args.profile).material_admission(args.context) != material_policy:
            raise ValueError("material profile or policy changed during evaluation")
        body = value.get("result", {})
        if "context" in body and (body["context"].get("id") != args.context or
                body.get("contextPolicySha256") != material_policy["contextPolicySha256"] or
                body.get("admissionPolicySha256") != material_policy["sha256"]):
            raise ValueError("native result differs from the explicitly selected material profile policy")
        from .material_checks import archive_inventory, project_source_comparison
        if (archive_inventory(args.program) != material_program
                or baseline_program is not None and archive_inventory(args.baseline_program) != baseline_program):
            raise ValueError("submitted material source inventory changed during evaluation")
        project_source_comparison(value, material_program, baseline_program)
    # Preserve native outcomes and diagnostics; source deltas explicitly belong to Core.
    value["invocation"] = {
        "installationSha256": installation_digest, "requestSha256": sha256(request).hexdigest(),
        "installationIdentityScope": "preflight", "launchTimeLibraryIdentityVerified": False,
    }
    if capture_directory is not None:
        value["invocation"]["capture"] = result.reference
    if profile_identity is not None:
        value["invocation"]["profile"] = profile_identity
    if platform_identity is not None:
        value["invocation"]["platformProfile"] = platform_identity
    if material_identity is not None:
        value["invocation"]["materialProfile"] = material_identity
    return value, result.exit_code


def main(operation, arguments, context):
    parser = argparse.ArgumentParser(prog="workbench axiom " + operation)
    parser.add_argument("--engine-home", type=Path, required=True, help="Explicit installed Axiom distribution, not a source checkout")
    parser.add_argument("--java", type=Path, required=True, help="Explicit trusted profile-pinned Temurin 25.0.4+7 executable")
    if operation == "target":
        parser.add_argument("--target", type=Path, required=True, help="Immutable Axiom source target package")
        parser.add_argument("--artifacts", type=Path, help="Optional immutable offline artifact bundle; never a mod installation")
        parser.add_argument("--platform", type=Path, help="Explicit platform bundle; no implicit Forge/Cleanroom substitution")
        parser.add_argument("--platform-profile", help="Installed owner required with --platform")
        parser.add_argument("--profile", required=True, help="Explicit installed owner of this source target policy")
        parser.add_argument("--request", type=Path, help="Optional exact-base candidate overlay request")
    elif operation == "platform":
        parser.add_argument("--platform", type=Path, required=True, help="Immutable Axiom platform package")
        parser.add_argument("--profile", required=True, help="Explicit installed owner of the platform policy")
        parser.add_argument("--request", type=Path, help="Optional exact-platform detail request")
    elif operation == "material-program":
        parser.add_argument("--runtime-home", type=Path, required=True, help="Explicit local native material runtime package")
        parser.add_argument("--program", type=Path, required=True, help="Complete saved Groovy source program ZIP")
        parser.add_argument("--baseline-program", type=Path, help="Optional complete baseline ZIP; evaluated in a separate fresh worker")
        parser.add_argument("--profile", required=True, help="Installed owner of the native material context")
        parser.add_argument("--context", required=True, help="Explicit material context ID from the installed profile")
        parser.add_argument("--request", type=Path, help="Optional saved observations/expectations JSON; omit to collect native diagnostics without material selectors")
    elif operation != "coverage":
        parser.add_argument("--request", type=Path, required=True, help="Local axiom.request.v1 JSON file")
    args = parser.parse_args(arguments)
    try:
        value, code = invoke(operation, args, context)
        print(_response(value), end="")
        return code
    except (OSError, ValueError, TypeError, KeyError, ProcessError) as exc:
        print(json.dumps({"schema": "axiom.integration-error.v1", "message": str(exc), "completion": "not-evaluated"}), file=sys.stderr)
        return 4
