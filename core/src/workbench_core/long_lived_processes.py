"""Conservative host boundary for restartable client and staged-tool custody.

The current Core runners can close a process group or a Windows Job Object in
one live supervisor. Neither supplies a sealed, restartable containment scope
for a Prism launcher or SUSY materialization tool and descendants after the
supervisor crashes. No lease is issued until a host broker can prove that
stronger contract.
"""

from __future__ import annotations

import os
import platform

from workbench_api.long_lived_processes import (
    LongLivedProcessError,
    LongLivedProcessRequest,
    StagedToolProcessRequest,
)


class CoreLongLivedProcessHost:
    def reserve(self, request: LongLivedProcessRequest):
        if request.host_os == "windows" and os.name != "nt":
            reason = "Windows-host client from WSL requires a Windows-side process broker"
        elif request.host_os == "windows":
            reason = "native Windows client requires a restartable Job Object broker"
        elif platform.system() == "Linux":
            reason = "Linux client requires a sealed restartable containment scope"
        else:
            reason = "this host has no restartable long-lived process scope"
        raise LongLivedProcessError(reason)

    def reserve_staged_tool(self, request: StagedToolProcessRequest):
        if platform.system() == "Linux":
            reason = "Linux staged tool requires a sealed restartable containment scope"
        else:
            reason = "this host has no restartable staged tool process scope"
        raise LongLivedProcessError(reason)


HOST = CoreLongLivedProcessHost()
