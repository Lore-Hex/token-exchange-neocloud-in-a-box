# GPU Pre-Serve Diagnostic

The explicit `--prepare-pre-serve` (four B200) and
`--prepare-pre-serve-b300` (four B300) profiles reuse the complete image
application, strict CC/readiness checks, sealed Python bootstrap and fixed
argument parser from the functional lab. They stop after checkpoint 16:
`ServeSubcommand.validate` and the bootstrap state check, before `serve.cmd`.

This boundary is visible in the pinned
[vLLM serve implementation](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/entrypoints/cli/serve.py)
and its [pre-load argument checks](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/entrypoints/launchers/cli_args.py#L476-L517).
Imports can inspect devices and initialize native dependencies. This is not a
test of model loading, kernels, inference, model accuracy or attestation.

No weights are mounted and `--weights` is rejected for this mode. There are no
HTTP probes or synthetic requests. The container retains network-none, no
published ports, dropped capabilities and the existing owned watchdog/cleanup.
It is limited to 8 GiB RAM, no additional swap and two CPUs. The entry wait is
300 seconds and the caller's entire run must end within 18 minutes. Source
and hardware checks are unchanged; missing GPUs never select CPU mode.

Successful `entry_pass:true` requires exit zero, all 16 checkpoints, no exception
packet, unchanged hardware observations, an application receipt and stopped
entry process. `functional_pass`, `confidential` and `deployable` stay false.
The host independently verifies the new `nib.deepseek-lab-pre-serve/v1` result.
Full inference profiles still require the complete hash-verified model pack.

Example preparation and bounded execution on an already approved test host:

```sh
python3 -I -S host/record-deepseek-lab.py \
  --prepare-pre-serve-b300 /absolute/private/bundle
python3 -I -S /absolute/private/bundle/record-deepseek-lab.py \
  --run /absolute/private/bundle --receipt-sha256 RECEIPT_SHA256 \
  --output /absolute/private/evidence --deadline APPROVED_UTC_CUTOFF
```

Verification: GPU lifecycle unit tests and `make check`. No actual pre-serve
pass is claimed until a committed record from this producer is available.
