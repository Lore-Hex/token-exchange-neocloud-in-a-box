# Failed local sandbox attempt

This initial attempt generated the fixture catalog but failed with
`PermissionError` when starting the local HTTP listener under the restricted
sandbox. No checker request or inference ran. Go also reported denied module
cache writes in the operational log. This is a failed environment attempt,
not passing endpoint evidence.

The recorded Git SHA is the original worktree base: the implementation was
still uncommitted during this development attempt. The subsequent
[committed-source run](../2026-10-19-catalog-tier1/run.json) is the acceptance
evidence. These files are retained to avoid hiding the failed attempt; they
do not establish reproducibility for that earlier uncommitted source state.
