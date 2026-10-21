# SPDX-License-Identifier: Apache-2.0
"""Owned cgroup lifecycle tests; never mutate real host resource controllers."""
from contextlib import contextmanager
import os
from pathlib import Path
import runpy
import subprocess
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
source = ROOT / "host/probe-build-resources.py"
resource = types.SimpleNamespace(**runpy.run_path(str(source)))


class ResourceTests(unittest.TestCase):
    @contextmanager
    def fixture(self, *, mismatch=False, kill_failure=False):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            values = {"cgroup.type": "domain", "cgroup.events": "populated 0\nfrozen 0", "cgroup.procs": "",
                      "memory.events": "oom 0\noom_kill 0\noom_group_kill 0", "memory.peak": "40000000"}
            writes = []
            def opened():
                return os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            def read(fd, key):
                return "wrong" if mismatch and key == "memory.max" else values[key]
            def write(fd, key, value):
                writes.append((key, value))
                self.assertNotEqual(os.fstat(fd).st_ino, root.stat().st_ino)
                if key == "cgroup.kill" and kill_failure:
                    raise OSError("PRIVATE")
                values[key] = value
                if key == "cgroup.kill":
                    values["cgroup.events"] = "populated 0\nfrozen 0"
            with mock.patch.dict(resource.cgroup.__wrapped__.__globals__, open_root=opened, read=read, write=write):
                yield root, values, writes

    def test_limits_precede_worker_and_cleanup_only_own_group(self):
        with self.fixture() as (root, values, writes):
            sibling = root / "unrelated"
            sibling.mkdir()
            state = {}
            with resource.cgroup(state) as (fd, name):
                self.assertTrue(state["limits_read_back"])
                self.assertEqual(dict(writes), resource.LIMITS)
                self.assertTrue((root / name).is_dir())
                self.assertEqual(len(name), 42)
                self.assertEqual(os.fstat(fd).st_ino, (root / name).stat().st_ino)
                values["cgroup.events"] = "populated 1\nfrozen 0"
            self.assertTrue(state["empty_verified"])
            self.assertTrue(state["group_removed"])
            self.assertEqual(state["oom_kill"], 0)
            self.assertEqual(state["populated_before_cleanup"], 1)
            self.assertEqual(writes[-1], ("cgroup.kill", "1"))
            self.assertEqual(list(root.iterdir()), [sibling])

    def test_limit_mismatch_never_yields_but_removes_owned_group(self):
        with self.fixture(mismatch=True) as (root, _, writes):
            state = {}
            with self.assertRaises(ValueError):
                with resource.cgroup(state):
                    self.fail("mismatched limit ran a worker")
            self.assertNotIn("limits_read_back", state)
            self.assertTrue(state["group_removed"])
            self.assertEqual(list(root.iterdir()), [])
            self.assertEqual(writes[-1], ("cgroup.kill", "1"))

    def test_worker_failure_still_cleans(self):
        with self.fixture() as (root, _, _):
            state = {}
            with self.assertRaisesRegex(RuntimeError, "synthetic"):
                with resource.cgroup(state):
                    raise RuntimeError("synthetic")
            self.assertTrue(state["group_removed"])
            self.assertEqual(list(root.iterdir()), [])

    def test_cleanup_failure_is_never_success(self):
        with self.fixture(kill_failure=True) as (root, _, _):
            state = {}
            with self.assertRaises(OSError):
                with resource.cgroup(state):
                    pass
            self.assertNotIn("group_removed", state)
            self.assertEqual(len(list(root.iterdir())), 1)

    def test_event_parser_rejects_ambiguous_or_non_numeric_data(self):
        self.assertEqual(resource.numeric_fields("populated 0\nfrozen 0"), {"populated": 0, "frozen": 0})
        for text in ("", "populated 0\npopulated 1", "populated -1", "populated many", "populated 0 extra"):
            with self.assertRaises(ValueError):
                resource.numeric_fields(text)

    def test_scope_is_only_a_synthetic_worker(self):
        child = mock.Mock(returncode=0)
        child.communicate.return_value = (b"NIB_BUILD_CGROUP_OK\n", None)
        child.poll.return_value = 0
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "cgroup.procs").touch()
            fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                with mock.patch.object(resource.subprocess, "Popen", return_value=child) as launch:
                    resource.run_child(fd, "nib-build-" + "a" * 32)
                argv = launch.call_args.args[0]
                self.assertEqual(argv[1:5], ["-I", "-S", "-B", "-c"])
                self.assertEqual(argv[5], resource.CHILD)
                self.assertEqual(launch.call_args.kwargs["stderr"], subprocess.DEVNULL)
                child.communicate.assert_called_once_with(timeout=20)
                self.assertTrue(launch.call_args.kwargs["start_new_session"])
            finally:
                os.close(fd)

    def test_no_parent_controller_mutation_or_unbounded_commands(self):
        text = source.read_text()
        self.assertNotIn('write(root,', text)
        self.assertNotIn('shell=True', text)
        self.assertNotIn('rmtree', text)
        self.assertEqual(resource.LIMITS, {"memory.max": "8589934592", "memory.swap.max": "0",
            "memory.oom.group": "1", "pids.max": "256", "cpu.max": "200000 100000"})

    def test_empty_group_cannot_claim_detached_child_proof(self):
        @contextmanager
        def completed(state):
            state.update(limits_read_back=True, empty_verified=True, group_removed=True,
                         oom_kill=0, memory_peak_bytes=40 << 20, populated_before_cleanup=0)
            yield 123, "nib-build-" + "a" * 32
        with mock.patch.dict(resource.probe.__globals__, cgroup=completed, run_child=lambda fd, name: None):
            result = resource.probe()
        self.assertEqual(result["result"], "fail")
        self.assertFalse(result["detached_child_cleanup_verified"])

    def test_detached_child_is_fixed_and_independently_bounded(self):
        self.assertIn("os.setsid()", resource.CHILD)
        self.assertIn("signal.alarm(60)", resource.CHILD)
        self.assertIn("os.waitpid(child, os.WNOHANG) == (0, 0)", resource.CHILD)
        self.assertLess(resource.CHILD.index("os.close(descriptor)"), resource.CHILD.index("os.write(ready_write"))


if __name__ == "__main__":
    unittest.main()
