"""Bounded, exact-artifact static risk screening for generator edge cases.

The scanner deliberately reports candidates, not causes.  It parses Java class
constant pools so findings are class-scoped and exact-JAR-bound without
pretending that string presence proves an executed route.
"""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
import stat
import struct
from typing import Any, Iterable, Mapping, Sequence
import zipfile

from .model import (
    RISK_PREFIX,
    RISK_SCAN_FORMAT,
    QualifierError,
    content_id,
    require,
    sha256_file,
    utc_now,
)


class ClassFormatError(ValueError):
    pass


def _u1(data: bytes, offset: int) -> tuple[int, int]:
    if offset + 1 > len(data):
        raise ClassFormatError("truncated u1")
    return data[offset], offset + 1


def _u2(data: bytes, offset: int) -> tuple[int, int]:
    if offset + 2 > len(data):
        raise ClassFormatError("truncated u2")
    return struct.unpack_from(">H", data, offset)[0], offset + 2


def _u4(data: bytes, offset: int) -> tuple[int, int]:
    if offset + 4 > len(data):
        raise ClassFormatError("truncated u4")
    return struct.unpack_from(">I", data, offset)[0], offset + 4


def class_utf8_constants(data: bytes) -> frozenset[str]:
    """Return every modified-UTF8 constant using a strict bounded parser."""

    if len(data) < 10 or data[:4] != b"\xca\xfe\xba\xbe":
        raise ClassFormatError("missing Java class magic")
    count = struct.unpack_from(">H", data, 8)[0]
    if count < 1:
        raise ClassFormatError("invalid constant-pool count")
    offset = 10
    index = 1
    strings: set[str] = set()
    while index < count:
        tag, offset = _u1(data, offset)
        if tag == 1:
            length, offset = _u2(data, offset)
            if offset + length > len(data):
                raise ClassFormatError("truncated UTF8 constant")
            raw = data[offset : offset + length]
            offset += length
            try:
                strings.add(raw.decode("utf-8", errors="strict"))
            except UnicodeDecodeError:
                # Java's modified UTF-8 encodes NUL differently.  Replacement is
                # safe for risk matching and keeps malformed bytes from becoming
                # a silent full-coverage claim.
                strings.add(raw.decode("utf-8", errors="replace"))
        elif tag in {3, 4}:
            _, offset = _u4(data, offset)
        elif tag in {5, 6}:
            _, offset = _u4(data, offset)
            _, offset = _u4(data, offset)
            index += 1
        elif tag in {7, 8, 16, 19, 20}:
            _, offset = _u2(data, offset)
        elif tag in {9, 10, 11, 12, 17, 18}:
            _, offset = _u2(data, offset)
            _, offset = _u2(data, offset)
        elif tag == 15:
            _, offset = _u1(data, offset)
            _, offset = _u2(data, offset)
        else:
            raise ClassFormatError(f"unsupported constant-pool tag {tag}")
        index += 1
    return frozenset(strings)


def _has(constants: Iterable[str], *needles: str) -> bool:
    values = tuple(constants)
    return any(any(needle in value for needle in needles) for value in values)


