"""The independently installable Workbench module API."""

from .modules import API_VERSION, Capability, EnvironmentSelection, ExecutionContext, Module, ModuleError
from .durable_resources import DurableResourceError, DurableResources, ResourceReference

__all__ = [
    "API_VERSION", "Capability", "EnvironmentSelection", "ExecutionContext", "Module", "ModuleError",
    "DurableResourceError", "DurableResources", "ResourceReference",
]
