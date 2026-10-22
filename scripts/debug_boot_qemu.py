#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""One fixed TCG boot in an offline, read-only authenticated namespace."""
import json
import os
from pathlib import Path
import resource
import socket
import subprocess
import sys
import threading
import time
import types

TIMEOUT = 180
EXPECTED = b"NIB_DEBUG_BOOT_V1 INITRD\nNIB_DEBUG_BOOT_V1 PASS\n"
INITRD = b"NIB_DEBUG_BOOT_V1 INITRD\n"
FAILURE_STAGES = ("INITRD_STAGE", "INITRD_MARKER", "KERNEL", "PID1", "CMDLINE",
    "PID1_MOUNT", "SELF_MOUNT", "MOUNT_MATCH", "DM_NAME", "DM_SUSPENDED", "DM_READONLY",
    "TABLE_QUERY", "TABLE_FORMAT", "ROOT_OPEN", "ROOT_READ", "TABLE_RECHECK",
    "STATUS_QUERY", "STATUS_FORMAT", "DEADLINE")
FAILURES = {b"NIB_DEBUG_BOOT_V1 FAIL\n": "unspecified",
            INITRD + b"NIB_DEBUG_BOOT_V1 FAIL\n": "unspecified"}
FAILURES.update({(b"" if stage == "INITRD_STAGE" else INITRD)
                 + b"NIB_DEBUG_BOOT_V1 FAIL " + stage.encode("ascii") + b"\n": stage
                 for stage in FAILURE_STAGES})
RECORDS = (EXPECTED, *FAILURES)
MAX_PROOF_BYTES = max(map(len, RECORDS))
ROOT_READ_PREFIX = INITRD + b"NIB_DEBUG_BOOT_V1 R "
ROOT_READ_REASONS = {1: "size", 2: "deadline", 3: "short-read", 4: "io",
                     5: "extra-data", 6: "eof", 7: "late-eof", 8: "close"}
MAX_ROOT_BYTES = 1536 << 20
MAX_ROOT_READ_SECONDS = 145
SOCKET = Path("/work/proof.sock")
QEMU = "/runtime/usr/bin/qemu-system-x86_64"
LOADER = "/runtime/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2"
LIBRARIES = "/runtime/lib/x86_64-linux-gnu:/runtime/usr/lib/x86_64-linux-gnu"
CODE = "/runtime/usr/share/OVMF/OVMF_CODE.fd"
VARS = "/runtime/usr/share/OVMF/OVMF_VARS.fd"
DROP_CAPS = ("/usr/bin/setpriv", "--no-new-privs", "--inh-caps=-all", "--ambient-caps=-all", "--bounding-set=-all", "--")


def require(value):
    if not value:
        raise ValueError("debug-boot-rejected")


def module(name):
    value = types.ModuleType("debug_" + name)
    value.__file__ = "/nib/" + name + ".py"
    with open(value.__file__, "rb") as stream:
        raw = stream.read((1 << 20) + 1)
    require(0 < len(raw) <= 1 << 20)
    exec(compile(raw, value.__file__, "exec", dont_inherit=True), value.__dict__)
    return value


def prefix():
    # main() has re-execed with an empty bounding set before reaching here.
    return [LOADER, "--inhibit-cache", "--library-path", LIBRARIES, QEMU]


def environment():
    # QEMU v6.2.0 util/module.c reads this variable before searching module dirs.
    return {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C",
            "QEMU_MODULE_DIR": "/runtime/usr/lib/x86_64-linux-gnu/qemu"}


def command():
    # Pinned mkosi qemu.py / QEMU 6.2 qemu-options.hx. No caller flags or paths.
    return prefix() + ["-L", "/runtime/usr/share/qemu", "-machine", "q35,smm=off,hpet=off",
        "-accel", "tcg", "-cpu", "max", "-smp", "2", "-m", "1024", "-nodefaults",
        "-no-user-config", "-display", "none", "-serial", "none", "-monitor", "none", "-nic", "none",
        "-no-reboot", "-sandbox", "on,obsolete=deny,elevateprivileges=deny,spawn=deny,resourcecontrol=deny",
        "-drive", "if=pflash,format=raw,readonly=on,file=" + CODE,
        "-drive", "if=pflash,format=raw,file=/work/OVMF_VARS.fd",
        "-kernel", "/input/debug.efi",
        "-drive", "if=none,id=root,format=raw,readonly=on,file=/input/root.raw",
        "-device", "virtio-blk-pci,drive=root,logical_block_size=4096,physical_block_size=4096",
        "-device", "virtio-serial-pci", "-chardev", "socket,id=proof,path=" + str(SOCKET),
        "-device", "virtserialport,chardev=proof,name=org.nib.boot-proof"]


