"""GroovyScript-first Pack Program Studio."""

from .analyzer import AnalysisContext, analyze_program, assess_change, compare_programs
from .language_model import validate_language_result
from .language_profile import LoadedLanguageProfile, load_language_profile
from .language_service import build_language_service_result
from .ide_bridge import load_live_session_descriptor, proxy_descriptor_stdio
from .managed_model import (
    validate_managed_session_receipt,
    validate_session_descriptor,
)
from .managed_profile import (
    LoadedManagedSessionProfile,
    load_managed_session_profile,
)
from .managed_session import run_managed_language_session
from .model import PackProgramError, validate_report
from .profile import LoadedProfile, load_profile
from .declarations import (
    DECLARATION_FORMAT,
    build_source_declarations,
    declarations_from_program,
    source_declaration,
    validate_source_declarations,
)
from .studio import build_report


__all__ = [
    "AnalysisContext",
    "DECLARATION_FORMAT",
    "LoadedLanguageProfile",
    "LoadedManagedSessionProfile",
    "LoadedProfile",
    "PackProgramError",
    "analyze_program",
    "assess_change",
    "build_language_service_result",
    "build_report",
    "build_source_declarations",
    "compare_programs",
    "declarations_from_program",
    "load_language_profile",
    "load_live_session_descriptor",
    "load_managed_session_profile",
    "load_profile",
    "proxy_descriptor_stdio",
    "run_managed_language_session",
    "source_declaration",
    "validate_language_result",
    "validate_managed_session_receipt",
    "validate_report",
    "validate_session_descriptor",
    "validate_source_declarations",
]
