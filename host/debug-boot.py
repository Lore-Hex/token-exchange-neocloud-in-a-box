#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Source-bound private debug assembly and one CPU-only boot; no publication."""
import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode):
    sys.exit("Use python3.10 -I -S -B host/debug-boot.py")

import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time

ROOT = Path(__file__).absolute().parent.parent
# Existing bootstrap compiles verified source rather than importing local pyc.
import runpy
import types
native = types.SimpleNamespace(**runpy.run_path(str(ROOT / "host/build-mkosi-native.py")))
base, tools, require = native.base, native.tools, native.require
runtime, RUNTIME_SOURCE = base.helper("debug_runtime", "scripts/prepare_debug_boot_runtime.py")
validator, VALIDATOR_SOURCE = base.helper("debug_export_verifier", "host/validate-native-boot-export.py")
SCRIPT = "host/debug-boot.py"
ROOT_LAUNCHER = "host/run-debug-boot.py"
GUEST = tuple("guest/debugboot/" + p for p in (
    "probe.go", "query_unix.go", "stage_unix.go", "run_linux.go", "cmd/nib-debug-boot/main_linux.go",
    "nib-debug-boot.service", "nib-debug-boot.target", "nib-debug-boot-stage.service", "10-nib-debug-boot.conf",
    "nib-debug-network-failure.service", "90-nib-debug-network-failure.conf"))
TOPOLOGY_PROFILE = "debug-topology/v1"
TOPOLOGY_GUEST = (
    "guest/debugboot/topology_linux.go", "guest/debugboot/topology_stage_unix.go",
    "guest/debugboot/cmd/nib-debug-topology/main_linux.go", "guest/debugboot/nib-debug-topology.service",
    "guest/debugtopology/topology.go", "guest/debugtopology/wire.go", "guest/debugtopology/run_linux.go")
EXTRA = (SCRIPT, ROOT_LAUNCHER, "scripts/debug_boot_uki.py", "scripts/debug_boot_qemu.py",
         "scripts/fetch_debug_boot_runtime.py", runtime.SCRIPT, runtime.LOCK, validator.SCRIPT, "go.mod", "go.sum", *GUEST)
STAGE = ROOT / "build/debug-boot/private"
RESULT = ROOT / "build/debug-boot/worker.json"
EVIDENCE = ROOT / "build/native-install/evidence"
SELF = base.read(ROOT / SCRIPT, 1 << 20)
WORKFLOW = ".github/workflows/debug-boot.yml"


def workflow_source(revision):
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
    return raw


def topology_options(guest_network, debug_topology):
    require(type(guest_network) is bool and type(debug_topology) is bool
            and (not debug_topology or guest_network))
    return {**({"guest_network": True} if guest_network else {}),
            **({"debug_topology": True} if debug_topology else {})}


def debug_identity(revision, sources, debug_topology=False):
    require(type(debug_topology) is bool)
    value = native.source_identity(revision, sources)
    if debug_topology:
        require(set(sources) == set((*tools.FILES, *EXTRA, *TOPOLOGY_GUEST, WORKFLOW)))
        value["profile"] = TOPOLOGY_PROFILE
    return value


def committed(guest_network=False, debug_topology=False):
    topology_options(guest_network, debug_topology)
    revision, sources = native.committed_sources(True, True, True, True,
        **({"guest_network": True} if guest_network else {}))
    other, debug = tools.committed(EXTRA + (TOPOLOGY_GUEST if debug_topology else ()))
    require(other == revision and debug[SCRIPT] == SELF and debug[runtime.SCRIPT] == RUNTIME_SOURCE
            and debug[validator.SCRIPT] == VALIDATOR_SOURCE)
    debug[WORKFLOW] = workflow_source(revision)
    if debug_topology:
        debug_identity(revision, debug, True)
    return revision, sources, debug


def preflight():
    native.probe.native.preflight()
    membership = Path("/proc/self/cgroup").read_text()
    match = re.fullmatch(r"0::/(nib-build-[0-9a-f]{32})\n", membership)
    require(match is not None)
    group = Path("/sys/fs/cgroup") / match[1]
    limits = {**native.resources.LIMITS, "memory.max": str(2 << 30), "pids.max": "128"}
    require(group.stat().st_uid == 0 and not group.stat().st_mode & 0o022
            and {name: (group / name).read_text().strip() for name in limits} == limits)


