"""Supersymmetry server log and shutdown interpretation."""

import re
from typing import Any

SERVER_READY_RE = re.compile(
    r"^\[[^\]\r\n]+\] \[Server thread/INFO\] "
    r"\[(?:minecraft/DedicatedServer|net\.minecraft\.server\.dedicated\.DedicatedServer)\]"
    r": Done \([^\r\n]+\)! For help, type "
    r'"help" or "\?"$',
    re.MULTILINE,
)

FML_LOADED_TEXT = "Forge Mod Loader has successfully loaded"

SUSY_PACK_READY_RE = re.compile(
    r"^\[[^\]\r\n]+\] \[Server thread/INFO\] \[FTB Library\]: "
    r"Reloaded server(?: in [^\r\n]+)?$",
    re.MULTILINE,
)

GROOVY_SCRIPT_FAILURE_TEXT = "An exception occurred while running scripts."

TERMINAL_RE = re.compile(
    r"MixinTargetAlreadyLoadedException"
    r"|\[main/FATAL\] \[Foundation\]: Unable to launch"
    r"|\[Server thread/(?:ERROR|FATAL)\] "
    r"\[(?:minecraft/MinecraftServer|net\.minecraft\.server\.MinecraftServer)\]"
    r": Encountered an unexpected exception"
    r"|\[Server thread/ERROR\] "
    r"\[(?:minecraft/MinecraftServer|net\.minecraft\.server\.MinecraftServer)\]"
    r": Exception stopping the server"
    r"|Exception in thread \"main\""
    r"|A fatal error has been detected by the Java Runtime",
    re.IGNORECASE,
)

LOG_ENTRY_RE = re.compile(
    r"^\[[^\]\r\n]+\] \[(?P<thread>[^\]/\r\n]+)/(?P<level>INFO|WARN|ERROR|FATAL)\] "
    r"\[(?P<logger>[^\]\r\n]+)\]: (?P<message>[^\r\n]*)$",
    re.MULTILINE,
)

SHUTDOWN_ACKNOWLEDGMENTS = {
    "dedicated-server-stopping": re.compile(
        r"^\[[^\]\r\n]+\] \[Server thread/INFO\] "
        r"\[(?:minecraft/DedicatedServer|net\.minecraft\.server\.dedicated\.DedicatedServer)\]"
        r": Stopping the server$",
        re.MULTILINE,
    ),
    "minecraft-server-stopping": re.compile(
        r"^\[[^\]\r\n]+\] \[Server thread/INFO\] "
        r"\[(?:minecraft/MinecraftServer|net\.minecraft\.server\.MinecraftServer)\]"
        r": Stopping server$",
        re.MULTILINE,
    ),
    "saving-players": re.compile(
        r"^\[[^\]\r\n]+\] \[Server thread/INFO\] "
        r"\[(?:minecraft/MinecraftServer|net\.minecraft\.server\.MinecraftServer)\]"
        r": Saving players$",
        re.MULTILINE,
    ),
    "saving-worlds": re.compile(
        r"^\[[^\]\r\n]+\] \[Server thread/INFO\] "
        r"\[(?:minecraft/MinecraftServer|net\.minecraft\.server\.MinecraftServer)\]"
        r": Saving worlds$",
        re.MULTILINE,
    ),
}


def _shutdown_acknowledgment(text: str) -> dict[str, Any]:
    markers: dict[str, str | None] = {}
    for marker_id, pattern in SHUTDOWN_ACKNOWLEDGMENTS.items():
        match = pattern.search(text)
        markers[marker_id] = None if match is None else match.group(0)
    return {
        "complete": all(value is not None for value in markers.values()),
        "markers": markers,
    }
