"""Cleanroom JVM, Gradle, Minecraft and mod-loader console classification.

These are conservative observations, not a replacement for domain evidence.
The event wire vocabulary and classification basis identities are retained.
"""
from __future__ import annotations

import re

from workbench_api.events import EventClassification

_COMPILER_RE = re.compile(
    r"^\s*(?P<path>(?:[A-Za-z]:[\\/]|/)?[^\r\n]+?\.(?:java|groovy|kt|scala))"
    r":(?P<line>[1-9]\d*)(?::(?P<column>[1-9]\d*))?"
    r":\s*(?P<level>error|warning|note):\s*(?P<message>.*)$",
    re.IGNORECASE,
)
_STACK_FRAME_RE = re.compile(
    r"^\s*at\s+(?P<owner>[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+)"
    r"\((?P<location>[^()]*)\)\s*$"
)
_EXCEPTION_RE = re.compile(
    r'^\s*(?:(?P<prefix>Exception in thread "[^"]+"|Caused by|Suppressed)\s*:?\s*)?'
    r"(?P<type>(?:[A-Za-z_$][\w$]*\.)*[A-Za-z_$][\w$]*(?:Exception|Error|Throwable))"
    r"(?::\s*(?P<detail>.*))?\s*$"
)
_GRADLE_TASK_RE = re.compile(
    r"^\s*> Task (?P<task>:[^\s]+)(?:\s+(?P<state>FAILED|UP-TO-DATE|FROM-CACHE|SKIPPED|NO-SOURCE))?\s*$"
)
_BUILD_FAILED_RE = re.compile(r"^\s*BUILD FAILED(?: in .+)?\s*$")
_BUILD_FAILURE_HEADER_RE = re.compile(
    r"^\s*FAILURE: Build failed with an exception\.\s*$"
)
_BUILD_SUCCESS_RE = re.compile(r"^\s*BUILD SUCCESSFUL(?: in .+)?\s*$")
_CRASH_MARKERS = (
    re.compile(r"^\s*---- Minecraft Crash Report ----\s*$"),
    re.compile(
        r"^\s*#?\s*A fatal error has been detected by the Java Runtime Environment:\s*$"
    ),
    re.compile(r"^\s*(?:This )?crash report has been saved to:\s*.+\s*$", re.I),
    re.compile(r"^\s*Minecraft ran into a problem and crashed\.\s*$", re.I),
)


def _logger_subsystem(logger: str | None) -> tuple[str | None, str | None]:
    if not logger:
        return None, None
    folded = logger.casefold()
    if "cleanmix" in folded:
        return "cleanmix", "classify.logger.cleanmix-v1"
    if "mixin" in folded or "spongepowered" in folded:
        return "mixin", "classify.logger.mixin-v1"
    if "groovy" in folded:
        return "groovy", "classify.logger.groovy-v1"
    if "cleanroom" in folded or "fml" in folded or "forge" in folded:
        return "cleanroom-fml", "classify.logger.cleanroom-fml-v1"
    if "minecraft" in folded:
        return "minecraft", "classify.logger.minecraft-v1"
    return None, None


def _content_subsystem(text: str) -> tuple[str | None, str | None]:
    folded = text.casefold()
    if "cleanmix" in folded:
        return "cleanmix", "classify.keyword.cleanmix-v1"
    if any(
        token in folded
        for token in (
            "mixinapplyerror",
            "mixintransformererror",
            "invalidmixinexception",
            "injectionerror",
            "injection failed",
            "mixin config",
            "mixins.",
            "org.spongepowered.asm.mixin",
        )
    ):
        return "mixin", "classify.keyword.mixin-v1"
    if any(
        token in folded
        for token in (
            "groovyscript",
            "multiplecompilationerrorsexception",
            "groovy.lang.",
            ".groovy:",
            "groovy compilation",
        )
    ):
        return "groovy", "classify.keyword.groovy-v1"
    if any(
        token in folded
        for token in (
            "registryevent",
            "missing mappings",
            "missingmapping",
            "registry name",
            "registry object",
            "gamedata",
            "forge registry",
        )
    ):
        return "registry", "classify.keyword.registry-v1"
    if any(
        token in folded
        for token in (
            "worldgen",
            "world generation",
            "populatechunkevent",
            "decoratebiomeevent",
            "oregenevent",
            "terraingen",
            "cascading worldgen",
            "chunk generator",
        )
    ):
        return "worldgen", "classify.keyword.worldgen-v1"
    return None, None