def safe_directory(path):
    with base.directory(path) as fd:
        info = os.fstat(fd)
        require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700)
        return info


def initrd_units(debug, guest_network, debug_topology=False):
    topology_options(guest_network, debug_topology)
    units = {name: debug["guest/debugboot/" + name] for name in ("nib-debug-boot-stage.service", "10-nib-debug-boot.conf")}
    if guest_network:
        name = "nib-debug-boot-stage.service"
        command = b"ExecStart=/usr/libexec/nib-debug-boot --stage-initrd\n"
        require(units[name].count(command) == 1)
        flag = b"--stage-initrd-topology" if debug_topology else b"--stage-initrd-network"
        units[name] = units[name].replace(command, b"ExecStart=/usr/libexec/nib-debug-boot " + flag + b"\n")
    return units


def prepare_tools(downloads, directory, revision, sources, export):
    lock = json.loads(sources[native.prep.LOCK])
    native.mkosi.validate_tools_lock(lock, downloads / "metadata")
    directory.mkdir(mode=0o700)
    files = {"mkosi-tools-inputs.json": sources[native.prep.LOCK]}
    for index in lock["indexes"]:
        files["metadata/" + index["file"]] = base.read(downloads / "metadata" / index["file"], 32 << 20)
    prepared = directory / "inputs"
    base.publish(prepared, files)
    (prepared / "repo").mkdir(mode=0o700)
    (prepared / "repo/pool").mkdir(mode=0o700)
    deadline = time.monotonic() + 120
    for row in lock["packages"]:
        export.copy(downloads / "archives" / (row["SHA256"] + ".deb"),
                    prepared / "repo/pool" / base.package_name(row),
                    {"bytes": int(row["Size"]), "sha256": row["SHA256"]}, base.MAX_ARCHIVE,
                    deadline, base.directory)
    files, receipt = native.prep.assemble(prepared, revision, sources)
    native.probe.materialize(files, directory / "root")
    return receipt


def compile_probe(go, directory, sources, debug_topology=False):
    """Build only the audited stdlib guest source; observe the host compiler.

    The compiler version is pinned; its hash is a host observation, not a
    vendor attestation. The exact resulting ELF is bound to the debug UKI.
    """
    require(go.is_absolute() and type(debug_topology) is bool)
    compiler = base.read(go, 64 << 20)
    source = {p: sources[p] for p in ("go.mod", "go.sum", *GUEST, *(TOPOLOGY_GUEST if debug_topology else ()))}
    base.publish(directory / "source", source)
    for leaf in ("cache", "modules", "tmp"):
        (directory / leaf).mkdir(mode=0o700)
    env = {"PATH": str(go.parent) + ":/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C",
           "GOENV": "off", "GOWORK": "off", "GOTOOLCHAIN": "local", "GOPROXY": "off", "GOSUMDB": "off",
           "GOFLAGS": "-mod=readonly", "GOOS": "linux", "GOARCH": "amd64", "GOAMD64": "v1", "CGO_ENABLED": "0",
           "GOCACHE": str(directory / "cache"), "GOMODCACHE": str(directory / "modules"), "TMPDIR": str(directory / "tmp")}
    def call(args, limit=65536):
        result = base.processes.execute_process_group([str(go), *args], env=env,
            cwd=directory / "source", timeout=120, max_output_bytes=limit, stdin=subprocess.DEVNULL)
        result.check_returncode()
        require(not result.stderr)
        return result.stdout
    version = call(["env", "GOVERSION"])
    require(version == b"go1.27.2\n")
    name = "nib-debug-topology" if debug_topology else "nib-debug-boot"
    tag = "nibdebugtopology" if debug_topology else "nibdebugboot"
    package = "./guest/debugboot/cmd/" + name
    imports = call(["list", "-deps", "-tags=" + tag, "-f", "{{if not .Standard}}{{.ImportPath}}{{end}}", package])
    prefix = b"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/guest/debugboot"
    expected = ([prefix.replace(b"/debugboot", b"/debugtopology")] if debug_topology else [])
    require(imports.split() == [*expected, prefix, prefix + b"/cmd/" + name.encode("ascii")])
    require(call(["build", "-trimpath", "-buildvcs=false", "-tags=" + tag, "-ldflags=-buildid=",
                  "-o", str(directory / "probe"), package]) == b"")
    raw = base.read(directory / "probe", 8 << 20)
    require(raw[:7] == b"\x7fELF\x02\x01\x01" and base.read(go, 64 << 20) == compiler)
    base.verify(directory / "source", source)
    return raw, {"version": "go1.27.2", "compiler_sha256": base.digest(compiler),
                 "compiler_authenticated": False, "source_hashes": {p: base.digest(v) for p, v in source.items()},
                 "probe": {"bytes": len(raw), "sha256": base.digest(raw)}}


