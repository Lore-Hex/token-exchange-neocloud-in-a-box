# Blocked vLLM CC Candidate

**BLOCKED. Source preparation and software contracts only.** Nothing here changes
an image, model pack, runtime profile, admission policy or deployment gate.
The twelve `base/` files are unmodified, hash-pinned sparse test inputs, not an
installable vLLM distribution. No upstream package is imported by the checks.

See [the task and reproduction guide](../../../docs/tasks/p0-t4-cc-candidate.md).
[candidate.json](candidate.json) records the exact before/after SHA-256 of every
file at all five patch stages, patch hashes, immutable source URLs and parents.
[NOTICE](NOTICE) describes local modifications; [LICENSE](LICENSE) preserves the
upstream license. The two original patches remain under `upstream/`.

Apply order, for the source checker only:

1. `0001-v2-copy-backport.patch`: rebase the first Apache PR59981 commit onto
   vLLM 0.32.0 without discarding newer release behavior.
2. `0002-prep-stream-ordering.patch`: original second commit, unchanged.
3. `0003-fail-closed-and-buffer-ordering.patch`: local strict selection,
   fixed diagnostics and unconditional staged-buffer handoff ordering.
4. `0004-reject-confidential-host-mapping.patch`: local fixed-error rejection
   at the generic CUDA host-mapping helper when the CC path is selected. It does
   not substitute a copy for the caller's required alias.
5. `0005-confidential-collective-guards.patch`: reject conflicting collective
   controls and AllReduce fusion construction on CC selection before backend or
   workspace setup. Shared strict selection emits fixed diagnostics only.

The separate `audit/` mHC input is unmodified and hash/blob-pinned. Tests extract
its actual support predicate to prove `ca_comm=None` prevents fused mHC selection;
they never import the complete module or run its kernels.

Only the last stage is the complete candidate; intermediate stages retain
upstream defects and must not be used as a runtime. Even the final stage is
not globally UVA-free, offload-free, multi-GPU-qualified or attested.
