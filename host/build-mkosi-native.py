#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Reconstruct and run one fixed offline image-installation candidate."""
import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode):
    sys.exit("Use python3 -I -S -B host/build-mkosi-native.py")

import argparse
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
import tarfile
import tempfile
import time
import types

ROOT = Path(__file__).absolute().parent.parent
SCRIPT = "host/build-mkosi-native.py"
PROBE = "host/probe-mkosi-native-tools.py"
WORKER = "scripts/mkosi_build_worker.py"
ENTRY = "scripts/mkosi_build_entry.py"
RESOURCES = "host/probe-build-resources.py"
LAUNCHER = "host/run-mkosi-build.py"
probe = types.SimpleNamespace(**runpy.run_path(str(ROOT / PROBE), run_name="build_probe"))
resources = types.SimpleNamespace(**runpy.run_path(str(ROOT / RESOURCES), run_name="build_limits"))
prep, base, tools, require = probe.prep, probe.base, probe.tools, probe.require
mkosi = prep.mkosi


def preflight():
    probe.native.preflight()
    membership = Path("/proc/self/cgroup").read_text()
    match = re.fullmatch(r"0::/(nib-build-[0-9a-f]{32})\n", membership)
    require(match is not None)
    group = Path("/sys/fs/cgroup") / match[1]
    require(group.stat().st_uid == 0 and not group.stat().st_mode & 0o022)
    require({name: (group / name).read_text().strip() for name in resources.LIMITS} == resources.LIMITS)


def command(root, payload, output, mapped):
    before = [os.readlink("/proc/self/ns/" + name) for name in ("user", "mnt", "net", "ipc", "pid")]
    return [*probe.ownership.prefix(root, mapped), probe.native.PYTHON, "-I", "-S", "-B",
        str(payload / "mkosi-sandbox.py"), "--ro-bind", str(root), "/",
        "--ro-bind", str(payload), "/nib", "--ro-bind", "/proc", "/proc",
        "--bind", str(output), "/out", "--dev", "/dev", "--tmpfs", "/tmp", "--tmpfs", "/work",
        "--unshare-net", "--unshare-ipc", "--become-root",
        "/usr/bin/unshare", "--pid", "--fork", "--mount-proc=/proc", "--kill-child=KILL", "--",
        "/usr/bin/python3.12", "-I", "-S", "-B", "/nib/build-worker.py", *before]


def prepare(downloads, source_archive, revision, sources):
    raw = base.read(source_archive, mkosi.MAX_SOURCE)
    prepared, preparation = mkosi.prepare(downloads / "metadata", downloads / "archives", raw, revision, sources)
    pin = json.loads(sources[mkosi.EXTRA[1]])
    _, upstream = mkosi.verify_source(raw, pin)
    upstream[mkosi.PATCH_TARGET] = prepared["mkosi-overlay/" + mkosi.PATCH_TARGET]
    payload = {"prepared/" + name: data for name, data in prepared.items()}
    payload.update({"upstream/" + name: data for name, data in upstream.items()
                    if pin["source_files"][name]["mode"] != "120000"})
    # Only regular upstream source is needed. Preserve links as links later;
    # never accidentally execute a link target serialized as file contents.
    links = {"upstream/" + name: data.decode("utf-8") for name, data in upstream.items()
             if pin["source_files"][name]["mode"] == "120000"}
    payload.update({"mkosi-sandbox.py": upstream["mkosi/sandbox.py"],
        "build-worker.py": sources[WORKER], "mkosi-entry.py": sources[ENTRY],
        "snapshot.py": sources[tools.FILES[4]], "package-inputs.py": sources[tools.FILES[3]],
        "namespace-check.py": sources[probe.native.CHECKER], "pid-check.py": sources[probe.PID_CHECKER],
        "ownership.py": sources[probe.OWNERSHIP],
        "package-worker.py": prepared["nib/package-worker.py"],
        "confinement.py": prepared["nib/confinement.py"]})
    modes = {"upstream/" + name: 0o555 if row["mode"] == "100755" else 0o444
             for name, row in pin["source_files"].items() if row["mode"] != "120000"}
    return prepared, preparation, payload, modes, links


