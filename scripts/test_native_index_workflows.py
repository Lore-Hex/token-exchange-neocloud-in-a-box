# SPDX-License-Identifier: Apache-2.0
"""Both native paths consume the same authenticated historical public indexes."""

from pathlib import Path
import shlex
import subprocess
import unittest

import yaml

ROOT = Path(__file__).resolve().parent.parent


class NativeIndexWorkflowTests(unittest.TestCase):
    def test_install_preserves_fetch_diagnostic_even_before_worker_exists(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/mkosi-native-build.yml").read_text())
        steps = workflow["jobs"]["install"]["steps"]
        self.assertIn('> build/native-install/input-fetch.log', steps[1]["run"])
        self.assertNotIn("|| true", steps[1]["run"])
        self.assertIs(workflow[True]["workflow_dispatch"]["inputs"]["validate_export"]["default"], False)
        uploads = [step for step in steps if step.get("uses", "").startswith("actions/upload-artifact@")]
        self.assertEqual(len(uploads), 2)
        ordinary, private_export = uploads
        self.assertEqual(ordinary["if"], "${{ always() && !inputs.validate_export }}")
        self.assertEqual(ordinary["with"]["path"].splitlines(), [
            "build/native-install/input-fetch.log", "build/native-install/evidence/",
            "build/native-install/evidence-resources.json"])
        self.assertEqual(private_export["if"], "${{ always() && inputs.validate_export && "
                         "steps.export_validation.outputs.metadata_ready == 'true' }}")
        self.assertNotIn("build/native-install/input-fetch.log", private_export["with"]["path"].splitlines())

    def test_tools_and_install_use_identical_pinned_index_archive(self):
        for name, job, directory in (
            ("mkosi-native-tools.yml", "probe", "native-tools"),
            ("mkosi-native-build.yml", "install", "native-install"),
        ):
            with self.subTest(workflow=name):
                workflow = yaml.safe_load((ROOT / ".github/workflows" / name).read_text())
                self.assertEqual(workflow["permissions"], {"contents": "read"})
                fetch = workflow["jobs"][job]["steps"][1]["run"]
                self.assertIn("build-input-indexes-20261020/metadata.tar.gz", fetch)
                self.assertIn("--max-filesize 23847293", fetch)
                self.assertIn("54def86b7b37b3b5178782965e499f0242a98f36bc7828105f112005576663a7", fetch)
                self.assertIn("sha256sum --check --strict", fetch)
                self.assertLess(fetch.index("sha256sum --check --strict"), fetch.index("scripts/fetch_mkosi_tools.py"))
                command = fetch[fetch.index("/usr/bin/python3.10"):].replace("\\\n", " ")
                args = shlex.split(command)
                self.assertEqual(args[args.index("--metadata-archive") + 1],
                                 "$PWD/build/" + directory + "/metadata.tar.gz")
                if job == "install":
                    self.assertEqual(args[5], "$target")
                    selection = fetch[fetch.index("target=--include-guest"):fetch.index("/usr/bin/python3.10")]
                    self.assertEqual(selection, 'target=--include-guest\n'
                                     'if [ "$GUEST_RUNTIME" = true ]; then target=--guest-runtime; fi\n')
                    for value, expected in (("false", "--include-guest"), ("true", "--guest-runtime"),
                                            ("", "--include-guest")):
                        result = subprocess.run(["/bin/bash", "-c", selection + 'printf "%s" "$target"'],
                                                env={"GUEST_RUNTIME": value}, capture_output=True,
                                                check=True, timeout=5, text=True)
                        self.assertEqual(result.stdout, expected)
                else:
                    self.assertNotIn("--include-guest", args)
                    self.assertNotIn("--guest-runtime", args)
                    self.assertNotIn("$target", args)
                self.assertNotIn("secrets.", fetch)


if __name__ == "__main__":
    unittest.main()
