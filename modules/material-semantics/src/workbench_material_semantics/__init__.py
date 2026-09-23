"""Versioned material semantics shared by source and runtime projections."""

from .policy import (
    ConditionalFlagRule,
    FlagDefinition,
    FlagPresetDefinition,
    MaterialSemanticLayer,
    MaterialSymbolDefinition,
    MaterialSemanticsError,
    MaterialSemanticsPolicy,
    SemanticConstant,
    SemanticSourceFile,
    ValueCondition,
)

__all__ = [
    "ConditionalFlagRule",
    "FlagDefinition",
    "FlagPresetDefinition",
    "MaterialSemanticLayer",
    "MaterialSymbolDefinition",
    "MaterialSemanticsError",
    "MaterialSemanticsPolicy",
    "SemanticConstant",
    "SemanticSourceFile",
    "ValueCondition",
]
