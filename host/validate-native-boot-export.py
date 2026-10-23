#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Verify then discard fixed private boot artifacts; expose bounded JSON only."""
import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode):
    sys.exit("Use python3.10 -I -S -B host/validate-native-boot-export.py")

import argparse
import os
from pathlib import Path
import runpy
import stat
import subprocess
import time
import types

ROOT = Path(__file__).absolute().parent.parent
SCRIPT = "host/validate-native-boot-export.py"
WORKFLOW = ".github/workflows/mkosi-native-build.yml"
native = types.SimpleNamespace(**runpy.run_path(str(ROOT / "host/build-mkosi-native.py")))
base, require = native.base, native.require
EVIDENCE = ROOT / "build/native-install/evidence"
METADATA = ROOT / "build/native-install/export-validation"
RECEIPTS = ("run.json", "result.json", "resources.json")
SELF = base.read(ROOT / SCRIPT, 1 << 20)
BUILD_STAGES = ("preparation", "guest-helper-compilation", "tools-materialization", "payload-publication",
                "upstream-modes-and-links", "installation", "complete")
WORKER_STAGES = ("preflight", "namespaces", "pid", "ownership", "configuration", "installation", "inventory",
                 "driver-build", "runtime-policy", "appraisal-cli", "guest-network", "verity-root", "boot-uki")
BUILD_FAILURES = (None, "timeout", "process-exit", "startup-error", "invalid-result-or-cleanup",
                  "unshare-permission-denied", "unshare-missing-path", "unshare-invalid-argument", "unshare-exit",
                  *("worker-" + stage for stage in native.probe.FAILURE_STAGES))
# These existing detailed fields are recognized but never copied into the public
# projection. In particular, no trace, process list, path or tool output survives.
BUILD_DETAILS = {"mapping_helper_hashes", "prepared_receipt_sha256", "tools_receipt_sha256"}
WORKER_DETAILS = {"driver_build", "runtime_policy", "appraisal_cli_diagnostics", "appraisal_cli",
                  "guest_network", "boot_input_diagnostics", "artifact_stage", "boot_uki", "verity_root",
                  "failure_frames", "private_process_states", "output_limit_stream"}


def enum(value, choices):
    require((value is None and None in choices) or (type(value) is str and value in choices))
    return value


def number(value, low=0, high=(1 << 63) - 1):
    require(value is None or (type(value) is int and low <= value <= high))
    return value


def fields(value, required, optional=()):
    require(type(value) is dict and set(required) <= set(value) <= set(required) | set(optional))


def receipt(path, export):
    observed = False
    try:
        with base.directory(path.parent) as fd:
            before = os.stat(path.name, dir_fd=fd, follow_symlinks=False)
            observed = True
            require(before.st_uid == os.getuid() and not stat.S_IMODE(before.st_mode) & 0o022)
            raw = base.read_at(fd, path.name, export.MAX_JSON)
            require(export.stamp(before) == export.stamp(os.stat(path.name, dir_fd=fd, follow_symlinks=False)))
    except FileNotFoundError:
        require(not observed)
        return None, None
    return raw, export.parsed(raw)


