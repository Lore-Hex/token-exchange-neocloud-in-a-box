# SPDX-License-Identifier: Apache-2.0
"""Real private-pipe children and local synthetic HTTP, never an engine."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import unittest
from unittest import mock

import stream_scaling as scaling
import test_stream_c8 as fixtures
from test_stream_cohort import workload

ROOT = Path(__file__).absolute().parents[1]
REAL_POPEN = subprocess.Popen


class ProcessTests(unittest.TestCase):
    @contextmanager
    def children(self, mode="normal", *, fail_at=None, inherited=()):
        children = []
        def spawn(args, **kwargs):
            self.assertEqual(args, ["/opt/nib/python", "scaling-collector"])
            self.assertEqual(kwargs, dict(stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True))
            if len(children) == fail_at:
                raise OSError("PRIVATE_CANARY")
            child = REAL_POPEN([sys.executable, "-I", "-S", "-B",
                str(ROOT / "scripts/fixtures/stream_scaling_child.py"), mode, fixtures.MODEL,
                json.dumps(inherited), json.dumps(fixtures.endpoint().expected)], **kwargs)
            children.append(child)
            return child
        with mock.patch.object(scaling.subprocess, "Popen", side_effect=spawn):
            try:
                yield children
            finally:
                for child in children:
                    if child.poll() is None:
                        os.killpg(child.pid, signal.SIGKILL)
                        child.wait(timeout=3)
                        self.fail("collector escaped cleanup")
                    self.assertTrue(child.stdin.closed and child.stdout.closed)

    def wave(self, concurrency=16, admission=15):
        row, epoch = {}, time.monotonic()
        scaling.wave(fixtures.endpoint(), workload(), row, concurrency=concurrency,
            deadline=epoch + 20, admission_end=epoch + admission, epoch=epoch,
            model=fixtures.MODEL, nonce="a" * 64, condition="isolated", offset=0)
        self.assertTrue(row["collectors_reaped"])
        self.assertNotIn("PRIVATE_CANARY", json.dumps(row))
        return row

    def test_64_real_collectors_do_not_inherit_owner_pipes(self):
        pipes = [fd for _ in range(4) for fd in os.pipe()]
        try:
            inherited = []
            for fd in pipes:
                os.set_inheritable(fd, True)
                info = os.fstat(fd)
                inherited.append((fd, info.st_dev, info.st_ino))
            with self.children(inherited=inherited) as children:
                row = self.wave(64)
            self.assertEqual((len(children), len(row["samples"])), (64, 64))
            self.assertEqual(row["stop_reason"], "complete")
            self.assertEqual(sum(s["completion_tokens"] for s in row["samples"]), 320)
        finally:
            for fd in pipes:
                os.close(fd)

    def test_malformed_oversized_and_lost_reports_preserve_actual_dispatch(self):
        for mode in ("malformed", "oversized", "nested", "lost"):
            with self.subTest(mode=mode), self.children(mode):
                row = self.wave()
            self.assertNotEqual(row["stop_reason"], "complete")
            self.assertTrue(0 < len(row["samples"]) <= 16)
            self.assertTrue(all(s["completion_tokens"] is None for s in row["samples"]))

    def test_spawn_failure_and_closed_admission_do_not_dispatch(self):
        with self.children(fail_at=3) as children:
            row = self.wave()
        self.assertEqual((len(children), len(row["samples"])), (3, 0))
        with self.children() as children:
            row = self.wave(admission=0)
        self.assertEqual((len(children), len(row["samples"])), (0, 0))

    def test_interrupt_during_spawn_reaps_registered_child(self):
        original = scaling.spawn
        def spawn():
            child = original()
            os.kill(os.getpid(), signal.SIGINT)
            return child
        with self.children() as children, mock.patch.object(scaling, "spawn", side_effect=spawn):
            row = self.wave()
        self.assertEqual(len(children), 1)
        self.assertEqual(row["stop_reason"], "cancelled")

    def test_completed_usage_preserved_on_late_cancellation(self):
        original = scaling.c8.drain
        def drain(workers, *args):
            original(workers, *args)
            if len(workers) == 16 and all(w["packet"] is not None for w in workers):
                raise KeyboardInterrupt("PRIVATE_CANARY")
        # One interruption only: cleanup must be allowed to finish.
        interrupted = False
        def once(workers, *args):
            nonlocal interrupted
            if interrupted:
                return original(workers, *args)
            try:
                return drain(workers, *args)
            except KeyboardInterrupt:
                interrupted = True
                raise
        with self.children(), mock.patch.object(scaling.c8, "drain", side_effect=once):
            row = self.wave()
        self.assertEqual(row["stop_reason"], "cancelled")
        self.assertEqual(sum(s["completion_tokens"] or 0 for s in row["samples"]), 80)

    def test_actual_http_child_main_thread_peer_checks_and_unique_salts(self):
        fixture = fixtures.ProcessTests()
        with fixture.http_server() as seen, self.children("http"):
            row = self.wave()
        self.assertEqual(row["stop_reason"], "complete")
        self.assertEqual(len(seen), 16)
        self.assertEqual(len({body["cache_salt"] for _, body in seen}), 16)
        self.assertTrue(all(path == "/v1/chat/completions" and body["model"] == fixtures.MODEL
                            and body["max_completion_tokens"] == 512 for path, body in seen))
        self.assertTrue(all(s["cached_prompt_tokens"] is None for s in row["samples"]))


if __name__ == "__main__":
    unittest.main()
