#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Prepare a private BLOCKED engine experiment context; never build or launch it."""

import sys

if __name__ == "__main__" and not sys.flags.isolated:
    sys.exit("Use python3 -I scripts/prepare-engine-overlay.py")

import hashlib
import json
import os
from pathlib import Path
import stat
import types

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = "scripts/prepare-engine-overlay.py"
INSPECTOR = "scripts/engine_overlay.py"
PINS = "scripts/engine-overlay-pins.json"
TEST = "scripts/test_engine_overlay.py"
BOOTSTRAP = Path(__file__).read_bytes()
MODULE_HASHES = {
    "overlay_candidate": "5d14105a8f6fa0e5239a6e71a2c79f7c1b07304ced2352accaec4a82aea6075d",
    "overlay_inspector": "1f42e2e90dbca12d5173b28daffb3be6964126a63f1d0690b719891ba73afcde",
}


def source_module(name, path):
    module = types.ModuleType(name)
    module.__file__ = str(path)
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != MODULE_HASHES[name]:
        raise ValueError("offline helper source differs")
    exec(compile(data, str(path), "exec"), module.__dict__)
    return module, data


try:
    checker, CHECKER_SOURCE = source_module("overlay_candidate", ROOT / "scripts/check-engine-candidate.py")
    inspector, INSPECTOR_SOURCE = source_module("overlay_inspector", ROOT / INSPECTOR)
except Exception:
    if __name__ == "__main__":
        sys.exit("Offline helper validation failed.")
    raise


def inputs(root=ROOT):
    revision, bundle = checker.committed_bundle(root)
    checker.validate_bootstrap(bundle)
    extra = {}
    for name in (SCRIPT, INSPECTOR, PINS, TEST):
        entry = checker.git(root, "ls-tree", revision, "--", name).decode().split()
        inspector.require(len(entry) == 4 and entry[0] in ("100644", "100755") and entry[1] == "blob"
                          and entry[3] == name)
        inspector.require(int(checker.git(root, "cat-file", "-s", entry[2])) <= checker.LIMIT)
        extra[name] = checker.git(root, "cat-file", "blob", entry[2])
    inspector.require(extra[SCRIPT] == BOOTSTRAP and extra[INSPECTOR] == INSPECTOR_SOURCE
                      and bundle[checker.SCRIPT] == CHECKER_SOURCE)
    return revision, bundle, extra


def launch_plan():
    # Explicit argv only. No host inputs, model path, device selection, or launcher.
    # Supported spellings/fields are pinned in engine-overlay-pins.json.
    return {"status": "BLOCKED", "executable": False, "hardware_validated": False,
            "requires": ["verified-local-model", "approved-device-selection", "finite-runtime-budget",
                         "native-abi-and-topology-check", "reviewed-loopback-synthetic-harness"],
            "isolation": {"network": "none", "publish_ports": False, "production": False},
            "fixed_engine_arguments": ["--host", "127.0.0.1", "--tensor-parallel-size", "4",
                "--data-parallel-size", "1", "--no-enable-expert-parallel", "--disable-custom-all-reduce",
                "--offload-backend", "auto", "--cpu-offload-gb", "0", "--offload-group-size", "0",
                "--engram-config", '{"cpu_offload":false}',
                "--compilation-config", '{"pass_config":{"fuse_allreduce_rms":false}}',
                "--no-enable-log-requests", "--no-enable-log-outputs", "--disable-uvicorn-access-log"],
            "fixed_engine_environment": {"VLLM_CONFIDENTIAL_COMPUTE": "1",
                "VLLM_ALLREDUCE_USE_SYMM_MEM": "0", "VLLM_ALLREDUCE_USE_FLASHINFER": "0",
                "VLLM_ALLREDUCE_USE_FLASHINFER_PCIE_IPC": "0", "VLLM_USE_NCCL_SYMM_MEM": "0",
                "VLLM_ENABLE_GEMM_RS": "0", "VLLM_USE_RUST_FRONTEND": "0",
                "VLLM_NO_USAGE_STATS": "1", "VLLM_DO_NOT_TRACK": "1", "VLLM_PLUGINS": ""},
            "must_remain_absent": ["kv_offloading_size", "kv_transfer_config", "ec_transfer_config",
                                   "speculative_config", "user_arguments", "inherited_environment"],
            "selection_meaning": "software-copy-path-only-not-attestation",
            "scope": "partial-sealed-controls-not-a-runnable-command"}


