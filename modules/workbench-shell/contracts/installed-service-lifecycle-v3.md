# Explicit Service V3 process lifecycle

The native installation runs one explicit host process. It does not install a
background system service or provide a side-by-side upgrade/rollback daemon.
Use the native environment's `workbench` executable:

```text
workbench service-host-v3 --service-root PRIVATE_SERVICE_ROOT --endpoint PRIVATE_SERVICE_ROOT/run/service.sock --ready-file PRIVATE_READY_PATH --process-nonce service-process-nonce:32_HEX_DIGITS
```

The owner-private service root contains the durable store, credentials, run
directory and logs. Startup refuses an already-held store lease or an existing
endpoint. A failed duplicate startup must not unlink another owner's endpoint.
The endpoint is removed on graceful shutdown only if its filesystem identity
still matches the path created by this process.

After startup, the host writes a [readiness record](../schemas/installed-service-ready-v2.schema.json)
binding its PID, caller-supplied nonce, environment identity, observed native
package fingerprint, exact registry/distribution and host-adapter receipt.
The record is an observation, not a process-control credential. Its existence
does not prove that a process is still alive. Use an authenticated probe:

```text
workbench service-probe-v3 --endpoint PRIVATE_SERVICE_ROOT/run/service.sock --credential PRIVATE_SERVICE_ROOT/credentials/local-service-v3.token --json
```

Clients continue to supply the exact endpoint and credential to Service V3 or
its stdio proxy. They do not depend on an installer-local lifecycle script.
Protocol V3 request, result, durable-job, cancellation and restart semantics
are unchanged; an authenticated endpoint does not grant profile support or
release authority.

Feature Studio operations require an admitted, enabled Supersymmetry profile
and its compatible dependencies at the actual owner handler. Endpoint or
embedded access cannot bypass that requirement. Generic service discovery,
context custody and job-control APIs remain available when a profile is
disabled or unavailable. Retained bytes remain in custody, but Feature-specific
result revalidation still requires its profile owner; it is not a profile-free
semantic interpretation service.

## Package changes and recovery

The command and composed service hold environment-wide package activity
leases. Workbench rejects package mutation while they remain active. External
`pip` cannot be made transactional by those advisory locks: metadata drift
requires stopping and restarting affected processes. No rollback is claimed.

For an update, stop the exact process you launched, install reviewed packages
or a fresh native environment, and restart against the retained private state.
The durable store and user data are not deleted by stop/restart. A crash may
leave an endpoint or readiness record. Startup fails closed until an operator
independently verifies the old process is dead and removes only its exact
stale endpoint. Never signal a process or delete a path merely because a PID
or path appears in an old readiness record.

POSIX uses an owner-private local Unix socket. Windows uses an authenticated,
exclusive loopback endpoint and owner-private endpoint/credential files. The
automated POSIX crash/process tests are not Windows or macOS release evidence;
each claimed release platform requires separate installed-artifact qualification.
