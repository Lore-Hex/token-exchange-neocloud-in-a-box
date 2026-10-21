# SPDX-License-Identifier: Apache-2.0
"""Fake Docker lifecycle only; no image or packaged executable runs here."""
import copy
import json
from pathlib import Path
import subprocess
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    module = types.ModuleType("probe_runner_fixture")
    module.__file__ = str(ROOT / path)
    exec(compile(Path(module.__file__).read_bytes(), module.__file__, "exec",
                 dont_inherit=True), module.__dict__)
    return module


host = load("host/probe-mkosi-tools.py")
fixtures = load("scripts/test_guest_tools.py")


class ProbeRunnerTests(unittest.TestCase):
    def setUp(self):
        self.receipt = {"git_sha": "a" * 40, "tools_receipt_sha256": "b" * 64,
                        "image_tag": "nib-tools-startup-probe:" + "c" * 24,
                        "config_sha256": "c" * 64, "layer_sha256s": ["d" * 64, "e" * 64],
                        "files": {"image.tar": {"sha256": "f" * 64}}}
        inputs = {"schema": "nib.mkosi-tools-probe-input/v1", "git_sha": "a" * 40,
                  "tools_receipt_sha256": "b" * 64, "snapshot_sha256": "c" * 64}
        self.result = host.worker.probe(inputs, mock.Mock(return_value=
            subprocess.CompletedProcess([], 0, b"synthetic version", b"")))
        self.row = fixtures.container(self.receipt["image_tag"])
        self.row["Config"]["Entrypoint"][-1] = "/nib/probe.py"
        self.calls = []

    def call(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        out, code = b"", 0
        if argv[:2] == ["image", "inspect"]:
            out = json.dumps([{"Os": "linux", "Architecture": "amd64",
                "RootFS": {"Type": "layers", "Layers": ["sha256:" + s for s in self.receipt["layer_sha256s"]]},
                "Config": {"Labels": {"org.opencontainers.image.revision": self.receipt["git_sha"]}}}]).encode()
        elif argv[:2] == ["container", "inspect"]:
            out = json.dumps([self.row]).encode()
        elif argv[:2] == ["container", "start"]:
            out = json.dumps(self.result).encode()
            code = 0 if self.result["result"] == "pass" else 1
        return subprocess.CompletedProcess(argv, code, out, b"")

    def execute(self, call=None):
        return host.execute({"image.tar": b"verified fixture"}, self.receipt, call or self.call)

    def test_exact_worker_result_and_sandbox_lifecycle(self):
        value = self.execute()
        self.assertEqual((value["result"], value["stage"], value["cleanup"]),
                         ("pass", "complete", "confirmed"))
        self.assertEqual(value["probe"], self.result)
        for name in host.worker.FALSE_CLAIMS:
            self.assertIs(value[name], False)
        create = next(a for a, _ in self.calls if a[:2] == ["container", "create"])
        for flag in ("--pull=never", "--network=none", "--read-only", "--cap-drop=ALL",
                     "--security-opt=no-new-privileges", "--user=65534:65534", "--pids-limit=64"):
            self.assertIn(flag, create)
        self.assertEqual(sum(a[:2] == ["container", "rm"] for a, _ in self.calls), 1)
        self.assertEqual(sum(a[:2] == ["image", "rm"] for a, _ in self.calls), 1)
        self.assertNotIn("synthetic version", json.dumps(value))

    def test_each_partial_failure_is_retained_without_promoting_claims(self):
        original = copy.deepcopy(self.result)
        for i, (name, _) in enumerate(host.worker.COMMANDS):
            for attempts in (i, i + 1):
                self.result = {**original, "result": "fail", "stage": name,
                               "checks": original["checks"][:i], "programs_attempted": attempts}
                self.row["State"]["ExitCode"] = 1
                with self.subTest(stage=name, attempts=attempts):
                    value = self.execute()
                    self.assertEqual((value["result"], value["cleanup"]), ("fail", "confirmed"))
                    self.assertEqual(value["probe"], self.result)

    def test_invalid_envelopes_and_claims_rejected(self):
        for field, value in (("git_sha", "9" * 40), ("tools_receipt_sha256", "9" * 64),
                             ("stage", "python"), ("programs_attempted", True),
                             ("programs_attempted", 6), ("checks", []), ("result", "unknown"),
                             ("raw_log", "PRIVATE")):
            changed = {**self.result, field: value}
            with self.subTest(field=field), self.assertRaises(ValueError):
                host.validate_result(changed, self.receipt)
        for field in host.worker.FALSE_CLAIMS:
            with self.subTest(field=field), self.assertRaises(ValueError):
                host.validate_result({**self.result, field: True}, self.receipt)
        for field, value in (("program", "untrusted"), ("stdout_bytes", True),
                             ("stdout_bytes", 65537), ("stdout_sha256", "PRIVATE"),
                             ("raw_output", "PRIVATE")):
            changed = copy.deepcopy(self.result)
            changed["checks"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                host.validate_result(changed, self.receipt)

    def test_policy_drift_rejects_before_start(self):
        original = copy.deepcopy(self.row)
        for key, value in (("NetworkMode", "bridge"), ("Privileged", True),
                           ("Binds", ["/private:/data"]), ("Devices", [{}]),
                           ("PortBindings", {"443/tcp": []}), ("Memory", 0)):
            self.row = copy.deepcopy(original)
            self.row["HostConfig"][key] = value
            self.calls.clear()
            result = self.execute()
            self.assertEqual(result["result"], "fail")
            self.assertFalse(any(a[:2] == ["container", "start"] for a, _ in self.calls))
        for key, value in (("Entrypoint", ["/bin/sh"]), ("Env", ["TOKEN=PRIVATE"]),
                           ("User", "root"), ("Cmd", ["--arbitrary"])):
            self.row = copy.deepcopy(original)
            self.row["Config"][key] = value
            self.calls.clear()
            self.assertEqual(self.execute()["result"], "fail")
            self.assertFalse(any(a[:2] == ["container", "start"] for a, _ in self.calls))

    def test_load_create_attach_and_cleanup_failures_are_closed_and_redacted(self):
        for where in (["image", "load"], ["container", "create"], ["container", "start"],
                      ["container", "rm"], ["container", "ls"], ["image", "rm"]):
            def call(argv, **kwargs):
                if argv[:2] == where:
                    raise subprocess.TimeoutExpired(["PRIVATE"], 1)
                return self.call(argv, **kwargs)
            with self.subTest(where=where):
                result = self.execute(call)
                self.assertEqual(result["result"], "fail")
                self.assertNotIn("PRIVATE", json.dumps(result))
                if where != ["container", "start"]:
                    self.assertEqual(result["cleanup"], "unconfirmed")

    def test_preexisting_image_never_deleted(self):
        call = mock.Mock(return_value=subprocess.CompletedProcess([], 0, b"existing", b""))
        result = self.execute(call)
        self.assertEqual((result["result"], result["cleanup"]), ("fail", "not-created"))
        call.assert_called_once()

    def test_result_must_match_terminal_exit(self):
        self.row["State"]["ExitCode"] = 1
        result = self.execute()
        self.assertEqual((result["result"], result["probe"], result["cleanup"]),
                         ("fail", None, "confirmed"))

    def test_builder_and_runner_use_identical_committed_input_set(self):
        with mock.patch.object(host.prep.tools, "committed", return_value=("a" * 40, {})) as committed:
            host.prep.committed()
        extra = committed.call_args.args[0]
        self.assertIn(host.SCRIPT, extra)
        self.assertIn(host.PREPARER, extra)
        self.assertIn(host.prep.WORKER, extra)
        self.assertIn(host.LIFECYCLE, host.prep.tools.FILES)

    def test_evidence_retains_actual_failure_and_declares_software_scope(self):
        for result in ("pass", "fail"):
            report = {**self.execute(), "result": result, "started_at": "2026-10-21T02:56:24Z",
                      "finished_at": "2026-10-21T02:56:27Z"}
            files = host.evidence_files(report)
            self.assertEqual(set(files), {"run.json", "output.log"})
            run = json.loads(files["run.json"])
            self.assertEqual(run["result"], result)
            self.assertEqual(run["git_sha"], report["git_sha"])
            self.assertEqual(run["started_utc"], report["started_at"])
            self.assertEqual(run["execution"], "software")
            self.assertIs(run["hardware_validated"], False)
            self.assertEqual(json.loads(files["output.log"]), report)


if __name__ == "__main__":
    unittest.main()
