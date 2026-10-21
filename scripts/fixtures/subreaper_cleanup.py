# SPDX-License-Identifier: Apache-2.0
"""Private Linux regression subprocess: adopted grandchildren, no host setting."""
import ctypes
import os
from pathlib import Path
import runpy
import subprocess
import sys
import time
import types
from unittest import mock

assert sys.platform == "linux"
# Linux v6.8 include/uapi/linux/prctl.h: PR_SET_CHILD_SUBREAPER = 36.
# https://man7.org/linux/man-pages/man2/PR_SET_CHILD_SUBREAPER.2const.html
libc = ctypes.CDLL(None, use_errno=True)
libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
libc.prctl.restype = ctypes.c_int
assert libc.prctl(36, 1, 0, 0, 0) == 0
snapshot = types.SimpleNamespace(**runpy.run_path(sys.argv[1]))
child_source = """
import os, sys
child = os.fork()
if child == 0:
    os._exit(0)
os.waitid(os.P_PID, child, os.WEXITED | os.WNOWAIT)
os.write(1, b'public diagnostic')
os.write(2, str(child).encode('ascii'))
os._exit(int(sys.argv[1]))
"""
unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"], start_new_session=True)
try:
    child = subprocess.Popen([sys.executable, "-I", "-c", child_source, "7"], start_new_session=True,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = child.communicate(timeout=5)
    assert child.returncode == 7 and stdout == b"public diagnostic" and stderr.isdigit()
    os.killpg(child.pid, 0)  # The exited grandchild still owns the group.
    status = (Path("/proc") / str(int(stderr)) / "status").read_bytes()
    assert b"State:\tZ" in status and (b"PPid:\t" + str(os.getpid()).encode() + b"\n") in status
    with mock.patch.dict(snapshot.cleanup_process_group.__globals__, {"reap_group_children": lambda _: None}):
        try:
            snapshot.cleanup_process_group(child)
        except ValueError:
            pass
        else:
            raise AssertionError("old cleanup accepted adopted zombie")
    snapshot.cleanup_process_group(child)
    assert child.returncode == 7 and unrelated.poll() is None
    try:
        os.killpg(child.pid, 0)
    except ProcessLookupError:
        pass
    else:
        raise AssertionError("tool group survived")
    # The shared execution path now returns the real nonzero installer result.
    result = snapshot.execute_process_group([sys.executable, "-I", "-c", child_source, "7"],
                                            env=os.environ, timeout=5, max_output_bytes=1024)
    assert result.returncode == 7 and result.stdout == b"public diagnostic"
finally:
    unrelated.terminate()
    unrelated.wait(timeout=5)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            break
        if not pid:
            time.sleep(0.01)
print("old-rejected new-reaped exit-status-preserved")
