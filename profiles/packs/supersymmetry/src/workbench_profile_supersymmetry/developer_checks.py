"""Supersymmetry client cold-start policy for ordinary saved source edits."""

import json
from pathlib import Path
import re
import tomllib

from workbench_api.checks import observation
from workbench_api.profile_extensions import require_profile_extension
from workbench_api.packwiz import client_dependencies
from .check_diagnostics import findings as collect_findings

PROFILE_API_VERSION = 1
SOURCE_ROOTS = ("groovy", "config", "resources", "scripts")
LOG_PATHS = ("logs/latest.log", "logs/groovy.log")
PREFIX = "[WORKBENCH-SAVED-CHECK]"


def descriptor():
    return {
        "id": "supersymmetry.client-cold-start",
        "label": "Supersymmetry client cold start",
        "platform": "cleanroom",
        "variant": "cleanroom-provisional",
        "side": "client",
        "source_roots": list(SOURCE_ROOTS),
        "excluded_roots": [
            *SOURCE_ROOTS,
            "logs",
            "crash-reports",
            "saves",
            "screenshots",
            ".check-tmp",
        ],
        "log_paths": list(LOG_PATHS),
        "optional_log_globs": ["crash-reports/*.txt", "logs/cleanmix.log"],
        "limitations": [
            "Native Linux, client only. No gameplay, reload, server parity or publication qualification.",
            "Registration totals are observations, not proof that an edited recipe is registered or executable.",
            "The image is dependency-bound and prepared separately; this check does not download or update a modpack.",
            "Disposable execution is not a security sandbox; run trusted projects only.",
        ],
    }


def binding(inputs):
    # Packwiz dependencies participate independently of generated index hashes.
    # Script/config edits can reuse an image; dependency edits need preparation.
    if (
        "pack.toml" not in inputs.sources
        or "groovy/runConfig.json" not in inputs.sources
    ):
        raise ValueError(
            "select a Supersymmetry Packwiz checkout with Groovy run configuration"
        )
    return {
        "pack": "supersymmetry",
        "platform": "cleanroom",
        "variant": "cleanroom-provisional",
        "side": "client",
        "dependency_sha256": dependency_requirements(inputs)["id"].split(":")[-1],
    }


def dependency_requirements(inputs):
    return client_dependencies(dict(inputs.files), source_roots=SOURCE_ROOTS)


def provenance(inputs, executable):
    dependencies = dependency_requirements(inputs)
    pack = dependencies["pack"]
    if any(not isinstance(pack.get(key), str) or not 0 < len(pack[key]) <= 256 for key in ("name", "version")):
        raise ValueError("check provenance requires explicit bounded pack name and version")
    return {
        "pack": {"name": pack.get("name"), "version": pack.get("version"),
                 "declared_versions": pack["versions"]},
        "dependencies": [
            {key: row[key] for key in ("metadata_path", "output_path", "hash_format", "hash")}
            for row in dependencies["artifacts"] if row["enabled"]
        ],
        "platform": require_profile_extension("workbench.check_platforms", "cleanroom").provenance(executable),
    }


def validate_image(inputs, runtime, executable, arguments):
    """Reject mismatched installed artifacts rather than silently reusing a seed."""
    require_profile_extension(
        "workbench.check_platforms", "cleanroom"
    ).validate_client_image(runtime, executable, arguments)
    required = set()
    for name, raw in inputs.files:
        if not name.startswith("mods/") or not name.endswith(".pw.toml"):
            continue
        metadata = tomllib.loads(raw.decode())
        if (
            metadata.get("side") == "server"
            or metadata.get("option", {}).get("optional")
            and not metadata["option"].get("default", False)
        ):
            continue
        filename = metadata.get("filename", "")
        if not filename or Path(filename).name != filename:
            raise ValueError("pack mod filename is not portable")
        if filename.casefold() in {value.casefold() for value in required}:
            raise ValueError("pack contains duplicate client mod filenames")
        download = metadata.get("download", {})
        algorithm = download.get("hash-format")
        if algorithm not in {"sha1", "sha256", "sha512", "md5"}:
            raise ValueError(
                "client check requires hash-locked installed mod artifacts"
            )
        import hashlib

        path = runtime / "mods" / filename
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"runtime image lacks client mod: {filename}")
        with path.open("rb") as stream:
            observed = hashlib.file_digest(stream, algorithm).hexdigest()
        if observed != download.get("hash"):
            raise ValueError(
                f"runtime image mod differs from saved dependency: {filename}"
            )
        required.add(filename)
    if not required:
        raise ValueError("client runtime image requires a nonempty hash-locked mod set")
    actual = {
        path.relative_to(runtime / "mods").as_posix()
        for path in (runtime / "mods").rglob("*")
        if path.is_file() and path.suffix.lower() in {".jar", ".zip"}
    }
    if actual != required:
        raise ValueError(
            "runtime image contains undeclared or nested client mods: "
            + ", ".join(sorted(actual - required))
        )
    return binding(inputs)


