#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Qualify fixed pinned tools startup inside the native namespace boundary."""
import sys

if __name__ == "__main__" and not (
        sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode):
    sys.exit("Use python3 -I -S -B host/probe-mkosi-native-tools.py")

import argparse
import io
import json
import os
from pathlib import Path, PurePosixPath
import runpy
import subprocess
import tarfile
import tempfile
import time
import types

ROOT = Path(__file__).absolute().parent.parent
SCRIPT = "host/probe-mkosi-native-tools.py"
FETCHER = "scripts/fetch_mkosi_tools.py"
NATIVE = "host/probe-mkosi-native.py"
CHILD = "scripts/mkosi_native_tools_worker.py"
PID_CHECKER = "scripts/mkosi_pid_check.py"
OWNERSHIP = "scripts/mkosi_ownership.py"
FAILURE_STAGES = ("worker-preflight", "namespaces", "pid", "ownership", "inputs", "tools")
fetcher = types.SimpleNamespace(**runpy.run_path(str(ROOT / FETCHER), run_name="tools_fetcher"))
native = types.SimpleNamespace(**runpy.run_path(str(ROOT / NATIVE), run_name="native_probe"))
ownership = types.SimpleNamespace(**runpy.run_path(str(ROOT / OWNERSHIP), run_name="ownership_probe"))
prep, base, tools, require = fetcher.prep, fetcher.base, fetcher.tools, fetcher.require
SUCCESS = b"NIB_NATIVE_TOOLS_OK\n"


def materialize(files, destination):
    """Materialize reconstructed tar bytes, never extract through a symlink."""
    require(not destination.exists() and not destination.is_symlink())
    inventory = json.loads(files["root-inventory.json"])
    require(type(inventory) is dict and 0 < len(inventory) <= tools.MAX_ENTRIES
            and 0 < len(files["tools-root.tar"]) <= tools.MAX_TREE)
    tree = {}
    with tarfile.open(fileobj=io.BytesIO(files["tools-root.tar"]), mode="r:") as archive:
        for member in archive:
            name = member.name.rstrip("/")
            path = PurePosixPath(name)
            require(name in inventory and name not in tree and str(path) == name
                    and not path.is_absolute() and all(part not in (".", "..", "") for part in path.parts)
                    and not member.pax_headers and not member.issparse()
                    and member.uid == member.gid == 0 and member.mtime == tools.EPOCH
                    and 0 <= member.mode <= 0o777)
            row = {"mode": member.mode}
            if member.isdir():
                require(member.mode == 0o755)
                row.update(kind="dir")
            elif member.issym():
                tools.link_target(name, member.linkname)
                row.update(kind="link", target=member.linkname)
            else:
                require(member.isfile() and 0 <= member.size <= 64 << 20)
                with archive.extractfile(member) as stream:
                    data = stream.read(member.size + 1)
                require(len(data) == member.size)
                row.update(kind="file", data=data)
            tree[name] = row
    require(tools.observation(tree) == inventory)
    tools.complete_tree(tree)
    require(tools.observation(tree) == inventory)
    destination.mkdir(mode=0o700)
    for name, row in sorted(tree.items(), key=lambda pair: (pair[0].count("/"), pair[0])):
        if row["kind"] == "dir":
            (destination / name).mkdir(mode=0o755)
    for name, row in tree.items():
        if row["kind"] == "file":
            path = destination / name
            with path.open("xb") as stream:
                stream.write(row["data"])
            path.chmod(row["mode"])
    # No regular-file writes follow link creation. Absolute links are rooted only once
    # the sandbox pivots into this tree; they are never traversed on the host.
    for name, row in tree.items():
        if row["kind"] == "link":
            os.symlink(row["target"], destination / name)
    for name in ("nib", "proc", "proc/self", "proc/self/ns", "dev", "tmp"):
        path = destination / name
        require(not path.is_symlink() and (not path.exists() or path.is_dir()))
        path.mkdir(mode=0o755, exist_ok=True)


