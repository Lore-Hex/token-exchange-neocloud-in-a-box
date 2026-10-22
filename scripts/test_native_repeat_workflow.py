# SPDX-License-Identifier: Apache-2.0
"""Repeat-build orchestration stays source-bound, sequential and metadata-only."""
from pathlib import Path
import subprocess
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
CHECKOUT = "actions/checkout@11d5960a326750d5838078e36cf38b85af677262"
UPLOAD = "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02"


class NativeRepeatWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.repeat = yaml.safe_load((ROOT / ".github/workflows/mkosi-repeat-build.yml").read_text())
        self.build = yaml.safe_load((ROOT / ".github/workflows/mkosi-native-build.yml").read_text())

    def test_opt_in_sequential_same_commit_calls_preserve_build_safety(self):
        self.assertEqual(self.repeat[True], {"workflow_dispatch": None})
        self.assertEqual(self.repeat["permissions"], {"contents": "read"})
        self.assertEqual(self.repeat["concurrency"], {
            "group": "bounded-native-repeat-build", "cancel-in-progress": False})
        self.assertEqual(self.build["concurrency"], {
            "group": "bounded-offline-mkosi-installation", "cancel-in-progress": False})
        jobs = self.repeat["jobs"]
        self.assertEqual(set(jobs), {"first", "second", "compare"})
        flags = ("compile_driver", "install_driver", "guest_runtime", "verity_root",
                 "boot_uki", "validate_export")
        for slot in ("first", "second"):
            expected = {"uses": "./.github/workflows/mkosi-native-build.yml",
                        "with": {**dict.fromkeys(flags, True), "artifact_slot": slot}}
            expected.update({"if": "github.ref == 'refs/heads/main'"} if slot == "first"
                            else {"needs": "first"})
            self.assertEqual(jobs[slot], expected)
        install = self.build["jobs"]["install"]
        self.assertEqual(install["if"], "github.ref == 'refs/heads/main'")
        self.assertEqual(install["timeout-minutes"], 55)
        self.assertEqual(install["env"], {
            "ARTIFACT_SUFFIX": "${{ inputs.artifact_slot && format('-{0}', inputs.artifact_slot) || '' }}"})
        uploads = [step for step in install["steps"] if step.get("uses") == UPLOAD]
        self.assertEqual(len(uploads), 2)
        for step, prefix in zip(uploads, ("native-mkosi-install", "native-mkosi-export-validation")):
            self.assertEqual(step["with"]["name"], prefix +
                             "-${{ github.run_id }}-${{ github.run_attempt }}${{ env.ARTIFACT_SUFFIX }}")

    def test_slot_validation_rejects_unknown_names_before_fetch(self):
        steps = self.build["jobs"]["install"]["steps"]
        guard = steps[1]
        self.assertEqual(guard["env"], {"ARTIFACT_SLOT": "${{ inputs.artifact_slot }}"})
        self.assertNotIn("${{", guard["run"])
        self.assertIn("curl ", steps[2]["run"])
        for slot in ("", "first", "second", "third", "../first", "first\nsecond", "$(exit 0)", "*"):
            with self.subTest(slot=slot):
                result = subprocess.run(["/bin/bash", "-e", "-c", guard["run"]],
                                        env={"ARTIFACT_SLOT": slot}, capture_output=True, timeout=5)
                self.assertEqual(result.returncode, 0 if slot in ("", "first", "second") else 1)
                self.assertEqual(result.stdout + result.stderr, b"")

    def test_compare_fetches_this_attempt_and_uploads_only_comparison(self):
        compare = self.repeat["jobs"]["compare"]
        self.assertEqual(compare["needs"], ["first", "second"])
        self.assertEqual(compare["runs-on"], "ubuntu-24.04")
        self.assertEqual(compare["timeout-minutes"], 5)
        self.assertEqual(compare["permissions"], {"contents": "read", "actions": "read"})
        checkout, download, run, upload = compare["steps"]
        self.assertEqual(checkout, {"uses": CHECKOUT,
                                   "with": {"persist-credentials": False, "fetch-depth": 0}})
        self.assertEqual(download["env"], {"GH_TOKEN": "${{ github.token }}"})
        self.assertIn('for slot in first second; do', download["run"])
        self.assertIn('gh run download "$GITHUB_RUN_ID" --repo "$GITHUB_REPOSITORY"', download["run"])
        self.assertIn('"native-mkosi-export-validation-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT-$slot"',
                      download["run"])
        self.assertIn('--dir "build/native-repeat/$slot"', download["run"])
        self.assertNotIn("env", run)
        self.assertIn("env -i PATH=/usr/bin:/bin LANG=C /usr/bin/python3 -s -S -B", run["run"])
        self.assertIn('--expected-source "$GITHUB_SHA" build/native-repeat/first build/native-repeat/second',
                      run["run"])
        self.assertIn('> build/native-repeat/comparison.json', run["run"])
        self.assertNotIn("||", run["run"])
        self.assertEqual(upload["uses"], UPLOAD)
        self.assertEqual(upload["if"], "${{ always() }}")
        self.assertEqual(upload["with"], {
            "name": "native-repeat-comparison-${{ github.run_id }}-${{ github.run_attempt }}",
            "path": "build/native-repeat/comparison.json", "if-no-files-found": "error", "retention-days": 7})


if __name__ == "__main__":
    unittest.main()