def sandbox(root, payload, inputs, output, mapped, worker, runtime_root=None):
    before = [os.readlink("/proc/self/ns/" + name) for name in ("user", "mnt", "net", "ipc", "pid")]
    require(worker in ("debug-uki.py", "debug-qemu.py"))
    return [*native.probe.ownership.prefix(root, mapped), native.probe.native.PYTHON, "-I", "-S", "-B",
        str(payload / "mkosi-sandbox.py"), "--ro-bind", str(root), "/", "--ro-bind", str(payload), "/nib",
        "--ro-bind", str(inputs), "/input", "--ro-bind", "/proc", "/proc", "--bind", str(output), "/out",
        *(["--ro-bind", str(runtime_root), "/runtime"] if runtime_root is not None else []),
        "--dev", "/dev", "--tmpfs", "/tmp", "--tmpfs", "/work", "--unshare-net", "--unshare-ipc",
        "--become-root", "--chdir", "/",
        "/usr/bin/unshare", "--pid", "--fork", "--mount-proc=/proc", "--kill-child=KILL", "--",
        *(native.probe.confinement.PREFIX if worker == "debug-uki.py" else ()),
        "/usr/bin/python3.12", "-I", "-S", "-B", "/nib/" + worker, *before]


def execute_worker(command, output, name, timeout):
    result = None
    try:
        result = base.processes.execute_process_group(command,
            env=dict(native.probe.native.worker.ENV), timeout=timeout, max_output_bytes=65536,
            max_stderr_bytes=65536, stdin=subprocess.DEVNULL)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    value = json.loads(base.read(output / name, 65536)) if os.path.lexists(output / name) else None
    # A diagnostic may survive nonzero exit, but never becomes a successful run.
    return value, result is not None and result.returncode == 0 and not result.stdout and not result.stderr


