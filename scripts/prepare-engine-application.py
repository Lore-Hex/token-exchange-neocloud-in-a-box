#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Prepare exact-image application inputs; never build, execute or publish images."""

import sys

if __name__ == "__main__" and not sys.flags.isolated:
    sys.exit("Use python3 -I scripts/prepare-engine-application.py")

import hashlib
import os
from pathlib import Path
import stat
import types

ROOT = Path(__file__).absolute().parent.parent
SCRIPT = "scripts/prepare-engine-application.py"
APPLICATION = "scripts/engine_application.py"
TEST = "scripts/test_engine_application.py"
RECORDER_SHA = "4bd6f1abc7dd2c992c5559bad7b9d38d8ac01d5decef3f0e106b250aa0676935"
APPLICATION_SHA = "ce69e94d64434294f674c4be66e1a41fe8a08864535e6dbe242db365559b7406"


def bootstrap_read(path):
    parent = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        path = Path(path)
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError("source rejected")
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = child
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > 2 << 20:
            os.close(fd)
            raise ValueError("source rejected")
        with os.fdopen(fd, "rb") as stream:
            data = stream.read((2 << 20) + 1)
            def identity(info):
                return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            if (len(data) != before.st_size or identity(before) != identity(os.fstat(stream.fileno()))
                    or identity(before) != identity(os.stat(path.name, dir_fd=parent, follow_symlinks=False))):
                raise ValueError("source changed")
            return data
    except OSError:
        raise ValueError("source rejected") from None
    finally:
        os.close(parent)


try:
    BOOTSTRAP = bootstrap_read(Path(__file__).absolute())
    path = ROOT / "scripts/record-engine-image.py"
    RECORDER_SOURCE = bootstrap_read(path)
    if hashlib.sha256(RECORDER_SOURCE).hexdigest() != RECORDER_SHA:
        raise ValueError("source differs")
    recorder = types.ModuleType("application_recorder")
    recorder.__file__ = str(path)
    exec(compile(RECORDER_SOURCE, str(path), "exec"), recorder.__dict__)
    application, APPLICATION_SOURCE = recorder.load("application_contract", ROOT / APPLICATION, APPLICATION_SHA)
except Exception:
    if __name__ == "__main__":
        sys.exit("Experimental preparation bootstrap rejected.")
    raise

overlay, checker = recorder.overlay, recorder.checker


def inputs(root=ROOT):
    revision, bundle, extra = recorder.inputs(root)
    for name in (SCRIPT, APPLICATION, TEST, application.EVIDENCE):
        entry = checker.git(root, "ls-tree", revision, "--", name).decode().split()
        overlay.require(len(entry) == 4 and entry[:2] == ["100644", "blob"] and entry[3] == name)
        overlay.require(int(checker.git(root, "cat-file", "-s", entry[2])) <= checker.LIMIT)
        extra[name] = checker.git(root, "cat-file", "blob", entry[2])
    overlay.require(extra[SCRIPT] == BOOTSTRAP and extra[APPLICATION] == APPLICATION_SOURCE
                    and extra[recorder.SCRIPT] == RECORDER_SOURCE)
    return revision, bundle, extra


def prepare_files(revision, bundle, extra):
    measured = application.measurement(overlay, extra[application.EVIDENCE])
    files, contract = recorder.payload_files(revision, bundle, extra, mode="python-descriptor")
    # Existing verifier constructs all stages from committed patch bytes.
    stages = checker.verify_stages(bundle)
    payload = {name: files[name] for name in ("engine_overlay.py", "engine_image_diagnostic.py", "engine_python_descriptor.py")}
    payload.update({"application.py": extra[APPLICATION], "measurement.json": extra[application.EVIDENCE]})
    for n, stage in enumerate(stages, 1):
        payload.update({f"stages/{n}/" + name: data for name, data in stage.items()})
    _, local = checker.metadata(bundle)
    payload.update({"LICENSE": local["LICENSE"], "NOTICE": local["NOTICE"]})
    contract.update(application_mode=application.MODE, application_measurement_sha256=application.EVIDENCE_SHA,
                    import_contract=application.import_contract(overlay, measured),
                    source_bundle_sha256=checker.bundle_digest({**bundle, **extra}),
                    application_sources={name: overlay.digest(data) for name, data in payload.items()})
    application.require_contract(overlay, contract)
    payload["contract.json"] = overlay.json_bytes(contract)
    overlay.require(len(payload) <= 100 and sum(map(len, payload.values())) <= overlay.MAX_CONTEXT)
    payload_hash = checker.bundle_digest(payload)
    contract_hash = overlay.digest(payload["contract.json"])
    result = {"payload/" + name: data for name, data in payload.items()}
    result["import-contract.json"] = overlay.json_bytes(application.import_contract(overlay, measured))
    result["launch-plan.json"] = overlay.json_bytes(recorder.prep.launch_plan())
    result["Dockerfile"] = (
        "# Experimental application only. Build/run requires separate review and approval.\n"
        "FROM " + overlay.BASE_IMAGE + "\n"
        "COPY payload /opt/nib-engine-application\n"
        "RUN --network=none /usr/bin/python3 -I -S -B /opt/nib-engine-application/application.py " + contract_hash + " --apply\n"
        'ENTRYPOINT ["/usr/bin/python3.12", "-I", "-S", "-B", "-c", "raise SystemExit(\'BLOCKED: no engine launcher\')"]\n'
        "CMD []\n").encode()
    receipt = {"schema": "nib.engine-application-preparation/v1", "status": "BLOCKED", "deployable": False,
               "image": None, "git_sha": revision, "base_image": overlay.BASE_IMAGE,
               "config_sha256": contract["official_config"]["sha256"],
               "source_bundle_sha256": contract["source_bundle_sha256"],
               "candidate_bundle_sha256": contract["candidate_bundle_sha256"],
               "measurement_sha256": application.EVIDENCE_SHA, "measurement_git_sha": application.EVIDENCE_SOURCE,
               "payload_sha256": payload_hash, "context_sha256": checker.bundle_digest(result),
               "engine_executed": False, "image_built": False, "application_performed": False,
               "abi_validated": False, "hardware_validated": False,
               "scope": "experimental-application-inputs-not-runtime-approval"}
    result["receipt.json"] = overlay.json_bytes(receipt)
    return result, receipt


def main():
    parser = overlay.QuietParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    files, receipt = prepare_files(*inputs())
    recorder.prep.publish(args.output, files)
    print(overlay.json_bytes(receipt).decode(), end="")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit("Experimental application preparation failed. No image or engine was executed.")
