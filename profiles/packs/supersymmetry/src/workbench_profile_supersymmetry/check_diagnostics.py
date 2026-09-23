"""Supersymmetry log classification and exact captured-source diagnostic anchors.

Log levels alone are not compiler diagnoses. Unknown errors remain unresolved;
only narrow, explained policy rules may identify nonblocking observations.
"""

from collections import defaultdict
from hashlib import sha256
import json
from pathlib import PurePosixPath
import re
from urllib.parse import unquote, urlsplit

from workbench_pack_program_studio.source_locations import source_location


HEADER = re.compile(
    r"^(?:\[\d{2}:\d{2}:\d{2}\] )?\[(?P<thread>[^\]\n]+)/(?P<level>TRACE|DEBUG|INFO|WARN|ERROR|FATAL)\] "
    r"(?:\[(?P<logger>[^\]\n]+)\]:? )?(?P<message>.*)$"
)
ANSI = re.compile(r"\x1b\[[0-9;]*m")
COMPILER = re.compile(
    r"MultipleCompilationErrorsException|An error occurred while trying to compile script class|\bcompilation failed\b",
    re.I,
)
SCRIPT_FAILURE = re.compile(
    r"^Script '[^'\r\n]+' ran into an issue while executing\.|"
    r"^An exception occurred while trying to run groovy code!|"
    r"^An unknown error occurred while running scripts|"
    r"^An exception occurred while running a closure at least once!"
)
RUNTIME_FAILURE = re.compile(
    r"There was a severe problem during mod loading that has caused the game to fail|"
    r"Exception in thread \"(?:main|Client thread)\"|"
    r"Minecraft has crashed!|This crash report has been saved to:|"
    r"the game will display an error screen and halt"
)
ENVIRONMENT_FAILURE = re.compile(
    r"Could not reserve enough space for|Could not create the Java Virtual Machine|"
    r"Unable to initialize main class|Error: Could not find or load main class|"
    r"No OpenGL context found|Failed to create (?:the )?(?:OpenGL|GLFW) window"
)
SOURCE = re.compile(
    r"(?<![\w/])(?P<path>file:/[^\r\n:]+\.groovy|/[^\r\n:]+\.groovy|groovy/[^\r\n:]+\.groovy):\s*(?P<line>\d+):"
)


def diagnostic_metadata(code, category, message, emitter, location, runtime_root):
    """Conservative identities: normalize only our disposable root, not values."""
    normalized = re.sub(re.escape(str(runtime_root)) + r"(?=/|$|[\s'\"\)\],:])", "$RUNTIME", message) if runtime_root else message
    family, subject = code, location["path"] if location else emitter or "unattributed"
    origin = {"kind": "unknown", "name": emitter or "unknown", "basis": "Reporter alone does not identify the responsible source or dependency."}
    guidance = "Inspect the retained event and its cause chain; no exemption or source ownership is inferred."
    if location:
        origin = {"kind": "pack-source", "name": location["path"], "basis": "Exact captured source path and compiler/runtime coordinates."}
        guidance = "Inspect the verified saved source location; newer or unsaved editor bytes are not this execution."
    elif "groovy/workbenchChecks/Observe.groovy" in normalized:
        origin = {"kind": "workbench", "name": "saved-check probe", "basis": "Reserved Workbench instrumentation path in the diagnostic."}
        guidance = "Inspect Workbench instrumentation; do not attribute this probe error to the developer's saved script."
    first = normalized.split("\n", 1)[0]
    material = re.fullmatch(r"Material with id (.+) is already registered\.", first)
    recipe = re.fullmatch(r"Parsing error loading recipe ([a-z0-9_.-]+):([a-z0-9_./-]+)", first)
    if emitter == "ForgeMicroBlockCBE" and material:
        family, subject = "microblock-material-registration", material[1]
        guidance = "Duplicate microblock material registration, not evidence of duplicate GregTech materials. Inspect microblock registrations/configuration; responsible input remains unproven."
    elif emitter == "FML" and recipe:
        family, subject = "recipe-json-loading", recipe[1] + ":" + recipe[2]
        guidance = f"Recipe JSON failed to load. Inspect assets/{recipe[1]}/recipes/{recipe[2]}.json and the retained exception. The namespace does not prove which artifact or pack override is responsible."
    elif category == "environment":
        origin = {"kind": "host-environment", "name": emitter or "JVM/graphics", "basis": "Recognized launch-environment failure; exact cause still requires its evidence."}
    key = sha256(json.dumps({"family": family, "subject": subject, "emitter": emitter, "message": normalized}, sort_keys=True).encode()).hexdigest()
    return {"key": "diagnostic:sha256:" + key, "family": family, "subject": subject,
            "origin": origin, "guidance": guidance}


