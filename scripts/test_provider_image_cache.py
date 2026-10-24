# SPDX-License-Identifier: Apache-2.0
"""The replacement-node prerequisite fetches a fixed image, never executes it."""

import ast
import contextlib
import copy
import io
import json
from pathlib import Path
import subprocess
import unittest
from unittest import mock

from test_lab_runtime import pull

ROOT = Path(__file__).resolve().parents[1]


def metadata():
    return {"Id": pull.CHECKER_MANIFEST, "Os": "linux", "Architecture": "amd64",
            "RepoDigests": ["python@" + pull.CHECKER_IMAGE.rsplit("@", 1)[1]], "Config": {},
            "Descriptor": {"digest": pull.CHECKER_MANIFEST, "mediaType": "application/vnd.oci.image.manifest.v1+json",
                           "platform": {"os": "linux", "architecture": "amd64"}}}


class ProviderImageCacheTests(unittest.TestCase):
    def test_pins_match_existing_checker_and_tau_chain(self):
        pins = json.loads((ROOT / "scripts/e2e/pins.json").read_bytes())
        self.assertEqual(pull.CHECKER_IMAGE, pins["python_image"])
        tree = ast.parse((ROOT / "scripts/tau_trial.py").read_bytes())
        constants = {node.targets[0].id: ast.literal_eval(node.value) for node in tree.body
                     if isinstance(node, ast.Assign) and len(node.targets) == 1
                     and isinstance(node.targets[0], ast.Name) and node.targets[0].id in ("PROBE_CONFIG", "PROBE_MANIFEST")}
        self.assertEqual(constants, {"PROBE_CONFIG": pull.CHECKER_CONFIG, "PROBE_MANIFEST": pull.CHECKER_MANIFEST})

    def test_exact_classic_and_descriptor_stores(self):
        for classic in (False, True):
            row = metadata()
            if classic:
                row["Id"] = pull.CHECKER_CONFIG
                del row["Descriptor"]
            with self.subTest(classic=classic), mock.patch.object(pull.subprocess, "run") as run:
                run.return_value = subprocess.CompletedProcess([], 0, json.dumps([row]).encode(), b"")
                self.assertTrue(pull.image_identity(pull.ENV, "provider-check"))
                self.assertEqual(run.call_args.args[0][-3:], ["--platform", "linux/amd64", pull.CHECKER_IMAGE])

    def test_cross_image_platform_and_manifest_are_rejected(self):
        base = metadata()
        mutations = [dict(base, Id=pull.CONFIG), dict(base, Architecture="arm64"), dict(base, Os="windows"),
                     dict(base, RepoDigests=[]), dict(base, RepoDigests=[pull.IMAGE]),
                     dict(base, Config={"Volumes": {"/private": {}}}), dict(base, Config=None),
                     dict(base, Variant="v8"), dict(base, Descriptor={"digest": "sha256:" + "a" * 64})]
        wrong_platform = copy.deepcopy(base)
        wrong_platform["Descriptor"]["platform"]["architecture"] = "arm64"
        mutations.append(wrong_platform)
        for row in mutations:
            with self.subTest(row=row), mock.patch.object(pull.subprocess, "run") as run:
                run.return_value = subprocess.CompletedProcess([], 0, json.dumps([row]).encode(), b"")
                with self.assertRaises(ValueError):
                    pull.image_identity(pull.ENV, "provider-check")

    def test_no_arbitrary_profile_or_image(self):
        with mock.patch.object(pull.subprocess, "run") as run, mock.patch.object(pull.subprocess, "Popen") as start:
            for value in (None, "", "python:latest", "https://example.invalid", "provider-check\n"):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    pull.image_identity(pull.ENV, value)
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                pull.main(["--profile", "python:latest"])
            run.assert_not_called()
            start.assert_not_called()

    def test_cached_main_does_not_pull_or_execute(self):
        for args, profile in (([], "engine"), (["--profile", "provider-check"], "provider-check")):
            with self.subTest(profile=profile), mock.patch.object(pull, "image_identity", return_value=True) as identify, \
                    mock.patch.object(pull.subprocess, "Popen") as start, contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(pull.main(args), 0)
                start.assert_not_called()
                self.assertEqual([call.args[1] for call in identify.call_args_list], [profile, profile])
                row = json.loads(output.getvalue())
                self.assertEqual(row["image"], pull.selection(profile)[0])
                self.assertIs(row["cached"], True)
                self.assertIs(row["executed"], False)
                self.assertIs(row["confidential_qualified"], False)

    def test_missing_cache_has_bounded_pull_then_verification(self):
        with mock.patch.object(pull, "image_identity", side_effect=[False, True]), \
                mock.patch.object(pull.subprocess, "Popen") as start, contextlib.redirect_stdout(io.StringIO()):
            start.return_value.wait.return_value = 0
            start.return_value.poll.return_value = 0
            self.assertEqual(pull.main(["--profile", "provider-check"]), 0)
            self.assertEqual(start.call_args.args[0], ["/usr/bin/docker", "pull", "--platform", "linux/amd64", pull.CHECKER_IMAGE])
            options = start.call_args.kwargs
            self.assertIs(options["start_new_session"], True)
            for channel in ("stdin", "stdout", "stderr"):
                self.assertEqual(options[channel], subprocess.DEVNULL)
            self.assertEqual(options["env"]["HOME"], "/nonexistent")
            self.assertFalse(Path(options["env"]["DOCKER_CONFIG"]).exists())
            start.return_value.wait.assert_called_once_with(timeout=900)

    def test_timeout_kills_and_reaps_only_its_pull_group(self):
        with mock.patch.object(pull, "image_identity", return_value=False), \
                mock.patch.object(pull.subprocess, "Popen") as start, mock.patch.object(pull.os, "killpg") as kill:
            child = start.return_value
            child.pid, child.poll.return_value = 9876, None
            child.wait.side_effect = [subprocess.TimeoutExpired("fixed-pull", 900), 0]
            with self.assertRaises(subprocess.TimeoutExpired):
                pull.main(["--profile", "provider-check"])
            kill.assert_called_once_with(9876, pull.signal.SIGKILL)
            self.assertEqual(child.wait.call_args_list, [mock.call(timeout=900), mock.call(timeout=5)])

    def test_post_pull_missing_image_is_not_success(self):
        with mock.patch.object(pull, "image_identity", side_effect=[False, False]), \
                mock.patch.object(pull.subprocess, "Popen") as start:
            start.return_value.wait.return_value = start.return_value.poll.return_value = 0
            with self.assertRaisesRegex(ValueError, "image-not-present"):
                pull.main(["--profile", "provider-check"])


if __name__ == "__main__":
    unittest.main()
