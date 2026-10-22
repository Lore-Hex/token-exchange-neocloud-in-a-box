# SPDX-License-Identifier: Apache-2.0
"""Runner isolation tests; HCL behavior is exercised by make terraform-check."""

import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

import check_gcp_terraform as checker


class GcpTerraformTests(unittest.TestCase):
    def test_environment_does_not_inherit_credentials_or_cli_overrides(self):
        poisoned = {"GOOGLE_APPLICATION_CREDENTIALS": "/synthetic/not-a-credential",
                    "TF_CLI_ARGS_test": "apply", "TF_VAR_instance_count": "1",
                    "TF_CLI_CONFIG_FILE": "/synthetic/config", "TF_REATTACH_PROVIDERS": "{}",
                    "HTTPS_PROXY": "https://synthetic.invalid", "CLOUDSDK_CONFIG": "/synthetic"}
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, poisoned):
            env = checker.isolated_environment(Path(temporary))
            self.assertEqual(set(env) & set(poisoned), {"TF_CLI_CONFIG_FILE"})
            self.assertNotEqual(env["TF_CLI_CONFIG_FILE"], poisoned["TF_CLI_CONFIG_FILE"])
            self.assertEqual(list(Path(env["HOME"]).iterdir()), [])
            self.assertEqual(env["PATH"], "/usr/bin:/bin")

    def test_only_reviewed_inputs_are_staged(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, target = Path(temporary) / "source", Path(temporary) / "target"
            source.mkdir()
            for name in checker.FILES:
                path = source / name
                path.parent.mkdir(exist_ok=True, parents=True)
                path.write_text("synthetic fixture")
            for name in ("terraform.tfstate", "secret.auto.tfvars", "terraform.rc",
                         "tests/unreviewed.tftest.hcl"):
                (source / name).write_text("must not copy")
            checker.stage_module(source, target)
            copied = {p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file()}
            self.assertEqual(copied, set(checker.FILES))
            (source / "extra.tf").write_text("unreviewed")
            with self.assertRaisesRegex(ValueError, "file set"):
                checker.stage_module(source, target)
            (source / "extra.tf").unlink()
            (source / "override.tf.json").write_text("{}")
            with self.assertRaisesRegex(ValueError, "file set"):
                checker.stage_module(source, target)

    def test_symlink_input_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, target = Path(temporary) / "source", Path(temporary) / "target"
            checker.stage_module(checker.ROOT / checker.MODULE, source)
            (source / "versions.tf").unlink()
            (source / "versions.tf").symlink_to(source / "main.tf")
            with self.assertRaisesRegex(ValueError, "regular files"):
                checker.stage_module(source, target)

    def test_all_tests_must_finish_and_pass(self):
        events = [{"type": "test_run", "test_run": {"run": name, "status": "pass",
                   "progress": "complete"}} for name in sorted(checker.RUNS)]
        events.append({"type": "test_summary", "test_summary": {"status": "pass",
                       "passed": len(checker.RUNS), "failed": 0, "errored": 0, "skipped": 0}})

        def encoded(items):
            return "\n".join(json.dumps(e) for e in items).encode()

        checker.require_pass(encoded(events))
        for mutation in (events[:-1], events[1:], events + [events[-1]]):
            with self.subTest(mutation=mutation):
                with self.assertRaises(ValueError):
                    checker.require_pass(encoded(mutation))
        events[0]["test_run"]["status"] = "fail"
        with self.assertRaises(ValueError):
            checker.require_pass(encoded(events))

    def test_plan_only_and_no_real_provider_configuration(self):
        hcl = (checker.ROOT / checker.MODULE / "tests/lab.tftest.hcl").read_text()
        self.assertEqual(set(re.findall(r'^run "([^"]+)"', hcl, re.M)), checker.RUNS)
        self.assertEqual(re.findall(r'^  command = (\w+)$', hcl, re.M), ["plan"] * len(checker.RUNS))
        self.assertIn('mock_provider "google" {', hcl)
        self.assertIn("override_during = plan", hcl)
        self.assertNotRegex(hcl, r'(?m)^provider\s+"')
        self.assertEqual([cmd[0] for cmd in checker.COMMANDS], ["fmt", "init", "validate", "test", "test"])
        self.assertIn("-backend=false", checker.COMMANDS[1])
        self.assertIn("-lockfile=readonly", checker.COMMANDS[1])


if __name__ == "__main__":
    unittest.main()
