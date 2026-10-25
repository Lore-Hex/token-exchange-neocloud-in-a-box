# SPDX-License-Identifier: Apache-2.0
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import quality_protocol as quality


class QualityProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.member = self.root / "source"
        self.member.write_bytes(b"immutable input\n")
        self.sha = quality.digest(self.member.read_bytes())

    def test_valid_member(self):
        quality.checked_file(self.root, "source", self.sha, 16)

    def test_wrong_hash_or_length_rejected(self):
        for sha, size in (("0" * 64, 16), (self.sha, 17)):
            with self.subTest(sha=sha, size=size), self.assertRaises(ValueError):
                quality.checked_file(self.root, "source", sha, size)

    def test_symlink_member_rejected(self):
        (self.root / "link").symlink_to(self.member)
        with self.assertRaises(ValueError):
            quality.checked_file(self.root, "link", self.sha)

    def test_symlink_parent_rejected(self):
        (self.root / "linked").symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            quality.checked_file(self.root, "linked/source", self.sha)

    def test_directory_empty_or_oversize_rejected(self):
        for size in (0, 8 * 1024 * 1024 + 1):
            with self.member.open("wb") as stream:
                stream.truncate(size)
            with self.subTest(size=size), self.assertRaises(ValueError):
                quality.checked_file(self.root, "source", self.sha)
        with self.assertRaises(ValueError):
            quality.checked_file(self.root, ".", self.sha)

    def test_two_hundred_unique_sorted_indices_for_each_frozen_population(self):
        pins = json.loads(quality.PINS.read_bytes())
        for name, spec in pins["datasets"].items():
            result = quality.indices(name, spec["revision"], spec["test_rows"])
            self.assertEqual(len(result), 200)
            self.assertEqual(result, sorted(set(result)))
            self.assertTrue(all(0 <= value < spec["test_rows"] for value in result))
            self.assertEqual(result, quality.indices(name, spec["revision"], spec["test_rows"]))

    def test_selection_depends_on_revision(self):
        self.assertNotEqual(quality.indices("gsm8k", "a" * 40, 1319),
                            quality.indices("gsm8k", "b" * 40, 1319))

    def test_frozen_selection_digests(self):
        expected = {"gsm8k": "dd9c9fb003ef91b244d539013a6e50e717b42fda2d75a36a87989c64139d4787",
                    "mmlu_pro": "576eb22e821904587bfdcea0e05bc360beca7975c46ebf4f19b92dee8f5c4f66"}
        for name, spec in json.loads(quality.PINS.read_bytes())["datasets"].items():
            raw = json.dumps(quality.indices(name, spec["revision"], spec["test_rows"]),
                             separators=(",", ":")).encode()
            self.assertEqual(quality.digest(raw), expected[name])

    def test_population_validation(self):
        for name, total in (("unknown", 1319), ("gsm8k", True), ("gsm8k", 199),
                            ("gsm8k", 20001), ("gsm8k", 200.0)):
            with self.subTest(name=name, total=total), self.assertRaises(ValueError):
                quality.indices(name, "a" * 40, total)

    def test_short_output_capacity_rejected_before_reading_any_inputs(self):
        for maximum in (256, 2047, True, 2048.0):
            with self.subTest(maximum=maximum), patch.object(quality, "checked_file") as check:
                with self.assertRaises(ValueError):
                    quality.prepare(self.root, self.root, maximum)
                check.assert_not_called()

    def test_preparation_checks_all_pins_and_makes_no_execution_claim(self):
        pins = json.loads(quality.PINS.read_bytes())
        expected_count = len(pins["harness"]["files"]) + sum(
            len(value["files"]) for value in pins["datasets"].values())
        with patch.object(quality, "checked_file") as check:
            report = quality.prepare(self.root, self.root, 2048)
        self.assertEqual(check.call_count, expected_count)
        self.assertTrue(report["inputs_verified"])
        for field in ("inference_performed", "quality_qualified", "confidential",
                      "context_fit_verified", "runtime_dependency_closure_verified"):
            self.assertIs(report[field], False)
        self.assertEqual(report["settings"]["max_gen_toks"], 2048)
        self.assertFalse(report["settings"]["log_samples"])

    def test_first_bad_member_stops_preparation(self):
        with patch.object(quality, "checked_file", side_effect=ValueError("bad input")) as check:
            with self.assertRaises(ValueError):
                quality.prepare(self.root, self.root, 2048)
            self.assertEqual(check.call_count, 1)

    def test_cli_failure_redacts_private_path_and_exception(self):
        args = ["quality_protocol.py", "--harness-root", "private-harness-path",
                "--dataset-root", "private-data-path", "--max-output-tokens", "2048"]
        output = io.StringIO()
        with patch("sys.argv", args), patch.object(quality, "prepare", side_effect=OSError("private-error")), \
                contextlib.redirect_stdout(output):
            self.assertEqual(quality.main(), 1)
        self.assertNotIn("private", output.getvalue())
        self.assertFalse(json.loads(output.getvalue())["inputs_verified"])


if __name__ == "__main__":
    unittest.main()