def _rule_matches(class_name: str, constants: frozenset[str]) -> list[dict[str, str]]:
    unordered = _has(constants, "java/util/HashMap", "java/util/HashSet")
    rng = _has(
        constants,
        "java/util/Random",
        "java/util/concurrent/ThreadLocalRandom",
        "java/security/SecureRandom",
    )
    draws = _has(constants, "nextInt", "nextLong", "nextDouble", "nextFloat", "nextBoolean")
    iterates = _has(constants, "entrySet", "keySet", "iterator", "forEach", "spliterator")
    sorting = _has(constants, "java/util/LinkedHashMap", "java/util/TreeMap", "sorted") or (
        _has(constants, "java/util/Collections", "java/util/Arrays") and _has(constants, "sort")
    )
    lower_name = class_name.lower()
    simple_name = lower_name.rsplit(".", 1)[-1]
    world_api = _has(
        constants,
        "net/minecraft/world",
        "net/minecraftforge/fml/common/IWorldGenerator",
        "IChunkGenerator",
        "BiomeDecorator",
        "MapGenBase",
        "WorldGenerator",
    )
    generator_named = any(
        token in lower_name
        for token in ("worldgen", ".world.", ".biome.", ".terrain.", "chunkgenerator", "biomegenerator", "oregenerator", "cavegenerator", "structuregenerator", "terraingenerator")
    ) or (
        "generator" in simple_name
        and not any(token in simple_name for token in ("codegenerator", "parsergenerator", "namegenerator", "stubgenerator"))
    )
    worldish = world_api or generator_named
    results: list[dict[str, str]] = []

    if worldish and unordered and rng and draws and iterates and not sorting:
        results.append(
            {
                "rule_id": "unordered-rng-selection",
                "severity": "high",
                "title": "RNG selection traverses an unordered collection candidate",
                "why": "Class-level constants combine HashMap/HashSet iteration with random draws and expose no ordering marker; object identity or insertion history may remap a seeded roll.",
            }
        )
    if generator_named and _has(constants, "java/util/IdentityHashMap", "identityHashCode") and iterates:
        results.append(
            {
                "rule_id": "identity-order-dependence",
                "severity": "high",
                "title": "Identity-sensitive iteration candidate",
                "why": "Identity hashing or IdentityHashMap iteration can vary across equivalent JVM constructions.",
            }
        )
    ambient_entropy = _has(
        constants,
        "currentTimeMillis",
        "nanoTime",
        "randomUUID",
        "java/util/concurrent/ThreadLocalRandom",
        "java/security/SecureRandom",
    )
    if ambient_entropy and generator_named:
        results.append(
            {
                "rule_id": "ambient-entropy",
                "severity": "high",
                "title": "Ambient entropy reaches a world-generation-shaped class",
                "why": "Clock, UUID, thread-local, secure, or global random sources are visible in a generator-shaped class and require runtime disposition.",
            }
        )
    filesystem = _has(constants, "java/io/File", "java/nio/file/Files", "DirectoryStream") and _has(constants, "listFiles", "Files.list", "newDirectoryStream", "list")
    if worldish and filesystem and not sorting:
        results.append(
            {
                "rule_id": "filesystem-enumeration-order",
                "severity": "medium",
                "title": "Filesystem enumeration has no visible canonical ordering",
                "why": "Directory iteration order is platform/filesystem-dependent unless normalized before it affects registration or selection.",
            }
        )
    reflection = _has(constants, "getDeclaredMethods", "getMethods", "getDeclaredFields", "getFields", "ServiceLoader")
    if worldish and reflection and not sorting:
        results.append(
            {
                "rule_id": "reflection-enumeration-order",
                "severity": "medium",
                "title": "Reflection or service enumeration has no visible ordering marker",
                "why": "Reflection and service enumeration order is not a portable semantic contract.",
            }
        )
    asynchronous = _has(constants, "ForkJoinPool", "CompletableFuture", "ExecutorService", "parallelStream", "Thread.start")
    if asynchronous and generator_named:
        results.append(
            {
                "rule_id": "asynchronous-worldgen",
                "severity": "high",
                "title": "Asynchronous execution candidate in a world-generation-shaped class",
                "why": "Concurrent completion and neighbor writes require an explicit quiescence and ordering contract before final capture.",
            }
        )
    if worldish and _has(constants, "java/util/WeakHashMap", "java/lang/ref/WeakReference") and iterates:
        results.append(
            {
                "rule_id": "gc-sensitive-cache",
                "severity": "medium",
                "title": "GC-sensitive cache iteration candidate",
                "why": "Weak-key reachability and collection timing can vary under heap or GC perturbation.",
            }
        )
    host_default = _has(constants, "user.language", "file.encoding") or (
        _has(constants, "java/util/Locale", "java/util/TimeZone") and _has(constants, "getDefault")
    )
    if host_default and worldish:
        results.append(
            {
                "rule_id": "host-default-dependence",
                "severity": "medium",
                "title": "Host locale, timezone, or encoding candidate",
                "why": "Host defaults must not affect registry, configuration, seed, or generation decisions.",
            }
        )
    if worldish and unordered and _has(constants, "doubleValue", "floatValue", "sum", "reduce") and _has(constants, "parallel", "ForkJoinPool"):
        results.append(
            {
                "rule_id": "nonassociative-parallel-reduction",
                "severity": "medium",
                "title": "Floating-point reduction order candidate",
                "why": "Parallel floating-point accumulation can change results when traversal or partition order changes.",
            }
        )
    return results


