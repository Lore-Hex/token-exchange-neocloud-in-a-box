# P3.T1: Appraiser Harness Codec Boundary

[Native run 39779656194](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39779656194)
passed the fixed input download, configured 212 packages, built and installed
all five NVIDIA modules, and passed the runtime-startup policy checks. It then
failed before executing the vendor appraiser. The original
[stderr](../../evidence/p3/2026-10-22-appraisal-codec-failure/appraisal-cli-help.stderr)
identifies `LookupError: unknown encoding: ascii` in our privilege-drop helper.
The [result](../../evidence/p3/2026-10-22-appraisal-codec-failure/result.json),
[run](../../evidence/p3/2026-10-22-appraisal-codec-failure/run.json), and
[resource cleanup](../../evidence/p3/2026-10-22-appraisal-codec-failure/evidence-resources.json)
are preserved unchanged. No OOM occurred and private processes/payloads were
removed. This failed run did not reach root-verity generation.

The Python harness enters the minimal installed guest root before dropping
privileges and reading `/proc/self/status`. That root deliberately contains no
Python standard library. `TextIOWrapper` lazily resolves its ASCII codec; the
codec was not cached before the root changed. Python documents the codec cache
and lookup failure behavior in [codecs.lookup](https://docs.python.org/3.12/library/codecs.html#codecs.lookup).

Resolve that one standard codec before `chroot`, while the pinned build-tool
runtime is available. No library is copied into the guest, no path is exposed,
and no privilege or isolation check is removed. UID/GID/group/capability,
no-new-privileges and seccomp verification still happen after the drop and
before `execve`. The appraiser still receives only fixed `--help` commands,
without network, GPU devices, credentials or caller-controlled arguments.

Regression tests verify ordering and reproduce both cold-cache failure and
prepared-cache success after disabling imports in fresh isolated interpreters.
The Linux-only regression helper also offers `--codec-cold` and `--codec-warm`
controls using a real empty temporary chroot. It requires an explicitly
selected disposable container; it never changes the caller's host root or
executes vendor/GPU software. Exact-hash tests retain the original failure.

```
uv run --project tools --frozen python -m unittest discover -s scripts -p 'test_mkosi_appraisal_probe.py'
make check
```

Native appraiser execution and root-verity remain pending until a new guarded
workflow passes. A codec regression pass is not a GPU, guest boot, UKI,
reproducibility or confidential-service result.