def run(downloads, archive, runtime_inputs, go, revision, sources, debug, guest_network=False, debug_topology=False):
    topology_options(guest_network, debug_topology)
    report = {"schema": "nib.debug-boot/v1", "result": "fail", "stage": "source",
              "source": native.source_identity(revision, sources, guest_network), "debug_source": debug_identity(revision, debug, debug_topology),
              "boot_verified": False, "confidentiality_verified": False, "private_work_removed": False}
    if debug_topology:
        report.update(schema="nib.debug-topology-boot/v1", profile=TOPOLOGY_PROFILE, topology_down_only_verified=False)
    export, _publication = native.export_helpers(sources)
    _, bundle = export.paths(EVIDENCE)
    try:
        require(set(os.listdir(STAGE)) == set())
        report["stage"] = "bundle"
        original = safe_directory(bundle)
        metadata = validator.verify_bundle(EVIDENCE, bundle, original, report["source"], export, time.monotonic() + 120)
        manifest = export.parsed(metadata["manifest.json"])
        inputs = STAGE / "input"
        inputs.mkdir(mode=0o700)
        for name, limit in export.FILES.items():
            export.copy(bundle / name, inputs / name, manifest["files"][name], limit, time.monotonic() + 120, base.directory)
        report["root_hash"] = manifest["root_hash"]
        report["input_files"] = manifest["files"]
        report["stage"] = "tools"
        tool_receipt = prepare_tools(downloads, STAGE / "tools", revision, sources, export)
        root = STAGE / "tools/root"
        for name in ("input", "out", "work", "runtime"):
            require(not os.path.lexists(root / name))
            (root / name).mkdir(mode=0o755)
        report["tools_root"] = tool_receipt["files"]["tools-root.tar"]
        lock = json.loads(debug[runtime.LOCK])
        runtime.metadata_check(lock, runtime_inputs / "metadata")
        tree, _ = runtime.assemble(lock, runtime_inputs / "archives")
        runtime_files = {"tools-root.tar": tools.tar_bytes(tree), "root-inventory.json": base.encoded(tools.observation(tree))}
        report["runtime_root"] = {"sha256": base.digest(runtime_files["tools-root.tar"]), "bytes": len(runtime_files["tools-root.tar"])}
        runtime_root = STAGE / "runtime"
        native.probe.materialize(runtime_files, runtime_root)
        member_names = ("usr/bin/qemu-system-x86_64", "lib/x86_64-linux-gnu/ld-linux-x86-64.so.2",
                        "usr/share/OVMF/OVMF_CODE.fd", "usr/share/OVMF/OVMF_VARS.fd")
        runtime_members = {"/runtime/" + name: {"bytes": len(tree[name]["data"]), "sha256": base.digest(tree[name]["data"])} for name in member_names}
        del tree, runtime_files
        report["stage"] = "probe-build"
        build = STAGE / "compile"
        build.mkdir(mode=0o700)
        probe, report["compiler"] = compile_probe(go, build, debug, **({"debug_topology": True} if debug_topology else {}))
        # Pinned toolchain and source artifacts are recorded; no release trust claim.
        report["stage"] = "assembly"
        pin = json.loads(sources[native.mkosi.EXTRA[1]])
        _, upstream = native.mkosi.verify_source(base.read(archive, native.mkosi.MAX_SOURCE), pin)
        mapped, report["mapping_helpers"] = native.probe.ownership.host_inputs()
        stub_name = "usr/lib/systemd/boot/efi/linuxx64.efi.stub"
        guest_lock = json.loads(sources[native.hooks.RUNTIME_LOCK])
        stub_package = next(row for row in guest_lock["packages"] if row["Package"] == "systemd-boot-efi")
        raw = base.read(downloads / "archives" / (stub_package["SHA256"] + ".deb"), base.MAX_ARCHIVE)
        require(base.digest(raw) == stub_package["SHA256"] and len(raw) == int(stub_package["Size"]))
        stub = tools.payload(raw)[stub_name]["data"]
        original_boot = export.parsed(metadata["result.json"])["worker"]["boot_uki"]
        payload_files = {"mkosi-sandbox.py": upstream["mkosi/sandbox.py"], "probe": probe, "stub": stub,
            "boot-uki.py": sources[native.BOOT], "snapshot.py": sources[tools.FILES[4]],
            "namespace-check.py": sources[native.probe.native.CHECKER], "pid-check.py": sources[native.probe.PID_CHECKER],
            "ownership.py": sources[native.probe.OWNERSHIP], "ownership-input.json": base.encoded(mapped),
            "confinement.py": sources[native.probe.CONFINEMENT],
            "debug-uki.py": debug["scripts/debug_boot_uki.py"], "debug-qemu.py": debug["scripts/debug_boot_qemu.py"]}
        units = initrd_units(debug, guest_network, debug_topology)
        payload_files.update(units)
        config = {"schema": "nib.debug-uki-input/v1", "root_hash": manifest["root_hash"], "original": original_boot,
                  "probe": report["compiler"]["probe"], "units": {name: {"bytes": len(raw), "sha256": base.digest(raw)} for name, raw in units.items()}}
        if debug_topology:
            config.update(schema="nib.debug-uki-input/v2", profile=TOPOLOGY_PROFILE)
        payload_files["debug-input.json"] = base.encoded(config)
        payload = STAGE / "payload"
        base.publish(payload, payload_files)
        output = STAGE / "assembled"
        output.mkdir(mode=0o700)
        value, ok = execute_worker(sandbox(root, payload, inputs, output, mapped, "debug-uki.py"), output, "debug.json", 300)
        report["assembly"] = value
        require(ok and value is not None and value["result"] == "pass" and value["root_hash"] == manifest["root_hash"]
                and value["original_uki"] == manifest["files"]["nib.efi"] and value["probe"] == config["probe"]
                and value["signed"] is False and value["confidentiality_verified"] is False)
        if debug_topology:
            require(value.get("schema") == "nib.debug-topology-uki/v1" and value.get("profile") == TOPOLOGY_PROFILE)
        export.copy(output / "debug.efi", inputs / "debug.efi", value["uki"]["image"], 256 << 20, time.monotonic() + 120, base.directory)
        qemu_config = {"schema": "nib.debug-qemu-input/v1", "runtime_members": runtime_members, "debug_uki": value["uki"]["image"]}
        if debug_topology:
            qemu_config.update(schema="nib.debug-qemu-input/v2", profile=TOPOLOGY_PROFILE)
        qemu_payload = STAGE / "qemu-payload"
        base.publish(qemu_payload, {**payload_files, "qemu-input.json": base.encoded(qemu_config)})
        qemu_output = STAGE / "observed"
        qemu_output.mkdir(mode=0o700)
        report["stage"] = "boot"
        value, ok = execute_worker(sandbox(root, qemu_payload, inputs, qemu_output, mapped, "debug-qemu.py", runtime_root),
                                   qemu_output, "qemu.json", 260)
        report["qemu"] = value
        require(ok and value is not None and value["result"] == "pass" and value["stage"] == "complete"
                and value["boot_verified"] is True and value["confidentiality_verified"] is False)
        if debug_topology:
            require(value.get("schema") == "nib.debug-topology-qemu/v1" and value.get("profile") == TOPOLOGY_PROFILE
                    and value.get("topology_down_only_verified") is True
                    and value.get("debug_uki") == qemu_config["debug_uki"]
                    and type(value.get("proof")) is dict and value["proof"].get("topology_down_only") is True)
        for name, expected in manifest["files"].items():
            export.transfer(inputs / name, expected, export.FILES[name], time.monotonic() + 120, base.directory)
        report.update(result="pass", stage="complete", boot_verified=True)
        if debug_topology:
            report["topology_down_only_verified"] = True
    except (OSError, ValueError, KeyError, TypeError, StopIteration, subprocess.SubprocessError):
        report["result"] = "fail"
    # The independent root wrapper kills the cgroup and removes this owned tree
    # even if this process is killed before cleanup or receipt publication.
    return report