def failure_receipt(evidence, source, resources, export):
    """Project only finite observations; never execute or copy failed artifacts."""
    result = {"schema": "nib.native-build-failure/v1", "git_sha": source["git_sha"],
              "installation": None, "resources": None}
    if resources is not None:
        fields(resources, ("schema", "result", "limits", "limits_read_back", "empty_verified",
                           "group_removed", "child_exit_code"),
               ("oom_kill", "memory_peak_bytes", "populated_before_cleanup"))
        require(resources["schema"] == "nib.mkosi-build-resources/v1"
                and resources["limits"] == native.resources.LIMITS
                and resources["empty_verified"] is True and resources["group_removed"] is True
                and type(resources["limits_read_back"]) is bool)
        result["resources"] = {"result": enum(resources["result"], ("pass", "fail")),
            "limits_read_back": resources["limits_read_back"],
            "child_exit_code": number(resources["child_exit_code"], -64, 255),
            "oom_kill": number(resources.get("oom_kill")),
            "memory_peak_bytes": number(resources.get("memory_peak_bytes")),
            "populated_before_cleanup": number(resources.get("populated_before_cleanup"), 0, 1)}
    raw, build = receipt(evidence / "result.json", export)
    if raw is None:
        # Killed/early workers may never publish a report. Absence is not a
        # successful stage, zero OOM count, or an inferred failure diagnosis.
        return result
    fields(build, ("schema", "git_sha", "result", "stage", "worker", "payload_removed",
                   "process_cleanup_confirmed", "failure_kind", "bootable", "deployable"), BUILD_DETAILS)
    require(build["schema"] == "nib.mkosi-installation/v1" and build["git_sha"] == source["git_sha"]
            and build["bootable"] is False and build["deployable"] is False
            and type(build["payload_removed"]) is bool and type(build["process_cleanup_confirmed"]) is bool)
    observed = {"result": enum(build["result"], ("pass", "fail")),
                "stage": enum(build["stage"], BUILD_STAGES),
                "failure_kind": enum(build["failure_kind"], BUILD_FAILURES),
                "payload_removed": build["payload_removed"],
                "process_cleanup_confirmed": build["process_cleanup_confirmed"], "worker": None,
                "receipt": {"bytes": len(raw), "sha256": base.digest(raw)}}
    worker = build["worker"]
    if worker is not None:
        fields(worker, ("schema", "result", "stage", "installed_packages", "installer_exit_code", "failure_kind",
                        "package_installation_executed", "configured_state_verified", "dkms_built",
                        "reproducibility_verified", "bootable", "deployable"), WORKER_DETAILS)
        require(worker["schema"] == "nib.mkosi-install-worker/v1"
                and all(worker[key] is False for key in ("reproducibility_verified", "bootable", "deployable"))
                and all(type(worker[key]) is bool for key in ("configured_state_verified", "dkms_built"))
                and (worker["package_installation_executed"] is None
                     or type(worker["package_installation_executed"]) is bool)
                and type(worker["installed_packages"]) is int and 0 <= worker["installed_packages"] <= 212)
        observed["worker"] = {"result": enum(worker["result"], ("pass", "fail")),
            "stage": enum(worker["stage"], WORKER_STAGES),
            "failure_kind": enum(worker["failure_kind"], (None, "timeout", "process-exit", "output-limit", "rejected")),
            "installed_packages": worker["installed_packages"],
            "installer_exit_code": number(worker["installer_exit_code"], -64, 255)}
    result["installation"] = observed
    return result


def present(parent, name):
    try:
        return os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return None


def same_directory(parent, path, publication):
    check = publication.open_parent(path)
    try:
        require((os.fstat(check).st_dev, os.fstat(check).st_ino)
                == (os.fstat(parent).st_dev, os.fstat(parent).st_ino))
    finally:
        os.close(check)


def committed_validator(revision):
    checked_revision, sources = native.tools.committed((SCRIPT,))
    require(checked_revision == revision and sources[SCRIPT] == SELF)
    # The generic source gate deliberately rejects leading-dot paths. Audit
    # this one fixed workflow separately without widening that gate.
    def git(*args):
        result = base.processes.execute_process_group(
            ["git", "-c", "core.hooksPath=" + os.devnull, "-C", str(ROOT), *args],
            env=base.GIT_ENV, timeout=30, max_output_bytes=65536, stdin=subprocess.DEVNULL)
        result.check_returncode()
        return result.stdout
    row = git("ls-tree", revision, "--", WORKFLOW).decode("ascii").split()
    require(len(row) == 4 and row[:2] == ["100644", "blob"] and row[3] == WORKFLOW)
    raw = git("cat-file", "blob", row[2])
    require(raw == base.read(ROOT / WORKFLOW, 65536))
    return {SCRIPT: base.digest(sources[SCRIPT]), WORKFLOW: base.digest(raw)}


def verify_bundle(evidence, bundle, original, source, export, deadline):
    with base.directory(bundle) as fd:
        info = os.fstat(fd)
        require((info.st_dev, info.st_ino) == (original.st_dev, original.st_ino)
                and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700
                and set(os.listdir(fd)) == set(export.FILES) | set(RECEIPTS) | {"manifest.json"})
        metadata = {}
        for name in (*RECEIPTS, "manifest.json"):
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o600)
            metadata[name] = base.read_at(fd, name, export.MAX_JSON)
        manifest = export.parsed(metadata["manifest.json"])
        staged = export.validate_proof(export.parsed(metadata["result.json"]), source)
        run = export.parsed(metadata["run.json"])
        require(run["result"] == "pass" and run["git_sha"] == source["git_sha"]
                and run["hardware_validated"] is False)
        export.validate_resources(export.parsed(metadata["resources.json"]))
        expected = {"schema": "nib.private-boot-bundle/v1", "source": source,
            "root_hash": staged["root_hash"], "files": staged["files"],
            "receipts": {name: {"bytes": len(metadata[name]), "sha256": base.digest(metadata[name])}
                         for name in RECEIPTS},
            "local_only": True, "signed": False, "boot_verified": False,
            "reproducibility_verified": False, "confidentiality_verified": False}
        require(manifest == expected and manifest["local_only"] is True
                and all(manifest[k] is False for k in (
                    "signed", "boot_verified", "reproducibility_verified", "confidentiality_verified")))
        for name in RECEIPTS:
            path = (evidence.with_name(evidence.name + "-resources.json") if name == "resources.json"
                    else evidence / name)
            require(base.read(path, export.MAX_JSON) == metadata[name])
        for name, limit in export.FILES.items():
            require(stat.S_IMODE(os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode) == 0o600)
            export.transfer(bundle / name, manifest["files"][name], limit, deadline, base.directory)
        return metadata


