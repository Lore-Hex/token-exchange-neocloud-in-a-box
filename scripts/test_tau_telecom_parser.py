# SPDX-License-Identifier: Apache-2.0
"""Pinned-file-only row allowance; ordinary inputs keep the original bounds."""

from contextlib import contextmanager
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

import tau_pipe_driver as driver
import tau_runtime as runtime

worker, tau = driver.worker, driver.tau
NAME = "data/tau2/domains/telecom/tasks.json"


def rows(count, assertions=None):
    return [{"id": str(index), "evaluation_criteria": {"nl_assertions": assertions if index == 0 else []}}
            for index in range(count)]


@contextmanager
def payload(raw, *, domain="telecom", pinned=None):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        path = root / "upstream/data/tau2/domains" / domain / "tasks.json"
        path.parent.mkdir(parents=True)
        path.write_bytes(raw)
        pinned = raw if pinned is None else pinned
        pins = {"revision": tau.REVISION, "files": {NAME: {"bytes": len(pinned), "sha256": worker.digest(pinned)}}}
        pins_raw = worker.encoded(pins)
        manifest = root / tau.PINS
        manifest.parent.mkdir(parents=True)
        manifest.write_bytes(pins_raw)
        # Only the test's trust root changes; production accepts solely PINS_SHA.
        with mock.patch.object(tau, "PINS_SHA", worker.digest(pins_raw)):
            yield root, path, manifest, {"domain": domain, "task_id": "0"}


class ParserTests(unittest.TestCase):
    def test_default_boundary_1024_for_every_domain(self):
        for domain in tau.DOMAINS:
            for count in (0, 1024, 1025):
                with self.subTest(domain=domain, count=count), payload(worker.encoded(rows(count)), domain=domain) as (root, _, manifest, selected):
                    manifest.unlink()  # No trusted source identity available.
                    if count == 1024:
                        self.assertEqual(driver.selected_assertions(root, selected), [])
                    else:
                        with self.assertRaises((worker.Abort, OSError)):
                            driver.selected_assertions(root, selected)

    def test_authenticated_boundary_is_not_a_caller_limit(self):
        for count in (1025, 2285, 2286):
            with self.subTest(count=count), payload(worker.encoded(rows(count))) as (root, _, _, selected):
                selected["maximum"] = 99999
                if count <= 2285:
                    self.assertEqual(driver.selected_assertions(root, selected), [])
                else:
                    with self.assertRaises(worker.Abort): driver.selected_assertions(root, selected)

    def test_file_digest_and_byte_count_must_both_match(self):
        original = worker.encoded(rows(2285, ["synthetic"]))
        for forged in (original.replace(b"synthetic", b"forgedxxx"), original + b" "):
            with self.subTest(length=len(forged)), payload(forged, pinned=original) as (root, _, _, selected):
                with self.assertRaises(worker.Abort): driver.selected_assertions(root, selected)

    def test_forged_pins_cannot_authorize_different_file(self):
        raw = worker.encoded(rows(2285))
        with payload(raw) as (root, path, manifest, selected):
            forged = raw + b" "
            path.write_bytes(forged)
            pins = worker.decode(manifest.read_bytes())
            pins["files"][NAME].update(bytes=len(forged), sha256=worker.digest(forged))
            manifest.write_bytes(worker.encoded(pins))
            with self.assertRaises(worker.Abort): driver.selected_assertions(root, selected)

    def test_matching_file_in_other_domain_still_rejected(self):
        for domain in ("airline", "retail"):
            with self.subTest(domain=domain), payload(worker.encoded(rows(2285)), domain=domain) as (root, _, _, selected):
                with self.assertRaises(worker.Abort): driver.selected_assertions(root, selected)

    def test_duplicate_keys_and_duplicate_selected_task_still_rejected(self):
        for count in (2, 2285):
            duplicate = rows(count)
            duplicate[-1] = duplicate[0]
            cases = ((worker.encoded(rows(count)).replace(b'"id":"0"', b'"id":"0","id":"0"'), ValueError),
                     (worker.encoded(duplicate), worker.Abort))
            for raw, exception in cases:
                with self.subTest(count=count), payload(raw) as (root, _, _, selected):
                    with self.assertRaises(exception): driver.selected_assertions(root, selected)

    def test_existing_assertion_shape_string_and_count_bounds_remain(self):
        for assertions, exception in (([1], worker.Abort), ([["nested"]], worker.Abort),
                                     (["x" * (worker.MAX_RESPONSE + 1)], ValueError), ([""] * 20001, ValueError)):
            for count in (1, 2285):
                with self.subTest(count=count, kind=type(assertions[0]).__name__), payload(worker.encoded(rows(count, assertions))) as (root, _, _, selected):
                    with self.assertRaises(exception): driver.selected_assertions(root, selected)

    def test_existing_read_byte_bound_and_no_follow_remain(self):
        with payload(worker.encoded(rows(2285))) as (root, path, _, selected):
            saved = path.with_suffix(".saved")
            path.rename(saved)
            path.symlink_to(saved)
            with self.assertRaises(OSError): driver.selected_assertions(root, selected)
            path.unlink()
            with path.open("wb") as stream:
                stream.truncate((16 << 20) + 1)
            with self.assertRaises(ValueError): driver.selected_assertions(root, selected)

    @unittest.skipUnless(os.environ.get("NIB_TAU_SOURCE_ARCHIVE"), "explicit local pinned source archive required")
    def test_real_authenticated_source_is_exactly_2285_and_all_five_are_readable(self):
        deadline = time.monotonic() + 60
        pins_raw = runtime.read_file(runtime.ROOT / tau.PINS, 1 << 20, deadline)
        self.assertEqual(worker.digest(pins_raw), tau.PINS_SHA)
        pins = worker.decode(pins_raw)
        archive = runtime.read_file(Path(os.environ["NIB_TAU_SOURCE_ARCHIVE"]), runtime.ARCHIVE_LIMIT, deadline)
        _, sources = runtime.source_inputs(archive, pins, deadline)
        raw = sources[NAME]
        self.assertEqual(len(worker.decode(raw)), 2285)
        self.assertEqual(len(raw), pins["files"][NAME]["bytes"])
        self.assertEqual(worker.digest(raw), pins["files"][NAME]["sha256"])
        ids = tau.task_ids(raw, sources["data/tau2/domains/telecom/split_tasks.json"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, data in (("upstream/" + NAME, raw), (tau.PINS, pins_raw)):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            for task_id in ids[:5]:
                result = driver.selected_assertions(root, {"domain": "telecom", "task_id": task_id})
                expected = next(row for row in worker.decode(raw) if row["id"] == task_id)["evaluation_criteria"]["nl_assertions"] or []
                self.assertEqual(result, expected)


if __name__ == "__main__":
    unittest.main()
