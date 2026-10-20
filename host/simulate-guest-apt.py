#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Explicit local Docker execution of a reverified offline APT simulation bundle."""
import sys

if __name__ == "__main__" and not sys.flags.isolated:
    sys.exit("Use python3 -I host/simulate-guest-apt.py")

import argparse
import datetime
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import types
import uuid

ROOT = Path(__file__).absolute().parent.parent


def load():
    path = ROOT / "scripts/prepare_guest_tools.py"
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 0 < before.st_size < 1 << 20:
            raise ValueError("simulation-bootstrap-rejected")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read((1 << 20) + 1)
        after = os.fstat(fd)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_nlink,
                              s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if len(raw) != before.st_size or identity(before) != identity(after):
            raise ValueError("simulation-bootstrap-rejected")
    finally:
        os.close(fd)
    module = types.ModuleType("apt_tools_preparer")
    module.__file__ = str(path)
    exec(compile(raw, str(path), "exec", dont_inherit=True), module.__dict__)
    return module, raw


prep, PREPARER_SOURCE = load()
base, require = prep.base, prep.require
sim, SIMULATION_SOURCE = base.helper("apt_simulation_contract", prep.FILES[1])


def validate_container(row, tag):
    host, config = row["HostConfig"], row["Config"]
    require(config["Image"] == tag and config["User"] == "65534:65534"
            and config["WorkingDir"] == "/work"
            and config["Entrypoint"] == ["/usr/bin/python3.12", "-I", "-S", "-B", "/nib/simulate.py"]
            and not config.get("Cmd"))
    require(host["NetworkMode"] == "none" and host["ReadonlyRootfs"] is True
            and host["Privileged"] is False and host["IpcMode"] == "private"
            and host["CapDrop"] == ["ALL"] and not host.get("CapAdd")
            and not host.get("Binds") and not host.get("Devices")
            and not host.get("DeviceRequests") and not host.get("PortBindings")
            and host["Memory"] == 1 << 30 and host["MemorySwap"] == 1 << 30
            and host["NanoCpus"] == 2_000_000_000
            and host["PidsLimit"] == 64 and host["Init"] is True
            and host["SecurityOpt"] in (["no-new-privileges"], ["no-new-privileges=true"])
            and host["Tmpfs"] == {"/work": "rw,nosuid,nodev,noexec,size=128m,uid=65534,gid=65534,mode=0700"})
    require(not row.get("Mounts"))
    require(sorted(config["Env"]) == sorted([
        "PATH=/usr/bin:/bin", "HOME=/nonexistent", "LANG=C", "LC_ALL=C"]))


def validate_result(result, receipt, guest):
    keys = {"schema", "result", "stage", "git_sha", "guest_lock_sha256",
            "target_packages", "plan", "diagnostic", "package_installation_executed",
            "maintainer_scripts_executed", "installable_verified", "bootable", "deployable"}
    require(isinstance(result, dict) and set(result) == keys
            and result["schema"] == "nib.apt-simulation/v2"
            and result["git_sha"] == receipt["git_sha"]
            and result["guest_lock_sha256"] == receipt["source_hashes"][prep.FILES[-1]]
            and type(result["target_packages"]) is int
            and result["target_packages"] == len(guest["packages"]))
    require(all(result[k] is False for k in ("package_installation_executed",
                "maintainer_scripts_executed", "installable_verified", "bootable", "deployable")))
    require(result["result"] in ("pass", "fail")
            and result["stage"] in ("setup", "apt-version", "local-index", "resolve", "complete"))
    if result["result"] == "pass":
        plan = result["plan"]
        require(result["stage"] == "complete" and result["diagnostic"] is None and isinstance(plan, dict)
                and set(plan) == {"installed", "configured", "removed", "plan_sha256"}
                and all(type(plan[k]) is int for k in ("installed", "configured", "removed"))
                and plan["installed"] == plan["configured"] == len(guest["packages"])
                and plan["removed"] == 0
                and re.fullmatch("[0-9a-f]{64}", plan["plan_sha256"]))
    else:
        require(result["stage"] != "complete" and result["plan"] is None)
        sim.validate_diagnostic(result["diagnostic"], result["stage"],
                                {r["Package"]: r["Version"] for r in guest["packages"]})


