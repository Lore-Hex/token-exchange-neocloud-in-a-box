# SPDX-License-Identifier: Apache-2.0
"""Freeze quality-smoke inputs offline; never download, infer, or score a model."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat

ROOT = Path(__file__).resolve().parents[1]
PINS = ROOT / "bench/quality-upstream-pins.json"
COUNT = 200
SEED = "nib-quality-smoke-v1"
OUTPUT_TOKENS = 2048


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def indices(name, revision, total):
    """Stable uniform subset, independent of answers, models, and Python RNG."""
    if name not in {"gsm8k", "mmlu_pro"} or type(total) is not int or not COUNT <= total <= 20000:
        raise ValueError("invalid dataset population")
    ranked = sorted(range(total), key=lambda index: digest(
        f"{SEED}\0{name}\0{revision}\0{index}".encode("ascii")))
    return sorted(ranked[:COUNT])


def checked_file(root, relative, expected, size=None):
    path = root / relative
    if root.is_symlink() or not root.is_dir():
        raise ValueError("invalid source root")
    for parent in path.relative_to(root).parents:
        if (root / parent).is_symlink():
            raise ValueError("symlink source parent")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 8 * 1024 * 1024:
        raise ValueError("invalid source member")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise ValueError("source member changed")
        raw = stream.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError("source member grew")
    if (size is not None and len(raw) != size) or digest(raw) != expected:
        raise ValueError("source member mismatch")


def prepare(harness_root, dataset_root, max_output_tokens):
    if type(max_output_tokens) is not int or max_output_tokens < OUTPUT_TOKENS:
        raise ValueError("output capacity below the frozen protocol")
    pins_raw = PINS.read_bytes()
    pins = json.loads(pins_raw)
    for name, expected in pins["harness"]["files"].items():
        checked_file(harness_root, name, expected)
    datasets = {}
    for name, spec in pins["datasets"].items():
        for relative, expected in spec["files"].items():
            checked_file(dataset_root / name, relative, expected["sha256"], expected["bytes"])
        datasets[name] = {
            "repository": spec["repository"], "revision": spec["revision"],
            "configuration": spec["configuration"], "test_rows": spec["test_rows"],
            "fewshot_split": spec["fewshot_split"],
            "global_test_indices": indices(name, spec["revision"], spec["test_rows"]),
        }
    return {
        "schema": "nib-quality-protocol/v1", "pins_sha256": digest(pins_raw),
        "harness_revision": pins["harness"]["revision"], "datasets": datasets,
        "settings": {"num_fewshot": 5, "temperature": 0, "do_sample": False,
                     "max_gen_toks": OUTPUT_TOKENS, "fewshot_seed": 1234,
                     "samples_per_dataset": COUNT, "log_samples": False,
                     "cache_requests": False, "use_cache": None},
        "inputs_verified": True, "inference_performed": False,
        "quality_qualified": False, "confidential": False,
        "context_fit_verified": False, "runtime_dependency_closure_verified": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--harness-root", type=Path, required=True)
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--max-output-tokens", type=int, required=True)
    args = parser.parse_args()
    try:
        report = prepare(args.harness_root, args.dataset_root, args.max_output_tokens)
    except (OSError, ValueError, KeyError, TypeError):
        print(json.dumps({"schema": "nib-quality-protocol/v1", "inputs_verified": False,
                          "inference_performed": False, "quality_qualified": False}))
        return 1
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
