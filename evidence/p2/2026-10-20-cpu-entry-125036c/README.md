# CPU-Only Entry Failure

Original recorder output from committed source
`125036cb61a9d15d624f5d1c8a0c65d69076574c`, after independent review and green
CI. The local cached immutable image completed preflight and all five
application stages, then its pre-serve child exited 1 after 9,148 ms.

- Last checkpoint: 11, before importing `ServeSubcommand`.
- The final import-finder comparison differed. All nine other observed
  state comparisons matched.
- No `serve.cmd`, GPU, model weights, inference or network was used.
- Owned container cleanup was confirmed; a subsequent label-filtered Docker
  listing also found no remaining diagnostic container.

The last checkpoint locates the failed interval, not the exact statement.
The final mismatch identifies changed state, not which dependency changed it
or which exception first caused termination. This CPU result does not prove
the cause of the earlier GPU startup failure. No ABI, inference, model quality
or confidentiality acceptance is claimed.

Original artifact SHA-256:

| File | SHA-256 |
| --- | --- |
| `run.json` | `c5a60637849450294deea13af77ddffea9fe8bdfe850a1b4d4f2466885d87b15` |
| `output.log` | `f4985fd5f781e0324cce4d4277970ad1a5e67c24aaa8d1395fe945c7f197d6c3` |

See the [diagnostic contract](../../../docs/tasks/p2-t3-cpu-entry.md) for
fixed checkpoint meanings, source/image binding, runtime limits and scope.