def command(root, payload, *, pid_isolation=False, mapped=None):
    require(type(pid_isolation) is bool)
    require(mapped is None or pid_isolation)
    original = [os.readlink("/proc/self/ns/" + name) for name in ("user", "mnt", "net", "ipc")]
    prefix = []
    proc_source = "/proc/self/ns"
    if pid_isolation:
        original.append(os.readlink("/proc/self/ns/pid"))
        # Pinned util-linux 2.39.3: mount-proc implies a private mount namespace;
        # kill-child + PID namespace bounds descendants when the launcher dies.
        prefix = ["/usr/bin/unshare", "--pid", "--fork", "--mount-proc=/proc", "--kill-child=KILL", "--"]
        # Linux mnt_already_visible requires a complete procfs during creation
        # of the replacement. Only the pinned launcher sees this read-only bind;
        # unshare replaces it before exec, and the worker proves private procfs.
        proc_source = "/proc"
    return [*(ownership.prefix(root, mapped) if mapped is not None else []),
        native.PYTHON, "-I", "-S", "-B", str(payload / "mkosi-sandbox.py"),
        "--ro-bind", str(root), "/", "--ro-bind", str(payload), "/nib",
        "--ro-bind", proc_source, proc_source, "--dev", "/dev", "--tmpfs", "/tmp",
        "--unshare-net", "--unshare-ipc", "--become-root",
        *prefix, "/usr/bin/python3.12", "-I", "-S", "-B", "/nib/native-tools.py", *original,
        *(["mapped-ownership"] if mapped is not None else [])]


def failure_kind(exc):
    if isinstance(exc, subprocess.TimeoutExpired):
        return "timeout"
    if isinstance(exc, subprocess.CalledProcessError):
        error = exc.stderr if isinstance(exc.stderr, bytes) else b""
        for stage in FAILURE_STAGES:
            if error == ("NIB_NATIVE_TOOLS_REJECTED:" + stage + "\n").encode("ascii"):
                return "worker-" + stage
        if error.startswith(b"unshare:"):
            if b"Operation not permitted" in error or b"Permission denied" in error:
                return "unshare-permission-denied"
            if b"No such file or directory" in error:
                return "unshare-missing-path"
            if b"Invalid argument" in error:
                return "unshare-invalid-argument"
            return "unshare-exit"
        return "process-exit"
    if isinstance(exc, OSError):
        return "startup-error"
    return "invalid-result-or-cleanup"


