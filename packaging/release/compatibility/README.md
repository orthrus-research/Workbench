# Compatibility and qualification evidence

Native package dependency ranges and API/provider contract versions govern
admission. They do not prove every permitted combination has been tested.

A native assembly records exact selected package versions, all dependency
wheels, hashes, and target Python/OS/architecture. Qualification records must
bind that assembly's manifest digest and exact artifact hashes, source revision,
test commands, outcomes, skips, and host identity. Preserve these receipts with
the candidate artifacts. Never convert an input range or configured CI matrix
into a claim of completed qualification.

API compatibility, Python distribution versions, IDE protocol compatibility,
and retained record/schema identities are separate. A change to one does not
automatically version every other owner. Installed-domain tests, Core-only
tests and IDE tests are independent gates; releasing one module need not
republish unchanged peers.
