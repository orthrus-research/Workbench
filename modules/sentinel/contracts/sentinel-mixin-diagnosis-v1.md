# Sentinel Mixin diagnosis V1

## Outcome

`workbench diagnose mixins JAR...` gives a developer a plain-English view of
the existing Cleanroom Mixin Doctor report for the exact selected archive
bytes.

## Authority boundary

- Project Intelligence owns the static archive facts.
- The selected Cleanroom profile policy owns every rule, rationale,
  disposition, coverage state, and limitation.
- Sentinel owns only the human presentation and the optional strict exit-code
  mapping.
- Workbench Shell owns CLI routing.

Sentinel must not add a finding, change a disposition, convert missing runtime
evidence into an acceptance, or describe a rare pattern as a violation.

## Interfaces

```text
workbench diagnose mixins JAR... [--policy POLICY] [--json] [--strict]
```

Human output lists the exact artifact label, disposition, severity, locator,
observed value, policy rationale, and rule ID supplied by the owner report.
The next-action sentence is presentation guidance for the existing aggregate
disposition; it is not a new policy decision.

`--json` emits the profile owner's
`workbench-cleanroom-mixin-doctor-report-v1` unchanged. Without `--strict`, a
successfully produced report exits 0 so it can be inspected. With `--strict`,
`review` and `reject` exit 1. Invalid inputs or an unavailable owner policy
exit 2.

## Limits

The flow performs a bounded static archive scan only. Runtime application,
target resolution, final transformed bytes, assembled-game compatibility,
gameplay correctness, and release readiness remain outside this contract.
