"""Bounded Prism LauncherPart wire contract, without processes or disk writes."""

SINGLE_FIELDS = frozenset(
    {
        "mainClass",
        "launcher",
        "windowTitle",
        "windowParams",
        "launcherBrand",
        "launcherVersion",
        "instanceName",
        "instanceIconKey",
        "instanceIconPath",
        "userName",
        "sessionId",
    }
)
REPEATED_FIELDS = frozenset({"param", "traits"})
MAX_PROTOCOL_BYTES = 128 * 1024


def parse_launch_script(raw):
    """Decode a complete launch instruction; never include input in errors."""
    if not isinstance(raw, bytes) or len(raw) > MAX_PROTOCOL_BYTES:
        raise ValueError("Prism launch protocol exceeds its bound")
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise ValueError("Prism launch protocol is not UTF-8") from exc
    lines = text.splitlines(keepends=True)
    if not lines or lines[-1] != "launch\n" or len(lines) > 1024:
        raise ValueError("Prism launch protocol is incomplete or unsupported")
    result = {}
    for line in lines[:-1]:
        if (
            not line.endswith("\n")
            or any(ord(c) < 32 and c != "\n" for c in line)
            or len(line) > 16384
        ):
            raise ValueError("Prism launch protocol contains an invalid field")
        if line == "\n":
            continue
        key, separator, value = line[:-1].partition(" ")
        if not separator or key not in SINGLE_FIELDS | REPEATED_FIELDS or not value:
            raise ValueError("Prism launch protocol contains an unsupported field")
        if key in SINGLE_FIELDS:
            if key in result:
                raise ValueError("Prism launch protocol repeats a singleton field")
            result[key] = value
        else:
            result.setdefault(key, []).append(value)
    if result.get("launcher") != "standard" or not result.get("mainClass"):
        raise ValueError(
            "Prism launch protocol requires an explicit standard main class"
        )
    return result


def encode_launch_script(fields):
    if not isinstance(fields, dict):
        raise ValueError("Prism launch fields must be a mapping")
    rows = []
    for key, value in fields.items():
        for item in value if isinstance(value, list) else [value]:
            if (
                not isinstance(key, str)
                or not isinstance(item, str)
                or any(ord(c) < 32 for c in item)
            ):
                raise ValueError(
                    "Prism launch fields must contain bounded single-line text"
                )
            rows.append(key + " " + item + "\n")
    raw = ("".join(rows) + "launch\n").encode()
    parse_launch_script(raw)
    return raw
