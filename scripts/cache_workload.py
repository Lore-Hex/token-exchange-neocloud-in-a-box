# SPDX-License-Identifier: Apache-2.0
"""Offline exact-token preparation for the fixed synthetic cache experiment."""

import argparse
import json
from pathlib import Path
import re
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cache_probe as probe
import tau_external_tokenizer as tokenizer

require, worker = probe.require, probe.worker
PREPARE_SECONDS = 180


def prepare(source_root, model_root, wheel, *, deadline, candidate="selected"):
    """Hash-verified local tokenizer only; never import an engine or send a request."""
    require(threading.current_thread() is threading.main_thread()
            and probe.meter.number(deadline) and deadline > time.monotonic())
    identity = probe.identity.fields(candidate)
    deadline = min(deadline, time.monotonic() + PREPARE_SECONDS)
    with worker.alarm(deadline):
        counter = tokenizer.Counter(source_root, model_root, wheel,
                                    context_tokens=tokenizer.CACHE_PREPARE_CONTEXT)
        try:
            require(type(counter.binding) is str and re.fullmatch("[0-9a-f]{64}", counter.binding))
            binding = (probe.identity.tokenizer_fields(counter.binding) if candidate == "original"
                       else {"tokenizer_binding_sha256": counter.binding})
            rows = []
            for target, output in probe.SHAPES.items():
                def measure(padding):
                    require(time.monotonic() < deadline)
                    body = probe.request(padding, target, "0" * 64, candidate=candidate)
                    ids = counter.tokens(body)
                    require(type(ids) is list and 0 < len(ids) <= tokenizer.CACHE_PREPARE_CONTEXT
                            and all(type(token) is int and 0 <= token < 2**31 for token in ids))
                    return len(ids), probe.workload.digest(ids)

                small, _ = measure(64)
                large, _ = measure(128)
                require(small < target and large > small)
                center = 64 + (target - small) * 64 // (large - small)
                # BPE need not be linear or monotone. The estimate only selects
                # a bounded search; only an exactly counted candidate is saved.
                candidates = [center] + [center + sign * delta
                    for delta in range(1, 33) for sign in (-1, 1)]
                for padding in candidates:
                    if not 0 <= padding <= probe.workload.MAX_PADDING:
                        continue
                    count, digest = measure(padding)
                    if count == target:
                        rows.append({"padding": padding, "prompt_tokens": count,
                                     "max_output_tokens": output, "tokens_sha256": digest,
                                     "messages_sha256": probe.workload.digest(probe.workload.messages(3, padding))})
                        break
                else:
                    require(False)
            require(time.monotonic() < deadline)
            return {"schema": probe.identity.schema("cache-workload", candidate), **identity, **binding,
                    "cases": rows, "inference_executed": False, "runtime_parity_verified": False,
                    "billing_reconciled": False, "tariff_change_justified": False}
        finally:
            counter.close()


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("cache workload preparation rejected")


def main(argv=None):
    try:
        parser = Parser(description=__doc__)
        parser.add_argument("--vllm-source", required=True)
        parser.add_argument("--tokenizer", required=True)
        parser.add_argument("--tokenizer-wheel", required=True)
        parser.add_argument("--candidate", choices=("selected", "original"), default="selected")
        args = parser.parse_args(argv)
        row = prepare(args.vllm_source, args.tokenizer, args.tokenizer_wheel,
                      deadline=time.monotonic() + PREPARE_SECONDS, candidate=args.candidate)
        print(json.dumps(row, sort_keys=True, separators=(",", ":")))
        return 0
    except (Exception, KeyboardInterrupt, worker.Abort):
        print("cache workload preparation rejected", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
