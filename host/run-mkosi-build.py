#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Root-only resource launcher; drops to the existing hosted runner before build."""
import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode):
    sys.exit("Use python3 -I -S -B host/run-mkosi-build.py")

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import platform
import pwd
import runpy
import signal
import shutil
import stat
import subprocess
import types

ROOT = Path(__file__).absolute().parent.parent
resources = types.SimpleNamespace(**runpy.run_path(str(ROOT / "host/probe-build-resources.py")))
base = types.SimpleNamespace(**runpy.run_path(str(ROOT / "scripts/prepare_guest_packages.py")))
require = resources.require
CHILD = """
import os, sys
fd, uid, gid = map(int, sys.argv[1:4])
assert uid > 0 and gid > 0
assert os.write(fd, str(os.getpid()).encode('ascii')) > 0
os.close(fd)
os.setgroups([])
os.setgid(gid)
os.setuid(uid)
assert os.getuid() == os.geteuid() == uid and os.getgid() == os.getegid() == gid
assert os.getgroups() == []
os.execve('/usr/bin/python3.10', ['/usr/bin/python3.10', '-I', '-S', '-B', *sys.argv[4:]],
          {'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'LANG': 'C', 'LC_ALL': 'C',
           'RUNNER_ENVIRONMENT': 'github-hosted'})
"""


def identity():
    release = platform.freedesktop_os_release()
    require(sys.platform == "linux" and platform.machine() == "x86_64"
            and os.getuid() == os.geteuid() == 0 and sys.version_info[:2] == (3, 10)
            and release.get("ID") == "ubuntu" and release.get("VERSION_ID") == "22.04"
            and Path(sys.executable).resolve() == Path("/usr/bin/python3.10").resolve())
    runner = pwd.getpwnam("runner")
    require(runner.pw_uid > 0 and runner.pw_gid > 0
            and os.environ.get("SUDO_UID") == str(runner.pw_uid)
            and os.environ.get("SUDO_GID") == str(runner.pw_gid)
            and ROOT.stat().st_uid == runner.pw_uid and not ROOT.stat().st_mode & 0o022)
    return runner.pw_uid, runner.pw_gid


def launch(fd, uid, gid, arguments):
    procs = os.open("cgroup.procs", os.O_WRONLY | os.O_NOFOLLOW, dir_fd=fd)
    child = None
    try:
        child = subprocess.Popen(["/usr/bin/python3.10", "-I", "-S", "-B", "-c", CHILD,
            str(procs), str(uid), str(gid), str(ROOT / "host/build-mkosi-native.py"), *arguments],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            pass_fds=(procs,), start_new_session=True, cwd=ROOT,
            env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C"})
        return child.wait(timeout=2500)
    finally:
        os.close(procs)
        if child is not None and child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait(timeout=10)


@contextmanager
def receipt_file(path, uid, gid):
    # Anchor creation to a no-symlink directory descriptor. Keep the fresh file
    # open across the child run, so replacement cannot redirect a root write.
    with base.directory(path.parent) as parent:
        identity = os.fstat(parent)
        require(identity.st_uid == uid and not identity.st_mode & 0o022)
        descriptor = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=parent)
        try:
            require(stat.S_ISREG(os.fstat(descriptor).st_mode) and os.fstat(descriptor).st_nlink == 1)
            yield descriptor
            os.fsync(descriptor)
            os.fchown(descriptor, uid, gid)
        finally:
            os.close(descriptor)


def execute(uid, gid, arguments):
    state = {"schema": "nib.mkosi-build-resources/v1", "result": "fail", "limits": resources.LIMITS,
             "limits_read_back": False, "empty_verified": False, "group_removed": False,
             "child_exit_code": None}
    try:
        with resources.cgroup(state) as (fd, _):
            # Permit limit readback after dropping to runner, never cgroup writes.
            os.fchmod(fd, 0o755)
            state["child_exit_code"] = launch(fd, uid, gid, arguments)
        require(state["child_exit_code"] == 0 and state["limits_read_back"]
                and state["empty_verified"] and state["group_removed"] and state["oom_kill"] == 0)
        state["result"] = "pass"
    except (OSError, ValueError, subprocess.SubprocessError, KeyboardInterrupt):
        state["result"] = "fail"
    return state


