[CmdletBinding()]
param(
    [switch]$PixiOnly,
    [switch]$PrintPlan
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$PixiVersion = "0.75.0"
$PixiReleaseBase = "https://github.com/prefix-dev/pixi/releases/download/v$PixiVersion"
$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

if (-not [System.Runtime.InteropServices.RuntimeInformation]::IsOSPlatform(
        [System.Runtime.InteropServices.OSPlatform]::Windows
    )) {
    throw "Workbench Pixi bootstrap supports this script only on Windows."
}

$Architecture = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString()
switch ($Architecture) {
    "X64" {
        $Target = "windows-x86-64"
        $Asset = "pixi-x86_64-pc-windows-msvc.zip"
        $ArchiveSha256 = "0c478f9efcb0f8ba984b21c3fa9f484a3d098fc9a46be896f0ac260939ddeaa9"
        $ExecutableSha256 = "63ec8945eed67b74ead220d1ece1167ac39166ef4f804ee46c37daf7105ec232"
        $SourceInstallerAvailable = $true
    }
    "Arm64" {
        $Target = "windows-arm64"
        $Asset = "pixi-aarch64-pc-windows-msvc.zip"
        $ArchiveSha256 = "826d9c588092351d5b15d485fca02cb7df984dbee647bb8a62e1b93c58cdf15d"
        $ExecutableSha256 = "7d42befbf1afd088e2f99f2c1be8fe343918e5ec514f36503ae929451ff9bdfc"
        $SourceInstallerAvailable = $false
    }
    default {
        throw "Workbench Pixi bootstrap does not recognize Windows architecture $Architecture."
    }
}

$AssetUrl = "$PixiReleaseBase/$Asset"

if ($PrintPlan) {
    @(
        "format=workbench-pixi-bootstrap-plan-v1"
        "pixi_version=$PixiVersion"
        "target=$Target"
        "asset=$Asset"
        "url=$AssetUrl"
        "archive_sha256=$ArchiveSha256"
        "executable_sha256=$ExecutableSha256"
        "source_installer_available=$($SourceInstallerAvailable.ToString().ToLowerInvariant())"
        "native_host_qualified=false"
        "release_qualified=false"
        "support_claimed=false"
    ) | Write-Output
    exit 0
}

if (-not $SourceInstallerAvailable -and -not $PixiOnly) {
    throw (
        "Windows ARM64 can bootstrap the exact Pixi with -PixiOnly, but the " +
        "Workbench source installer is not admitted on this row. Use the " +
        "native wheel installation route with a supported Python instead."
    )
}

$ProjectPixiConfig = Join-Path $RepositoryRoot ".pixi\config.toml"
$ProjectPixiConfigItem = Get-Item -LiteralPath $ProjectPixiConfig -Force -ErrorAction SilentlyContinue
if ($null -ne $ProjectPixiConfigItem) {
    throw "Remove the project-local .pixi\config.toml before an exact source install."
}

$BootstrapRoot = if ($env:WORKBENCH_PIXI_BOOTSTRAP_ROOT) {
    $env:WORKBENCH_PIXI_BOOTSTRAP_ROOT
} else {
    Join-Path $RepositoryRoot ".workbench\bootstrap\pixi"
}
$VersionRoot = Join-Path $BootstrapRoot "$PixiVersion\$Target"
$DownloadRoot = Join-Path $BootstrapRoot "$PixiVersion\downloads"
$PixiPath = Join-Path $VersionRoot "pixi.exe"
$ArchivePath = Join-Path $DownloadRoot $Asset

function Get-LowerSha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Test-VerifiedPixi {
    if (-not [IO.File]::Exists($PixiPath)) {
        return $false
    }
    if ((Get-LowerSha256 $PixiPath) -cne $ExecutableSha256) {
        return $false
    }
    $VersionOutput = & $PixiPath --version 2>$null
    return $LASTEXITCODE -eq 0 -and $VersionOutput -ceq "pixi $PixiVersion"
}

if (-not (Test-VerifiedPixi)) {
    $TemporaryRoot = Join-Path (
        [IO.Path]::GetTempPath()
    ) ("workbench-pixi-bootstrap-" + [Guid]::NewGuid().ToString("N"))
    $TemporaryArchive = Join-Path $TemporaryRoot $Asset
    $TemporaryExtract = Join-Path $TemporaryRoot "extract"
    New-Item -ItemType Directory -Path $TemporaryExtract -Force | Out-Null
    try {
        if (
            [IO.File]::Exists($ArchivePath) -and
            (Get-LowerSha256 $ArchivePath) -ceq $ArchiveSha256
        ) {
            Copy-Item -LiteralPath $ArchivePath -Destination $TemporaryArchive
        } else {
            Write-Host "Downloading Pixi $PixiVersion for $Target..."
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -UseBasicParsing -Uri $AssetUrl -OutFile $TemporaryArchive
        }
        if ((Get-LowerSha256 $TemporaryArchive) -cne $ArchiveSha256) {
            throw "Downloaded Pixi archive checksum does not match the checked-in authority."
        }
        Expand-Archive -LiteralPath $TemporaryArchive -DestinationPath $TemporaryExtract
        $TemporaryPixi = Join-Path $TemporaryExtract "pixi.exe"
        if (-not [IO.File]::Exists($TemporaryPixi)) {
            throw "The verified Pixi archive does not contain pixi.exe."
        }
        if ((Get-LowerSha256 $TemporaryPixi) -cne $ExecutableSha256) {
            throw "Extracted Pixi executable checksum does not match the checked-in authority."
        }
        $VersionOutput = & $TemporaryPixi --version 2>$null
        if ($LASTEXITCODE -ne 0 -or $VersionOutput -cne "pixi $PixiVersion") {
            throw "The verified Pixi executable reports an unexpected version."
        }

        New-Item -ItemType Directory -Path $VersionRoot -Force | Out-Null
        New-Item -ItemType Directory -Path $DownloadRoot -Force | Out-Null
        Copy-Item -LiteralPath $TemporaryArchive -Destination "$ArchivePath.new" -Force
        Move-Item -LiteralPath "$ArchivePath.new" -Destination $ArchivePath -Force
        Copy-Item -LiteralPath $TemporaryPixi -Destination "$PixiPath.new" -Force
        Move-Item -LiteralPath "$PixiPath.new" -Destination $PixiPath -Force
        if (-not (Test-VerifiedPixi)) {
            throw "Retained Pixi executable failed post-install verification."
        }
    } finally {
        if (Test-Path -LiteralPath $TemporaryRoot) {
            Remove-Item -LiteralPath $TemporaryRoot -Recurse -Force
        }
    }
}

if ($PixiOnly) {
    Write-Output $PixiPath
    exit 0
}

Write-Host "Using verified Pixi $PixiVersion for $Target."
Push-Location $RepositoryRoot
try {
    & $PixiPath run --locked --no-config setup-core
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