def prepare_files(revision, bundle, extra):
    stages = checker.verify_stages(bundle)
    manifest, local = checker.metadata(bundle)
    pins = json.loads(extra[PINS])
    inspector.require(pins["base_image"] == inspector.BASE_IMAGE and pins["base_revision"] == checker.BASE)
    contract = {**pins, "schema": "nib.engine-overlay-input/v1", "status": "BLOCKED",
                "deployable": False, "image": None, "git_sha": revision,
                "candidate_bundle_sha256": checker.bundle_digest(bundle), "files": manifest["files"],
                "source_bundle_sha256": checker.bundle_digest({**bundle, **extra})}
    payload = {"inspector.py": extra[INSPECTOR], "contract.json": inspector.json_bytes(contract),
               "LICENSE": local["LICENSE"], "NOTICE": local["NOTICE"]}
    for n, stage in enumerate(stages, 1):
        payload.update({f"stages/{n}/" + name: data for name, data in stage.items()})
    inspector.require(sum(map(len, payload.values())) <= inspector.MAX_CONTEXT)
    payload_hash = checker.bundle_digest(payload)
    dockerfile = ("# BLOCKED experiment only; this recipe is not executed by the preparer.\n"
                  "FROM " + inspector.BASE_IMAGE + "\n"
                  "COPY payload /opt/nib-engine-overlay/payload\n"
                  "RUN --network=none python3 -I -S /opt/nib-engine-overlay/payload/inspector.py "
                  "--context /opt/nib-engine-overlay/payload --payload-sha256 " + payload_hash + " --apply\n"
                  'ENTRYPOINT ["python3", "-I", "-S", "-c", "raise SystemExit(\'BLOCKED: no runtime launcher\')"]\n'
                  "CMD []\n").encode()
    files = {"payload/" + name: data for name, data in payload.items()}
    files.update({"Dockerfile": dockerfile, "launch-plan.json": inspector.json_bytes(launch_plan())})
    receipt = {"schema": "nib.engine-overlay-preparation/v1", "status": "BLOCKED", "deployable": False,
               "image": None, "image_origin_verified": False, "abi_validated": False,
               "hardware_validated": False, "installed_wheel_inspected": False,
               "git_sha": revision, "source_bundle_sha256": contract["source_bundle_sha256"],
               "candidate_bundle_sha256": contract["candidate_bundle_sha256"],
               "payload_sha256": payload_hash, "context_sha256": checker.bundle_digest(files),
               "base_image": inspector.BASE_IMAGE, "files": len(files), "bytes": sum(map(len, files.values()))}
    files["receipt.json"] = inspector.json_bytes(receipt)
    return files, receipt


def publish(destination, files):
    destination = Path(destination)
    inspector.require(destination.is_absolute() and destination.name not in ("", ".", ".."))
    with inspector.directory(destination.parent) as parent:
        info = os.fstat(parent)
        inspector.require(not info.st_mode & 0o022 or info.st_mode & stat.S_ISVTX)
        os.mkdir(destination.name, 0o700, dir_fd=parent)  # Exclusive; failures remain unaccepted, never overwritten.
        fd = os.open(destination.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            for name, data in sorted(files.items(), key=lambda row: (row[0] == "receipt.json", row[0])):
                current = os.dup(fd)
                try:
                    parts = inspector.safe_name(name)
                    for part in parts[:-1]:
                        try:
                            os.mkdir(part, 0o700, dir_fd=current)
                        except FileExistsError:
                            pass
                        child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
                        os.close(current)
                        current = child
                    output = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=current)
                    with os.fdopen(output, "wb") as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.fsync(current)
                finally:
                    os.close(current)
            os.fsync(fd)
            os.fsync(parent)
        finally:
            os.close(fd)


def main():
    parser = inspector.QuietParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    files, receipt = prepare_files(*inputs())
    publish(args.output, files)
    print(inspector.json_bytes(receipt).decode(), end="")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit("Engine overlay preparation failed. No image was built or launched.")
