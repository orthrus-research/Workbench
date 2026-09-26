# Managed tree inventories

Core's managed-tree API stages a directory under Core custody, inventories it
before and after the owner's validation, flushes its members, records an intent,
and publishes the directory without replacing an existing target. Reconciliation
uses the same intent after an interrupted publication. Domain modules validate
their own payload meaning; Core verifies the retained bytes and filesystem shape.

`stage.publish(...)` uses `inventory_policy="portable-v1"` by default. This
version keeps at most 4,096 file and directory rows inline in an intent below
4 MiB. It uses portable member paths and normalized file modes. Existing
callers and Atlas derived-member behavior keep this policy.

An owner may explicitly select `inventory_policy="posix-exact-v1"` for an
ordinary POSIX tree. This policy permits up to 100,000 files and 100,000
directories, at most 2 GiB per file and 32 GiB in total. It records exact
permission bits (`stat.S_IMODE`) for files, directories and the tree root.
Member names are literal POSIX names, including colons and `.git` components.
Traversal and reads use no-follow descriptors; links, special files and
hardlinked files are refused. Derived members are unavailable
under this policy.

The exact policy stores a sealed, compact V3 intent with a digest and counts
instead of inline member rows. Reconciliation inventories the retained target
or original stage again and checks the digest, counts, permissions and root
identity. `ManagedTreeReference.members` still returns the full verified rows.
An uncommitted or failed stage remains under Core custody for review.

The exact policy requires POSIX descriptor support. Directory publication also
requires the host's atomic no-replace operation. On WSL, a Linux filesystem
such as the distribution's ext4 volume provides the expected POSIX behavior;
mounted Windows filesystems need separate validation of permission and
publication semantics before they can carry this policy.
