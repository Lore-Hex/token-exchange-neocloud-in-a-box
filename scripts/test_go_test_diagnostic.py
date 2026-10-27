# SPDX-License-Identifier: Apache-2.0
"""Historical import checks do not run Go, use sockets or repeat diagnostics."""

from pathlib import Path
import json
import subprocess
import tempfile
import unittest
from unittest import mock

import record_go_test_diagnostic as recorder


class GoDiagnosticImportTests(unittest.TestCase):
    def test_changed_empty_or_oversized_inputs_are_rejected(self):
        raw = b"fixed synthetic diagnostic\n"
        expected = recorder.digest(raw)
        self.assertEqual(recorder.checked(raw, expected), raw)
        for changed in (b"", raw + b"changed", b"x" * ((1 << 20) + 1)):
            with self.subTest(size=len(changed)), self.assertRaises(ValueError):
                recorder.checked(changed, expected)

    def test_publication_never_overwrites_original_or_partial_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "evidence"
            recorder.publish(output, {"failure.log": b"original failure"})
            with self.assertRaises(FileExistsError):
                recorder.publish(output, {"failure.log": b"replacement"})
            self.assertEqual((output / "failure.log").read_bytes(), b"original failure")

    def test_source_manifests_are_identical_and_both_observations_retained(self):
        inputs = recorder.INPUTS
        self.assertEqual(inputs["full-breadth-once/source-before.json"],
                         inputs["full-breadth-once/source-after.json"])
        self.assertIn("original-failed-full-check.log", inputs)
        self.assertIn("full-breadth-once/01-default-parallel.log", inputs)
        self.assertIn("full-breadth-once/02-serialized.log", inputs)

    def test_manifest_digests_retained_without_publishing_source_listings(self):
        raw = b"fixed synthetic diagnostic\n"
        names = ("source-before.json", "source-after.json", "original-failed-full-check.log")
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "evidence"
            with mock.patch.object(recorder, "INPUTS", dict.fromkeys(names, recorder.digest(raw))), \
                    mock.patch.object(recorder, "read", return_value=raw), \
                    mock.patch.object(recorder, "committed", return_value="a" * 40), \
                    mock.patch.dict(recorder.os.environ, {"NIB_BIN": "/synthetic/nib"}), \
                    mock.patch.object(recorder.subprocess, "run", return_value=
                                      subprocess.CompletedProcess([], 0, raw)):
                recorder.record(Path(temporary), Path(temporary) / "deny", output)
            report = json.loads((output / "output.log").read_bytes())
            for name in names:
                published = name.endswith(".log")
                self.assertEqual((output / name).exists(), published)
                self.assertEqual(report["files"][name], {
                    "input_sha256": recorder.digest(raw), "scrubbed_sha256": recorder.digest(raw),
                    "published": published})


if __name__ == "__main__":
    unittest.main()
