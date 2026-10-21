# SPDX-License-Identifier: Apache-2.0
"""Both native paths consume the same authenticated historical public indexes."""

from pathlib import Path
import shlex
import unittest

import yaml

ROOT = Path(__file__).resolve().parent.parent


class NativeIndexWorkflowTests(unittest.TestCase):
    def test_install_preserves_fetch_diagnostic_even_before_worker_exists(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/mkosi-native-build.yml").read_text())
        steps = workflow["jobs"]["install"]["steps"]
        self.assertIn('> build/native-install/input-fetch.log', steps[1]["run"])
        self.assertNotIn("|| true", steps[1]["run"])
        self.assertEqual(steps[-1]["if"], "always()")
        self.assertIn("build/native-install/input-fetch.log", steps[-1]["with"]["path"].splitlines())

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
                self.assertEqual("--include-guest" in args, job == "install")
                self.assertNotIn("secrets.", fetch)


if __name__ == "__main__":
    unittest.main()