def recipe_catalog(inputs, path=None):
    from .recipe_checks import catalog
    return catalog(inputs, path)


def attachment(inputs, nonce, *, expectation):
    from .recipe_lifecycle import attachment as prepare_attachment
    return prepare_attachment(inputs, nonce, expectation=expectation)


def validate_attachment_image(runtime):
    from .recipe_lifecycle import validate_image as validate_targets
    validate_targets(runtime)


def recipe_expectation(inputs, source_inputs, recipe_id, *, mode="present", reference=None):
    from .recipe_checks import expectation
    return expectation(inputs, source_inputs, recipe_id, mode=mode, reference=reference)


def assertion_observation(inputs, logs, nonce, expectation):
    from .recipe_checks import interpret
    from .recipe_explanations import explain
    value = interpret(logs, nonce, expectation)
    if value is not None:
        value["details"]["explanation"] = explain(inputs, expectation, value)
    return value


def overlays(inputs, nonce, *, expectation=None, trace=False):
    configuration = json.loads(inputs.sources["groovy/runConfig.json"])
    paths = configuration.get("loaders", {}).get("postInit")
    if not isinstance(paths, list) or not all(isinstance(x, str) for x in paths):
        raise ValueError("Groovy postInit loader must be an explicit path list")
    if (
        any(name.startswith("groovy/workbenchChecks/") for name in inputs.sources)
        or "workbenchChecks/" in paths
    ):
        raise ValueError("reserved check instrumentation path is already present")
    paths.append("workbenchChecks/")
    # GroovyScript does not bundle groovy-json. Gson is part of the admitted
    # platform runtime, so the probe adds no optional compiler dependency.
    script = """import com.google.gson.Gson
import net.minecraftforge.fml.common.FMLCommonHandler
import net.minecraftforge.fml.common.registry.ForgeRegistries
import net.minecraftforge.fluids.FluidRegistry
def observation = [nonce: %s, side: FMLCommonHandler.instance().side.toString(), items: ForgeRegistries.ITEMS.keys.size(), fluids: FluidRegistry.getRegisteredFluids().size()]
log.infoMC(%s + new Gson().toJson(observation))
""" % (json.dumps(nonce), json.dumps(PREFIX))
    result = {
        "groovy/runConfig.json": (json.dumps(configuration, indent=2) + "\n").encode(),
        "groovy/workbenchChecks/Observe.groovy": script.encode(),
    }
    from .recipe_checks import probe
    recipe_probe = probe(expectation, nonce, trace=trace)
    if recipe_probe is not None:
        result["groovy/workbenchChecks/RecipeCapture.groovy"] = recipe_probe
    return result


def _marker(logs, nonce):
    matches = []
    for line in (logs.get("logs/latest.log", {}).get("text") or "").splitlines():
        if PREFIX in line:
            try:
                value = json.loads(line.split(PREFIX, 1)[1])
            except ValueError:
                continue
            if isinstance(value, dict) and value.get("nonce") == nonce:
                matches.append(value)
    if len(matches) != 1:
        return None
    value = matches[0]
    if (
        set(value) != {"nonce", "side", "items", "fluids"}
        or value["side"] != "CLIENT"
        or any(
            type(value[key]) is not int or value[key] < 0 for key in ("items", "fluids")
        )
    ):
        return None
    return value


def _checkpoint(logs, nonce):
    text = logs.get("logs/latest.log", {}).get("text") or ""
    return (
        _marker(logs, nonce) is not None
        and re.search(r"Forge Mod Loader has successfully loaded \d+ mods", text)
        is not None
    )


