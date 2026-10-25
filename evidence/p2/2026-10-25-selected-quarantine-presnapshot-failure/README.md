# Selected quarantine wrapper: pre-snapshot failure

[summary.json](summary.json) is the byte-exact original content-free output of
the operator's first approved baseline/quarantine wrapper attempt on 2026-10-25,
from source `e890860922bd94e75d332ad10461b60b6a1bfeac`.
Its SHA-256 is
`568c93565e8b3c147e968b99f41275fe6d8cc270880e1a6e73c6e517895b04d7`.
The source/run association comes from the operator handoff: this summary schema
contains no Git revision, venue or timestamps, and none have been added.

The original reports **fail**, stage **pre-snapshot**. Every verification and
eligibility boolean is false; there is no proof-receipt hash. This is neither
a quarantine observation nor a successful cleanup or host-baseline comparison.
No GPU, public endpoint, deployment eligibility or confidentiality claim is made.

The operator's separate read-only diagnosis found that the fixed resolver stub
was owned by its service (UID/GID 989), while this source required the calling
root UID for every public input. The private partial snapshot and absence of a
proof directory were inspected by the operator, not encoded into this receipt.
Raw firewall, resolver, account and host metadata remain private and are not
included here. The summary alone does not identify the failing file or establish
resource absence.

The [narrow resolver admission correction](../../../docs/tasks/p2-t4-selected-host-baseline.md#closed-resolver-ownership)
retains the exact failed artifact and all proof/policy behavior. Local tests are
not a successful retry; another run needs final-head independent review, CI and
separate operator approval.