def run(downloads, archive, output, revision, sources):
    require(not output.exists() and not output.is_symlink())
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    report = {"schema": "nib.mkosi-installation/v1", "git_sha": revision, "result": "fail",
              "stage": "preparation", "worker": None, "payload_removed": False,
              "process_cleanup_confirmed": False, "failure_kind": None,
              "bootable": False, "deployable": False}
    artifacts = {}
    with tempfile.TemporaryDirectory(prefix="nib-image-build-", dir="/tmp") as temporary:
        temporary = Path(temporary).resolve()
        try:
            prepared, preparation, payload_files, modes, links = prepare(downloads, archive, revision, sources)
            inputs = temporary / "prepared"
            base.publish(inputs, prepared)
            files, tree_receipt = prep.assemble(inputs, revision, sources)
            mapped, helpers = probe.ownership.host_inputs()
            report["mapping_helper_hashes"] = helpers
            payload_files["ownership-input.json"] = base.encoded(mapped)
            root, payload, resultdir = temporary / "root", temporary / "payload", temporary / "result"
            probe.materialize(files, root)
            for name in ("work", "out"):
                require(not (root / name).exists() and not (root / name).is_symlink())
                (root / name).mkdir(mode=0o755)
            base.publish(payload, payload_files)
            for name, mode in modes.items():
                (payload / name).chmod(mode)
            for name, target in links.items():
                require(tools.link_target(name, target).startswith("upstream/"))
                (payload / name).parent.mkdir(parents=True, exist_ok=True)
                os.symlink(target, payload / name)
            resultdir.mkdir(mode=0o700)
            report.update(stage="installation", prepared_receipt_sha256=base.digest(prepared["receipt.json"]),
                          tools_receipt_sha256=base.digest(files["receipt.json"]))
            result = base.processes.execute_process_group(command(root, payload, resultdir, mapped),
                env={**probe.native.worker.ENV, "RUNNER_ENVIRONMENT": "github-hosted"},
                timeout=1900, max_output_bytes=2 << 20, stdin=subprocess.DEVNULL)
            report["process_cleanup_confirmed"] = True
            artifacts["launcher.stdout"] = result.stdout
            artifacts["launcher.stderr"] = result.stderr
            if (resultdir / "result.json").exists():
                worker = json.loads(base.read(resultdir / "result.json", 65536))
                require(worker["schema"] == "nib.mkosi-install-worker/v1"
                        and worker["bootable"] is False and worker["deployable"] is False)
                report["worker"] = worker
            for name in ("installer.stdout", "installer.stderr"):
                if (resultdir / name).exists():
                    artifacts[name] = base.read(resultdir / name, 2 << 20)
            result.check_returncode()
            require(report["worker"] and report["worker"]["result"] == "pass"
                    and report["worker"]["configured_state_verified"] is True
                    and report["worker"]["installed_packages"] == 145)
            report.update(result="pass", stage="complete")
        except (OSError, ValueError, KeyError, TypeError, tarfile.TarError, subprocess.SubprocessError) as exc:
            report["failure_kind"] = probe.failure_kind(exc)
    report["payload_removed"] = not temporary.exists()
    ended = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    evidence = probe.native.evidence(report, revision, started, ended)
    evidence.update(command="host/run-mkosi-build.py --downloads PRIVATE --archive PRIVATE --output PRIVATE",
        notes="One bounded offline installation; no boot, UKI, verity, DKMS, reproducibility or hardware qualification.")
    artifacts.update({"run.json": base.encoded(evidence), "result.json": base.encoded(report)})
    base.publish(output, artifacts)
    return 0 if report["result"] == "pass" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("downloads", "archive", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    preflight()
    tools.separate_output(args.output, (args.downloads, args.archive))
    revision, sources = tools.committed((*mkosi.EXTRA, prep.SCRIPT, prep.POLICY,
        SCRIPT, PROBE, WORKER, ENTRY, RESOURCES, LAUNCHER, probe.NATIVE, probe.OWNERSHIP,
        probe.PID_CHECKER, probe.native.CHECKER, probe.native.WORKER))
    return run(args.downloads, args.archive, args.output, revision, sources)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        sys.exit("Native image installation rejected; no qualification.")
