# Workbench stopped-world block delta V2

Status: experimental executable contract.

`runtime-worldgen-block-delta` transports two explicitly selected stopped
worlds to the Atlas V2 semantic block-delta authority and can retain the
identity-bearing result at a new JSON path. It never overwrites an existing
artifact.

Atlas owns structural validation, fingerprinting, per-world Forge registry
resolution, semantic position comparison, raw-remap accounting, bounded
attribution, and the resulting identity. Workbench Shell owns suite discovery,
transport, exclusive artifact creation, CLI rendering, and errors. Shell does
not reinterpret a namespace count as proof that a mod caused generation drift.

The standalone command cannot establish that Minecraft is stopped. A caller
must bind both worlds to completed runtime-observation receipts or equivalent
lifecycle proof. Reading is non-mutating; optional output retention is a local
write outside both worlds and should normally target ignored `.workbench/`
evidence storage.
