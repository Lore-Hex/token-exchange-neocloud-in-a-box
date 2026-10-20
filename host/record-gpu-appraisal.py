#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Record committed lab GPU appraisal; never publish identities or raw tokens."""

import sys

if __name__ == "__main__" and not (
        sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
    sys.exit("Use python3 -I -S -B host/record-gpu-appraisal.py")

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import types

ROOT = Path(__file__).absolute().parent.parent
RECORDER = "host/record-gpu-appraisal.py"
APPRAISAL = "host/attest-lab-gpus.py"
PROBE = "host/probe.py"
SHARED = "host/record-probe.py"
PINS = {
    SHARED: "77858e523ae744bc585b28aa68cc759afbdc542dd972466185f52e3a699c40e2",
    APPRAISAL: "d27f1130c0d79dfd379d46a9da30ac1578aa504c2cfd6db38e780f649233ec4d",
}
LIMIT = 65536


def require(value):
    if not value:
        raise ValueError("appraisal-recording-rejected")


def source_bytes(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                and 0 < before.st_size <= LIMIT)
        data = stream.read(LIMIT + 1)
        after = os.fstat(stream.fileno())
        identity = lambda row: (row.st_dev, row.st_ino, row.st_mode, row.st_nlink,
                                row.st_size, row.st_mtime_ns, row.st_ctime_ns)
        require(identity(before) == identity(after) == identity(os.stat(path, follow_symlinks=False))
                and len(data) == before.st_size)
        return data


BOOTSTRAP = source_bytes(Path(__file__))


def load(data, filename, name):
    module = types.ModuleType(name)
    module.__file__ = str(filename)
    exec(compile(data, str(filename), "exec", dont_inherit=True, optimize=0), module.__dict__)
    return module


def helpers(root):
    modules, consumed = {}, {}
    for name, digest in PINS.items():
        data = source_bytes(root / name)
        require(hashlib.sha256(data).hexdigest() == digest)
        modules[name] = load(data, root / name, "pinned_" + Path(name).stem)
        consumed[name] = data
    return modules[SHARED], modules[APPRAISAL], consumed


def sources(root):
    shared, appraiser, consumed = helpers(root)
    revision = shared.git(root, "rev-parse", "--verify", "HEAD^{commit}").decode("ascii").strip()
    require(re.fullmatch(r"[0-9a-f]{40}", revision) and revision != "0" * 40)
    result = {}
    for name in (RECORDER, APPRAISAL, PROBE, SHARED):
        entry = shared.git(root, "ls-tree", revision, "--", name).decode("ascii").strip().split()
        require(len(entry) == 4 and entry[0] in ("100644", "100755")
                and entry[1] == "blob" and entry[3] == name
                and re.fullmatch(r"[0-9a-f]{40}", entry[2]))
        require(0 < int(shared.git(root, "cat-file", "-s", entry[2])) <= LIMIT)
        result[name] = shared.git(root, "cat-file", "blob", entry[2])
    require(result[RECORDER] == BOOTSTRAP)
    require(all(result[name] == data for name, data in consumed.items()))
    require(shared.digest(result[PROBE]) == appraiser.PROBE_SHA256)
    return revision, result, shared, appraiser


STAGES = {
    "platform", "offline-install", "pinned-files", "cli-contract",
    "inspection-complete-unappraised", "readiness-lock", "nvml-initialization",
    "nvml-inventory", "remote-attestation", "token-verification", "claims-policy",
    "inventory-recheck", "readiness-precondition", "readiness-transition",
    "readiness-readback", "nvml-shutdown", "complete",
}
TRANSITIONS = {"unverified", "unchanged", "attempted-unverified",
               "enabled-readback-verified", "already-ready-verified"}
CLAIMS = {"authenticated_gpu_count", "distinct_devices", "nonce_matches", "measurements_match",
          "certificates_and_revocation_valid", "secure_boot", "debug_disabled",
          "local_device_identity_matches"}
KEYS = {"schema", "result", "stage", "gpu_appraisal_verified", "claims", "runtime_ready",
        "confidential_qualified", "deployable", "readiness_transition", "readiness_requested",
        "readiness_header_reference", "source_reference", "package_version", "package_sha256",
        "cli_contract_verified", "installation_completed", "ready_before", "ready_after",
        "persistence_enabled_count", "nvml_shutdown", "claim_failure"}


def validate(data, appraiser, script_hash, *, attest=False, enable=False, install=False):
    require(type(data) is bytes and len(data) <= 16384)
    value = appraiser.strict_json(data)
    require(type(value) is dict and set(value) in (KEYS, KEYS | {"script_sha256"}))
    require(value["schema"] == "nib.lab-gpu-appraisal/v1"
            and value["result"] in ("pass", "fail") and value["stage"] in STAGES
            and value["readiness_transition"] in TRANSITIONS)
    require(all(value[name] is False for name in ("runtime_ready", "confidential_qualified", "deployable")))
    require(value["readiness_requested"] is enable)
    require(value["source_reference"] == appraiser.SOURCE
            and value["readiness_header_reference"] == appraiser.READINESS_HEADER
            and value["package_version"] == appraiser.VERSION
            and value["package_sha256"] == appraiser.PACKAGES)
    if "script_sha256" in value:
        require(value["script_sha256"] == script_hash)
    for key in ("gpu_appraisal_verified", "cli_contract_verified", "installation_completed"):
        require(type(value[key]) is bool)
    require(value["nvml_shutdown"] is None or type(value["nvml_shutdown"]) is bool)
    for key, maximum in (("ready_before", 1), ("ready_after", 1), ("persistence_enabled_count", 8)):
        require(value[key] is None or type(value[key]) is int and 0 <= value[key] <= maximum)
    require(not value["installation_completed"] or install)
    failure = value["claim_failure"]
    if failure is not None:
        require(type(failure) is dict and set(failure) == {"code", "source", "field"}
                and failure["code"] in appraiser.CLAIM_FAILURES
                and failure["source"] in appraiser.CLAIM_SOURCES
                and failure["field"] in appraiser.CLAIM_FIELDS
                and value["result"] == "fail" and value["stage"] == "claims-policy"
                and value["gpu_appraisal_verified"] is False)
    if value["gpu_appraisal_verified"]:
        require(attest and value["cli_contract_verified"] and value.get("script_sha256") == script_hash)
        claims = value["claims"]
        require(type(claims) is dict and set(claims) == CLAIMS)
        require(type(claims["authenticated_gpu_count"]) is int and claims["authenticated_gpu_count"] == 8
                and all(claims[key] is True for key in CLAIMS - {"authenticated_gpu_count"}))
    else:
        require(value["claims"] is None)
    if not enable:
        require(value["readiness_transition"] in ("unverified", "unchanged"))
    if value["result"] == "pass":
        require(attest and value["stage"] == "complete" and value["gpu_appraisal_verified"]
                and value["nvml_shutdown"] is True)
        if enable:
            require(value["ready_after"] == 1 and value["persistence_enabled_count"] == 8
                    and ((value["readiness_transition"] == "enabled-readback-verified"
                          and value["ready_before"] == 0)
                         or (value["readiness_transition"] == "already-ready-verified"
                             and value["ready_before"] == 1)))
        else:
            require(value["ready_before"] in (0, 1)
                    and value["ready_after"] == value["ready_before"]
                    and value["readiness_transition"] == "unchanged")
    return value


def record(root, revision, source, shared, appraiser, *, attest=False, enable=False,
           packages=None, now=None, capture=None):
    require(not enable or attest)
    require(not (packages is not None and attest))
    now = shared.utc_now if now is None else now
    started = now()
    binding = {"git_sha": revision,
               "files": {name: shared.digest(data) for name, data in source.items()}}
    output = {"schema": "nib.gpu-appraisal-recording/v1", "source": binding,
              "result": "fail", "appraisal": None, "hardware_validated": False,
              "runtime_ready": False, "confidential_qualified": False, "deployable": False}
    with shared.destination(root, started, label="gpu-appraisal") as (directory, relative):
        try:
            with tempfile.TemporaryDirectory(prefix="nib-appraisal-source-") as temporary:
                work = Path(temporary)
                os.chmod(work, 0o700)
                for name in (APPRAISAL, PROBE):
                    path = work / Path(name).name
                    path.write_bytes(source[name])
                    path.chmod(0o400)
                probe = shared.load_probe(source[PROBE], work / "probe.py")
                execute = probe.capture if capture is None else capture
                command = [sys.executable, "-I", "-S", "-B", str(work / Path(APPRAISAL).name),
                           "--worker", temporary]
                if attest:
                    command.append("--attest")
                if enable:
                    command.append("--enable-readiness")
                if packages is not None:
                    command.extend(["--install-packages", str(Path(packages).absolute())])
                data = execute(command, timeout=285, limit=16384,
                               env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": temporary,
                                    "TMPDIR": temporary, "LANG": "C", "LC_ALL": "C"})
                for name in (APPRAISAL, PROBE):
                    shared.unchanged_snapshot(work / Path(name).name, source[name])
                output["appraisal"] = validate(data, appraiser, binding["files"][APPRAISAL],
                                               attest=attest, enable=enable,
                                               install=packages is not None)
                output["result"] = output["appraisal"]["result"]
        except (Exception, KeyboardInterrupt):
            output.update(result="fail", appraisal=None,
                          failure_code="appraisal-execution-or-report-failed")
        ended = now()
        if ended < started:
            output.update(result="fail", appraisal=None, failure_code="recorder-clock-regressed")
            ended = started
        command = "python3 -I -S -B host/record-gpu-appraisal.py"
        if attest:
            command += " --attest"
        if enable:
            command += " --enable-readiness"
        if packages is not None:
            command += " --install-packages <private-package-directory>"
        run = {
            "schema": "nib.evidence/v1", "phase": "P2", "task": "T3", "git_sha": revision,
            "command": command, "started_utc": shared.utc_text(started),
            "ended_utc": shared.utc_text(ended), "result": output["result"],
            "execution": "software", "hardware_validated": False,
            "host": {"provider": "operator-approved-test-host", "machine": "linux",
                     "gpus": "eight-device appraisal scope", "driver": None, "cuda": None},
            "notes": "Committed software appraisal observation, not GPU execution or CPU/fabric/service "
                     "qualification. No raw identities, nonce, tokens or claims. Command package path "
                     "is normalized. Failure may follow a partial install or attempted transition; "
                     "no rollback or retry is inferred. Source hashes are in output.log.",
        }
        shared.write_json(directory, "output.log", output)
        shared.write_json(directory, "run.json", run)
        os.fsync(directory)
    return relative, 0 if output["result"] == "pass" else 1


def main():
    class Parser(argparse.ArgumentParser):
        def error(self, message):
            self.exit(2, "Invalid appraisal recording arguments.\n")
    parser = Parser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--attest", action="store_true")
    modes.add_argument("--install-packages")
    parser.add_argument("--enable-readiness", action="store_true")
    args = parser.parse_args()
    if args.enable_readiness and not args.attest:
        parser.error("invalid-operation")
    require(sys.platform == "linux")
    revision, source, shared, appraiser = sources(ROOT)
    _, code = record(ROOT, revision, source, shared, appraiser, attest=args.attest,
                     enable=args.enable_readiness, packages=args.install_packages)
    print(json.dumps({"recorded": True, "result": "pass" if code == 0 else "fail"}))
    return code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit("Appraisal recording failed; no qualification or readiness success may be inferred.")
