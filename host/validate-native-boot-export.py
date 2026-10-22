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
        metadata, verified = {}, False
        with export.deadline_alarm() as deadline:
            # A failed build can still have a valid cleanup receipt. Without it,
            # a live worker might recreate images after our deletion check.
            if build_result != "skipped" or any(originals.values()):
                resources = export.parsed(base.read(
                    evidence.with_name(evidence.name + "-resources.json"), export.MAX_JSON))
                require(resources["schema"] == "nib.mkosi-build-resources/v1"
                        and resources["empty_verified"] is True and resources["group_removed"] is True)
            try:
                require(build_result == "success" and originals[pending.name] is None
                        and originals[bundle.name] is not None)
                metadata = verify_bundle(evidence, bundle, originals[bundle.name], source, export, deadline)
                verified = True
            except (OSError, ValueError, KeyError, TypeError):
                metadata = {}
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
            metadata["validation.json"] = export.encoded(report)
            require(set(metadata) <= set(RECEIPTS) | {"manifest.json", "validation.json"})
            # Fresh, exclusive creation; no glob, arbitrary path, or image bytes.
            base.publish(destination, metadata)
            base.verify(destination, metadata)
            same_directory(parent, destination, publication)
            require(all(present(parent, name) is None for name in originals))
        return report["result"]
    finally:
        os.close(parent)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-result", required=True, choices=("success", "failure", "cancelled", "skipped"))
    args = parser.parse_args()
    native.probe.native.preflight()
    # Use existing committed-source gates unchanged, including the fixed 42-file
    # build closure. Audit this verifier/workflow separately, not in the worker.
    export, _ = base.helper("export_verification_alarm", native.EXPORT)
    with export.deadline_alarm():
        revision, sources = native.committed_sources(True, True, True, True)
        validator_revision, validator_sources = native.tools.committed((SCRIPT, WORKFLOW))
        require(revision == validator_revision and validator_sources[SCRIPT] == SELF)
        export, publication = native.export_helpers(sources)
    result = validate(EVIDENCE, METADATA, native.source_identity(revision, sources),
                      {name: base.digest(validator_sources[name]) for name in (SCRIPT, WORKFLOW)},
                      args.build_result, export, publication)
    # Only these fixed values reach the workflow. Exit zero means metadata is
    # safe to upload, not that export passed; the workflow separately checks it.
    print(result)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        sys.exit("Private export validation rejected; metadata upload not authorized.")
