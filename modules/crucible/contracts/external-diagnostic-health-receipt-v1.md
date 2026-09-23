# Crucible external diagnostic-health receipt v1

Status: experimental evidence gate

## Purpose

This receipt evaluates one exact completed runtime session against a literal
diagnostic policy owned by a platform or pack profile. It prevents a clean
compatibility seal when the profile has declared a matching failure, while
keeping free-form game and subsystem logs outside Crucible authority.

V1 answers only this question: which declared literal rules matched the exact
launch-log bytes bound by the completed session audit? It does not infer new
rules from log text and does not treat an unmatched log as proof of health.

## Exact inputs

The assembler consumes exactly three evidence files:

1. a content-addressed
   `workbench.crucible.exact-runtime-session-audit.v1` receipt whose outcome is
   `completed`, whose fixture result is complete/flushed/shutdown-requested,
   and whose launch terminal evidence is a clean Gradle exit zero;
2. the exact launch-log bytes whose SHA-256 and size are bound by that audit;
   and
3. a `workbench-crucible-literal-diagnostic-policy-v1` JSON policy.

The raw SHA-256 and size of every input are retained. The policy additionally
has a canonical semantic SHA-256 after closed-surface validation and rule
sorting. The audit's own semantic `audit_id` is recomputed before admission.

The policy declares one `subject_artifact_sha256`. That digest must occur
exactly once in `session_audit.installed_mod_set.verified_artifacts`; the
receipt retains its audited relative path and size. This is installed-artifact
custody, not proof that its code executed.

## Literal policy

The policy has exactly these fields:

- `format`, fixed to
  `workbench-crucible-literal-diagnostic-policy-v1`;
- `schema_version`, fixed to `1`;
- `policy_id` and `owner_profile_id`;
- `subject_artifact_sha256`;
- `log_format`, fixed to `forge-log4j2-bracketed-v1`;
- `match_semantics`, fixed to
  `literal-logger-level-message-prefix-v1`;
- `zero_match_state`, fixed to `inconclusive`; and
- one to 256 `rules`.

Each rule has `rule_id`, `disposition`, `logger`, `level`, and
`message_prefix`. `disposition` is exactly `fail` or `review`. Logger and level
use exact equality. `message_prefix` uses the host language's literal Unicode
prefix comparison after strict UTF-8 decoding. Policy fields for regular
expressions, globbing, scripts, expressions, or callbacks do not exist and
are rejected as unknown fields. Duplicate rule IDs and duplicate literal
predicates are rejected.

Policy array order has no meaning. Normalized rules are sorted by `rule_id`.
The raw policy-file hash still binds byte-level changes such as formatting or
declared order.

## Deterministic log parsing

Lines are separated only by LF; one immediately preceding CR is removed. A
terminal LF does not create an additional empty line. V1 parses only complete
Forge/Log4j2 headers of the form:

```text
[time] [thread/LEVEL] [logger]: message
```

The parser uses fixed delimiter operations and splits `thread/LEVEL` at its
last slash. It does not execute policy-provided patterns. Stack-trace lines,
Gradle lines, malformed headers, and other free-form text remain unparsed and
cannot match.

Every rule result retains:

- total occurrence count;
- first and last matching one-based log line, or null bounds for zero matches;
  and
- the sorted unique SHA-256 set of exact matched message strings.

Multiple declared rules may match one line. Occurrence totals are therefore
rule occurrences; `matched_log_line_count` separately counts distinct matched
lines.

## Gate states

Gate state is derived with strict precedence:

1. any `fail` occurrence yields `fail`;
2. otherwise any `review` occurrence yields `review`; and
3. zero occurrences yields `inconclusive`.

V1 has no `pass` or `healthy` state. A launch log may omit a subsystem's
diagnostics, a subsystem may fail open, and an independent diagnostic file may
never have been created. Therefore zero matches never proves health.
`clean_compatibility_seal_allowed` is false for all three V1 states: `fail`
blocks the seal, `review` requires review, and `inconclusive` lacks positive
health evidence.

Successful receipt assembly is distinct from the gate decision. The CLI exits
zero after writing a valid receipt even when its gate state is `fail`.

## Identity

The machine representation is
[`external-diagnostic-health-receipt-v1.schema.json`](../schemas/external-diagnostic-health-receipt-v1.schema.json).
The policy representation is
[`literal-diagnostic-policy-v1.schema.json`](../schemas/literal-diagnostic-policy-v1.schema.json).

`receipt_id` is
`crucible-external-diagnostic-health:sha256:<lowercase-hex>`, calculated over
canonical JSON for the entire receipt except `receipt_id`. Canonical JSON uses
UTF-8, sorted object keys, compact separators, retained normalized array
order, and no non-finite numbers.

## Boundaries

- Free-form log text is evidence, never generic Crucible authority.
- A literal policy match proves only that the bound message occurred in the
  bound log.
- Log absence does not prove diagnostic absence.
- Subject-artifact installation does not prove selection or execution.
- The receipt does not diagnose cause, authorize a fix, authorize a Blueprint,
  or approve a release.
- Pack-specific policies remain under their pack profiles. The generic
  contract contains no Recurrent Complex integration or private lifecycle
  path.