def run(files, payload_files, execute, *, pid_isolation=False, mapped_ownership=False):
    require(type(pid_isolation) is bool and type(mapped_ownership) is bool
            and (not mapped_ownership or pid_isolation))
    report = {"schema": "nib.mkosi-native-tools/v1", "result": "fail", "stage": "materialization",
              "failure_kind": None,
              "namespaces_validated": False, "tools_startup_validated": False,
              "tools_checked": 0, "process_cleanup_confirmed": False, "payload_removed": False,
              "package_installation_executed": False, "maintainer_scripts_executed": False,
              "mkosi_build_executed": False, "abi_validated": False,
              "bootable": False, "deployable": False}
    if pid_isolation:
        report.update(schema="nib.mkosi-native-tools/v2", pid_isolation_required=True,
                      pid_namespace_validated=False, host_processes_hidden=False)
    if mapped_ownership:
        report.update(schema="nib.mkosi-native-tools/v3", mapped_ownership_required=True,
                      mapped_ownership_validated=False, host_mapping_helpers_sha256={})
    with tempfile.TemporaryDirectory(prefix="nib-native-tools-", dir="/tmp") as temporary:
        temporary = Path(temporary).resolve()
        root, payload = temporary / "root", temporary / "payload"
        try:
            mapped = None
            if mapped_ownership:
                report["stage"] = "host-subordinate-mapping"
                mapped, helpers = ownership.host_inputs()
                report["host_mapping_helpers_sha256"] = helpers
                payload_files = {**payload_files, "ownership-input.json": base.encoded(mapped)}
            report["stage"] = "materialization"
            materialize(files, root)
            base.publish(payload, payload_files)
            report["stage"] = "tools-startup"
            result = execute(command(root, payload, pid_isolation=pid_isolation, mapped=mapped), env=dict(native.worker.ENV),
                timeout=90, max_output_bytes=native.worker.MAX_OUTPUT, stdin=subprocess.DEVNULL)
            report["process_cleanup_confirmed"] = True
            result.check_returncode()
            require(result.stdout == SUCCESS and result.stderr == b"")
            report.update(result="pass", stage="complete", namespaces_validated=True,
                          tools_startup_validated=True, tools_checked=len(native.worker.COMMANDS))
            if pid_isolation:
                report.update(pid_namespace_validated=True, host_processes_hidden=True)
            if mapped_ownership:
                report["mapped_ownership_validated"] = True
        except (OSError, ValueError, KeyError, TypeError, tarfile.TarError, subprocess.SubprocessError) as exc:
            report["failure_kind"] = failure_kind(exc)
    require(not temporary.exists())
    report["payload_removed"] = True
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("inputs", "archive", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--pid-isolation", action="store_true")
    parser.add_argument("--mapped-ownership", action="store_true")
    args = parser.parse_args()
    native.preflight()
    tools.separate_output(args.output, (args.inputs, args.archive))
    require(not args.mapped_ownership or args.pid_isolation)
    revision, sources = fetcher.committed((NATIVE, SCRIPT, CHILD, PID_CHECKER, OWNERSHIP, native.WORKER, native.CHECKER))
    require(sources[native.WORKER] == native.WORKER_SOURCE
            and sources[tools.FILES[0]] == native.mkosi.TOOLS_SOURCE)
    files, receipt = prep.assemble(args.inputs, revision, sources)
    pin = json.loads(sources[prep.mkosi.EXTRA[1]])
    source, upstream = prep.mkosi.verify_source(base.read(args.archive, prep.mkosi.MAX_SOURCE), pin)
    payload = {"mkosi-sandbox.py": upstream["mkosi/sandbox.py"],
               "native-tools.py": sources[CHILD], "probe.py": sources[native.WORKER],
               "pid-check.py": sources[PID_CHECKER],
               "ownership.py": sources[OWNERSHIP],
               "namespace-check.py": sources[native.CHECKER], "snapshot.py": sources[tools.FILES[4]]}
    payload.update({name: upstream[name] for name in pin["license_files"]})
    payload["input.json"] = base.encoded({"schema": "nib.mkosi-tools-probe-input/v1", "git_sha": revision,
        "tools_receipt_sha256": base.digest(files["receipt.json"]),
        "snapshot_sha256": base.digest(sources[tools.FILES[4]])})
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    report = run(files, payload, base.processes.execute_process_group, pid_isolation=args.pid_isolation,
                 mapped_ownership=args.mapped_ownership)
    ended = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    report.update(git_sha=revision, tools_packages=receipt["tools_packages"],
                  tools_lock_sha256=receipt["tools_lock_sha256"], source=source,
                  tools_root_sha256=base.digest(files["tools-root.tar"]),
                  payload_sha256={name: base.digest(raw) for name, raw in payload.items()})
    evidence = native.evidence(report, revision, started, ended)
    evidence.update(command="/usr/bin/python3.10 -I -S -B host/probe-mkosi-native-tools.py "
        "--inputs PRIVATE --archive PRIVATE --output PRIVATE",
        notes="Seven fixed startup checks in a read-only, reconstructed pinned tools root. "
        "Distinct namespaces, loopback only, isolated temporary storage. Host launcher/kernel unpinned. "
        "No package installation, maintainer hooks, image build, boot or attestation.")
    if args.pid_isolation:
        evidence["command"] += " --pid-isolation"
        evidence["notes"] += " Pinned unshare adds a child PID namespace and its own procfs; no host process view."
    if args.mapped_ownership:
        evidence["command"] += " --mapped-ownership"
        evidence["notes"] += " Existing host subordinate-ID helpers allocate the user namespace; private tmpfs ownership is checked. Host helper hashes are observations, not pinned build inputs."
    base.publish(args.output, {"run.json": base.encoded(evidence), "result.json": base.encoded(report)})
    print("Native pinned tools probe: " + report["result"])
    return 0 if report["result"] == "pass" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError, subprocess.SubprocessError):
        sys.exit("Native pinned tools rejected; no image qualification.")
