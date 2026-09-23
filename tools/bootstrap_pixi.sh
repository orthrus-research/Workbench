#!/usr/bin/env bash

# Bootstrap the exact Pixi required by this checkout without installing it
# globally or editing shell startup files.

set -euo pipefail

PIXI_VERSION="0.75.0"
PIXI_RELEASE_BASE="https://github.com/prefix-dev/pixi/releases/download/v${PIXI_VERSION}"
MODE="install"

usage() {
  cat <<'EOF'
Usage: bash tools/bootstrap_pixi.sh [--pixi-only | --print-plan]

Without an option, download and verify the exact Pixi required by Workbench,
then verify the locked source development environment. --pixi-only stops after provisioning the
private Pixi executable. --print-plan performs no download or mutation.

The private executable is retained below .workbench/bootstrap/pixi unless
WORKBENCH_PIXI_BOOTSTRAP_ROOT names another cache root.
EOF
}

fail() {
  printf 'Workbench Pixi bootstrap failed: %s\n' "$1" >&2
  exit 2
}

if (( $# > 1 )); then
  usage >&2
  exit 2
fi
if (( $# == 1 )); then
  case "$1" in
    --pixi-only) MODE="pixi-only" ;;
    --print-plan) MODE="print-plan" ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
fi

SCRIPT_DIRECTORY="$({ CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P; })"
REPOSITORY_ROOT="$({ CDPATH= cd -- "${SCRIPT_DIRECTORY}/.." && pwd -P; })"

case "$(uname -s):$(uname -m)" in
  Linux:x86_64|Linux:amd64)
    TARGET="linux-x86-64"
    ASSET="pixi-x86_64-unknown-linux-musl.tar.gz"
    ARCHIVE_SHA256="bcd825d62905c29b3c754b71f9cdc9d6f119454398f58330f111a7b6a0de0a3f"
    EXECUTABLE_SHA256="4383aed18b2d5569cf34a19638daf954aa4415cc87ad3a9da9f34059cc4a004c"
    ;;
  Linux:aarch64|Linux:arm64)
    TARGET="linux-arm64"
    ASSET="pixi-aarch64-unknown-linux-musl.tar.gz"
    ARCHIVE_SHA256="6476588859faa7232def49ff590f199390bd93fae3826617596befc52382724f"
    EXECUTABLE_SHA256="ea8f527789260a91b5b9afe96e226b3769512c7a5ed81a095f3d6e7cbdd95d2c"
    ;;
  Darwin:x86_64|Darwin:amd64)
    TARGET="macos-x86-64"
    ASSET="pixi-x86_64-apple-darwin.tar.gz"
    ARCHIVE_SHA256="f129e890366ad5502304c8f863cc5585d82143b64731f36bcd1283a27781097e"
    EXECUTABLE_SHA256="7f475ca1d41ac8cf392e125f5e39a02d910761c253a6e925b331decec09e8056"
    ;;
  Darwin:aarch64|Darwin:arm64)
    TARGET="macos-arm64"
    ASSET="pixi-aarch64-apple-darwin.tar.gz"
    ARCHIVE_SHA256="52a43f9268f3accb7155cf229937f2f5333559b1333615d610362c6151fded66"
    EXECUTABLE_SHA256="4c1683c64ef1ed2b36b210310bd0dcff4460b3fb74b59ddca3407c2943d4de15"
    ;;
  *)
    fail "unsupported host identity: $(uname -s) $(uname -m)"
    ;;
esac

ASSET_URL="${PIXI_RELEASE_BASE}/${ASSET}"
SOURCE_INSTALLER_AVAILABLE="true"

print_plan() {
  printf '%s\n' \
    "format=workbench-pixi-bootstrap-plan-v1" \
    "pixi_version=${PIXI_VERSION}" \
    "target=${TARGET}" \
    "asset=${ASSET}" \
    "url=${ASSET_URL}" \
    "archive_sha256=${ARCHIVE_SHA256}" \
    "executable_sha256=${EXECUTABLE_SHA256}" \
    "source_installer_available=${SOURCE_INSTALLER_AVAILABLE}" \
    "native_host_qualified=false" \
    "release_qualified=false" \
    "support_claimed=false"
}

if [[ "$MODE" == "print-plan" ]]; then
  print_plan
  exit 0
fi

if [[ -e "${REPOSITORY_ROOT}/.pixi/config.toml" || -L "${REPOSITORY_ROOT}/.pixi/config.toml" ]]; then
  fail "remove the project-local .pixi/config.toml before an exact source install"
fi

