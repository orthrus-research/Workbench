"""Legacy name for Material Semantics policy validation failures.

Atlas owns graph-query errors independently. This alias preserves existing
Material Semantics callers that catch policy errors through the old name.
"""

from workbench_api.material_classification import (
    MaterialPolicyValidationError as RuntimeGraphQueryError,
)

__all__ = ["RuntimeGraphQueryError"]
