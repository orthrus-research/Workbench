"""Durable Crucible application-service mechanics for Workbench protocol V3.

The package facade preserves the public Service V3 API without importing the
service runtime when a caller selects a focused sibling port such as host
filesystem custody.
"""

from importlib import import_module


__all__ = [
    "ContextListHandler",
    "DurableJobStore",
    "JobCancellationHandler",
    "JobCancellationPlan",
    "JobCancellationPlanResolver",
    "JobEventPageHandler",
    "JobSubscriptionHandler",
    "ServiceCapabilitiesHandler",
    "load_job_cancellation_plan",
    "seal_job_cancellation_plan",
    "validate_job_cancellation_arguments",
    "validate_job_handle_result",
    "validate_job_event_page_arguments",
    "validate_job_event_page_result",
    "validate_job_subscription_arguments",
    "validate_job_subscription_result",
    "validate_context_list_result",
    "validate_empty_service_arguments",
    "validate_service_capabilities_result",
]


def __getattr__(name: str):
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(".service", __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
