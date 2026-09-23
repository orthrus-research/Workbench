"""Host spelling and process requirements for profile-selected Axiom Java.

Core owns acquiring/selecting Java and enforcing process isolation. This module
only describes the domain invocation through the existing process port.
"""

import os
from pathlib import Path


def java_tool(home, name='java'):
    if name not in ('java', 'javac'):
        raise ValueError('Unknown Java tool')
    return Path(home) / 'bin' / (name + ('.exe' if os.name == 'nt' else ''))


def vm_arguments():
    # Windows managed Java may remove default CDS images for Unicode-path
    # portability. Always execute the pinned module image on this host.
    return ['-Xshare:off'] if os.name == 'nt' else []


def process_isolation(java, inputs, *, source_evaluation=True):
    if os.name != 'nt':
        return {}  # The engine's existing Linux namespace/seccomp supervisor.
    if not source_evaluation:
        return {}  # Coverage reads only the installed engine and pinned JVM.
    # Java 25 calls Windows canonical-path resolution while initializing its
    # security properties. AppContainer currently denies that operation even
    # for readable, explicitly granted files. Refuse source evaluation until
    # Core can qualify an isolation route on this host.
    raise ValueError('Windows Axiom source evaluation is unavailable: isolated Java 25 canonical-path resolution is not qualified')
