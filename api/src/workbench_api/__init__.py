"""The independently installable Workbench module API."""

from .modules import API_VERSION, Capability, ExecutionContext, Module, ModuleError

__all__ = ["API_VERSION", "Capability", "ExecutionContext", "Module", "ModuleError"]
