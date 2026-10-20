#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Exact-image experimental application only; no engine entrypoint or admission."""

import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
    sys.exit("Isolated experimental application required.")

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import posixpath
import stat
import types

EVIDENCE = "evidence/p0/2026-10-20-official-vllm-python-chain/output.log"
EVIDENCE_SHA = "52a15aafc3044821740c5d408e5fc44f6635bea36c30f5af51f5866120810440"
EVIDENCE_SOURCE = "d033d2198866f7fdbd509e56329702942f22de14"
MODE = "exact-image-application-contract"
HOOKS = {"distutils-precedence.pth", "nvidia_cutlass_dsl_packages.pth"}


def measurement(overlay, data):
    overlay.require(overlay.digest(data) == EVIDENCE_SHA)
    report = overlay.load_json(data)
    row = report["diagnostic"]
    overlay.require(report["result"] == "fail" and report["cleanup"] == "confirmed"
                    and row["collection"] == "pass" and row["canaries"]["status"] == "pass"
                    and row["packaging"]["status"] == "pass" and len(row["worker_sources"]) == 14
                    and all(p["matches"] for p in row["worker_sources"]) and all(c["count"] == 0 for c in row["collisions"]))
    return row


def require_contract(overlay, contract):
    overlay.require(contract["application_mode"] == MODE and contract["status"] == "BLOCKED"
                    and contract["deployable"] is False and contract["image"] is None
                    and contract["base_image"] == overlay.BASE_IMAGE
                    and contract["base_revision"] == overlay.BASE_REVISION
                    and contract["official_config"]["sha256"] == overlay.PACKAGING_POLICY["config_sha256"]
                    and contract["application_measurement_sha256"] == EVIDENCE_SHA
                    and contract["diagnostic_inventory_policy"] == overlay.DIAGNOSTIC_INVENTORY_POLICY
                    and contract["diagnostic_nonhost_policy"] == overlay.CUTLASS_STUB_POLICY)
    overlay.require_packaging_contract(contract)


def import_contract(overlay, measured):
    """Data contract, deliberately not a command or executable engine launcher."""
    return {"schema": "nib.engine-import-contract/v1", "status": "BLOCKED", "executable": False,
            "measurement_sha256": EVIDENCE_SHA, "python": measured["runtime"]["python"],
            "interpreter": {key: measured["files"][0][key] for key in ("resolved", "sha256", "bytes")},
            "flags": ["-I", "-S", "-B"], "paths": measured["proposed_paths"],
            "path_sha256": overlay.digest(overlay.json_bytes(measured["proposed_paths"])),
            "worker_method": "spawn", "distributed_executor_backend": "mp", "numa_bind": False,
            "required_environment": {"VLLM_WORKER_MULTIPROC_METHOD": "spawn", "VLLM_PLUGINS": ""},
            "unsupported": ["fork", "forkserver", "numa", "ray", "external-launcher", "arbitrary-python",
                            "site-main", "addsitedir", "inherited-pythonpath", "pycache-prefix"],
            "must_precede_unpickle": ["fixed-path-bootstrap", "verified-wrapper", "sealed-spawn-preparation"],
            "engine_launch_implemented": False, "engine_workers_validated": False,
            "scope": "application-input-and-required-worker-bootstrap-not-runtime-qualification"}


def compare_file(overlay, actual, expected):
    keys = ("name", "path", "status", "stage", "reason", "resolved", "links", "bytes", "sha256", "mode", "observed")
    overlay.require({key: actual[key] for key in keys} == {key: expected[key] for key in keys})


def verify_layout(overlay, descriptor, diagnostic, contract, measured, *, filesystem_root="/"):
    """Reobserve bounded prerequisites, without running children or imports."""
    require_contract(overlay, contract)
    current = descriptor.runtime_facts()
    expected = measured["runtime"]
    # The measured alternatives name and its verified terminal are equivalent
    # invocation spellings, not permission for arbitrary interpreter aliases.
    for row in current["locations"]:
        if row["name"] in ("executable", "base_executable"):
            overlay.require(row["path"] in (measured["files"][0]["path"], measured["files"][0]["resolved"]))
            row["path"] = measured["files"][0]["path"]
    overlay.require(current == expected)
    with overlay.directory(filesystem_root) as fd:
        fs = descriptor.Filesystem(overlay, fd, contract)
        for expected_file in measured["files"] + measured["worker_sources"]:
            compare_file(overlay, fs.file(expected_file["name"], expected_file["path"]), expected_file)
        names = {}
        for expected_scan in measured["scans"]:
            row, imports = descriptor.scan(fs, diagnostic, expected_scan["name"], expected_scan["path"])
            # Wall time is the sole intentionally variable observation.
            overlay.require({k: v for k, v in row.items() if k != "elapsed_ms"}
                            == {k: v for k, v in expected_scan.items() if k != "elapsed_ms"})
            names[row["name"]] = imports
        for left, right in (("stdlib", "selected"), ("stdlib", "cutlass"), ("selected", "cutlass")):
            overlay.require(not names[left] & names[right])


def experimental_tree(overlay, fd, contract):
    require_contract(overlay, contract)
    class ExactImageTree(overlay.Tree):
        def inventory(self):
            # All other entry/depth/time/size/native/link rules are unchanged.
            return super().inventory(diagnostic_policy=overlay.DIAGNOSTIC_INVENTORY_POLICY,
                                     nonhost_policy=overlay.CUTLASS_STUB_POLICY, classified=[])
    return ExactImageTree(fd)


