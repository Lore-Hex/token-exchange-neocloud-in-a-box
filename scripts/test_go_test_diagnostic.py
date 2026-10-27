# SPDX-License-Identifier: Apache-2.0
"""Historical import checks do not run Go, use sockets or repeat diagnostics."""

from pathlib import Path
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()