def startups(execute, observations):
    checks = ((["--version"], (b"QEMU emulator version 6.2.0",)),
              (["-accel", "help"], (b"tcg",)),
              (["-device", "virtio-blk-pci,help"], (b"logical_block_size", b"physical_block_size")),
              (["-device", "virtserialport,help"], (b"chardev", b"name")),
              (["-machine", "q35,help"], (b"hpet", b"smm")))
    for arguments, required in checks:
        result = execute(prefix() + arguments, env=environment(), timeout=10,
                         max_output_bytes=65536, max_stderr_bytes=65536, stdin=subprocess.DEVNULL)
        result.check_returncode()
        require(not result.stderr and all(word in result.stdout for word in required))
        observations.append({"check": len(observations), "stdout_bytes": len(result.stdout), "result": "pass"})
    return observations


def root_read_frame(raw):
    """Recognize only bounded canonical counters, including partial prefixes."""
    if ROOT_READ_PREFIX.startswith(raw):
        return True, None
    if not raw.startswith(ROOT_READ_PREFIX):
        return False, None
    body = raw[len(ROOT_READ_PREFIX):]
    complete = body.endswith(b"\n")
    fields = (body[:-1] if complete else body).split(b" ")
    if len(fields) > 3:
        return False, None
    values = []
    for i, field in enumerate(fields):
        if not field:
            return not complete and i == len(fields) - 1, None
        if not field.isdigit() or (len(field) > 1 and field.startswith(b"0")) or len(field) > (1, 10, 3)[i]:
            return False, None
        value = int(field)
        if (i == 0 and value not in ROOT_READ_REASONS) or value > (8, MAX_ROOT_BYTES, MAX_ROOT_READ_SECONDS)[i]:
            return False, None
        values.append(value)
    if not complete:
        return True, None
    if len(values) != 3:
        return False, None
    return True, {"reason": ROOT_READ_REASONS[values[0]], "bytes_read": values[1], "elapsed_seconds": values[2]}


def receive(listener, stop, deadline, state):
    """Only fixed protocol prefixes are retained; never report arbitrary bytes."""
    connection = None
    raw = b""
    eof = False
    try:
        # Drain an already queued connection even if QEMU exited before this
        # thread was scheduled. After one idle poll, a stopped child cannot send.
        while time.monotonic() < deadline:
            try:
                connection, _ = listener.accept()
                break
            except socket.timeout:
                if stop.is_set():
                    break
                continue
        if connection is None:
            state["protocol_error"] = "no-connection"
        require(connection is not None)
        connection.settimeout(0.1)
        while time.monotonic() < deadline:
            try:
                chunk = connection.recv(MAX_PROOF_BYTES + 1 - len(raw))
            except socket.timeout:
                if stop.is_set():
                    break
                continue
            if not chunk:
                eof = True
                state["complete"] = raw == EXPECTED
                if raw not in RECORDS and root_read_frame(raw)[1] is None:
                    state["protocol_error"] = "incomplete"
                break
            raw += chunk
            root_prefix, root_read = root_read_frame(raw)
            if not any(record.startswith(raw) for record in RECORDS) and not root_prefix:
                state["protocol_error"] = "invalid-frame"
                raise ValueError("debug-proof-rejected")
            state["initrd"] = raw.startswith(INITRD)
            state["root"] = raw == EXPECTED
            if raw in FAILURES:
                state["guest_failure"] = FAILURES[raw]
            if root_read is not None:
                state.update(guest_failure="ROOT_READ", root_read=root_read)
    except (OSError, ValueError):
        state["complete"] = False
    finally:
        if not eof and not state.get("protocol_error"):
            state["protocol_error"] = "missing-eof"
        if connection is not None:
            connection.close()


