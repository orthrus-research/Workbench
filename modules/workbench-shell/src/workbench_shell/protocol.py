"""Stateful JSON-RPC dispatch for the Workbench client protocol V2."""

from __future__ import annotations

from workbench_api.resources import module_root as _module_resource_root, repository_root as _repository_resource_root

from collections.abc import Callable
from hashlib import sha256
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import url2pathname

import workbench_project_intelligence
from workbench_project_intelligence import ProjectInspectionError

from .bootstrap import (
    REGISTRY_PATH,
    discover_suite_root,
    inspect_project,
)
from .active_instance import (
    ActiveInstanceError,
    initialize_active_instance,
    load_active_instance,
)
from .component_graph import (
    ComponentGraphError,
    load_component_graph,
)
from workbench_core.configuration import (
    CONFIGURATION_PATH,
    WorkbenchConfiguration,
    WorkbenchConfigurationError,
    load_workbench_configuration,
)
from .runtime_diagnose import (
    RuntimeDiagnosisError,
    diagnose_project_runtime,
)
from .runtime_plan import RuntimePlanError, plan_project_runtime
from .registration_wizard import (
    RegistrationWizardError,
    apply_active_registration,
    plan_active_registration,
    registration_capabilities,
)


PROTOCOL_MAJOR = 2
PROTOCOL_MINOR = 3
SERVER_VERSION = "0.1.0-dev"
SUPPORTED_METHODS = (
    {
        "method": "workspace/inspect",
        "operation_class": "read-only",
    },
    {
        "method": "runtime/plan",
        "operation_class": "read-only",
    },
    {
        "method": "runtime/diagnose",
        "operation_class": "read-only",
    },
    {
        "method": "instance/select",
        "operation_class": "reversible",
    },
    {
        "method": "instance/current",
        "operation_class": "read-only",
    },
    {
        "method": "registration/capabilities",
        "operation_class": "read-only",
    },
    {
        "method": "registration/plan",
        "operation_class": "read-only",
    },
    {
        "method": "registration/apply",
        "operation_class": "reversible",
    },
    {
        "method": "shutdown",
        "operation_class": "read-only",
    },
)
SUPPORTED_METHOD_NAMES = frozenset(
    capability["method"] for capability in SUPPORTED_METHODS
)
UNAVAILABLE_METHODS: tuple[dict[str, str], ...] = ()
FEATURES = (
    {
        "feature": "cancellation",
        "available": False,
        "reason": "The current request handlers do not expose cancellation.",
    },
    {
        "feature": "progress",
        "available": False,
        "reason": "The current request handlers do not emit progress.",
    },
)


class ProtocolMethodError(ValueError):
    """A JSON-RPC method failure safe to return to the client."""

    def __init__(
        self,
        code: int,
        message: str,
        *,
        kind: str,
        state: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        data: dict[str, Any] = {"kind": kind}
        if state is not None:
            data["state"] = state
        if details:
            data.update(details)
        self.data = data


def _exact_object(
    value: Any,
    *,
    label: str,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProtocolMethodError(
            -32602,
            f"{label} must be an object",
            kind="invalid_params",
        )
    keys = set(value)
    missing = sorted(required - keys)
    unexpected = sorted(keys - required - optional)
    if missing:
        raise ProtocolMethodError(
            -32602,
            f"{label} lacks required fields: {', '.join(missing)}",
            kind="invalid_params",
        )
    if unexpected:
        raise ProtocolMethodError(
            -32602,
            f"{label} has unexpected fields: {', '.join(unexpected)}",
            kind="invalid_params",
        )
    return value


def _nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ProtocolMethodError(
            -32602,
            f"{label} must be a non-empty string",
            kind="invalid_params",
        )
    return value


def _json_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProtocolMethodError(
            -32602,
            f"{label} must be an object",
            kind="invalid_params",
        )
    return value


def _sha256_identity(value: Any, label: str) -> str:
    identity = _nonempty_string(value, label)
    if (
        len(identity) != 71
        or not identity.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in identity[7:])
    ):
        raise ProtocolMethodError(
            -32602,
            f"{label} must be a sha256 identity",
            kind="invalid_params",
        )
    return identity


def _nonnegative_integer(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ProtocolMethodError(
            -32602,
            f"{label} must be a non-negative integer",
            kind="invalid_params",
        )
    return value


def _string_array(
    value: Any,
    label: str,
    *,
    required_length: int | None = None,
) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item for item in value)
    ):
        raise ProtocolMethodError(
            -32602,
            f"{label} must be a string array",
            kind="invalid_params",
        )
    if required_length is not None and len(value) != required_length:
        raise ProtocolMethodError(
            -32602,
            f"{label} must contain exactly {required_length} entry",
            kind="invalid_params",
        )
    if len(value) != len(set(value)):
        raise ProtocolMethodError(
            -32602,
            f"{label} contains duplicates",
            kind="invalid_params",
        )
    return tuple(value)