def export_preflight(output, uid):
    # The two names are reserved for this invocation; collisions are never
    # adopted or deleted. Images are siblings, outside the evidence upload tree.
    with base.directory(output.parent) as parent:
        info = os.fstat(parent)
        require(info.st_uid == uid and stat.S_IMODE(info.st_mode) == 0o700)
        for name in ("." + output.name + "-boot-pending", output.name + "-boot"):
            try:
                os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                continue
            raise ValueError("private-boot-export-collision")


def finish_export(output, uid, gid):
    # No root file copying/publication. All cgroup work and the durable
    # independent resource receipt precede this irreversible privilege drop.
    os.setgroups([])
    os.setgid(gid)
    os.setuid(uid)
    require(os.getuid() == os.geteuid() == uid > 0 and os.getgid() == os.getegid() == gid > 0
            and os.getgroups() == [])
    with base.directory(output.parent) as parent:
        name = "." + output.name + "-boot-pending"
        original = os.stat(name, dir_fd=parent, follow_symlinks=False)
        require(stat.S_ISDIR(original.st_mode) and original.st_uid == uid
                and stat.S_IMODE(original.st_mode) == 0o700)
        try:
            # Bound the fresh source audit as well as the final copy/hash phase.
            def expired(signum, frame):
                raise ValueError("private-boot-source-audit-timeout")
            require(signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0))
            previous = signal.signal(signal.SIGALRM, expired)
            signal.setitimer(signal.ITIMER_REAL, 120)
            try:
                native, _ = base.helper("native_boot_export", "host/build-mkosi-native.py")
                revision, sources = native.committed_sources(True, True, True, True)
                export, publication = native.export_helpers(sources)
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0)
                signal.signal(signal.SIGALRM, previous)
        except BaseException:
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
            require((current.st_dev, current.st_ino) == (original.st_dev, original.st_ino))
            shutil.rmtree(name, dir_fd=parent)
            raise
    return export.finish(output, base, publication, native.source_identity(revision, sources))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("downloads", "archive", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--compile-driver", action="store_true")
    parser.add_argument("--install-driver", action="store_true")
    parser.add_argument("--guest-runtime", action="store_true")
    parser.add_argument("--verity-root", action="store_true")
    parser.add_argument("--boot-uki", action="store_true")
    parser.add_argument("--export-boot", action="store_true",
                        help="publish a private OUTPUT-boot GPT/UKI bundle after all build/cleanup checks")
    args = parser.parse_args()
    require(not args.install_driver or args.compile_driver)
    require(not args.verity_root or (args.guest_runtime and args.install_driver))
    require(not args.boot_uki or args.verity_root)
    require(not args.export_boot or args.boot_uki)
    uid, gid = identity()
    require(all(path.is_absolute() and ".." not in path.parts
                for path in (args.downloads, args.archive, args.output)))
    require(not args.output.exists() and not args.output.is_symlink())
    if args.export_boot:
        export_preflight(args.output, uid)
    # The unprivileged child publishes its evidence. Root writes only this
    # separate fresh sibling receipt after the cgroup has been killed/removed.
    receipt = args.output.with_name(args.output.name + "-resources.json")
    with receipt_file(receipt, uid, gid) as descriptor:
        def cancel(signum, frame):
            raise KeyboardInterrupt
        previous = {signum: signal.signal(signum, cancel)
                    for signum in (signal.SIGINT, signal.SIGTERM)}
        try:
            state = execute(uid, gid, ["--downloads", str(args.downloads),
                "--archive", str(args.archive), "--output", str(args.output),
                *(["--compile-driver"] if args.compile_driver else []),
                *(["--install-driver"] if args.install_driver else []),
                *(["--guest-runtime"] if args.guest_runtime else []),
                *(["--verity-root"] if args.verity_root else []),
                *(["--boot-uki"] if args.boot_uki else []),
                *(["--stage-boot"] if args.export_boot else [])])
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)
        raw = (json.dumps(state, sort_keys=True, indent=2) + "\n").encode()
        require(os.write(descriptor, raw) == len(raw))
    if args.export_boot:
        # finish also rejects a failed resource receipt and removes pending
        # artifacts. Its final no-replace rename is the only publication point.
        finish_export(args.output, uid, gid)
    print("Bounded native installation: " + state["result"])
    return 0 if state["result"] == "pass" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        sys.exit("Build resource launcher rejected; no qualification.")
