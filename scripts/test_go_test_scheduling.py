# SPDX-License-Identifier: Apache-2.0
"""Both Go package schedulers are explicit; no deadline or coverage relaxation."""

from pathlib import Path
import re
import shlex
import unittest

ROOT = Path(__file__).resolve().parents[1]


def make_commands(source):
    recipe = source.split("\ntest:\n", 1)[1].split("\n\n", 1)[0]
    return [shlex.split(line) for line in recipe.splitlines()]


def nested_command(source):
    body = source.split("func TestTaggedGuestSuite(t *testing.T) {\n", 1)[1].split("\n}", 1)[0]
    call = re.fullmatch(r'.*\n\tt.Log\(goCommand\(t, env, (.*)\)\)', body, re.DOTALL)
    if call is None:
        raise ValueError("nested suite command must remain explicit")
    return shlex.split(call[1].replace(",", ""))


class GoTestSchedulingTests(unittest.TestCase):
    def test_both_make_race_variants_keep_full_coverage_and_disable_cache(self):
        self.assertEqual(make_commands((ROOT / "Makefile").read_text()), [
            ["go", "test", "-race", "-p=1", "-count=1", "./..."],
            ["go", "test", "-race", "-p=1", "-tags", "nibfake", "-count=1", "./..."],
        ])

    def test_nested_scheduler_does_not_depend_on_cleared_goflags(self):
        source = (ROOT / "guest/build_test.go").read_text()
        self.assertTrue(source.startswith("//go:build !nibfake\n"))
        self.assertIn('"GOFLAGS="', source)
        self.assertEqual(nested_command(source),
                         ["test", "-race", "-p=1", "-tags=nibfake", "-count=1", "./..."])

    def test_each_scheduler_detects_missing_serial_argument(self):
        source = (ROOT / "Makefile").read_text()
        expected = make_commands(source)
        for index in (0, 1):
            lines = source.splitlines(keepends=True)
            matching = [i for i, line in enumerate(lines) if line.startswith("\tgo test ")]
            lines[matching[index]] = lines[matching[index]].replace(" -p=1", "")
            self.assertNotEqual(make_commands("".join(lines)), expected)
        source = (ROOT / "guest/build_test.go").read_text()
        self.assertNotEqual(nested_command(source.replace(', "-p=1"', "")), nested_command(source))


if __name__ == "__main__":
    unittest.main()