def _workspace_from_uri(uri: str) -> tuple[Path, str]:
    parsed = urlparse(uri)
    if (
        parsed.scheme != "file"
        or parsed.query
        or parsed.fragment
        or parsed.params
        or parsed.netloc not in {"", "localhost"}
    ):
        raise ProtocolMethodError(
            -32602,
            "workspace root must be a local file URI",
            kind="invalid_workspace_uri",
        )
    path_text = url2pathname(parsed.path)
    if os.name == "nt" and len(path_text) >= 3:
        if path_text[0] == "/" and path_text[2] == ":":
            path_text = path_text[1:]
    workspace = Path(path_text).resolve()
    if not workspace.is_dir():
        raise ProtocolMethodError(
            -32602,
            "workspace root is not a directory",
            kind="invalid_workspace_root",
        )
    return workspace, workspace.as_uri()


def _local_path_from_uri(uri: str, label: str) -> Path:
    parsed = urlparse(uri)
    if (
        parsed.scheme != "file"
        or parsed.query
        or parsed.fragment
        or parsed.params
        or parsed.netloc not in {"", "localhost"}
    ):
        raise ProtocolMethodError(
            -32602,
            f"{label} must be a local file URI",
            kind="invalid_params",
        )
    path_text = url2pathname(parsed.path)
    if os.name == "nt" and len(path_text) >= 3:
        if path_text[0] == "/" and path_text[2] == ":":
            path_text = path_text[1:]
    return Path(path_text)


def _source_distribution_identity() -> str:
    shell_root = _module_resource_root(__file__, 'workbench-shell')
    project_intelligence_root = Path(
        workbench_project_intelligence.__file__
    ).resolve().parents[2]
    roots = (
        shell_root / "src",
        shell_root / "contracts",
        shell_root / "schemas",
        shell_root / "data",
        project_intelligence_root / "src",
        project_intelligence_root / "contracts",
        project_intelligence_root / "schemas",
    )
    files = sorted(
        path
        for root in roots
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    )
    digest = sha256()
    for path in files:
        if path.is_relative_to(shell_root):
            identity = Path("workbench-shell") / path.relative_to(shell_root)
        else:
            identity = (
                Path("project-intelligence")
                / path.relative_to(project_intelligence_root)
            )
        digest.update(identity.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _success(request_id: str | int, result: Any) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": result,
    }


def _error(
    request_id: str | int | None,
    code: int,
    message: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {
        "code": code,
        "message": message,
    }
    if data:
        error["data"] = data
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": error,
    }