def classify(previous: EventClassification) -> EventClassification:
    message = previous.message
    source_timestamp = previous.source_timestamp
    logger = previous.logger
    thread = previous.thread
    severity = previous.severity
    kind = previous.kind
    subsystem = previous.subsystem
    provenance = previous.parse_provenance
    basis = list(previous.basis)
    outcome_failure = previous.outcome_failure

    compiler_match = _COMPILER_RE.match(message)
    if compiler_match is not None:
        compiler_level = compiler_match.group("level").casefold()
        kind = "compiler_diagnostic"
        subsystem = "compiler"
        severity = {
            "error": "error",
            "warning": "warning",
            "note": "info",
        }[compiler_level]
        provenance = "parsed"
        basis.append("parse.compiler-diagnostic-v1")

    gradle_task = _GRADLE_TASK_RE.match(message)
    if gradle_task is not None:
        kind = "build"
        subsystem = "gradle"
        provenance = "parsed"
        basis.append("parse.gradle-task-v1")
        if gradle_task.group("state") == "FAILED":
            severity = "error"
            outcome_failure = True
            basis.append("outcome.gradle-task-failed-v1")
        elif severity == "unknown":
            severity = "info"

    if _BUILD_FAILED_RE.match(message) or _BUILD_FAILURE_HEADER_RE.match(message):
        kind = "build"
        subsystem = "gradle"
        severity = "error"
        provenance = "parsed"
        outcome_failure = True
        basis.extend(("parse.gradle-build-status-v1", "outcome.build-failed-v1"))
    elif _BUILD_SUCCESS_RE.match(message):
        kind = "build"
        subsystem = "gradle"
        severity = "info"
        provenance = "parsed"
        basis.append("parse.gradle-build-status-v1")

    stack_match = _STACK_FRAME_RE.match(message)
    if stack_match is not None:
        kind = "stack_frame"
        subsystem = "java"
        provenance = "parsed"
        if severity == "unknown":
            severity = "info"
        basis.append("parse.java-stack-frame-v1")
    else:
        exception_match = _EXCEPTION_RE.match(message)
        if exception_match is not None:
            kind = "exception"
            subsystem = "java"
            provenance = "parsed"
            if severity in {"unknown", "info", "warning"}:
                severity = "error"
            basis.append("parse.java-exception-v1")

    if any(pattern.match(message) for pattern in _CRASH_MARKERS):
        kind = "exception"
        subsystem = "minecraft" if "minecraft" in message.casefold() or "crash report" in message.casefold() else "java"
        severity = "fatal"
        provenance = "parsed"
        outcome_failure = True
        basis.extend(("parse.crash-marker-v1", "outcome.crash-marker-v1"))

    logger_subsystem, logger_basis = _logger_subsystem(logger)
    content_subsystem, content_basis = _content_subsystem(
        f"{logger or ''}\n{message}"
    )
    chosen_subsystem = content_subsystem or logger_subsystem
    if chosen_subsystem and subsystem in {"generic", "java", "minecraft", "cleanroom-fml"}:
        subsystem = chosen_subsystem
        if content_basis:
            basis.append(content_basis)
        elif logger_basis:
            basis.append(logger_basis)

        if kind not in {"build", "compiler_diagnostic", "stage", "stack_frame"}:
            kind = {
                "mixin": "mixin",
                "cleanmix": "mixin",
                "groovy": "groovy",
                "registry": "registry",
                "worldgen": "worldgen",
            }.get(subsystem, kind)
        if provenance == "raw":
            provenance = "heuristic"

    if subsystem == "generic" and logger_subsystem:
        subsystem = logger_subsystem
        basis.append(logger_basis or "classify.logger-v1")

    # Gradle/compiler hints that do not have a structured diagnostic/status.
    folded = f"{logger or ''}\n{message}".casefold()
    if subsystem == "generic" and any(
        token in folded
        for token in ("org.gradle", "gradle daemon", "execution failed for task")
    ):
        subsystem = "gradle"
        kind = "build"
        provenance = "heuristic" if provenance == "raw" else provenance
        basis.append("classify.keyword.gradle-v1")
    if subsystem == "generic" and any(
        token in folded for token in ("javac", "cannot find symbol", "compiler error")
    ):
        subsystem = "compiler"
        kind = "compiler_diagnostic"
        provenance = "heuristic" if provenance == "raw" else provenance
        basis.append("classify.keyword.compiler-v1")

    basis_tuple = tuple(dict.fromkeys(basis))
    signal = outcome_failure or severity in {"warning", "error", "fatal"} or kind == "stage"
    return EventClassification(
        message=message,
        source_timestamp=source_timestamp,
        kind=kind,
        severity=severity,
        subsystem=subsystem,
        logger=logger,
        thread=thread,
        parse_provenance=provenance,
        basis=basis_tuple,
        signal=signal,
        outcome_failure=outcome_failure,
    )
