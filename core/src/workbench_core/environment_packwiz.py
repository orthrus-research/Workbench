"""Verified Packwiz staging and payload boundaries for Core preparation."""

import hashlib
from pathlib import Path
from urllib.parse import urlparse

import tomllib

from . import check_storage as storage


def validate_downloads(dependencies):
    for row in dependencies["artifacts"]:
        download = row["metadata"]["download"]
        url = download.get("url")
        if url is not None:
            parsed = urlparse(url)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.fragment
            ):
                raise ValueError("Packwiz download URLs require credential-free HTTPS")
        elif download.get("mode") != "metadata:curseforge":
            raise ValueError("Packwiz artifact has no supported acquisition mode")


def refreshed_index(stage, dependencies):
    """Validate every refreshed entry before the installer can consume it."""
    pack_raw = storage.ordinary(stage / "pack.toml").read_bytes()
    pack = tomllib.loads(pack_raw.decode())
    index = pack["index"]
    normalized = {**pack, "index": {k: v for k, v in index.items() if k != "hash"}}
    if normalized != dependencies["pack"]:
        raise ValueError("Packwiz refresh changed dependency metadata")
    index_raw = storage.ordinary(stage / storage.safe_path(index["file"])).read_bytes()
    if hashlib.new(index["hash-format"], index_raw).hexdigest() != index["hash"]:
        raise ValueError("refreshed Packwiz index hash differs")
    value = tomllib.loads(index_raw.decode())
    if value.get("hash-format") not in {"sha256", "sha512"}:
        raise ValueError("refreshed Packwiz index requires strong file hashes")
    entries = value.get("files", [])
    if not isinstance(entries, list) or len(entries) > 100_000:
        raise ValueError("refreshed Packwiz index exceeds its bound")
    seen, metadata = set(), set()
    artifact_outputs = {
        row["output_path"].casefold() for row in dependencies["artifacts"]
    }
    for row in entries:
        name = str(storage.safe_path(row["file"]))
        if name.casefold() in seen or row.get("preserve", False):
            raise ValueError(
                "fresh preparation rejects duplicate or preserve-mode index entries"
            )
        seen.add(name.casefold())
        if not row.get("metafile", False) and name.casefold() in artifact_outputs:
            raise ValueError("Packwiz loose input collides with a downloaded artifact")
        raw = storage.ordinary(stage / name).read_bytes()
        if hashlib.new(value["hash-format"], raw).hexdigest() != row["hash"]:
            raise ValueError("refreshed Packwiz entry differs from staged source")
        if row.get("metafile") is True:
            metadata.add(name)
    if metadata != {row["metadata_path"] for row in dependencies["artifacts"]}:
        raise ValueError("Packwiz refresh excluded or introduced dependency metadata")
    return {
        "pack_sha256": hashlib.sha256(pack_raw).hexdigest(),
        "index": index,
        "hash_format": value["hash-format"],
        "entries": entries,
    }


def initialize_payload(payload, dependencies, seeds):
    """Apply declared defaults and copy only matching hash-locked seed artifacts."""
    payload.mkdir(mode=0o700)
    state = {
        "cachedSide": "client",
        "cachedFiles": {
            row["metadata_path"]: {"isOptional": True, "optionValue": row["enabled"]}
            for row in dependencies["artifacts"]
            if row["optional"] and row["side"] != "server"
        },
    }
    storage.write_json(payload / "packwiz.json", state)
    seeded = []
    for row in dependencies["artifacts"]:
        if not row["enabled"]:
            continue
        relative = storage.safe_path(row["output_path"])
        for root in seeds:
            root = storage.ordinary(Path(root), directory=True)
            matches = [root / relative, root / relative.name]
            for source in matches:
                if not source.exists() and not source.is_symlink():
                    continue
                source = storage.ordinary(source)
                if source.stat().st_size > 2 * 1024**3:
                    raise ValueError("seed artifact exceeds its bound")
                with source.open("rb") as stream:
                    digest = hashlib.file_digest(stream, row["hash_format"]).hexdigest()
                if digest != row["hash"]:
                    continue
                target = payload / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open("rb") as stream, target.open("xb") as output:
                    while chunk := stream.read(1024**2):
                        output.write(chunk)
                seeded.append(row["output_path"])
                break
            if row["output_path"] in seeded:
                break
    return seeded


def verify_payload(payload, stage, dependencies, refreshed):
    state = storage.read_json(payload / "packwiz.json")
    index = refreshed["index"]
    if (
        state.get("cachedSide") != "client"
        or state.get("packFileHash")
        != {"type": "sha256", "value": refreshed["pack_sha256"]}
        or state.get("indexFileHash")
        != {"type": index["hash-format"], "value": index["hash"]}
    ):
        raise ValueError("Packwiz final state differs from the refreshed input")
    expected = {"packwiz.json"}
    for row in dependencies["artifacts"]:
        target = payload / storage.safe_path(row["output_path"])
        if not row["enabled"]:
            if target.exists() or target.is_symlink():
                raise ValueError("disabled or server-only artifact was installed")
            continue
        target = storage.ordinary(target)
        with target.open("rb") as stream:
            if (
                hashlib.file_digest(stream, row["hash_format"]).hexdigest()
                != row["hash"]
            ):
                raise ValueError(
                    "installed Packwiz artifact differs from its content lock"
                )
        expected.add(row["output_path"])
    for row in refreshed["entries"]:
        if row.get("metafile"):
            continue
        name = str(storage.safe_path(row["file"]))
        if (
            storage.ordinary(payload / name).read_bytes()
            != storage.ordinary(stage / name).read_bytes()
        ):
            raise ValueError("installed loose file differs from the saved candidate")
        expected.add(name)
    for row in dependencies["artifacts"]:
        if row["optional"] and row["side"] != "server":
            cached = state.get("cachedFiles", {}).get(row["metadata_path"], {})
            if (
                cached.get("isOptional") is not True
                or cached.get("optionValue") is not row["enabled"]
            ):
                raise ValueError("installer changed the declared optional decision")
    manifest = storage.tree_manifest(payload)
    if {row["path"] for row in manifest} != expected:
        raise ValueError("Packwiz installed undeclared payload files")
    return manifest
