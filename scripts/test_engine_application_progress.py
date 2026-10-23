# SPDX-License-Identifier: Apache-2.0
"""Synthetic files/kernel metadata only; no container, model or runtime proof."""

import copy
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

import engine_application_progress as progress

ROOT = Path(__file__).resolve().parent.parent


def cgroup_values():
    values = dict.fromkeys(progress.CGROUP_KEYS, 0)
    values.update({"memory.current": 1024, "memory.peak": 2048, "memory.max": 8 << 30})
    return values


def fixture_row(sha, phase="complete"):
    observer = progress.Progress(sha, time.monotonic(), time.monotonic() + 720)
    with mock.patch.object(progress, "cgroup", return_value=cgroup_values()):
        observer.boundary("layout")
        if phase != "layout":
            observer.phase = phase if phase != "complete" else "application"
        if phase == "complete":
            observer.counts.update(inventories_started=8, inventories_completed=8, reads_completed=8, read_bytes_completed=8)
        observer.finish(phase == "complete")
    for row in observer.row["samples"]:
        row.update(elapsed_ms=0, process_cpu_ms=0)
    return observer.row


class KernelTests(unittest.TestCase):
    def kernel(self):
        raw = {"/proc/self/cgroup": b"0::/\n",
               "/proc/self/mountinfo": b"31 25 0:28 / /sys/fs/cgroup ro,nosuid,nodev,noexec - cgroup2 cgroup rw\n"}
        values = {"memory.current": b"1024\n", "memory.peak": b"2048\n",
                  "memory.max": str(8 << 30).encode() + b"\n", "memory.swap.max": b"0\n",
                  "memory.stat": b"anon 12\nfile 34\nworkingset_refault_file 5\npgscan 6\npgsteal 7\n",
                  "memory.events": b"high 0\nmax 1\noom 0\noom_kill 0\n",
                  "io.stat": b"8:0 rbytes=10 wbytes=20 rios=2 wios=3 dbytes=0 dios=0\n"
                             b"253:1 rbytes=30 wbytes=40 rios=4 wios=5\n"}
        raw.update({"/sys/fs/cgroup/" + name: value for name, value in values.items()})
        return raw

    def collect(self, raw):
        with mock.patch.object(progress, "read_kernel", side_effect=lambda path, deadline: raw[path]):
            return progress.cgroup(time.monotonic() + 10)

    def test_fixed_root_aggregates_and_no_device_labels(self):
        actual = self.collect(self.kernel())
        self.assertEqual(set(actual), progress.CGROUP_KEYS)
        self.assertEqual([actual["io." + key] for key in progress.IO], [40, 60, 6, 8])
        self.assertNotIn("253:1", json.dumps(actual))
        self.assertEqual(actual["memory.stat.workingset_refault_file"], 5)
        raw = self.kernel()
        raw["/sys/fs/cgroup/io.stat"] = b""
        self.assertTrue(all(self.collect(raw)["io." + key] == 0 for key in progress.IO))

    def test_missing_duplicate_out_of_range_and_nonprivate_metadata_rejected(self):
        changes = [("/proc/self/cgroup", b"0::/somewhere-private\n"),
                   ("/proc/self/mountinfo", b"31 25 0:28 / /sys/fs/cgroup rw - cgroup2 cgroup rw\n"),
                   ("/sys/fs/cgroup/memory.max", b"max\n"),
                   ("/sys/fs/cgroup/memory.swap.max", b"1\n"),
                   ("/sys/fs/cgroup/memory.current", b"-1\n"),
                   ("/sys/fs/cgroup/memory.peak", b"9999999999999999999\n"),
                   ("/sys/fs/cgroup/memory.stat", b"anon 0\n"),
                   ("/sys/fs/cgroup/memory.events", b"high 0\nhigh 1\n"),
                   ("/sys/fs/cgroup/io.stat", b"8:0 rbytes=1 rbytes=2 wbytes=0 rios=0 wios=0\n"),
                   ("/sys/fs/cgroup/io.stat", b"8:0 rbytes=1 wbytes=0 rios=0 wios=0\n" * 2),
                   ("/sys/fs/cgroup/io.stat", b"PRIVATE rbytes=1 wbytes=0 rios=0 wios=0\n")]
        for path, raw in changes:
            with self.subTest(path=path), self.assertRaises((ValueError, KeyError)):
                self.collect({**self.kernel(), path: raw})

    def test_kernel_reader_rejects_arbitrary_names_and_expired_deadline(self):
        for path, deadline in (("/private/content", time.monotonic() + 1),
                               ("/proc/self/cgroup", time.monotonic() - 1)):
            with mock.patch.object(os, "open") as opened, self.assertRaises(ValueError):
                progress.read_kernel(path, deadline)
            opened.assert_not_called()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture"
            path.write_bytes(b"x" * 65537)
            original = os.open
            with mock.patch.object(os, "open", side_effect=lambda ignored, flags: original(path, flags)), \
                    self.assertRaises(ValueError):
                progress.read_kernel("/proc/self/cgroup", time.monotonic() + 1)


