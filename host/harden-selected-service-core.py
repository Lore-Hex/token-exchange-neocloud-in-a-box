#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Explicit core-policy migration; no restart, inference, or serving-process signal."""

import sys
from pathlib import Path

ROOT = Path(__file__).absolute().parents[1]
if __name__ == "__main__":
    if not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
        sys.exit("Use python3 -I -S -B host/harden-selected-service-core.py")
    sys.path.insert(0, str(ROOT / "scripts"))

import argparse
import hashlib
import os
import stat
import time

import selected_core_limits as core
import selected_service_owner as owner

network, smoke = owner.network, owner.smoke
require = network.require
host = network.source_module("core_policy_host", "host/record-selected-service.py")
CONFIG = Path("/etc/sysctl.d/zz-nib-disable-core.conf")
MASK = Path("/etc/systemd/system/apport.service")
WORKER = ROOT / "scripts/selected_core_limits.py"


class Receipt(network.Journal):
    def __init__(self, root):
        self.root = Path(root)

    def save(self, name, value):
        require(name in ("baseline.json", "result.json"))
        with self._directory() as fd:
            self._check(fd)
            if name == "baseline.json":
                with os.scandir(fd) as entries:
                    require(next(entries, None) is None)
            self._write(fd, name, value)


class Commands(network.Commands):
    def inspect(self, identifier):
        # Never retrieve environment, argv, mounts, labels other than our token,
        # or the container log path. Reuse Docker.owned's exact-ID join.
        fields = '{"Id":{{json .Id}},"Name":{{json .Name}},"Image":{{json .Image}},' \
            '"State":{"Running":{{json .State.Running}},"Paused":{{json .State.Paused}},' \
            '"Restarting":{{json .State.Restarting}},"Dead":{{json .State.Dead}},"Pid":{{json .State.Pid}}},' \
            '"Config":{"User":{{json .Config.User}},"Labels":{' \
            '"' + smoke.LABEL + '":{{json (index .Config.Labels "' + smoke.LABEL + '")}}}},' \
            '"HostConfig":{"PidMode":{{json .HostConfig.PidMode}},"Ulimits":{{json .HostConfig.Ulimits}}}}'
        return network.policy.decode(self.call(
            ["docker", "container", "inspect", "--format", fields, identifier], limit=8192))


def binding(commands, state, daemon):
    require(owner.daemon_identity(commands, daemon[0]) == daemon)
    rows = state.read()
    require("start-intent.json" in rows)
    contract, runtime = rows["contract.json"], rows["runtime.json"]
    require(time.time() < contract["wall"] and time.monotonic() < contract["monotonic"])
    token = contract["network"]["policy"]["token"]
    identifier = rows["container-resolved.json"]["id"]
    info = commands.owned("nib-selected-" + token, token, runtime["image_id"], identifier)
    require(info is not None and info["Config"]["User"] == "65532:65532"
            and not info["HostConfig"]["PidMode"] and info["State"]["Running"] is True
            and all(info["State"][key] is False for key in ("Paused", "Restarting", "Dead")))
    pid = info["State"]["Pid"]
    require(type(pid) is int and pid > 1)
    space = core.namespace(f"/proc/{pid}/ns/pid")
    require(space != core.namespace("/proc/self/ns/pid"))
    start = core.read(f"/proc/{pid}/stat", 4096).rsplit(b") ", 1)[1].split()[19].decode("ascii")
    require(start.isdecimal() and int(start) > 0)
    return {"container": identifier, "image": runtime["image_id"], "pid": pid, "start": start,
            "namespace": space, "ulimits": info["HostConfig"]["Ulimits"]}


def limits(commands, selected, mode, source):
    raw = commands.run(["docker", "exec", "--interactive", "--user", "65532:65532",
        selected["container"], "/usr/bin/python3.12", "-I", "-S", "-B", "-", mode,
        *(str(value) for value in selected["namespace"])], data=source).stdout
    require(len(raw) <= 1024)
    row = network.policy.decode(raw)
    require(type(row) is dict and set(row) == {"processes", "zero", "limits"}
            and type(row["processes"]) is int and 0 < row["processes"] <= core.MAX_PROCESSES
            and type(row["zero"]) is int and 0 <= row["zero"] <= row["processes"]
            and type(row["limits"]) is list and len(row["limits"]) <= 16
            and all(type(pair) is list and len(pair) == 2 and all(type(n) is int and -1 <= n < 2**64
                for n in pair) for pair in row["limits"]))
    require(mode != "apply" or (row["zero"] == row["processes"] and row["limits"] == [[0, 0]]))
    return row


def config_bytes():
    try:
        with network.tau_runtime.directory(CONFIG.parent) as fd:
            info = os.stat(CONFIG.name, dir_fd=fd, follow_symlinks=False)
            require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                    and not info.st_mode & 0o022)
            return network.tau_runtime.read_file(CONFIG, 4096, time.monotonic() + 1)
    except FileNotFoundError:
        return None