def inspect(overlay, tree, contract, measured, console):
    require_contract(overlay, contract)
    overlay.require(contract["import_contract"] == import_contract(overlay, measured))
    inventory = tree.inventory()
    overlay.require(overlay.digest(overlay.json_bytes(inventory)) == measured["packaging"]["inventory_sha256"])
    # The exact inventory includes both inert hook bytes. No pattern allowlist:
    # additional/modified hooks or any other file change fails the digest above.
    overlay.require({name for name in inventory if name.endswith(".pth")} == HOOKS)
    before = overlay.inspect_integrity(tree, contract, console=console, inventory=inventory, packaging=True)
    plan = before["packaging_removals"]
    overlay.require(overlay.digest(overlay.json_bytes(plan)) == measured["packaging"]["removal_plan_sha256"]
                    and len(plan) == measured["packaging"]["removal_count"]
                    and sum(row["bytes"] for row in plan) == measured["packaging"]["removal_bytes"])
    return before


def apply(overlay, tree, contract, stages, before, measured, console):
    # Compare the entire sealed inventory again before the first deletion.
    overlay.require(inspect(overlay, tree, contract, measured, console) == before)
    result = overlay.apply_verified_stages(tree, contract, stages, before, console=console, packaging=True)
    result.update(scope="exact-image-experimental-application-only", image=None,
                  application_measurement_sha256=EVIDENCE_SHA,
                  import_contract_sha256=overlay.digest(overlay.json_bytes(import_contract(overlay, measured))),
                  generic_overlay_admission="not-granted", engine_executed=False,
                  startup_hooks="unchanged-inert-only-under-isolated-no-site-bootstrap",
                  alternate_roots="unchanged-excluded-from-import-contract",
                  classified_nonhost_assets="four-exact-published-crosscompile-assets-not-host-abi")
    return result


@contextmanager
def selected_tree(overlay, contract, measured, *, filesystem_root="/"):
    locations = {row["name"]: row["path"] for row in measured["runtime"]["locations"]}
    root, scripts = locations["purelib"], locations["scripts"]
    filesystem_root = Path(filesystem_root)
    with overlay.directory(filesystem_root / root.lstrip("/")) as fd, overlay.directory(filesystem_root / scripts.lstrip("/")) as script_fd:
        tree = experimental_tree(overlay, fd, contract)
        console = (posixpath.relpath(scripts + "/vllm", root), overlay.Tree(script_fd))
        console[1].deadline = tree.deadline
        yield tree, console


def read_payload(name):
    # Bootstrap loads source bytes, never .pyc or an ordinary sibling import.
    # The trusted OS/interpreter/script startup is not attestation.
    if name not in {"contract.json", "engine_overlay.py", "engine_image_diagnostic.py", "engine_python_descriptor.py"}:
        raise ValueError("payload name rejected")
    parent = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in Path(__file__).absolute().parent.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = child
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > 2 << 20:
            os.close(fd)
            raise ValueError("payload source rejected")
        with os.fdopen(fd, "rb") as stream:
            data = stream.read((2 << 20) + 1)
            def identity(info):
                return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            if (len(data) != before.st_size or identity(before) != identity(os.fstat(stream.fileno()))
                    or identity(before) != identity(os.stat(name, dir_fd=parent, follow_symlinks=False))):
                raise ValueError("payload source changed")
            return data
    finally:
        os.close(parent)


def main():
    import json
    if len(sys.argv) not in (2, 3) or (len(sys.argv) == 3 and sys.argv[2] != "--apply"):
        raise ValueError("application arguments rejected")
    raw = read_payload("contract.json")
    if hashlib.sha256(raw).hexdigest() != sys.argv[1]:
        raise ValueError("application contract rejected")
    contract = json.loads(raw)
    modules = []
    for name in ("engine_overlay.py", "engine_image_diagnostic.py", "engine_python_descriptor.py"):
        data = read_payload(name)
        if hashlib.sha256(data).hexdigest() != contract["application_sources"][name]:
            raise ValueError("application source differs")
        module = types.ModuleType(name)
        module.__file__ = str(Path(__file__).parent / name)
        exec(compile(data, module.__file__, "exec"), module.__dict__)
        modules.append(module)
    overlay, diagnostic, descriptor = modules
    require_contract(overlay, contract)
    with overlay.directory(Path(__file__).absolute().parent) as fd:
        payload = overlay.Tree(fd)
        inventory = payload.inventory()
        overlay.require(len(inventory) <= 100 and sum(row["bytes"] for row in inventory.values()) <= overlay.MAX_CONTEXT)
        overlay.require(set(inventory) == set(contract["application_sources"]) | {"contract.json"})
        for name, expected in contract["application_sources"].items():
            overlay.require(inventory[name]["sha256"] == expected)
        measured = measurement(overlay, payload.read("measurement.json"))
        stages = [{item["path"]: payload.read(f"stages/{n}/" + item["path"]) for item in contract["files"]}
                  for n in range(1, 6)]
    verify_layout(overlay, descriptor, diagnostic, contract, measured)
    with selected_tree(overlay, contract, measured) as (tree, console):
        before = inspect(overlay, tree, contract, measured, console)
        result = apply(overlay, tree, contract, stages, before, measured, console) if len(sys.argv) == 3 else {
            "status": "BLOCKED", "result": "experimental-inspection-only", "image": None,
            "generic_overlay_admission": "not-granted", "deployable": False, "abi_validated": False,
            "hardware_validated": False, "engine_executed": False,
            "inventory_sha256": overlay.digest(overlay.json_bytes(before))}
    print(overlay.json_bytes(result).decode(), end="")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit("Experimental application failed; discard any modified image. No runtime approval.")
