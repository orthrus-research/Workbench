"""Pure Packwiz input contracts; installation remains an external tool operation."""

import hashlib
import json
from hashlib import sha256
from pathlib import PurePosixPath

import tomllib


def _path(value):
    if not isinstance(value, str) or not value or any(c in value for c in "\\:\0\r\n"):
        raise ValueError("Packwiz requires a portable relative path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or any(p in {".", "..", ".git"} for p in path.parts)
    ):
        raise ValueError("Packwiz requires a portable relative path")
    return path


def client_dependencies(files, *, source_roots):
    """Bind selected client artifacts independently of saved loose source bytes.

    This bounded contract admits Packwiz 1.1 metadata outside projected source
    roots. The installer, not this parser, implements acquisition and refresh.
    Explicit side/optional decisions are carried through final verification.
    """
    pack = tomllib.loads(files["pack.toml"].decode("utf-8"))
    if pack.get("pack-format") != "packwiz:1.1.0" or not isinstance(
        pack.get("versions"), dict
    ):
        raise ValueError(
            "client preparation requires Packwiz 1.1 and explicit versions"
        )
    index = pack.get("index", {})
    if not isinstance(index, dict):
        raise ValueError("Packwiz index must be a table")  # noqa: TRY004 - invalid TOML value
    _path(index.get("file"))
    if index.get("hash-format") not in {"sha256", "sha512"}:
        raise ValueError("Packwiz index requires a supported strong hash")
    artifacts, outputs = [], set()
    for name, raw in sorted(files.items()):
        if not name.endswith(".pw.toml"):
            continue
        path = _path(name)
        if path.parts[0] in source_roots:
            raise ValueError(
                "downloaded Packwiz artifacts cannot overlap projected source roots"
            )
        metadata = tomllib.loads(raw.decode("utf-8"))
        side = metadata.get("side", "both")
        if side not in {"client", "server", "both"}:
            raise ValueError("Packwiz metadata has an invalid side")
        option = metadata.get("option", {})
        if not isinstance(option, dict) or any(
            type(option.get(key, False)) is not bool for key in ("optional", "default")
        ):
            raise ValueError("Packwiz optional decisions must be boolean")
        optional = option.get("optional", False)
        enabled = side != "server" and (not optional or option.get("default", False))
        filename = metadata.get("filename")
        if not isinstance(filename, str) or _path(filename).name != filename:
            raise ValueError("Packwiz artifact filename must be a basename")
        output = (path.parent / filename).as_posix()
        if output.casefold() in outputs:
            raise ValueError("Packwiz artifact output paths collide")
        outputs.add(output.casefold())
        download = metadata.get("download", {})
        if not isinstance(download, dict):
            raise ValueError("Packwiz download must be a table")  # noqa: TRY004 - invalid TOML value
        algorithm, digest = download.get("hash-format"), download.get("hash")
        if (
            algorithm not in {"sha1", "sha256", "sha512", "md5"}
            or not isinstance(digest, str)
            or len(digest) != hashlib.new(algorithm).digest_size * 2
            or any(c not in "0123456789abcdef" for c in digest)
        ):
            raise ValueError(
                "Packwiz artifact requires an exact supported content hash"
            )
        artifacts.append(
            {
                "metadata_path": name,
                "output_path": output,
                "side": side,
                "optional": optional,
                "enabled": enabled,
                "hash_format": algorithm,
                "hash": digest,
                "metadata": metadata,
            }
        )
    indexed = tomllib.loads(files.get(index["file"], b"").decode("utf-8"))
    if not isinstance(indexed.get("files", []), list):
        raise ValueError("Packwiz index files must be an array")  # noqa: TRY004 - invalid TOML value
    indexed_paths = {
        row.get("file") for row in indexed.get("files", []) if isinstance(row, dict)
    }
    auxiliary = []
    for name, raw in sorted(files.items()):
        if (
            name in {"pack.toml", index["file"], ".packwizignore"}
            or name.endswith(".pw.toml")
            or _path(name).parts[0] in source_roots
        ):
            continue
        # Only demonstrably unindexed documentation is ignored. Unknown loose
        # inputs remain conservative dependencies until a profile models them.
        if name.endswith(".md") and name not in indexed_paths:
            continue
        auxiliary.append({"path": name, "sha256": sha256(raw).hexdigest()})
    # Index content is a generated projection of saved files. Retain index
    # location/algorithm, never a stale source-dependent index hash as a tool lock.
    pack["index"] = {key: value for key, value in index.items() if key != "hash"}
    body = {
        "format": "workbench-packwiz-client-dependencies-v1",
        "pack": pack,
        "ignore_sha256": sha256(files.get(".packwizignore", b"")).hexdigest(),
        "artifacts": artifacts,
        "auxiliary": auxiliary,
    }
    return {
        **body,
        "id": "packwiz-dependencies:sha256:"
        + sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }
