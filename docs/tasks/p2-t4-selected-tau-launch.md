# Selected Operational Trial Controller

`scripts/record-tau-selected.py` joins the existing isolated GPU lab, CPU
support-pair owner and sealed scorer. It is an explicit hardware operation on
an approved ordinary Linux/amd64 eight-B300 host, not a public serving command.

Prepare the GPU bundle from the **same clean commit** with the selected
`uncensored` checkpoint, `tau8k-eager` profile, tokenizer parity,
materialization, runtime observation and `--tau-owner`. Supply the exact
bundle receipt hash and the independently restored/hash-verified weight tree.
The CPU inputs are the four fixed files in
`scripts/prepare-tau-cpu-parity-native.py`'s `plan`; the scorer archive and
wheels are pinned by the existing tau runtime lock. Nothing downloads during
inference. Both pinned Go/Python images and the GPU image must already exist.
Before preparing the scorer payload or starting either owner, the controller
performs bounded read-only image checks: the existing selected-scorer descriptor
validator on its fixed local socket, and the CPU recorder's existing base-image
validator on the CPU owner's local Docker context. Missing images, unsupported
image-inspection commands and incompatible metadata fail before GPU startup.
This does not add a classic-store fallback, pull images or replace the original
checks repeated by each consumer at use time.

```sh
python3 -I -S -B scripts/record-tau-selected.py \
  --gpu-bundle /private/gpu-bundle --gpu-receipt-sha256 EXACT_HASH \
  --weights /private/verified-weights --cpu-inputs /private/cpu-inputs \
  --source-archive /private/tau-source.tar.gz --wheelhouse /private/wheels \
  --output /private/new-trial --deadline YYYY-MM-DDTHH:MM:SSZ
```

The explicit cutoff must be more than 22 minutes and no more than 70 minutes
away. Six minutes are reserved for owner cleanup. Original worker expirations
are never extended; a slower startup reduces the available trial time and may
fail closed. The fixed task is retail task 2, trial 0, seed 42, at concurrency
one with no retries. Selected policy limits remain unchanged.

The controller starts both owners, validates the original private readiness
packets and their pinned source identities, then passes borrowed descriptors
to the existing scorer lifecycle. It closes and reaps both children even on
failure. A pass also requires exact joins to the final CPU/GPU reports, both
finish acknowledgements, actual usage, and independently confirmed owner and
scorer container cleanup. The GPU lab's original outer `FAIL/BLOCKED` result
is preserved: an operational trial is not full model qualification.

The output contains separate original GPU, CPU and scorer evidence plus the
joined `evidence/run.json` and `evidence/output.log`. No model text or exception
messages are logged. Support-model quality limits comparison: even an evaluated
task is **not** a full tau score, a frontier-judge evaluation, a production
listing, or confidential-compute proof. GPU-free tests establish controller
behavior only; actual results require separate original hardware evidence.
