# CPU Parity Readiness Failure

Exact producer: `e75dca644aa891a1c762635ef67d9730fac936b7`.
The authorized local OrbStack Linux/amd64 run started at 19:53:06 UTC and
ended at 19:58:35 UTC on 2026-10-22. The original `run.json` and `output.log`
are copied byte-for-byte; their SHA-256 pins are in the regression test.

Offline image assembly and Linux compilation completed. The independent
vocabulary-only helper completed for Qwen3-0.6B before the worker reached
readiness. Readiness then failed, before a process/socket observation was
accepted or any endpoint comparison was recorded. No second model ran.
The receipt cannot distinguish engine exit, readiness timeout or a rejected
kernel identity check. None is asserted as the root cause.

The owned container and image were removed; image build completion was
confirmed. Existing cached base images/BuildKit cache were retained as designed.
The run retained the fixed four-CPU, 8-GiB, 128-PID, network-none, no-GPU bounds.

No tokenizer parity pass, live admission, tau trial, quality result, public
exposure or confidential execution is claimed. Native/compiler logs and model
content were not retained. The next committed diagnostic adds only fixed source
IDs, line numbers, bounded numeric errno/exit status and a fixed error category.