def _observation(logs, nonce, findings):
    failure = next((row for row in findings if row["blocking"]), None)
    if failure:
        return observation("failure", failure["code"], failure["reason"], failure["evidence"][:32])
    latest = (logs.get("logs/latest.log", {}).get("text") or "").splitlines()
    if _checkpoint(logs, nonce):
        evidence = [
            {"log": "logs/latest.log", "line": number}
            for number, line in enumerate(latest, 1)
            if PREFIX in line or re.search(r"Forge Mod Loader has successfully loaded \d+ mods", line)
        ]
        return observation("checkpoint", "client-started", "Client postInit probe and FML loaded checkpoint observed", evidence[:32])
    stages = (
        ("client-bootstrap", r"Loading tweak class name|Forge Mod Loader version", "Client bootstrap observed"),
        ("mod-discovery", r"Forge Mod Loader has identified \d+ mods", "Mod discovery observed"),
        ("groovy-preinit", r"Running scripts in loader 'preInit'|(?:compile|run) in preInit|running script preInit\.", "Groovy preInit observed"),
        ("client-init", r"Running scripts in loader 'init'|Skipping load stage init|(?:compile|run) in init|Starting up SoundSystem", "Client initialization observed"),
        ("groovy-postinit", r"Running scripts in loader 'postInit'|(?:compile|run) in postInit|running script postInit\.", "Groovy postInit observed"),
        ("postinit-probe", re.escape(PREFIX), "PostInit probe logged; waiting for the full startup checkpoint"),
    )
    selected = observation("running", "starting", "Waiting for client startup evidence")
    for stage, pattern, summary in stages:
        for name in LOG_PATHS:
            for number, line in enumerate((logs.get(name, {}).get("text") or "").splitlines(), 1):
                if re.search(pattern, line):
                    if stage != "postinit-probe" or _marker(logs, nonce) is not None:
                        selected = observation("running", stage, summary, [{"log": name, "line": number}])
                    break
    return selected


def observe(inputs, logs, nonce, *, runtime_root, expectation=None):
    value = _observation(logs, nonce, collect_findings(inputs, logs, runtime_root=runtime_root))
    if value["state"] == "checkpoint" and expectation is not None and expectation["support"] == "supported":
        from .recipe_checks import marker
        captured = marker(logs, nonce, expectation)
        if captured is None:
            return observation("running", "recipe-capture-pending", "Startup checkpoint observed; waiting for recipe capture after load complete", value["evidence"])
        return observation("checkpoint", "recipe-capture-finished", "Startup and post-load recipe capture observed; assertion result is separate", [*value["evidence"], captured[1]][:32])
    return value


def interpret(inputs, logs, nonce, *, runtime_root):
    findings = collect_findings(inputs, logs, runtime_root=runtime_root)
    blocking = sum(row["blocking"] for row in findings)
    unknown = sum(row["category"] == "unclassified" for row in findings)
    complete_logs = all(
        logs.get(name, {}).get("state") == "captured" for name in LOG_PATHS
    ) and all(record.get("state") == "captured" for record in logs.values())
    observed = _checkpoint(logs, nonce)
    marker = _marker(logs, nonce)
    graphics = sorted(set(re.findall(r"^\s*GL info: (.+)$", logs.get("logs/latest.log", {}).get("text") or "", re.M)))
    return {
        "outcome": "failed"
        if blocking
        else "completed"
        if complete_logs and observed and not unknown and len(findings) <= 1000
        else "inconclusive",
        "observation": _observation(logs, nonce, findings),
        "checks": [
            {
                "id": "client-startup",
                "state": "observed" if observed else "not-observed",
                "meaning": "Client postInit probe and FML loaded checkpoint",
            },
            {
                "id": "groovy-compilation",
                "state": "failed"
                if any(row["category"] == "compiler" for row in findings)
                else "no-errors-observed"
                if observed and complete_logs
                else "inconclusive",
                "meaning": "Runtime compiler/log observations, not an independent offline compilation or proof that every source file ran",
            },
            {
                "id": "registration",
                "state": "observed" if marker else "not-observed",
                "observation": marker,
                "meaning": "Forge item/fluid registration totals, not edited-recipe correctness",
            },
        ],
        "findings": findings[:1000],
        "findings_count": len(findings),
        "blocking_findings_count": blocking,
        "unclassified_findings_count": unknown,
        "truncated": len(findings) > 1000,
        "complete_logs": complete_logs,
        "runtime_observations": {
            "graphics": [row[:8000] for row in graphics[:8]], "complete": len(graphics) <= 8 and all(len(row) <= 8000 for row in graphics),
        },
    }