class ProtocolSession:
    """Dispatch one ordered Workbench protocol session."""

    def __init__(
        self,
        *,
        suite_root: Path | str | None = None,
        state_root: Path | str | None = None,
        logger: Callable[[str], None] | None = None,
        configuration: WorkbenchConfiguration | None = None,
        config_path: Path | str | None = None,
    ) -> None:
        self._state = "new"
        self._suite_root = (
            discover_suite_root()
            if suite_root is None
            else Path(suite_root).resolve()
        )
        if configuration is not None and config_path is not None:
            raise WorkbenchConfigurationError(
                "configuration and config_path are mutually exclusive"
            )
        self._configuration = configuration or load_workbench_configuration(
            self._suite_root,
            CONFIGURATION_PATH if config_path is None else config_path,
        )
        self._workspace: Path | None = None
        self._workspace_uri: str | None = None
        self._client: dict[str, str] | None = None
        self._execution_host: dict[str, str] | None = None
        self._state_root = (
            None
            if state_root is None
            else Path(state_root).expanduser().resolve()
        )
        self._logger = logger or (lambda _message: None)

    @property
    def state(self) -> str:
        return self._state

    @property
    def shutdown_requested(self) -> bool:
        return self._state == "shutdown"

    def handle_message(self, message: Any) -> dict[str, Any] | None:
        """Return a response, or None for an ignored notification."""

        if not isinstance(message, dict):
            return _error(
                None,
                -32600,
                "Invalid Request",
                {"kind": "invalid_request"},
            )
        request_id = message.get("id")
        valid_id = (
            isinstance(request_id, str)
            and bool(request_id)
            or type(request_id) is int
        )
        if (
            message.get("jsonrpc") != "2.0"
            or not isinstance(message.get("method"), str)
            or ("id" in message and not valid_id)
            or set(message) - {"jsonrpc", "id", "method", "params"}
        ):
            return _error(
                request_id if valid_id else None,
                -32600,
                "Invalid Request",
                {"kind": "invalid_request"},
            )
        if "id" not in message:
            self._logger(
                f"ignored notification: {message['method']}"
            )
            return None
        if "params" not in message:
            return _error(
                request_id,
                -32602,
                "Invalid params",
                {"kind": "invalid_params"},
            )
        params = message["params"]
        if not isinstance(params, dict):
            return _error(
                request_id,
                -32602,
                "Invalid params",
                {"kind": "invalid_params"},
            )

        method = message["method"]
        try:
            if method == "initialize":
                result = self._initialize(params)
            elif method == "workspace/inspect":
                result = self._inspect(params)
            elif method == "runtime/plan":
                result = self._runtime_plan(params)
            elif method == "runtime/diagnose":
                result = self._runtime_diagnose(params)
            elif method == "instance/select":
                result = self._instance_select(params)
            elif method == "instance/current":
                result = self._instance_current(params)
            elif method == "registration/capabilities":
                result = self._registration_capabilities(params)
            elif method == "registration/plan":
                result = self._registration_plan(params)
            elif method == "registration/apply":
                result = self._registration_apply(params)
            elif method == "shutdown":
                result = self._shutdown(params)
            else:
                raise ProtocolMethodError(
                    -32601,
                    f"Method not found: {method}",
                    kind="method_not_found",
                )
            return _success(request_id, result)
        except ProtocolMethodError as exc:
            self._logger(f"{method} rejected: {exc.message}")
            return _error(request_id, exc.code, exc.message, exc.data)
        except RuntimePlanError as exc:
            self._logger(f"{method} unavailable: {exc}")
            return _error(
                request_id,
                -32020,
                "Runtime planning failed",
                {
                    "kind": "runtime_plan_unavailable",
                    "state": "unavailable",
                },
            )
        except RuntimeDiagnosisError as exc:
            self._logger(f"{method} unavailable: {exc}")
            return _error(
                request_id,
                -32021,
                "Runtime diagnosis failed",
                {
                    "kind": "runtime_diagnosis_unavailable",
                    "state": "unavailable",
                },
            )
        except ActiveInstanceError as exc:
            self._logger(f"{method} unavailable: {exc}")
            return _error(
                request_id,
                -32030,
                "Active instance operation failed",
                {
                    "kind": "active_instance_unavailable",
                    "state": "unavailable",
                    "reason": str(exc),
                },
            )
        except RegistrationWizardError as exc:
            self._logger(f"{method} unavailable: {exc}")
            return _error(
                request_id,
                -32031,
                "Registration operation failed",
                {
                    "kind": "registration_unavailable",
                    "state": "unavailable",
                    "reason": str(exc),
                },
            )
        except (
            ComponentGraphError,
            ProjectInspectionError,
            WorkbenchConfigurationError,
        ) as exc:
            self._logger(f"{method} unavailable: {exc}")
            return _error(
                request_id,
                -32010,
                "Workspace inspection failed",
                {
                    "kind": "workspace_unavailable",
                    "state": "unavailable",
                },
            )
        except Exception as exc:  # pragma: no cover - defensive boundary
            self._logger(f"{method} internal failure: {type(exc).__name__}")
            return _error(
                request_id,
                -32603,
                "Internal error",
                {"kind": "internal_error"},
            )

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        if self._state != "new":
            raise ProtocolMethodError(
                -32003,
                "Session is already initialized",
                kind="already_initialized",
            )
        params = _exact_object(
            params,
            label="initialize params",
            required=frozenset(
                {
                    "protocol_version",
                    "client",
                    "workspace_roots",
                    "execution_host",
                }
            ),
            optional=frozenset({"required_methods"}),
        )
        protocol_version = _exact_object(
            params["protocol_version"],
            label="protocol_version",
            required=frozenset({"major", "minor"}),
        )
        major = _nonnegative_integer(
            protocol_version["major"],
            "protocol_version.major",
        )
        _nonnegative_integer(
            protocol_version["minor"],
            "protocol_version.minor",
        )
        if major != PROTOCOL_MAJOR:
            raise ProtocolMethodError(
                -32001,
                "Incompatible Workbench protocol major",
                kind="protocol_version_mismatch",
                state="unavailable",
                details={
                    "requested_major": major,
                    "supported_major": PROTOCOL_MAJOR,
                },
            )
        client = _exact_object(
            params["client"],
            label="client",
            required=frozenset({"id", "kind", "version"}),
        )
        normalized_client = {
            "id": _nonempty_string(client["id"], "client.id"),
            "kind": _nonempty_string(client["kind"], "client.kind"),
            "version": _nonempty_string(client["version"], "client.version"),
        }
        roots = _string_array(
            params["workspace_roots"],
            "workspace_roots",
            required_length=1,
        )
        workspace, workspace_uri = _workspace_from_uri(roots[0])
        execution_host = _exact_object(
            params["execution_host"],
            label="execution_host",
            required=frozenset({"kind"}),
            optional=frozenset({"authority"}),
        )
        normalized_host = {
            "kind": _nonempty_string(
                execution_host["kind"],
                "execution_host.kind",
            )
        }
        if "authority" in execution_host:
            normalized_host["authority"] = _nonempty_string(
                execution_host["authority"],
                "execution_host.authority",
            )
        required_methods = _string_array(
            params.get("required_methods", []),
            "required_methods",
        )
        unavailable_required = sorted(
            set(required_methods) - SUPPORTED_METHOD_NAMES
        )
        if unavailable_required:
            raise ProtocolMethodError(
                -32004,
                "Required Workbench methods are unavailable",
                kind="required_capability_unavailable",
                state="unavailable",
                details={"methods": unavailable_required},
            )

        graph = load_component_graph(
            self._suite_root / REGISTRY_PATH,
            self._suite_root,
        )
        distribution_identity = _source_distribution_identity()

        self._workspace = workspace
        self._workspace_uri = workspace_uri
        self._client = normalized_client
        self._execution_host = normalized_host
        self._state = "initialized"
        self._logger(
            "initialized "
            f"{normalized_client['kind']} client for {workspace_uri}"
        )
        return {
            "format": "workbench-initialize-result-v2",
            "schema_version": 2,
            "protocol_version": {
                "major": PROTOCOL_MAJOR,
                "minor": PROTOCOL_MINOR,
            },
            "server": {
                "name": "workbench-shell",
                "version": SERVER_VERSION,
                "distribution": {
                    "kind": "source-tree",
                    "identity": distribution_identity,
                    "verified": False,
                },
            },
            "client": normalized_client,
            "workspace": {
                "root_uri": workspace_uri,
                "execution_host": normalized_host,
            },
            "capabilities": {
                "supported_methods": [dict(item) for item in SUPPORTED_METHODS],
                "unavailable_methods": [
                    dict(item) for item in UNAVAILABLE_METHODS
                ],
                "features": [dict(item) for item in FEATURES],
            },
        }

    def _inspect(self, params: dict[str, Any]) -> dict[str, Any]:
        if self._state != "initialized" or self._workspace is None:
            raise ProtocolMethodError(
                -32002,
                "Session is not initialized",
                kind="not_initialized",
            )
        _exact_object(
            params,
            label="workspace/inspect params",
            required=frozenset(),
        )
        return inspect_project(
            self._suite_root,
            self._workspace,
            configuration=self._configuration,
        )

    def _runtime_plan(self, params: dict[str, Any]) -> dict[str, Any]:
        if self._state != "initialized" or self._workspace is None:
            raise ProtocolMethodError(
                -32002,
                "Session is not initialized",
                kind="not_initialized",
            )
        params = _exact_object(
            params,
            label="runtime/plan params",
            required=frozenset({"side", "launcher"}),
        )
        side = _nonempty_string(params["side"], "side")
        launcher = _nonempty_string(params["launcher"], "launcher")
        if side not in {"client", "server"}:
            raise ProtocolMethodError(
                -32602,
                "side must be client or server",
                kind="invalid_params",
            )
        valid_launchers = (
            {"prism", "multimc"}
            if side == "client"
            else {"dedicated-server"}
        )
        if launcher not in valid_launchers:
            raise ProtocolMethodError(
                -32602,
                f"launcher is invalid for {side}",
                kind="invalid_params",
            )
        return plan_project_runtime(
            self._suite_root,
            self._workspace,
            side=side,
            launcher=launcher,
            configuration=self._configuration,
        )

    def _runtime_diagnose(self, params: dict[str, Any]) -> dict[str, Any]:
        if self._state != "initialized" or self._workspace is None:
            raise ProtocolMethodError(
                -32002,
                "Session is not initialized",
                kind="not_initialized",
            )
        params = _exact_object(
            params,
            label="runtime/diagnose params",
            required=frozenset({"receipt_uri"}),
            optional=frozenset({"artifact_root_uris"}),
        )
        receipt_uri = _nonempty_string(
            params["receipt_uri"],
            "receipt_uri",
        )
        artifact_root_uris = _string_array(
            params.get("artifact_root_uris", []),
            "artifact_root_uris",
        )
        receipt = _local_path_from_uri(receipt_uri, "receipt_uri")
        artifact_roots = [
            _local_path_from_uri(uri, "artifact_root_uris entry")
            for uri in artifact_root_uris
        ]
        return diagnose_project_runtime(
            self._suite_root,
            self._workspace,
            launch_receipt=receipt,
            artifact_roots=artifact_roots,
            configuration=self._configuration,
        )

    def _instance_select(self, params: dict[str, Any]) -> dict[str, Any]:
        if self._state != "initialized" or self._workspace is None:
            raise ProtocolMethodError(
                -32002,
                "Session is not initialized",
                kind="not_initialized",
            )
        params = _exact_object(
            params,
            label="instance/select params",
            required=frozenset({"instance_uri"}),
        )
        instance = _local_path_from_uri(
            _nonempty_string(params["instance_uri"], "instance_uri"),
            "instance_uri",
        )
        return initialize_active_instance(
            self._suite_root,
            self._workspace,
            instance,
            state_root=self._state_root,
            configuration=self._configuration,
        )

    def _instance_current(self, params: dict[str, Any]) -> dict[str, Any]:
        if self._state != "initialized" or self._workspace is None:
            raise ProtocolMethodError(
                -32002,
                "Session is not initialized",
                kind="not_initialized",
            )
        _exact_object(
            params,
            label="instance/current params",
            required=frozenset(),
        )
        selection = load_active_instance(
            self._suite_root,
            self._workspace,
            state_root=self._state_root,
            configuration=self._configuration,
        )
        return {
            key: value
            for key, value in selection.items()
            if key not in {"instance_path", "payload_path"}
        }

    def _registration_capabilities(
        self,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        if self._state != "initialized" or self._workspace is None:
            raise ProtocolMethodError(
                -32002,
                "Session is not initialized",
                kind="not_initialized",
            )
        params = _exact_object(
            params,
            label="registration/capabilities params",
            required=frozenset(),
            optional=frozenset({"pattern"}),
        )
        pattern = (
            _nonempty_string(params["pattern"], "pattern")
            if "pattern" in params
            else None
        )
        return registration_capabilities(
            self._suite_root,
            self._workspace,
            pattern_key=pattern,
            state_root=self._state_root,
            configuration=self._configuration,
        )

    def _registration_plan(self, params: dict[str, Any]) -> dict[str, Any]:
        if self._state != "initialized" or self._workspace is None:
            raise ProtocolMethodError(
                -32002,
                "Session is not initialized",
                kind="not_initialized",
            )
        params = _exact_object(
            params,
            label="registration/plan params",
            required=frozenset({"pattern", "answers"}),
        )
        return plan_active_registration(
            self._suite_root,
            self._workspace,
            pattern_key=_nonempty_string(params["pattern"], "pattern"),
            answers=_json_object(params["answers"], "answers"),
            state_root=self._state_root,
            configuration=self._configuration,
        )

    def _registration_apply(self, params: dict[str, Any]) -> dict[str, Any]:
        if self._state != "initialized" or self._workspace is None:
            raise ProtocolMethodError(
                -32002,
                "Session is not initialized",
                kind="not_initialized",
            )
        params = _exact_object(
            params,
            label="registration/apply params",
            required=frozenset({"pattern", "answers", "consent"}),
        )
        consent = _exact_object(
            params["consent"],
            label="consent",
            required=frozenset({"approved", "plan_id", "operation_class"}),
        )
        if consent["approved"] is not True:
            raise ProtocolMethodError(
                -32602,
                "consent.approved must be true",
                kind="consent_required",
            )
        if consent["operation_class"] != "local-mutation":
            raise ProtocolMethodError(
                -32602,
                "consent.operation_class must be local-mutation",
                kind="invalid_params",
            )
        return apply_active_registration(
            self._suite_root,
            self._workspace,
            pattern_key=_nonempty_string(params["pattern"], "pattern"),
            answers=_json_object(params["answers"], "answers"),
            expected_plan_id=_sha256_identity(
                consent["plan_id"],
                "consent.plan_id",
            ),
            state_root=self._state_root,
            configuration=self._configuration,
        )

    def _shutdown(self, params: dict[str, Any]) -> None:
        if self._state != "initialized":
            raise ProtocolMethodError(
                -32002,
                "Session is not initialized",
                kind="not_initialized",
            )
        _exact_object(
            params,
            label="shutdown params",
            required=frozenset(),
        )
        self._state = "shutdown"
        self._logger("shutdown requested")
        return None