def mask_state():
    with network.tau_runtime.directory(MASK.parent) as fd:
        try:
            info = os.stat(MASK.name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return "absent"
        require(stat.S_ISLNK(info.st_mode) and info.st_uid == 0
                and os.readlink(MASK.name, dir_fd=fd) == "/dev/null")
    info = Path("/dev/null").lstat()
    require(stat.S_ISCHR(info.st_mode) and (os.major(info.st_rdev), os.minor(info.st_rdev)) == (1, 3))
    return "masked"


def check_order():
    # Reject even unrelated later files rather than inventing a second sysctl
    # parser. Earlier files cannot override these exact assignments at boot.
    count = 0
    for directory in ("/etc/sysctl.d", "/run/sysctl.d", "/usr/local/lib/sysctl.d", "/usr/lib/sysctl.d"):
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    count += 1
                    require(count <= 1024 and not (entry.name.endswith(".conf") and entry.name > CONFIG.name))
        except FileNotFoundError:
            continue


def install(commands):
    check_order()
    require(config_bytes() in (None, core.CONFIG))
    if config_bytes() is None:
        with network.tau_runtime.directory(CONFIG.parent) as fd:
            target = os.open(CONFIG.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=fd)
            with os.fdopen(target, "wb") as stream:
                stream.write(core.CONFIG)
                stream.flush()
                os.fsync(stream.fileno())
            os.fsync(fd)
    require(config_bytes() == core.CONFIG)
    mask_state()
    # No --now: Apport's ExecStop resets core_pattern; never invoke it here.
    commands.call(["systemctl", "mask", "--no-reload", "apport.service"], limit=4096)
    require(mask_state() == "masked")
    with network.tau_runtime.directory(MASK.parent) as fd:
        os.fsync(fd)
    commands.call(["systemctl", "daemon-reload"], limit=4096)
    require(commands.call(["systemctl", "show", "--property=LoadState", "--value", "apport.service"],
                          limit=128).strip() == b"masked")
    # This explicit file exercises systemd's empty-assignment parser; no --system
    # and no unrelated sysctls. Its writer emits a newline even for an empty value.
    commands.call(["/usr/lib/systemd/systemd-sysctl", str(CONFIG)], limit=4096)
    core.check_host()
    check_order()


def execute(state, receipt, commands, apply):
    source = network.tau_runtime.read_file(WORKER, 16384, time.monotonic() + 1)
    daemon = owner.daemon_identity(commands)
    selected = binding(commands, state, daemon)
    require(config_bytes() in (None, core.CONFIG))
    check_order()
    before = {"schema": "nib.selected-core-policy/v1", "mode": "apply" if apply else "inspect",
        "utc_unix": time.time(), "identity": selected, "host": core.host_values(), "apport": mask_state(),
        "config_present": config_bytes() is not None, "limits": limits(commands, selected, "inspect", source),
        "worker_sha256": hashlib.sha256(source).hexdigest()}
    receipt.save("baseline.json", before)
    result = {"schema": before["schema"], "result": "fail", "applied": False, "apply_requested": apply,
              "core_policy_verified": False, "persistent_policy_verified": False,
              "zdr": False, "confidential": False, "restart_performed": False}
    try:
        require(binding(commands, state, daemon) == selected)
        if apply:
            install(commands)
            require(binding(commands, state, daemon) == selected)
            result["limits"] = limits(commands, selected, "apply", source)
            core.check_host()
            require(mask_state() == "masked" and config_bytes() == core.CONFIG)
            result.update(core_policy_verified=True, persistent_policy_verified=True)
        else:
            result["core_policy_verified"] = (before["host"] == {k: v.decode() for k, v in core.SETTINGS.items()}
                and before["limits"]["zero"] == before["limits"]["processes"])
            result["persistent_policy_verified"] = (result["core_policy_verified"]
                and before["apport"] == "masked" and before["config_present"])
        require(binding(commands, state, daemon) == selected)
        result["applied"] = apply
        result["result"] = "pass"
    finally:
        result["utc_unix"] = time.time()
        receipt.save("result.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner-state", required=True, type=Path)
    parser.add_argument("--receipt-dir", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        require(sys.platform == "linux" and os.getuid() == 0)
        state, receipt = owner.State(args.owner_state), Receipt(args.receipt_dir)
        contract = state.read()["contract.json"]
        _, recorder = smoke.host_recorder()
        wall, deadline = min(time.time() + 90, contract["wall"]), min(time.monotonic() + 90, contract["monotonic"])
        commands = Commands(recorder, host.environment(), wall, deadline)
        with smoke.service.identity.worker.alarm(deadline):
            execute(state, receipt, commands, args.apply)
        print("core-policy-receipt-written")
        return 0
    except BaseException:
        print("core-policy-failed; preserve private receipts; do not retry automatically")
        return 1


if __name__ == "__main__":
    sys.exit(main())
