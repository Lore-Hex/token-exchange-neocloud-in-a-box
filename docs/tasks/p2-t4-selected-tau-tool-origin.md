# P2.T4: content-free selected tool rejection origin

Status: the origin observer ran in the
[second hardware diagnostic](../../evidence/p2/2026-10-25-selected-tau-email-rejection-0333/README.md),
identifying the pinned retail email-lookup rejection at line 330. The preceding
[hardware diagnostic](../../evidence/p2/2026-10-25-selected-tau-tool-rejection-0245/README.md)
failed on one known assistant tool raising `ValueError`. Its originals do not
contain an exact rejection site. The optional containment diagnostic below
has only local test coverage and has not run on hardware.

The selected operational observer may now include `origin` with only
`source_sha256` and an integer `line`. Only three exact source files already
sealed by [the runtime lock](../../bench/tau-runtime-lock.json) are eligible:
the retail tools, environment and toolkit from tau revision
`fc0055dc` (the full revision is in the lock). It checks already-loaded modules,
regular-file identity, byte length, SHA-256 and line count once on first error.
It never imports a tool to collect diagnostics.

A bounded traceback walk accepts only the deepest frame, from an eligible file.
Unknown sources, unavailable files or more than 32 frames omit the origin;
they never substitute a known outer frame. No filename, function name, local
variable, tool identity, argument, exception message, prompt or response is
serialized. The wire validator rejects all additional fields and unknown
digests or lines. Old observations without an origin remain valid.

For the exact known assistant email-lookup `ValueError` at line 330, a future
observation may also include `prior_user_text_contains_argument`, a boolean.
It compares the submitted email with earlier simulator-user text transiently,
using the upstream lookup's case-insensitive spelling. Neither the email nor
the message text is retained in the diagnostic. Inputs are bounded to 256
messages, 1,048,576 user-text characters and a 320-character email; unavailable, oversized
or unexpected inputs omit the field rather than claiming a mismatch. A
substring match is not proof of the correct customer identity, user intent,
or simulator correctness. No relation can be reconstructed for the earlier
hardware evidence, and this field cannot change scoring or tool execution.

This is an ordinary trusted diagnostic, not an attestation or proof against
arbitrary modifications of Python modules in memory. It changes no task,
request, token, retry or error limit, and cannot turn a failed trial into a
quality result. The new regression test is included in the selected scorer's
source identity.

Validation includes byte-pin joins, missing/changed/symlink/FIFO files,
unknown and overlong tracebacks, hostile exception hooks, legacy records,
mixed sources and private-content canaries. The optional local audit checks
the pinned upstream archive and executes only its original `_get_user`,
`_get_order` and `find_user_id_by_email` method bodies with synthetic absent
entries. The email method's exact registration decorator is checked and omitted
from this isolated audit; its body and line numbers are unchanged:

```sh
python3 scripts/test_tau_tool_origin.py --upstream-source /path/to/pinned-tau.tar.gz
```

It checks exact source-line identification, with no imports of upstream tools,
model calls or changed scoring. Source:
[tau retail tools at the pinned revision](https://github.com/sierra-research/tau2-bench/blob/fc0055dc/src/tau2/domains/retail/tools.py).