def main():
    if sys.argv[1:] in (["--source-only"], ["--source-only", "--guest-network"],
                        ["--source-only", "--guest-network", "--debug-topology"]):
        guest_network = "--guest-network" in sys.argv[1:]
        debug_topology = "--debug-topology" in sys.argv[1:]
        native.probe.native.preflight()
        revision, sources, debug = committed(**topology_options(guest_network, debug_topology))
        print(json.dumps({"schema": "nib.debug-producer/v1", "source": native.source_identity(revision, sources, guest_network),
                          "debug_source": debug_identity(revision, debug, debug_topology)}, sort_keys=True))
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("downloads", "archive", "runtime-inputs", "go"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--guest-network", action="store_true")
    parser.add_argument("--debug-topology", action="store_true")
    args = parser.parse_args()
    network_options = topology_options(args.guest_network, args.debug_topology)
    preflight()
    safe_directory(STAGE)
    require(all(path.is_absolute() and ".." not in path.parts for path in (args.downloads, args.archive, args.runtime_inputs, args.go)))
    revision, sources, debug = committed(**network_options)
    report = run(args.downloads, args.archive, args.runtime_inputs, args.go, revision, sources, debug, **network_options)
    with base.directory(RESULT.parent) as fd:
        target = os.open(RESULT.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        with os.fdopen(target, "wb") as stream:
            stream.write(base.encoded(report))
            stream.flush()
            os.fsync(stream.fileno())
    return 0 if report["result"] == "pass" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        sys.exit("NIB_DEBUG_BOOT_REJECTED")
