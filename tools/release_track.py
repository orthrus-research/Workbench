"""Derive release metadata from the native version authorities."""
from __future__ import annotations
from pathlib import Path
from component_versions import ComponentVersionError, load_authority

PUBLIC_TRACK = "public-v1"
ReleaseTrackError = ComponentVersionError


def load_release_descriptor(root: Path):
    return load_authority(root)[0]


def component(root: Path, component_id: str):
    try:
        return load_authority(root)[1][component_id]
    except KeyError as exc:
        raise ReleaseTrackError(f"unknown native component: {component_id}") from exc


def artifact_filename(root: Path, artifact_id: str):
    for owner in load_authority(root)[1].values():
        for artifact in owner["artifacts"]:
            if artifact["id"] == artifact_id:
                return artifact["filename_template"].format(version=owner["version"])
    raise ReleaseTrackError(f"unknown native artifact: {artifact_id}")