def execute(image, receipt, guest, call):
    """call is the bounded trusted-host Docker wrapper; fakes never run image code."""
    tag = receipt["image_tag"]
    require(re.fullmatch(r"nib-apt-simulation:[0-9a-f]{24}", tag))
    name = "nib-apt-" + uuid.uuid4().hex
    label = "org.nib.apt-run=" + name
    image_owned = container_owned = completion_unknown = False
    report = {"schema": "nib.apt-container-run/v2", "result": "fail", "stage": "preflight",
              "git_sha": receipt["git_sha"], "image_archive_sha256": receipt["archive_sha256"],
              "config_sha256": receipt["config_sha256"], "layer_sha256": receipt["layer_sha256"],
              "simulation": None, "cleanup": "not-created",
              "installable_verified": False, "bootable": False, "deployable": False}
    try:
        require(not call(["image", "ls", "--quiet", "--filter", "reference=" + tag]).stdout.strip())
        image_owned = True
        report["stage"] = "load"
        completion_unknown = True
        with tempfile.TemporaryFile() as source:
            source.write(image)
            source.seek(0)
            call(["image", "load"], stdin=source, timeout=120)
        observed = json.loads(call(["image", "inspect", tag]).stdout)
        require(len(observed) == 1 and observed[0]["Os"] == "linux"
                and observed[0]["Architecture"] == "amd64"
                and observed[0]["RootFS"] == {"Type": "layers", "Layers": ["sha256:" + receipt["layer_sha256"]]}
                and observed[0]["Config"]["Labels"]["org.opencontainers.image.revision"] == receipt["git_sha"])
        completion_unknown = False
        report["stage"] = "create"
        container_owned = True
        completion_unknown = True
        call(["container", "create", "--name", name, "--label", label, "--pull=never",
              "--platform=linux/amd64", "--network=none", "--read-only", "--ipc=private",
              "--cap-drop=ALL", "--security-opt=no-new-privileges", "--pids-limit=64",
              "--memory=1g", "--memory-swap=1g", "--cpus=2", "--user=65534:65534", "--init",
              "--tmpfs=/work:rw,nosuid,nodev,noexec,size=128m,uid=65534,gid=65534,mode=0700",
              "--no-healthcheck", "--stop-timeout=5", tag])
        info = json.loads(call(["container", "inspect", name]).stdout)
        require(len(info) == 1)
        validate_container(info[0], tag)
        completion_unknown = False
        report["stage"] = "simulation"
        result = call(["container", "start", "--attach", name], timeout=160, check=False)
        terminal = json.loads(call(["container", "inspect", name]).stdout)[0]["State"]
        require(terminal["Running"] is False and terminal["OOMKilled"] is False
                and terminal["ExitCode"] in (0, 1) and result.returncode == terminal["ExitCode"]
                and not result.stderr)
        parsed = json.loads(result.stdout)
        validate_result(parsed, receipt, guest)
        require((parsed["result"] == "pass") == (result.returncode == 0))
        report["simulation"] = parsed
        report["stage"] = "complete"
        report["result"] = parsed["result"]
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        pass
    finally:
        failures = []
        if container_owned:
            try:
                call(["container", "rm", "--force", name], check=False)
                require(not call(["container", "ls", "--all", "--quiet", "--filter",
                                  "label=" + label]).stdout.strip())
            except (OSError, ValueError, subprocess.SubprocessError):
                failures.append("container")
        if image_owned:
            try:
                call(["image", "rm", tag], check=False)
                require(not call(["image", "ls", "--quiet", "--filter",
                                  "reference=" + tag]).stdout.strip())
            except (OSError, ValueError, subprocess.SubprocessError):
                failures.append("image")
        if failures or completion_unknown:
            report["cleanup"], report["result"] = "unconfirmed", "fail"
        elif image_owned or container_owned:
            report["cleanup"] = "confirmed"
    return report


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", required=True,
                        help="explicitly authorize this local, CPU-only Docker execution")
    for name in ("metadata", "archives", "bundle", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    prep.separate_output(args.output, (args.metadata, args.archives, args.bundle))
    require(not args.output.exists() and not args.output.is_symlink())
    revision, sources = prep.committed()
    require(sources[prep.FILES[0]] == PREPARER_SOURCE and sources[prep.FILES[1]] == SIMULATION_SOURCE)
    files, receipt = prep.build(args.metadata, args.archives, revision, sources)
    base.verify(args.bundle, files)
    started = utc()
    with tempfile.TemporaryDirectory(prefix="nib-apt-control-") as temporary:
        control = Path(temporary).resolve()
        (control / "docker").mkdir(mode=0o700)
        env = {**base.TOOL_ENV, "HOME": str(control), "DOCKER_CONFIG": str(control / "docker")}
        docker = shutil.which("docker", path=env["PATH"])
        require(docker is not None)
        endpoint = base.processes.execute_process_group(
            [docker, "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
            env={**env, "HOME": str(Path.home()), "DOCKER_CONFIG": str(Path.home() / ".docker")},
            timeout=10, max_output_bytes=16384, stdin=subprocess.DEVNULL)
        endpoint.check_returncode()
        host = endpoint.stdout.decode().strip()
        require(host.startswith("unix:///") and "\n" not in host and "\r" not in host)
        env["DOCKER_HOST"] = host
        deadline = time.monotonic() + 360

        def call(args, *, stdin=subprocess.DEVNULL, timeout=20, check=True):
            # Cleanup has its own bounded calls after an interrupted attach.
            cleanup = args[:2] in (["container", "rm"], ["container", "ls"],
                                   ["image", "rm"], ["image", "ls"])
            remaining = 20 if cleanup else min(timeout, deadline - time.monotonic())
            require(0 < remaining <= 160)
            value = base.processes.execute_process_group(
                [docker, *args], env=env, timeout=remaining, max_output_bytes=128 << 10, stdin=stdin)
            if check:
                value.check_returncode()
            return value
        def interrupted(signum, frame):
            for sig in (signal.SIGINT, signal.SIGTERM):
                signal.signal(sig, signal.SIG_IGN)
            raise ValueError("simulation-interrupted")

        previous = {sig: signal.signal(sig, interrupted) for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            result = execute(files["image.tar"], receipt, json.loads(sources[prep.FILES[-1]]), call)
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
    result.update(started_at=started, finished_at=utc())
    base.publish(args.output, {"report.json": base.encoded(result)})
    print(json.dumps({k: result[k] for k in ("result", "stage", "cleanup", "git_sha",
                      "installable_verified", "deployable")}, sort_keys=True))
    return 0 if result["result"] == "pass" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        sys.exit("APT container simulation rejected; no installation success may be inferred.")