BOOTSTRAP_ROOT="${WORKBENCH_PIXI_BOOTSTRAP_ROOT:-${REPOSITORY_ROOT}/.workbench/bootstrap/pixi}"
VERSION_ROOT="${BOOTSTRAP_ROOT}/${PIXI_VERSION}/${TARGET}"
DOWNLOAD_ROOT="${BOOTSTRAP_ROOT}/${PIXI_VERSION}/downloads"
PIXI_PATH="${VERSION_ROOT}/pixi"
ARCHIVE_PATH="${DOWNLOAD_ROOT}/${ASSET}"

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print tolower($1)}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print tolower($1)}'
  else
    fail "sha256sum or shasum is required to verify Pixi"
  fi
}

verified_pixi() {
  [[ -f "$PIXI_PATH" && ! -L "$PIXI_PATH" ]] || return 1
  [[ "$(sha256_file "$PIXI_PATH")" == "$EXECUTABLE_SHA256" ]] || return 1
  [[ "$($PIXI_PATH --version 2>/dev/null)" == "pixi ${PIXI_VERSION}" ]] || return 1
}

download_archive() {
  local destination="$1"
  if command -v curl >/dev/null 2>&1; then
    curl --fail --location --proto '=https' --tlsv1.2 \
      --output "$destination" "$ASSET_URL"
  elif command -v wget >/dev/null 2>&1; then
    wget --https-only --output-document="$destination" "$ASSET_URL"
  else
    fail "curl or wget is required to download the pinned Pixi archive"
  fi
}

if ! verified_pixi; then
  TEMPORARY_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/workbench-pixi-bootstrap.XXXXXX")"
  trap 'rm -rf -- "$TEMPORARY_ROOT"' EXIT
  TEMPORARY_ARCHIVE="${TEMPORARY_ROOT}/${ASSET}"
  TEMPORARY_EXTRACT="${TEMPORARY_ROOT}/extract"
  mkdir -p -- "$TEMPORARY_EXTRACT"

  if [[ -f "$ARCHIVE_PATH" && ! -L "$ARCHIVE_PATH" ]] \
      && [[ "$(sha256_file "$ARCHIVE_PATH")" == "$ARCHIVE_SHA256" ]]; then
    cp -- "$ARCHIVE_PATH" "$TEMPORARY_ARCHIVE"
  else
    # --pixi-only is a scripting contract: stdout contains exactly the
    # retained executable path.  Human progress always belongs on stderr.
    printf 'Downloading Pixi %s for %s...\n' "$PIXI_VERSION" "$TARGET" >&2
    download_archive "$TEMPORARY_ARCHIVE"
  fi
  [[ "$(sha256_file "$TEMPORARY_ARCHIVE")" == "$ARCHIVE_SHA256" ]] \
    || fail "downloaded Pixi archive checksum does not match the checked-in authority"

  tar -xzf "$TEMPORARY_ARCHIVE" -C "$TEMPORARY_EXTRACT"
  TEMPORARY_PIXI="${TEMPORARY_EXTRACT}/pixi"
  [[ -f "$TEMPORARY_PIXI" && ! -L "$TEMPORARY_PIXI" ]] \
    || fail "the verified Pixi archive does not contain one ordinary pixi executable"
  [[ "$(sha256_file "$TEMPORARY_PIXI")" == "$EXECUTABLE_SHA256" ]] \
    || fail "extracted Pixi executable checksum does not match the checked-in authority"
  chmod 0755 "$TEMPORARY_PIXI"
  [[ "$($TEMPORARY_PIXI --version 2>/dev/null)" == "pixi ${PIXI_VERSION}" ]] \
    || fail "the verified Pixi executable reports an unexpected version"

  mkdir -p -- "$VERSION_ROOT" "$DOWNLOAD_ROOT"
  cp -- "$TEMPORARY_ARCHIVE" "${ARCHIVE_PATH}.new"
  mv -f -- "${ARCHIVE_PATH}.new" "$ARCHIVE_PATH"
  cp -- "$TEMPORARY_PIXI" "${PIXI_PATH}.new"
  chmod 0755 "${PIXI_PATH}.new"
  mv -f -- "${PIXI_PATH}.new" "$PIXI_PATH"
  verified_pixi || fail "retained Pixi executable failed post-install verification"
fi

if [[ "$MODE" == "pixi-only" ]]; then
  printf '%s\n' "$PIXI_PATH"
  exit 0
fi

printf 'Using verified Pixi %s for %s.\n' "$PIXI_VERSION" "$TARGET"
cd -- "$REPOSITORY_ROOT"
exec "$PIXI_PATH" run --locked --no-config setup-core