def trial(execute, state, terminal):
    stop = threading.Event()
    result, cleaned = None, False
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(SOCKET))
        listener.listen(1)
        listener.settimeout(0.1)
        thread = threading.Thread(target=receive, args=(listener, stop, time.monotonic() + TIMEOUT + 1, state))
        thread.start()
        try:
            result = execute(command(), env=environment(), timeout=TIMEOUT,
                             max_output_bytes=65536, max_stderr_bytes=65536, stdin=subprocess.DEVNULL)
            cleaned = True
            terminal.update(exit_code=result.returncode, stdout_bytes=len(result.stdout),
                            stderr_bytes=len(result.stderr), process_group_cleaned=True)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            terminal["failure_kind"] = "timeout" if isinstance(exc, subprocess.TimeoutExpired) else "process-or-cleanup"
            raise
        finally:
            stop.set()
            thread.join(timeout=2)
            require(not thread.is_alive())
            SOCKET.unlink()
    require(cleaned and result is not None and result.returncode == 0
            and not result.stdout and not result.stderr and state["complete"])
    return state


def main():
    report = {"schema": "nib.debug-qemu/v1", "result": "fail", "stage": "preflight",
              "tcg_only": True, "confidentiality_verified": False, "startup_checks": [], "boot_verified": False,
              "proof": {"initrd": False, "root": False, "complete": False,
                        "guest_failure": None, "protocol_error": None},
              "terminal": {"exit_code": None, "stdout_bytes": None, "stderr_bytes": None,
                           "process_group_cleaned": False, "failure_kind": None}}
    boot = module("boot-uki")
    try:
        require(sys.platform == "linux" and sys.version_info[:2] == (3, 12)
                and sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode
                and len(sys.argv) in (6, 7))
        module("namespace-check").check(sys.argv[1:5])
        module("pid-check").check(sys.argv[5])
        if len(sys.argv) == 6:
            # Mapping/chown probes need namespace capabilities. Drop all of
            # them in one re-exec while CAP_SETPCAP is still available; an
            # already restricted package-worker cannot drop its bounding set.
            module("ownership").check(json.loads(boot.read(Path("/nib/ownership-input.json"), 4096)))
            os.execve(DROP_CAPS[0], [*DROP_CAPS, "/usr/bin/python3.12", "-I", "-S", "-B",
                                   "/nib/debug-qemu.py", *sys.argv[1:], "--confined"], environment())
            raise ValueError("debug-reexec-returned")
        require(sys.argv[-1] == "--confined")
        guard = module("confinement")
        guard.CAP_MASK = 0  # Strictly narrower than the native package boundary.
        guard.check()
        os.umask(0o077)
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_FSIZE, (256 << 20, 256 << 20))
        config = json.loads(boot.read(Path("/nib/qemu-input.json"), 65536), object_pairs_hook=boot.unique_object)
        require(config["schema"] == "nib.debug-qemu-input/v1")
        for path, expected in config["runtime_members"].items():
            require(path in (QEMU, LOADER, CODE, VARS))
            require(boot.identity(boot.read(Path(path), 32 << 20)) == expected)
        require(set(config["runtime_members"]) == {QEMU, LOADER, CODE, VARS})
        require(boot.identity(boot.read(Path("/input/debug.efi"), boot.MAX_IMAGE)) == config["debug_uki"])
        boot.write(Path("/work/OVMF_VARS.fd"), boot.read(Path(VARS), 1 << 20))
        report["stage"] = "startup"
        processes = module("snapshot")
        startups(processes.execute_process_group, report["startup_checks"])
        report["stage"] = "boot"
        trial(processes.execute_process_group, report["proof"], report["terminal"])
        report.update(result="pass", stage="complete", boot_verified=True)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        report["result"] = "fail"
    finally:
        for path in (Path("/work/OVMF_VARS.fd"), SOCKET):
            if os.path.lexists(path):
                path.unlink()
    boot.write(Path("/out/qemu.json"), (json.dumps(report, sort_keys=True) + "\n").encode())
    return 0 if report["result"] == "pass" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError):
        sys.exit("NIB_DEBUG_QEMU_REJECTED")