def validate(evidence, destination, source, validator_source, build_result, export, publication):
    """Only fixed JSON survives. Unknown cleanup never authorizes an upload."""
    export.source_identity(source)
    export.source_identity({"git_sha": source["git_sha"], "sources": validator_source})
    require(build_result in ("success", "failure", "cancelled", "skipped")
            and destination.parent == evidence.parent and set(validator_source) == {SCRIPT, WORKFLOW})
    pending, bundle = export.paths(evidence)
    parent = publication.open_parent(bundle)
    try:
        require(present(parent, destination.name) is None)
        originals = {path.name: present(parent, path.name) for path in (pending, bundle)}
        # Do not delete a replacement, a linked directory, or an unowned tree.
        for info in originals.values():
            require(info is None or (stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
                                    and stat.S_IMODE(info.st_mode) == 0o700))
        metadata, verified, failure = {}, False, None
        with export.deadline_alarm() as deadline:
            # A failed build can still have a valid cleanup receipt. Without it,
            # a live worker might recreate images after our deletion check.
            resources = None
            if build_result != "skipped" or any(originals.values()):
                resources = export.parsed(base.read(
                    evidence.with_name(evidence.name + "-resources.json"), export.MAX_JSON))
                require(resources["schema"] == "nib.mkosi-build-resources/v1"
                        and resources["empty_verified"] is True and resources["group_removed"] is True)
            try:
                try:
                    require(build_result == "success" and originals[pending.name] is None
                            and originals[bundle.name] is not None)
                    metadata = verify_bundle(evidence, bundle, originals[bundle.name], source, export, deadline)
                    verified = True
                except (OSError, ValueError, KeyError, TypeError):
                    metadata = {}
                if build_result != "success":
                    failure = failure_receipt(evidence, source, resources, export)
            finally:
                for name, info in originals.items():
                    if info is not None:
                        base.inputs.remove_flat_directory(parent, name, info)
            same_directory(parent, bundle, publication)
            require(all(present(parent, name) is None for name in originals))
            os.fsync(parent)
            report = {"schema": "nib.native-private-export-validation/v1",
                "result": "pass" if verified else "fail", "source": source,
                "validator_source": validator_source, "build_result": build_result,
                "artifacts_verified": verified, "private_images_removed": True,
                "metadata_only": True, "signed": False, "boot_verified": False,
                "reproducibility_verified": False, "confidentiality_verified": False}
            if verified:
                manifest = export.parsed(metadata["manifest.json"])
                report.update(root_hash=manifest["root_hash"], files=manifest["files"])
            if failure is not None:
                report["failure"] = failure
            metadata["validation.json"] = export.encoded(report)
            require(len(metadata["validation.json"]) <= export.MAX_JSON)
            require(set(metadata) <= set(RECEIPTS) | {"manifest.json", "validation.json"}
                    and time.monotonic() < deadline)
            # Fresh, exclusive creation; no glob, arbitrary path, or image bytes.
            base.publish(destination, metadata)
            base.verify(destination, metadata)
            same_directory(parent, destination, publication)
            require(all(present(parent, name) is None for name in originals) and time.monotonic() < deadline)
        return report["result"]
    finally:
        os.close(parent)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-result", required=True, choices=("success", "failure", "cancelled", "skipped"))
    parser.add_argument("--guest-network", action="store_true")
    args = parser.parse_args()
    native.probe.native.preflight()
    # Audit the explicitly selected fixed profile, never adopt a profile from
    # the artifact. The verifier/workflow remain outside the worker closure.
    export, _ = base.helper("export_verification_alarm", native.EXPORT)
    with export.deadline_alarm():
        revision, sources = native.committed_sources(True, True, True, True,
            **({"guest_network": True} if args.guest_network else {}))
        validator_source = committed_validator(revision)
        export, publication = native.export_helpers(sources)
    result = validate(EVIDENCE, METADATA, native.source_identity(revision, sources,
                      **({"guest_network": True} if args.guest_network else {})),
                      validator_source, args.build_result, export, publication)
    # Only these fixed values reach the workflow. Exit zero means metadata is
    # safe to upload, not that export passed; the workflow separately checks it.
    print(result)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        sys.exit("Private export validation rejected; metadata upload not authorized.")
