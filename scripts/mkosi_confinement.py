#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Final package-worker privilege boundary; only after namespace setup."""
import ctypes
import errno
import os
from pathlib import Path
import re
import stat

# util-linux 2.39.3 setpriv(1); Linux 6.8 uapi/linux/capability.h.
CAPS = ("chown", "dac_override", "fowner", "fsetid", "setgid", "setuid", "sys_chroot", "setfcap")
CAP_MASK = sum(1 << bit for bit in (0, 1, 3, 4, 6, 7, 18, 31))
PREFIX = ("/usr/bin/setpriv", "--no-new-privs", "--inh-caps=-all", "--ambient-caps=-all",
          "--bounding-set=-all," + ",".join("+" + cap for cap in CAPS), "--")
# Linux 6.8 sched.h: clone's seven namespace flags (NEWTIME is not a clone flag).
CLONE_NAMESPACES = (0x20000, 0x2000000, 0x4000000, 0x8000000, 0x10000000, 0x20000000, 0x40000000)
DENIED = ("unshare", "setns", "mount", "umount2", "pivot_root", "move_mount", "fsopen", "fsconfig",
          "fsmount", "open_tree", "mount_setattr", "ptrace", "process_vm_readv", "process_vm_writev",
          "open_by_handle_at", "bpf", "perf_event_open", "userfaultfd", "io_uring_setup")


def require(value):
    if not value:
        raise ValueError("confinement-rejected")


def status_check(text, *, filtered=False):
    expected = {"CapInh": 0, "CapPrm": CAP_MASK, "CapEff": CAP_MASK, "CapBnd": CAP_MASK, "CapAmb": 0}
    for field, value in expected.items():
        matches = re.findall(r"^" + field + r":\s*([0-9a-f]{16})$", text, re.M)
        require(len(matches) == 1 and int(matches[0], 16) == value)
    require(re.findall(r"^NoNewPrivs:\s*(\d+)$", text, re.M) == ["1"])
    if filtered:
        require(re.findall(r"^Seccomp:\s*(\d+)$", text, re.M) == ["2"])


class Comparison(ctypes.Structure):
    _fields_ = (("arg", ctypes.c_uint), ("op", ctypes.c_int),
                ("datum_a", ctypes.c_uint64), ("datum_b", ctypes.c_uint64))


def library():
    # Absolute path in the reconstructed, hash-verified read-only tools tree.
    lib = ctypes.CDLL("/usr/lib/x86_64-linux-gnu/libseccomp.so.2", use_errno=True)
    signatures = {
        "seccomp_init": (ctypes.c_void_p, [ctypes.c_uint32]),
        "seccomp_release": (None, [ctypes.c_void_p]),
        "seccomp_syscall_resolve_name": (ctypes.c_int, [ctypes.c_char_p]),
        "seccomp_rule_add_array": (ctypes.c_int, [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int,
                                                ctypes.c_uint, ctypes.POINTER(Comparison)]),
        "seccomp_load": (ctypes.c_int, [ctypes.c_void_p]),
    }
    for name, (result, arguments) in signatures.items():
        method = getattr(lib, name)
        method.restype, method.argtypes = result, arguments
    return lib


def filter_install(lib):
    # libseccomp 2.5.5 seccomp.h: ALLOW, ERRNO, MASKED_EQ. Default native
    # architecture only: foreign-ABI calls retain libseccomp's kill action.
    context = lib.seccomp_init(0x7fff0000)
    require(context)
    try:
        def rule(name, error, comparisons=()):
            number = lib.seccomp_syscall_resolve_name(name.encode("ascii"))
            require(number >= 0)
            rows = (Comparison * len(comparisons))(*comparisons)
            require(lib.seccomp_rule_add_array(context, 0x50000 | error, number, len(rows), rows) == 0)
        for name in DENIED:
            rule(name, errno.EPERM)
        # clone3 takes a pointer, so filters cannot inspect its flags. ENOSYS
        # lets libc fall back to ordinary clone for normal process/thread use.
        rule("clone3", errno.ENOSYS)
        for flag in CLONE_NAMESPACES:
            rule("clone", errno.EPERM, (Comparison(0, 7, flag, flag),))
        require(lib.seccomp_load(context) == 0)
    finally:
        lib.seccomp_release(context)


def file_descriptors_check():
    # scandir's own transient FD disappears before checking. No directory or
    # hidden host mount handle may survive the trusted launcher.
    with os.scandir("/proc/self/fd") as entries:
        names = [entry.name for entry in entries]
    for name in names:
        require(name.isdecimal())
        if int(name) > 2:
            try:
                os.stat("/proc/self/fd/" + name)
            except FileNotFoundError:
                continue
            raise ValueError("confinement-rejected")
    for fd in (0, 1, 2):
        require(not stat.S_ISDIR(os.fstat(fd).st_mode))


def check():
    require(os.uname().machine == "x86_64" and os.getcwd() == "/")
    status_check(Path("/proc/self/status").read_text())
    file_descriptors_check()
    filter_install(library())
    status_check(Path("/proc/self/status").read_text(), filtered=True)
    libc = ctypes.CDLL(None, use_errno=True)
    libc.unshare.argtypes, libc.unshare.restype = [ctypes.c_int], ctypes.c_int
    libc.mount.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
                          ctypes.c_ulong, ctypes.c_void_p]
    libc.mount.restype = ctypes.c_int
    for call in (lambda: libc.unshare(0x10000000),
                 lambda: libc.mount(None, b"/nib", None, 0x1020, None)):
        ctypes.set_errno(0)
        require(call() == -1 and ctypes.get_errno() == errno.EPERM)
    try:
        fd = os.open("/usr/confinement-write-probe", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except OSError as exc:
        require(exc.errno == errno.EROFS)
    else:
        os.close(fd)
        os.unlink("/usr/confinement-write-probe")
        raise ValueError("confinement-rejected")
