# SPDX-License-Identifier: Apache-2.0
"""Private-pipe synthetic collector fixture; never imports a model or engine."""

import json
import os
from pathlib import Path
import signal
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stream_c8 as c8

mode, model = sys.argv[1:3]
expected = json.loads(sys.argv[4])
assert os.getsid(0) == os.getpid()
for fd, device, inode in json.loads(sys.argv[3]):
    try:
        info = os.fstat(fd)
    except OSError:
        continue
    assert (info.st_dev, info.st_ino) != (device, inode), "unrelated descriptor inherited"

if mode in ("malformed", "oversized", "nested", "no-ready"):
    sys.stdin.buffer.readline(4097)
    if mode == "no-ready":
        time.sleep(30)
    os.write(1, c8.READY)
    assert sys.stdin.buffer.read(1) == c8.GO
    os.write(1, b"[" * 1500 + b"0" + b"]" * 1500 if mode == "nested"
             else b"PRIVATE_CANARY" if mode == "malformed" else b"x" * 5000)
else:
    if mode.startswith("http"):
        checks = []
        def check(self, deadline, sock=None):
            assert threading.current_thread() is threading.main_thread()
            assert self.expected == expected
            checks.append(sock is not None)
            if sock is not None:
                assert sock.getpeername() == (c8.stream.HOST, c8.stream.PORT)
                if mode == "http-peer-fail":
                    raise ValueError("PRIVATE_CANARY")
        c8.stream.identity.Endpoint.check = check
        original = c8.stream.OwnedStreamHTTP.collect
        def collect(self, *args, **kwargs):
            result = original(self, *args, **kwargs)
            if result.status == "completed":
                assert checks == [False, True, False]
            return result
    else:
        def collect(self, messages, **options):
            assert threading.current_thread() is threading.main_thread()
            assert self.endpoint.expected == expected
            started = time.monotonic()
            if mode == "lost":
                os._exit(4)
            if mode == "stubborn":
                signal.signal(signal.SIGINT, signal.SIG_IGN)
                time.sleep(30)
            try:
                with c8.stream.worker.alarm(options["deadline"]):
                    time.sleep(0.2 if mode != "known-cancel" else 30)
            except (KeyboardInterrupt, c8.stream.worker.Abort):
                return c8.meter.Sample("cancelled", time.monotonic() - started,
                                      None, None, None, 2, 2048, 5, None, "stop")
            return c8.meter.Sample("protocol-error" if mode == "known-failure" else "completed",
                                  time.monotonic() - started, 0.01, 0.01, 0.1, 2, 2048, 5, None, "stop")
    c8.stream.OwnedStreamHTTP.collect = collect
    c8.child(model)
    if mode == "bad-exit":
        os._exit(7)
