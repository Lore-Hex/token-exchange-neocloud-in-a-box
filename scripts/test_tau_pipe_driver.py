# SPDX-License-Identifier: Apache-2.0
"""Synthetic subprocess IPC and container-policy controls; no upstream calls."""

import copy
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import tau_pipe_driver as driver
import test_tau_trial as fixtures

ROOT = Path(__file__).resolve().parent.parent
runner, tau, worker = fixtures.runner, driver.tau, driver.worker

CHILD = """
import os, sys, time
sys.path.insert(0, sys.argv[1])
import tau_transport as transport
import tau_pipe_trial as trial
read_fd, write_fd = os.dup(0), os.dup(1)
with open(os.devnull, 'r+b', buffering=0) as sink:
    for fd in (0, 1, 2):
        os.dup2(sink.fileno(), fd)
channel = transport.Channel(read_fd, write_fd)
proxy = transport.WorkerProxy(channel, time.monotonic() + 4)
def execute(root, manifest, selection, bridge):
    print('private-stdout-canary')
    os.write(2, b'private-stderr-canary')
    for role in ('agent', 'simulator'):
        bridge.complete(model=transport.ALIASES[role], messages=[{'role':'user','content':'private-message-canary'}])
    return 0.0
try:
    trial.execute(None, {}, {}, proxy, run=execute)
finally:
    channel.close()
"""


class PipeDriverTests(unittest.TestCase):
    def setUp(self):
        self.manifest = fixtures.fixtures.manifest()
        self.selection = fixtures.chosen(self.manifest)
        self.plan = {"transport": "scripted-parent-pipe", "selection": self.selection,
                     "manifest_sha256": tau.digest(tau.encoded(self.manifest)), "producer": fixtures.producer()}

    def payload(self, root, assertions=None):
        path = root / "upstream/data/tau2/domains/airline/tasks.json"
        path.parent.mkdir(parents=True)
        path.write_bytes(worker.encoded([{"id": self.selection["task_id"],
                            "evaluation_criteria": {"nl_assertions": assertions}}]))
        return path

    def test_opt_in_pipe_does_not_relax_container_isolation(self):
        common = ("fixed", "a" * 32, Path("/private/payload"), "b" * 64)
        ordinary = runner.create_command(*common)
        pipe = runner.create_command(*common, parent_pipe=True)
        self.assertEqual([item for item in pipe if item not in ("--interactive", "--parent-pipe")], ordinary)
        self.assertEqual(pipe.count("--interactive"), 1)
        self.assertEqual(pipe[-1], "--parent-pipe")
        self.assertEqual(pipe[pipe.index("--network") + 1], "none")
        self.assertEqual(pipe[pipe.index("--log-driver") + 1], "none")

    def test_manifest_is_explicitly_synthetic_and_keeps_budget_settings(self):
        frozen = driver.scripted_manifest(self.manifest)
        driver.transport.smoke_manifest(frozen, tau.digest(tau.encoded(frozen)))
        self.assertEqual(frozen["settings"], self.manifest["settings"])
        self.assertFalse(frozen["quality_eligible"])
        self.assertEqual(frozen["benchmark_manifest_sha256"], self.plan["manifest_sha256"])
        self.assertTrue(all(row["served_model"].startswith("scripted-") for row in frozen["framing"].values()))

    def test_assertions_come_from_exact_verified_task_not_worker_messages(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = self.payload(root, ["public synthetic assertion"])
            self.assertEqual(driver.selected_assertions(root, self.selection), ["public synthetic assertion"])
            for assertions in (False, 0, {}, [1], "unexpected"):
                path.write_bytes(worker.encoded([{"id": self.selection["task_id"],
                                     "evaluation_criteria": {"nl_assertions": assertions}}]))
                with self.assertRaises(worker.Abort):
                    driver.selected_assertions(root, self.selection)
            path.write_bytes(worker.encoded([{"id": "missing"}]))
            with self.assertRaises(worker.Abort):
                driver.selected_assertions(root, self.selection)

    def test_real_subprocess_pipe_preserves_accounting_without_content_capture(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.payload(root)
            result = driver.run_process([sys.executable, "-I", "-S", "-B", "-c", CHILD, str(ROOT / "scripts")],
                {}, root, self.plan, self.manifest, time.monotonic() + 5)
        runner.check_result(result, self.plan, self.manifest)
        row = result["report"]["trials"][0]
        self.assertEqual(row["status"], "evaluated")
        self.assertEqual(row["requests"], 2)
        self.assertEqual(row["usage"]["total_tokens"], 4)
        self.assertNotIn("canary", worker.encoded(result).decode())
        self.assertFalse(result["quality_accepted"])
        self.assertFalse(result["live_transport"])

    def test_malformed_stdout_or_early_exit_is_failure_not_a_report(self):
        for code in ("print('private-message-canary')", "raise SystemExit(1)"):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                self.payload(root)
                with self.assertRaises(worker.Abort) as caught:
                    driver.run_process([sys.executable, "-I", "-S", "-B", "-c", code], {}, root,
                                       self.plan, self.manifest, time.monotonic() + 3)
                self.assertNotIn("canary", str(caught.exception))

    def test_stalled_attach_is_killed_and_reaped(self):
        children = []
        real_popen = subprocess.Popen
        def start(*args, **kwargs):
            self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
            self.assertTrue(kwargs["close_fds"] and kwargs["start_new_session"])
            child = real_popen(*args, **kwargs)
            children.append(child)
            return child
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(driver.subprocess, "Popen", side_effect=start):
            root = Path(temp)
            self.payload(root)
            begin = time.monotonic()
            with self.assertRaises(worker.Abort):
                driver.run_process([sys.executable, "-I", "-S", "-B", "-c", "import time;time.sleep(30)"],
                    {}, root, self.plan, self.manifest, begin + 0.1)
            self.assertLess(time.monotonic() - begin, 2)
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].poll())

    def test_no_process_for_wrong_mode_or_expired_deadline(self):
        wrong = copy.deepcopy(self.plan)
        wrong["transport"] = "http"
        with mock.patch.object(driver.subprocess, "Popen") as popen:
            for plan, deadline in ((wrong, time.monotonic() + 5), (self.plan, time.monotonic() - 1)):
                with self.assertRaises(worker.Abort):
                    driver.run_process([], {}, Path("/not-read"), plan, self.manifest, deadline)
            popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
