# SPDX-License-Identifier: Apache-2.0
"""Bounded fetch concurrency, cancellation and unchanged integrity gates."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import subprocess
import threading
import time
import unittest
from unittest import mock

import fetch_mkosi_tools as fetcher
from test_native_tools import lock


class ConcurrentFetchTests(unittest.TestCase):
    def items(self, count=12):
        return {str(i): ("https://archive.ubuntu.com/ubuntu/" + str(i), len(str(i)),
                         fetcher.base.digest(str(i).encode())) for i in range(count)}

    def test_four_transfers_overlap_and_return_exact_complete_set(self):
        barrier = threading.Barrier(4, timeout=5)
        mutex = threading.Lock()
        active = peak = 0
        def execute(args, **kwargs):
            nonlocal active, peak
            with mutex:
                active += 1
                peak = max(peak, active)
            try:
                barrier.wait()
                self.assertEqual(kwargs["timeout"], 60)
                self.assertEqual(kwargs["env"], fetcher.ENV)
                return subprocess.CompletedProcess(args, 0, args[-1].rsplit("/", 1)[1].encode(), b"")
            finally:
                with mutex:
                    active -= 1
        result = fetcher.fetch_parallel(self.items(), execute, 600, lambda: 0, time.sleep)
        self.assertEqual(result, {str(i): str(i).encode() for i in range(12)})
        self.assertEqual((active, peak), (0, 4))

    def test_failure_stops_queued_inputs_and_joins_active_workers(self):
        barrier = threading.Barrier(4, timeout=5)
        finished, started = [], []
        mutex = threading.Lock()
        def item(name, row, execute, deadline, clock, sleep, *, cancelled):
            with mutex:
                started.append(name)
            barrier.wait()
            if name == "0":
                raise fetcher.FetchFailure(name, row[1], row[2], "digest")
            cutoff = time.monotonic() + 5
            while not cancelled() and time.monotonic() < cutoff:
                time.sleep(0.001)
            self.assertTrue(cancelled())
            with mutex:
                finished.append(name)
            raise fetcher.FetchFailure(name, row[1], row[2], "cancelled")
        with mock.patch.object(fetcher, "fetch_item", side_effect=item), \
                self.assertRaises(fetcher.FetchFailure) as caught:
            fetcher.fetch_parallel(self.items(), mock.Mock(), 600, lambda: 0, time.sleep)
        self.assertEqual(caught.exception.report["failure"], "digest")
        self.assertEqual(len(started), 4)
        self.assertCountEqual(finished, [name for name in started if name != "0"])

    def test_real_item_cancellation_does_not_start_or_retry_a_process(self):
        row = self.items(1)["0"]
        execute = mock.Mock()
        with self.assertRaises(fetcher.FetchFailure):
            fetcher.fetch_item("0", row, execute, 600, lambda: 0, time.sleep, cancelled=lambda: True)
        execute.assert_not_called()
        stop = threading.Event()
        execute.return_value = subprocess.CompletedProcess([], 28, b"PRIVATE", b"PRIVATE")
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(fetcher.FetchFailure) as caught:
            fetcher.fetch_item("0", row, execute, 600, lambda: 0, lambda _: stop.set(), cancelled=stop.is_set)
        self.assertEqual(caught.exception.report["failure"], "cancelled")
        execute.assert_called_once()

    def test_parallel_mode_keeps_same_global_budget_and_all_or_nothing_result(self):
        execute = mock.Mock(return_value=subprocess.CompletedProcess([], 0, b"deb", b""))
        for workers in (True, 0, 2, 5, None):
            with self.subTest(workers=workers), self.assertRaises(ValueError):
                fetcher.fetch(b"{}", execute, workers=workers)
        execute.assert_not_called()
        metadata = {"metadata/noble-main.xz": b"idx"}
        for raw, code, category in ((b"bad", 0, "digest"), (b"de", 0, "size"),
                                     (b"PRIVATE", 60, "process-exit")):
            execute.reset_mock()
            execute.return_value = subprocess.CompletedProcess([], code, raw, b"")
            with self.subTest(category=category), self.assertRaises(fetcher.FetchFailure) as caught:
                fetcher.fetch(fetcher.base.encoded(lock()), execute, clock=lambda: 0,
                              metadata=metadata, workers=4)
            self.assertEqual(caught.exception.report["failure"], category)
            execute.assert_called_once()
        execute.reset_mock()
        with self.assertRaises(fetcher.FetchFailure) as caught:
            fetcher.fetch_parallel(self.items(), execute, 600, lambda: 601, time.sleep)
        self.assertEqual(caught.exception.report["failure"], "deadline")
        execute.assert_not_called()

    def test_parallel_fetch_preserves_metadata_and_does_not_refetch_it(self):
        execute = mock.Mock(return_value=subprocess.CompletedProcess([], 0, b"deb", b""))
        result = fetcher.fetch(fetcher.base.encoded(lock()), execute, clock=lambda: 0,
                               metadata={"metadata/noble-main.xz": b"idx"}, workers=4)
        self.assertEqual(result["metadata/noble-main.xz"], b"idx")
        self.assertIn(b"deb", result.values())
        execute.assert_called_once()
        execute.reset_mock()
        with self.assertRaises(ValueError):
            fetcher.fetch(fetcher.base.encoded(lock()), execute, clock=lambda: 0,
                           metadata={"metadata/noble-main.xz": b"BAD"}, workers=4)
        execute.assert_not_called()

    def test_retry_logs_are_complete_json_without_private_output(self):
        counts, mutex = {}, threading.Lock()
        def execute(args, **kwargs):
            name = args[-1].rsplit("/", 1)[1]
            with mutex:
                counts[name] = counts.get(name, 0) + 1
                count = counts[name]
            return subprocess.CompletedProcess(args, 28 if count == 1 else 0,
                                                b"PRIVATE" if count == 1 else name.encode(),
                                                b"PRIVATE" if count == 1 else b"")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = fetcher.fetch_parallel(self.items(), execute, 600, lambda: 0, lambda _: None)
        self.assertEqual(len(result), 12)
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(events), 12)
        self.assertTrue(all(row["schema"] == "nib.input-fetch-retry/v1" for row in events))
        self.assertNotIn("PRIVATE", output.getvalue())

    def test_all_parallel_inputs_share_one_unextended_deadline(self):
        deadlines = []
        def item(name, row, execute, deadline, clock, sleep, *, cancelled):
            deadlines.append(deadline)
            return name.encode()
        with mock.patch.object(fetcher, "fetch_item", side_effect=item):
            fetcher.fetch(fetcher.base.encoded(lock()), mock.Mock(), clock=lambda: 10, workers=4)
        self.assertEqual(deadlines, [610, 610])

    def test_preserved_native_deadline_failure_executed_no_packages(self):
        path = (Path(__file__).resolve().parents[1]
                / "evidence/p3/2026-10-22-bounded-fetch-deadline/output.log")
        raw = path.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
                         "8a65931bf7ec36c40a43895153298458a32acbfb3514597599229e28135779cf")
        rows = [json.loads(line) for line in raw.splitlines()]
        self.assertEqual([row["result"] for row in rows], ["retry"] * 5 + ["fail"])
        self.assertEqual(rows[-1]["failure"], "deadline")
        self.assertTrue(all(row["package_execution"] is False for row in rows))
        self.assertFalse(path.with_name("run.json").exists())


if __name__ == "__main__":
    unittest.main()
