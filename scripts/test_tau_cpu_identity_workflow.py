# SPDX-License-Identifier: Apache-2.0
"""Closed hosted profile wiring; stub commands only, no downloads or Docker."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

import yaml

from test_evidence_history import git

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ".github/workflows/tau-cpu-session-native.yml"
BASE = "46ef675afa9ce3c0031ce3078f2e3ec86eb70aa8"
SHA = "a" * 64
STUBS = r'''
uname() { printf '%s\n' "$TEST_MACHINE"; }
mkdir() { printf 'DIRECTORY\n'; }
python3() { printf 'CALL\0'; printf '%s\0' "$@"; }
jq() {
  test "$1" = -er && test "$2" = .plan_sha256
  test "$3" = "$PWD/build/tau-cpu-session/probe-inputs/setup.json"
  printf '%s\n' "$TEST_SHA"
}
'''


def preparer():
    module = types.ModuleType("test_identity_native_preparer")
    module.__file__ = str(ROOT / "scripts/prepare-tau-cpu-probe-native.py")
    exec(compile(Path(module.__file__).read_bytes(), module.__file__, "exec"), module.__dict__)
    return module


class IdentityWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.workflow = yaml.load((ROOT / WORKFLOW).read_bytes(), Loader=yaml.BaseLoader)
        self.steps = self.workflow["jobs"]["lifecycle"]["steps"]

    def run_step(self, script, support, identity, *, identity_17b="false", runner="github-hosted", machine="x86_64"):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary).resolve()
            result = subprocess.run(["/bin/bash", "--noprofile", "--norc", "-eo", "pipefail",
                                     "-c", STUBS + script], cwd=work, timeout=5,
                env={"PATH": "/usr/bin:/bin", "SUPPORT_PROBE": support, "IDENTITY_FOLLOWUP": identity,
                     "IDENTITY_FOLLOWUP_17B": identity_17b,
                     "RUNNER_ENVIRONMENT": runner, "TEST_MACHINE": machine, "TEST_SHA": SHA},
                stdin=subprocess.DEVNULL, capture_output=True)
            output = result.stdout.replace(os.fsencode(work), b"WORK")
        calls = [part.rstrip(b"\0").decode().split("\0") for part in output.split(b"CALL\0")[1:]]
        return result.returncode, output, calls

    def test_profiles_reject_conflicts_before_preparation_and_keep_venue_guards(self):
        gate = self.steps[1]
        self.assertEqual(gate["name"], "Require native ephemeral runner")
        self.assertEqual(gate["env"], {"SUPPORT_PROBE": "${{ inputs.support_probe }}",
                                      "IDENTITY_FOLLOWUP": "${{ inputs.identity_followup }}",
                                      "IDENTITY_FOLLOWUP_17B": "${{ inputs.identity_followup_17b }}"})
        for support, identity in (("false", "false"), ("true", "false"), ("false", "true")):
            code, output, calls = self.run_step(gate["run"], support, identity)
            self.assertEqual(code, 0)
            self.assertIn(b"DIRECTORY", output)
            self.assertEqual(calls, [])
        for support, identity, runner, machine in (
                ("true", "true", "github-hosted", "x86_64"),
                ("false", "unexpected", "github-hosted", "x86_64"),
                ("", "false", "github-hosted", "x86_64"),
                ("false", "true", "self-hosted", "x86_64"),
                ("false", "true", "github-hosted", "arm64")):
            code, output, calls = self.run_step(gate["run"], support, identity, runner=runner, machine=machine)
            self.assertNotEqual(code, 0)
            self.assertNotIn(b"DIRECTORY", output)
            self.assertEqual(calls, [])

    def test_actual_shell_keeps_default_and_old_probe_argv_and_selects_identity_explicitly(self):
        old = yaml.load(git(ROOT, "show", BASE + ":" + WORKFLOW), Loader=yaml.BaseLoader)
        old_steps = old["jobs"]["lifecycle"]["steps"]
        for support, identity in (("false", "false"), ("true", "false"), ("false", "true")):
            with self.subTest(support=support, identity=identity):
                for index in (2, 3):
                    code, _, calls = self.run_step(self.steps[index]["run"], support, identity)
                    self.assertEqual(code, 0)
                    _, _, previous = self.run_step(old_steps[index]["run"],
                                                  "true" if identity == "true" else support, "false")
                    if identity == "true" and index == 2:
                        previous[-1] += ["--identity-followup"]
                    self.assertEqual(calls, previous)
                    self.assertNotIn("--local-identity-followup", [arg for call in calls for arg in call])
        _, _, calls = self.run_step(self.steps[3]["run"], "false", "true")
        self.assertEqual(calls[0][-4:], ["--probe-payload", "WORK/build/tau-cpu-session/probe-inputs/payload",
                                       "--probe-plan-sha256", SHA])

    def test_workflow_remains_main_only_pinned_bounded_and_metadata_only(self):
        old = yaml.load(git(ROOT, "show", BASE + ":" + WORKFLOW), Loader=yaml.BaseLoader)
        self.assertEqual(set(self.workflow["on"]), {"workflow_dispatch"})
        inputs = self.workflow["on"]["workflow_dispatch"]["inputs"]
        self.assertEqual(set(inputs), {"support_probe", "identity_followup", "identity_followup_17b"})
        for row in inputs.values():
            self.assertEqual((row["type"], row["default"]), ("boolean", "false"))
        for key in ("permissions", "concurrency"):
            self.assertEqual(self.workflow[key], old[key])
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})
        self.assertEqual(set(self.workflow["jobs"]), {"lifecycle"})
        job = self.workflow["jobs"]["lifecycle"]
        self.assertEqual({key: job[key] for key in ("if", "runs-on", "timeout-minutes")},
                         {"if": "github.ref == 'refs/heads/main'", "runs-on": "ubuntu-24.04", "timeout-minutes": "60"})
        old_steps = old["jobs"]["lifecycle"]["steps"]
        self.assertEqual(len(self.steps), len(old_steps))
        for index in (0, 4, 6):
            self.assertEqual(self.steps[index], old_steps[index])
        self.assertEqual(self.steps[2]["timeout-minutes"], "17")
        self.assertEqual(self.steps[3]["timeout-minutes"], "40")
        self.assertEqual(self.steps[3]["continue-on-error"], "true")
        self.assertEqual(self.steps[5], {**old_steps[5],
                         "if": "always() && (inputs.support_probe || inputs.identity_followup || inputs.identity_followup_17b)"})
        self.assertNotIn("secrets.", (ROOT / WORKFLOW).read_text())


class IdentityNativePreparationTests(unittest.TestCase):
    def test_preparation_uses_same_inputs_deadline_and_closed_profile_receipts(self):
        prep = preparer()
        observed = []
        for identity in (False, True):
            with self.subTest(identity=identity), tempfile.TemporaryDirectory() as temporary:
                work = Path(temporary).resolve()
                destination = work / "published"
                runner = mock.Mock()
                runner.runtime.SECONDS = 300
                runner.runtime.CATALOG_SHA = "b" * 64
                files = {"trial.json": b"fixed synthetic plan\n"}
                runner.prepare.return_value = (files, {"producer": {"git_sha": "c" * 40}})
                runner.publish.side_effect = lambda directory, _: directory.mkdir(mode=0o700)
                archive, wheel = ("https://example.invalid/archive", 1, "d" * 64), ("https://example.invalid/wheel", 1, "e" * 64)
                env, snapshot, deadline = {}, object(), time.monotonic() + 10
                with mock.patch.object(prep.rec, "load", return_value=runner), \
                        mock.patch.object(prep, "plan", return_value=(archive, {"fixed.whl": wheel})), \
                        mock.patch.object(prep.native, "fetch") as fetch:
                    row = prep.prepare(destination, ROOT, work, env, snapshot, deadline,
                                       **({"identity_followup": True} if identity else {}))
                self.assertEqual([call.args[1:] for call in fetch.call_args_list],
                                 [("tau.tar.gz", archive, env, snapshot, deadline),
                                  ("fixed.whl", wheel, env, snapshot, deadline)])
                self.assertEqual(runner.prepare.call_args.kwargs,
                                 {"parent_pipe": True, "support_probe": True,
                                  **({"identity_followup": True} if identity else {})})
                self.assertEqual(runner.prepare.call_args.args,
                                 (work / "inputs/tau.tar.gz", work / "inputs", None, None, None, deadline))
                self.assertEqual(json.loads((destination / "setup.json").read_bytes()), row)
                self.assertEqual(row["schema"], "nib.tau-cpu-identity-probe-setup/v1" if identity else "nib.tau-cpu-probe-setup/v1")
                for name in ("upstream_executed", "quality_eligible", "confidential", "live_admission"):
                    self.assertIs(row[name], False)
                observed.append(row)
        self.assertEqual({**observed[1], "schema": observed[0]["schema"]}, observed[0])

    def test_bad_profile_rejected_before_input_loading(self):
        prep = preparer()
        for value in (None, 1, "true"):
            with mock.patch.object(prep.rec, "load") as load, self.assertRaises(ValueError):
                prep.prepare(None, None, None, None, None, 0, identity_followup=value)
            load.assert_not_called()

    def test_native_cli_cannot_bypass_main_or_hosted_preflight(self):
        prep = preparer()
        for args in ([], ["--identity-followup"]):
            for bad_host in (False, True):
                with mock.patch.object(sys, "argv", [prep.SCRIPT, "--output", "/unused", *args]), \
                        mock.patch.dict(os.environ, {"GITHUB_REF": "refs/heads/main" if bad_host else "refs/heads/other"}, clear=True), \
                        mock.patch.object(prep.rec, "host", side_effect=ValueError() if bad_host else None) as host, \
                        mock.patch.object(prep, "prepare") as prepare, self.assertRaises(ValueError):
                    prep.main()
                host.assert_called_once_with("github-actions")
                prepare.assert_not_called()

    def test_native_cli_forwards_only_explicit_profile_after_clean_snapshot_binding(self):
        prep = preparer()
        for identity, dirty in ((False, False), (True, False), (True, True)):
            snapshot = mock.Mock()
            snapshot.controlled_env.return_value = {}
            snapshot.git.return_value = types.SimpleNamespace(stdout=b"changed" if dirty else b"")

            def copy_sources(root, source, control, env):
                for name in (prep.SCRIPT, prep.rec.SCRIPT, prep.native.SCRIPT):
                    path = source / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(root / name, path)
                return "a" * 40, {}, None

            snapshot.snapshot_commit.side_effect = copy_sources
            args = [prep.SCRIPT, "--output", "/unused"] + (["--identity-followup"] if identity else [])
            with mock.patch.object(sys, "argv", args), mock.patch.object(prep.rec, "host"), \
                    mock.patch.dict(os.environ, {"GITHUB_REF": "refs/heads/main"}, clear=True), \
                    mock.patch.object(prep.rec, "load", return_value=snapshot), \
                    mock.patch.object(prep.os, "umask"), mock.patch("builtins.print"), \
                    mock.patch.object(prep, "prepare") as prepare:
                if dirty:
                    self.assertRaises(ValueError, prep.main)
                    prepare.assert_not_called()
                    snapshot.snapshot_commit.assert_not_called()
                else:
                    prep.main()
                    self.assertEqual(prepare.call_count, 1)
                    self.assertEqual(prepare.call_args.kwargs, {"identity_followup": True} if identity else {})
                    snapshot.validate_snapshot.assert_called_once()


if __name__ == "__main__":
    unittest.main()