def events(record):
    """One logger event plus its continuation lines; mirrored headers disappear."""
    current = None
    for number, raw in enumerate((record.get("text") or "").splitlines(), 1):
        line = ANSI.sub("", raw)
        header = HEADER.match(line)
        if header:
            if current:
                yield current
            current = {"line": number, "thread": header["thread"], "level": header["level"], "logger": header["logger"] or "", "lines": [header["message"]]}
        elif current is not None:
            current["lines"].append(line)
        else:
            # Launcher/JVM diagnostics may have no logger header.
            yield {"line": number, "thread": None, "level": "", "logger": "", "lines": [line]}
    if current:
        yield current


def classify(event):
    message = "\n".join(event["lines"])
    if COMPILER.search(message):
        return "groovy-compilation-failed", "compiler", "error", True, "The runtime compiler reported a compilation failure."
    if SCRIPT_FAILURE.search(message):
        return "groovy-execution-failed", "runtime", "error", True, "The Groovy loader reported a script execution failure."
    if ENVIRONMENT_FAILURE.search(message):
        return "runtime-environment-failed", "environment", "error", True, "The JVM or graphics environment reported a launch failure."
    if RUNTIME_FAILURE.search(message):
        return "client-startup-failed", "runtime", "error", True, "The client reported a terminal startup failure."
    if (
        event["logger"].casefold() == "mousetweaks"
        and event["level"] == "FATAL"
        and message.strip() == "Mouse Tweaks has been initialized."
    ):
        return "mousetweaks-initialized", "informational", "information", False, "This exact Mouse Tweaks initialization event uses FATAL for a successful initialization message; other messages are not exempt."
    if event["level"] in {"ERROR", "FATAL"} or (not event["level"] and re.search(r"(?:^|:\s+)(?:ERROR|FATAL|Error:)(?:\s|$)", message, re.I)):
        return "unclassified-runtime-error", "unclassified", "error", False, "Unclassified error: retained for review and prevents a completed check, but is not evidence of a compiler failure."
    return None


def locations(sources, lines, runtime_root):
    """Preserve reported coordinates; navigate exact lines/EOF, never a basename."""
    result = []
    for offset, line in enumerate(lines):
        for match in SOURCE.finditer(line):
            name = match["path"]
            if name.startswith("file:"):
                uri = urlsplit(name)
                if uri.netloc or uri.query or uri.fragment:
                    continue
                name = unquote(uri.path)
            path = PurePosixPath(name)
            if ".." in path.parts or "\\" in name:
                continue
            if path.is_absolute():
                if runtime_root is None:
                    continue
                try:
                    name = path.relative_to(PurePosixPath(runtime_root)).as_posix()
                except ValueError:
                    continue
            if name not in sources:
                continue
            raw = sources[name]
            number = int(match["line"])
            column_match = re.search(r"@ line \d+, column (\d+)", line[match.end():])
            reported = {"line": number, "column": int(column_match[1]) if column_match else None}
            rows = raw.split(b"\n")
            if not 1 <= number <= len(rows) + (not raw.endswith(b"\n")):
                continue
            if number > len(rows):
                start = end = len(raw)
                kind = "end-of-file-anchor"
            else:
                start = sum(len(row) + 1 for row in rows[:number - 1])
                end = start + len(rows[number - 1].removesuffix(b"\r"))
                kind = "reported-line" if end > start else "insertion-point"
            try:
                location = source_location(raw, name, start, end)
            except ValueError:
                continue
            result.append({"location": location, "reported_position": reported, "location_kind": kind, "line_offset": offset, "detail": line[match.end():].strip()})
    return result