class ProgressTests(unittest.TestCase):
    def observer(self):
        return progress.Progress("1" * 64, time.monotonic(), time.monotonic() + 720)

    def validate(self, row, completed=False):
        progress.validate(row, "1" * 64, application_completed=completed, elapsed_ms=1080000)

    def test_real_tree_delegation_counts_and_retains_failure_operation(self):
        spec = importlib.util.spec_from_file_location("progress_overlay_fixture", ROOT / "scripts/engine_overlay.py")
        overlay = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(overlay)
        with tempfile.TemporaryDirectory(dir="/private/tmp" if os.uname().sysname == "Darwin" else None) as directory, \
                mock.patch.object(progress, "cgroup", return_value=cgroup_values()):
            root = Path(directory).resolve()
            (root / "plain").write_bytes(b"synthetic")
            observer = self.observer()
            observer.boundary("layout")
            observer.boundary("inspection")
            with overlay.directory(root) as fd:
                original = overlay.Tree(fd)
                tree = observer.wrap(original)
                self.assertEqual(tree.deadline, original.deadline)
                self.assertEqual(tree.inventory(), original.inventory())
                self.assertEqual(observer.counts["read_bytes_completed"], 9)
                self.assertEqual(observer.counts["reads_completed"], 1)
                observer.boundary("application")
                tree.write("plain", b"other", 0o600)
                tree.unlink("plain")
                (root / "bad").symlink_to("missing")
                with self.assertRaises(ValueError):
                    tree.inventory()
                self.assertEqual(observer.operation, "inventory")
                observer.finish()
            self.validate(observer.row)
            self.assertEqual(observer.counts, dict(zip(progress.COUNTS, (2, 1, 1, 9, 1, 1))))
            self.assertNotIn("plain", json.dumps(observer.row))
            self.assertNotIn("missing", json.dumps(observer.row))

    def test_read_failure_not_counted_or_exception_text_retained(self):
        class Tree:
            def __init__(self, fd):
                self.fd, self.deadline = fd, time.monotonic() + 10

            def read(self, *args, **kwargs):
                raise ValueError("PRIVATE body path")
        observer = self.observer()
        with mock.patch.object(progress, "cgroup", return_value=cgroup_values()):
            observer.boundary("layout")
            tree = observer.wrap(Tree(9))
            with self.assertRaises(ValueError):
                tree.read("PRIVATE")
            observer.finish()
        self.assertEqual(observer.counts["reads_completed"], 0)
        self.assertEqual(observer.operation, "read")
        self.assertNotIn("PRIVATE", json.dumps(observer.row))
        self.validate(observer.row)

    def test_final_slot_byte_limit_throttle_and_monotonic_validation(self):
        observer = self.observer()
        with mock.patch.object(progress, "cgroup", return_value=cgroup_values()) as collect:
            observer.boundary("layout")
            for _ in range(100):
                observer.sample("periodic")
            self.assertEqual(collect.call_count, 1)
            for _ in range(40):
                observer.boundary("layout")
            observer.finish()
        self.assertEqual(len(observer.row["samples"]), progress.MAX_SAMPLES)
        self.assertGreater(observer.row["samples_omitted"], 0)
        self.assertEqual(observer.row["samples"][-1]["event"], "terminal")
        self.validate(observer.row)
        worst = copy.deepcopy(observer.row)
        for sample in worst["samples"]:
            sample["cgroup"].update(dict.fromkeys(progress.CGROUP_KEYS, progress.MAX_NUMBER))
        self.assertLess(len(json.dumps(worst, indent=2)), 32768)

    def test_unavailable_is_not_zero_and_terminal_does_not_mask_original_failure(self):
        observer = self.observer()
        with mock.patch.object(progress, "cgroup", side_effect=OSError("PRIVATE")):
            with self.assertRaises(ValueError):
                observer.boundary("layout")
            observer.finish()
        self.validate(observer.row)
        self.assertTrue(all(row["cgroup"] is None and row["cgroup_status"] == "unavailable"
                            for row in observer.row["samples"]))
        self.assertNotIn("PRIVATE", json.dumps(observer.row))

    def test_deadline_terminal_retains_partial_pass_without_another_kernel_read(self):
        observer = self.observer()
        with mock.patch.object(progress, "cgroup", return_value=cgroup_values()):
            observer.boundary("layout")
            observer.boundary("inspection")
        observer.counts.update(inventories_started=1, reads_completed=10, read_bytes_completed=100)
        observer.operation = "read"
        with mock.patch.object(progress.time, "monotonic", return_value=observer.deadline), \
                mock.patch.object(os, "open") as opened:
            self.assertFalse(observer.finish())
        opened.assert_not_called()
        self.validate(observer.row)
        terminal = observer.row["samples"][-1]
        self.assertEqual(terminal["cgroup_status"], "deadline")
        self.assertEqual(terminal["inventories_completed"], 0)
        self.assertEqual(terminal["operation"], "read")
        self.assertIsNone(terminal["cgroup"])

    def test_deadline_crossed_during_terminal_capture_never_marks_complete(self):
        clock = [100.0]
        with mock.patch.object(progress.time, "monotonic", side_effect=lambda: clock[0]), \
                mock.patch.object(progress, "cgroup", return_value=cgroup_values()):
            observer = progress.Progress("1" * 64, clock[0], 820.0)
            observer.boundary("layout")
            observer.phase = "application"
            observer.counts.update(inventories_started=8, inventories_completed=8, reads_completed=8, read_bytes_completed=8)
            clock[0] = 819.999
            def capture(deadline):
                self.assertEqual(deadline, 820.0)
                clock[0] = 820.005
                return cgroup_values()
            with mock.patch.object(progress, "cgroup", side_effect=capture):
                self.assertFalse(observer.finish(completed=True))
        terminal = observer.row["samples"][-1]
        self.assertEqual(terminal["phase"], "application")
        self.assertEqual(terminal["cgroup_status"], "observed")
        self.assertGreaterEqual(terminal["elapsed_ms"], 720004)
        self.validate(observer.row)

    def test_closed_schema_source_binding_and_success_require_eight_completed_passes(self):
        row = fixture_row("1" * 64)
        self.validate(row, True)
        changes = [("source_sha256", "2" * 64), ("samples_omitted", True), ("PRIVATE", "body")]
        for key, value in changes:
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.validate({**row, key: value}, True)
        for key, value in (("inventories_completed", 7), ("inventories_started", 9),
                           ("read_bytes_completed", -1), ("operation", "PRIVATE"),
                           ("event", "periodic"), ("elapsed_ms", True), ("cgroup_status", "unavailable")):
            bad = copy.deepcopy(row)
            bad["samples"][-1][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.validate(bad, True)
        bad = copy.deepcopy(row)
        bad["samples"][0]["read_bytes_completed"] = 20
        with self.assertRaises(ValueError):
            self.validate(bad, True)

    def test_pinned_integrity_implementation_is_byte_identical(self):
        for name, expected in (("engine_application.py", "ce69e94d64434294f674c4be66e1a41fe8a08864535e6dbe242db365559b7406"),
                               ("engine_overlay.py", "deb0d99de48a2f06cba20e36d2f707ffacf2c8afd35531447df59b920f288938")):
            self.assertEqual(hashlib.sha256((ROOT / "scripts" / name).read_bytes()).hexdigest(), expected)

    def test_real_synthetic_five_stage_writer_still_performs_all_eight_inventories(self):
        import test_engine_application as fixtures
        fixtures.ApplicationTests.setUpClass()
        owner = fixtures.ApplicationTests()
        receipts = []
        for observed in (False, True):
            with owner.wheel() as (fixture, tree, console, contract, measured), \
                    mock.patch.object(progress, "cgroup", return_value=cgroup_values()):
                observer = self.observer()
                observer.boundary("layout")
                if observed:
                    tree = observer.wrap(tree)
                observer.boundary("inspection")
                before = fixtures.app.inspect(fixtures.overlay, tree, contract, measured, console)
                observer.boundary("application")
                receipts.append(fixtures.app.apply(fixtures.overlay, tree, contract, fixture.stages, before, measured, console))
                if observed:
                    self.assertTrue(observer.finish(True))
                    self.validate(observer.row, True)
                    self.assertEqual(observer.counts["inventories_completed"], 8)
                    self.assertEqual(observer.counts["writes_completed"], 5 * len(contract["files"]))
                    self.assertEqual(observer.counts["unlinks_completed"], receipts[-1]["removed_bytecode_files"])
        self.assertEqual(receipts[0], receipts[1])

    def test_cpu_failure_finalization_retains_counters_not_entry_or_exception(self):
        import test_engine_cpu_entry as fixtures
        cpu, tool = fixtures.cpu, fixtures.tool
        app = mock.Mock()
        app.verify_layout.side_effect = ValueError("PRIVATE path and content")
        with mock.patch.object(cpu.progress, "cgroup", return_value=cgroup_values()):
            result = cpu.apply_and_observe({}, {}, (tool.overlay, None, None, app), {}, {}, tool.lab)
        self.assertEqual(result["stage"], "application-preflight")
        self.assertEqual(result["failure"], "bounded-cpu-entry-rejected")
        self.assertIsNone(result["application"])
        self.assertIsNone(result["entry"])
        self.assertNotIn("PRIVATE", json.dumps(result))
        cpu.progress.validate(result["application_progress"], cpu.PROGRESS_SHA, application_completed=False,
                              elapsed_ms=result["phase_elapsed_ms"]["application-preflight"])
        with mock.patch.object(cpu.progress, "cgroup", side_effect=OSError("PRIVATE")):
            result = cpu.apply_and_observe({}, {}, (tool.overlay, None, None, app), {}, {}, tool.lab)
        self.assertEqual(result["failure"], "bounded-cpu-entry-rejected")
        self.assertTrue(all(row["cgroup_status"] == "unavailable" for row in result["application_progress"]["samples"]))

    def test_cpu_terminal_deadline_crossing_preserves_valid_failure_and_never_enters(self):
        import test_engine_cpu_entry as fixtures
        fixtures.ContractTests.setUpClass()
        baseline = fixtures.ContractTests()
        cpu, tool = fixtures.cpu, fixtures.tool
        for crossing in ("during-sample", "after-finish"):
            clock = [100.0]
            observer = cpu.progress.Progress(cpu.PROGRESS_SHA, clock[0], 820.0)
            app = mock.Mock()
            @contextmanager
            def selected(*args):
                yield tool.overlay.Tree(-1), ("unused", tool.overlay.Tree(-1))
            app.selected_tree = selected
            def applied(*args):
                observer.counts.update(inventories_started=8, inventories_completed=8,
                                       reads_completed=8, read_bytes_completed=8)
                clock[0] = 819.999
                return baseline.valid_report()["application"]
            app.apply.side_effect = applied
            def capture(deadline):
                if crossing == "during-sample" and observer.counts["inventories_completed"] == 8:
                    clock[0] = 820.005
                return cgroup_values()
            finish = observer.finish
            def finish_then_schedule(*args, **kwargs):
                result = finish(*args, **kwargs)
                if crossing == "after-finish":
                    clock[0] = 820.005
                return result
            with self.subTest(crossing=crossing), \
                    mock.patch.object(cpu.time, "monotonic", side_effect=lambda: clock[0]), \
                    mock.patch.object(cpu.progress, "Progress", return_value=observer), \
                    mock.patch.object(cpu.progress, "cgroup", side_effect=capture), \
                    mock.patch.object(observer, "finish", side_effect=finish_then_schedule), \
                    mock.patch.object(cpu, "observe_entry") as entry:
                result = cpu.apply_and_observe({}, baseline.files, (tool.overlay, None, None, app),
                                               baseline.application, baseline.measured, tool.lab)
            entry.assert_not_called()
            self.assertEqual(result["stage"], "application")
            self.assertEqual(result["failure"], "bounded-cpu-entry-rejected")
            self.assertEqual(result["application_progress"]["samples"][-1]["phase"], "application")
            self.assertFalse(baseline.validate(result))


if __name__ == "__main__":
    unittest.main()
