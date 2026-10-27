# Retained Offline Replay Failure

The original producer was `a0852bda1dc1b892f6992b17c6b303653427cc06`.
The network-disabled controller's unprivileged user could not access the
mounted local Docker socket. A separate read-only daemon-version check
confirmed permission denial. Image inspection failed before scorer creation;
the controller could not confirm cleanup through that inaccessible socket.

The original failed trial receipt and journal are retained byte-for-byte.
The journal stopped `incomplete` / `failed`, completed zero of 15 planned
rows, and held the entire **synthetic** $70.044864 reservation. It did not
release unknown billing or continue to another row. Independent inspection
found no scorer container left by this attempt. No paid request occurred.

The successful new offline rehearsal used a fresh output directory and only
added the mounted socket's existing group to the local controller container.
No host socket permission, source, production, admission or cleanup check was
changed. This was not a resumed journal or automatic trial retry. See
[the subsequent offline replay](../2026-10-27-sequential-tau-offline/README.md).