def findings(inputs, logs, *, runtime_root):
    merged = {}
    for name, record in sorted(logs.items()):
        text = record.get("text") or ""
        if name.startswith("crash-reports/") and text.startswith("---- Minecraft Crash Report ----"):
            lines = text.splitlines()
            causes = [(i, line) for i, line in enumerate(lines, 1) if line.startswith("Caused by:") or re.match(r"[\w.$]+(?:Exception|Error)(?::|$)", line)]
            if "Description: Initializing game" in lines and causes:
                number, cause = causes[-1]
                merged[(name, "crash")] = {
                    "code": "client-startup-crash", "category": "runtime", "severity": "error", "blocking": True,
                    "reason": "A retained crash report confirms a client initialization failure.",
                    "message": "Client initialization crashed. " + cause[:3800],
                    "evidence": [{"log": name, "line": number}],
                    "location": None, "reported_position": None, "location_kind": None,
                    "diagnostic": diagnostic_metadata("client-startup-crash", "runtime", cause, "crash-report", None, runtime_root),
                }
                continue
            # A partial/unrecognized crash report can never silently become a pass.
            merged[(name, "unresolved-crash")] = {
                "code": "unclassified-crash-report", "category": "unclassified", "severity": "error", "blocking": False,
                "reason": "A crash report is present but its startup failure is not yet classified.",
                "message": "Inspect the retained crash report; startup cannot be accepted.",
                "evidence": [{"log": name, "line": 1}],
                "location": None, "reported_position": None, "location_kind": None,
                "diagnostic": diagnostic_metadata("unclassified-crash-report", "unclassified", text, "crash-report", None, runtime_root),
            }
            continue
        occurrences = defaultdict(int)
        entries = list(events(record))
        for index, event in enumerate(entries):
            if event.get("absorbed"):
                continue
            classification = classify(event)
            if classification is None:
                continue
            code, category, severity, blocking, reason = classification
            if code == "groovy-execution-failed" and index + 1 < len(entries):
                following = entries[index + 1]
                if following["thread"] == event["thread"] and following["lines"][0] == "Throwing":
                    # Latest.log splits the preface and stack into two events;
                    # groovy.log includes the stack in the preface itself.
                    event["lines"].extend(following["lines"])
                    event.setdefault("related_lines", []).append(following["line"])
                    following["absorbed"] = True
            message = "\n".join(event["lines"]).rstrip()
            anchors = locations(inputs.sources, event["lines"], runtime_root) if category in {"compiler", "runtime"} else []
            if category == "compiler" and not anchors and message.startswith("An error occurred while trying to compile script class") and index + 1 < len(entries):
                following = entries[index + 1]
                if following["thread"] == event["thread"] and COMPILER.search("\n".join(following["lines"])) and locations(inputs.sources, following["lines"], runtime_root):
                    # GroovyLog emits the compiler preface separately from its
                    # following "Throwing" event in latest.log. Preserve the
                    # preface as evidence of the same diagnostic, not another error.
                    following.setdefault("related_lines", []).append(event["line"])
                    continue
            for anchor in anchors or [None]:
                # Match the nth occurrence across streams; repeated identical
                # events in one stream remain distinct and reviewable.
                if category == "compiler" and anchor:
                    location = anchor["location"]
                    signature = (code, location["path"], location["sha256"], repr(anchor["reported_position"]), anchor["detail"])
                    displayed = f"{location['path']}:{anchor['reported_position']['line']}: {anchor['detail']}"
                elif code == "groovy-execution-failed":
                    cause = next((line.strip() for line in event["lines"] if re.match(r"\s*[\w.$]+(?:Exception|Error)(?::|$)", line)), "")
                    signature = (code, event["lines"][0], cause)
                    displayed = event["lines"][0] + ("\n" + cause if cause else "")
                else:
                    signature = (code, sha256(message.encode()).hexdigest(), repr(anchor))
                    displayed = message
                occurrence = occurrences[signature]
                occurrences[signature] += 1
                key = (*signature, occurrence)
                reference = {"log": name, "line": event["line"] + (anchor["line_offset"] if anchor else 0)}
                references = [reference, *({"log": name, "line": line} for line in event.get("related_lines", []))]
                if key in merged:
                    merged[key]["evidence"].extend(references)
                    continue
                merged[key] = {
                    "code": code, "category": category, "severity": severity,
                    "blocking": blocking, "reason": reason,
                    "message": displayed[:4000], "evidence": references,
                    "location": anchor["location"] if anchor else None,
                    "reported_position": anchor["reported_position"] if anchor else None,
                    "location_kind": anchor["location_kind"] if anchor else None,
                    "diagnostic": diagnostic_metadata(code, category, displayed, event["logger"], anchor["location"] if anchor else None, runtime_root),
                }
    return list(merged.values())