def _safe_entry(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(name) and not path.is_absolute() and ".." not in path.parts and "\\" not in name


def _disposition_for(
    *, jar_sha256: str, class_name: str, rule_id: str, dispositions: Sequence[Mapping[str, Any]]
) -> tuple[str, str | None]:
    matches = [
        row
        for row in dispositions
        if row.get("jar_sha256") == jar_sha256
        and row.get("class_name") == class_name
        and row.get("rule_id") == rule_id
    ]
    if not matches:
        return "unreviewed", None
    if len(matches) != 1:
        raise QualifierError(f"duplicate static-risk disposition for {jar_sha256}:{class_name}:{rule_id}")
    return str(matches[0]["disposition"]), str(matches[0]["rationale"])


def _scan_one(
    path: Path,
    *,
    limits: Mapping[str, int],
    dispositions: Sequence[Mapping[str, Any]],
    finding_budget: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    requested = path.expanduser()
    require(not requested.is_symlink(), f"risk-scan JAR cannot be a symlink: {requested}")
    resolved = requested.resolve(strict=True)
    before = resolved.stat()
    mode = before.st_mode
    require(stat.S_ISREG(mode), f"risk-scan input is not a regular file: {resolved}")
    require(resolved.suffix.lower() == ".jar", f"risk-scan input is not a JAR: {resolved}")
    digest = sha256_file(resolved)
    findings: list[dict[str, Any]] = []
    limitations: list[str] = []
    classes = 0
    malformed = 0
    nested = 0
    uncompressed = 0
    duplicate_names: list[str] = []
    try:
        with zipfile.ZipFile(resolved) as archive:
            infos = archive.infolist()
            names: set[str] = set()
            for info in infos:
                if info.filename in names:
                    duplicate_names.append(info.filename)
                names.add(info.filename)
                require(_safe_entry(info.filename), f"unsafe ZIP member in {resolved.name}: {info.filename!r}")
                require(not (info.flag_bits & 0x1), f"encrypted ZIP member is unsupported: {resolved.name}:{info.filename}")
                uncompressed += info.file_size
                require(uncompressed <= limits["max_uncompressed_bytes"], f"JAR exceeds uncompressed scan bound: {resolved}")
                unix_mode = (info.external_attr >> 16) & 0xFFFF
                require(not stat.S_ISLNK(unix_mode), f"symlink ZIP member is unsupported: {resolved.name}:{info.filename}")
                if info.filename.lower().endswith((".jar", ".zip")):
                    nested += 1
                if not info.filename.endswith(".class"):
                    continue
                classes += 1
                require(classes <= limits["max_classes"], f"JAR exceeds class scan bound: {resolved}")
                if info.file_size > limits["max_class_bytes"]:
                    malformed += 1
                    limitations.append(f"class exceeds per-entry bound: {resolved.name}:{info.filename}")
                    continue
                try:
                    data = archive.read(info)
                    constants = class_utf8_constants(data)
                except (ClassFormatError, OSError, RuntimeError, zipfile.BadZipFile) as exc:
                    malformed += 1
                    if len(limitations) < 32:
                        limitations.append(f"unscannable class {resolved.name}:{info.filename}: {exc}")
                    continue
                class_name = info.filename[:-6].replace("/", ".")
                for match in _rule_matches(class_name, constants):
                    if len(findings) >= finding_budget:
                        break
                    disposition, rationale = _disposition_for(
                        jar_sha256=digest,
                        class_name=class_name,
                        rule_id=match["rule_id"],
                        dispositions=dispositions,
                    )
                    finding = {
                        "finding_id": "",
                        "jar_sha256": digest,
                        "jar_name": resolved.name,
                        "class_name": class_name,
                        **match,
                        "disposition": disposition,
                        "rationale": rationale,
                        "evidence_state": "class-constant-pool-cooccurrence",
                    }
                    finding["finding_id"] = "workbench-worldgen-risk:sha256:" + hashlib.sha256(
                        (digest + "\0" + class_name + "\0" + match["rule_id"]).encode("utf-8")
                    ).hexdigest()
                    findings.append(finding)
    except zipfile.BadZipFile as exc:
        raise QualifierError(f"cannot open risk-scan JAR {resolved}: {exc}") from exc
    if duplicate_names:
        limitations.append(f"duplicate ZIP member names prevent complete static coverage ({len(duplicate_names)} entries)")
    if nested:
        limitations.append(f"nested archives were inventoried but not recursively scanned ({nested} entries)")
    after = resolved.stat()
    identity = lambda row: (row.st_dev, row.st_ino, row.st_mode, row.st_size, row.st_mtime_ns, row.st_ctime_ns)
    require(identity(before) == identity(after) and digest == sha256_file(resolved), f"risk-scan JAR changed while being scanned: {resolved}")
    coverage = "complete" if not limitations else "partial"
    record = {
        "path": str(resolved),
        "sha256": digest,
        "size_bytes": resolved.stat().st_size,
        "class_count": classes,
        "malformed_class_count": malformed,
        "nested_archive_count": nested,
        "uncompressed_bytes": uncompressed,
        "coverage": coverage,
        "finding_count": len(findings),
    }
    return record, findings, limitations


def scan_jars(
    paths: Sequence[Path],
    *,
    limits: Mapping[str, int],
    dispositions: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    require(bool(paths), "static risk scan requires at least one exact JAR")
    require(len(paths) <= limits["max_jars"], "static risk scan exceeds the JAR bound")
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path.expanduser().resolve(strict=True)
        if resolved not in seen:
            unique.append(resolved)
            seen.add(resolved)
    jars: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    limitations: list[str] = []
    remaining = limits["max_findings"]
    total_classes = 0
    total_uncompressed = 0
    for path in unique:
        jar, rows, gaps = _scan_one(
            path,
            limits=limits,
            dispositions=dispositions,
            finding_budget=max(0, remaining),
        )
        jars.append(jar)
        total_classes += jar["class_count"]
        total_uncompressed += jar["uncompressed_bytes"]
        require(total_classes <= limits["max_classes"], "static risk scan exceeds the aggregate class bound")
        require(total_uncompressed <= limits["max_uncompressed_bytes"], "static risk scan exceeds the aggregate uncompressed-byte bound")
        findings.extend(rows)
        limitations.extend(gaps)
        remaining = limits["max_findings"] - len(findings)
    if remaining <= 0:
        limitations.append("static findings reached the configured bound; additional matches were not retained")
    severity_order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda row: (severity_order[row["severity"]], row["jar_name"], row["class_name"], row["rule_id"]))
    counts = {
        severity: sum(1 for row in findings if row["severity"] == severity)
        for severity in ("high", "medium", "low")
    }
    dispositions_summary = {
        state: sum(1 for row in findings if row["disposition"] == state)
        for state in ("unreviewed", "accepted", "patched", "rejected")
    }
    report: dict[str, Any] = {
        "format": RISK_SCAN_FORMAT,
        "schema_version": 1,
        "report_id": "",
        "created_at": utc_now(),
        "coverage": "complete" if not limitations else "partial",
        "method": "bounded-java-class-constant-pool-cooccurrence-v1",
        "jars": jars,
        "findings": findings,
        "summary": {
            "jar_count": len(jars),
            "class_count": sum(row["class_count"] for row in jars),
            "finding_count": len(findings),
            "severity": counts,
            "dispositions": dispositions_summary,
        },
        "limitations": sorted(set(limitations + [
            "A static co-occurrence is a risk candidate, not proof that the class loads, the route executes, or the pattern causes a runtime delta.",
            "The scanner does not decompile control flow or recursively inspect nested archives in V1.",
        ])),
    }
    # The two methodological limitations do not make byte coverage partial.
    byte_gaps = [item for item in limitations]
    report["coverage"] = "complete" if not byte_gaps else "partial"
    report["report_id"] = content_id(RISK_PREFIX, report, "report_id")
    return report


__all__ = ["ClassFormatError", "class_utf8_constants", "scan_jars"]
