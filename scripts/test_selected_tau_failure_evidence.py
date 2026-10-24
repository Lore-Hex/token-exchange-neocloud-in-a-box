# SPDX-License-Identifier: Apache-2.0
"""Original controller failures; CPU-only cleanup is not whole-run cleanup."""

import ast
import copy
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

from test_evidence_history import git

ROOT = Path(__file__).resolve().parents[1]
PRODUCER = "025b7be189b18900d6ebd860e344a80096069aec"
SOURCE = "scripts/record-tau-selected.py"
SOURCE_SHA = "baf1fc9c2e84be9a5301f571209620c4ddd0917f1c6dc6e2c237a511cf3e7272"
RUNS = {
    "1736": {
        "controller": "",
        "window": ("2026-10-24T17:37:00Z", "2026-10-24T17:37:00Z"),
        "exits": {"candidate": 1, "support": 1},
        "hashes": {
            "run.json": (886, "e21582427b3e1ee8e20b7099c69705df0d086951bd5257bace298d6319e589cf"),
            "output.log": (430, "8279ea3f7244dd493b67d907296e69e7f3c63d6a949696ace60d552c077f6257"),
        },
    },
    "1741": {
        "controller": "evidence/",
        "window": ("2026-10-24T17:41:44Z", "2026-10-24T17:44:55Z"),
        "exits": {},
        "hashes": {
            "evidence/run.json": (886, "501432cbe4506e94baf746e0ce2404a6740f90be445b6324878b1691892ebdcc"),
            "evidence/output.log": (405, "bd67556ac1954fb0f6d84e4d0366e7d2700aa19f843ce0daf6ba53cf0c8845b5"),
            "cpu/run.json": (737, "05c04179dcaf4a98774af4cd6e0b9df90d2c21348b42d9ab25430b262de3f108"),
            "cpu/output.log": (532, "dcff3ecb22012c906786d941593f51249ee29d5c54400a6d74c2480b3b27fe72"),
        },
    },
    "1800": {
        "controller": "evidence/",
        "window": ("2026-10-24T17:59:09Z", "2026-10-24T18:05:41Z"),
        "exits": {},
        "hashes": {
            "evidence/run.json": (886, "79d3ba73d71a953201cd189ffcc44d9b6d3e3ca5df70ce1ef0a7b58ff678ac0c"),
            "evidence/output.log": (405, "f5c4d1ad2f2f5054c1b6c7a6089219843b095825219377c5436201117fb71a8e"),
            "cpu/run.json": (737, "c05d5ecca6f1a24597239475566192b577be11ccca0fd793325e7921c9e15937"),
            "cpu/output.log": (6080, "f5bb21cb236f9454c8f050fbe392c1db2cb44f1173f2c5d7802d9a9db0d31516"),
        },
    },
}


@contextmanager
def historical_cpu(producer, report):
    """Validate with the exact recorded source closure, without launching it."""
    git(ROOT, "merge-base", "--is-ancestor", producer, "HEAD")
    with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(sys.modules):
        root = Path(temporary).resolve()
        for path, sha in report["sources"].items():
            raw = git(ROOT, "show", producer + ":" + path)
            if hashlib.sha256(raw).hexdigest() != sha:
                raise AssertionError("original CPU source identity changed")
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        path = root / "scripts/record-tau-cpu-parity.py"
        recorder = types.ModuleType("historical_cpu_recorder")
        recorder.__file__ = str(path)
        exec(compile(path.read_bytes(), str(path), "exec", dont_inherit=True), recorder.__dict__)
        selected = report["schema"] == "nib.tau-cpu-owner-run/v1"
        expected = set(recorder.SOURCES + (recorder.OWNER_SOURCES if selected else ()))
        if set(report["sources"]) != expected:
            raise AssertionError("original CPU source closure changed")
        cpu = recorder.toolkit(root, selected_owner=selected)
        yield recorder, cpu, sys.modules["tau_cpu_session"]


class SelectedTauFailureEvidenceTests(unittest.TestCase):
    case = "1736"

    def setUp(self):
        self.spec = RUNS[self.case]
        directory = ROOT / "evidence/p2" / ("2026-10-24-selected-tau-readiness-failure-" + self.case)
        self.documents = {}
        for name, (size, sha) in self.spec["hashes"].items():
            raw = (directory / name).read_bytes()
            self.assertEqual((len(raw), hashlib.sha256(raw).hexdigest()), (size, sha))
            self.documents[name] = json.loads(raw)
        self.run, self.report = (self.documents[self.spec["controller"] + name]
                                 for name in ("run.json", "output.log"))

    def source_assignments(self, path, sha, function_name, names):
        raw = git(ROOT, "show", PRODUCER + ":" + path)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), sha)
        function = next(node for node in ast.parse(raw).body
                        if isinstance(node, ast.FunctionDef) and node.name == function_name)
        assignments = {target.id: node.value for node in ast.walk(function)
                       if isinstance(node, ast.Assign) for target in node.targets
                       if isinstance(target, ast.Name) and target.id in names}
        self.assertEqual(set(assignments), set(names))
        return function, assignments

    def test_original_bytes_and_exact_producer_are_retained(self):
        git(ROOT, "merge-base", "--is-ancestor", PRODUCER, "HEAD")
        for row in (self.run, self.report):
            self.assertEqual(row["git_sha"], PRODUCER)
            self.assertEqual((row["started_utc"], row["ended_utc"]), self.spec["window"])
            self.assertEqual(row["result"], "fail")
        self.assertEqual(self.run["execution"], "hardware")
        self.assertIs(self.run["hardware_validated"], False)

    def test_original_source_defines_the_record_shape(self):
        function, assignments = self.source_assignments(SOURCE, SOURCE_SHA, "run", ("report", "envelope"))
        namespace = {"toolkit": {"git_sha": PRODUCER},
                     "runner": types.SimpleNamespace(now=lambda: self.spec["window"][0])}
        # Evaluate only the two hash-pinned dictionary constructors, never the
        # launcher body, imports, filesystem operations or subprocess paths.
        expected = eval(compile(ast.Expression(assignments["report"]), SOURCE, "eval"),
                        {"__builtins__": {}}, namespace)
        updates = [(ast.literal_eval(target.slice), node.value.value)
                   for node in ast.walk(function) if isinstance(node, ast.Assign)
                   and isinstance(node.value, ast.Constant) for target in node.targets
                   if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name)
                   and target.value.id == "report"]
        self.assertIn(("stage", "readiness"), updates)
        self.assertIn(("failure", "selected-operational-run-failed"), updates)
        expected.update(stage="readiness", owner_exits=self.spec["exits"],
                        failure="selected-operational-run-failed", ended_utc=self.spec["window"][1])
        self.assertEqual(self.report, expected)
        envelope = eval(compile(ast.Expression(assignments["envelope"]), SOURCE, "eval"),
                        {"__builtins__": {}}, {**namespace, "report": expected})
        self.assertEqual(self.run, envelope)

    def test_failure_is_not_upgraded_to_readiness_cleanup_or_quality(self):
        self.assertEqual(self.report["schema"], "nib.tau-selected-run/v1")
        self.assertEqual(self.report["cleanup"], "unconfirmed")
        self.assertEqual(self.report["ready_sha256"], {})
        self.assertIsNone(self.report["owner_reports"])
        self.assertIsNone(self.report["scorer"])
        self.assertEqual(self.report["owner_exits"], self.spec["exits"])
        for name in ("quality_eligible", "confidential", "live_admission"):
            self.assertIs(self.report[name], False)
        self.assertNotIn("cause", self.report)
        self.assertNotIn("message", self.report)


class SelectedTau1741FailureEvidenceTests(SelectedTauFailureEvidenceTests):
    case = "1741"

    def test_cpu_input_failure_cleanup_does_not_upgrade_controller(self):
        path = "scripts/record-tau-cpu-parity.py"
        function, assignments = self.source_assignments(
            path, "1fe51980fb054b536ab08980b3103b8b6cbe60491a06fae6f2dd8628304fb322",
            "main", ("report", "record"))
        namespace = {
            "revision": PRODUCER,
            "archive_sha": "acb408ae38ffa016e1f75bfea76dff7b78de4651aaaa677df061c5e099cf6292",
            "now": lambda: self.spec["window"][0],
            "facts": {"provider": "local", "machine": "owned-linux-amd64-container",
                      "gpus": "none", "driver": None, "cuda": None},
        }
        for name, value in assignments.items():
            namespace[name] = eval(compile(ast.Expression(value), path, "eval"), {"__builtins__": {}}, namespace)
        owner = next(node for node in ast.walk(function) if isinstance(node, ast.If)
                     and isinstance(node.test, ast.Attribute) and node.test.attr == "selected_owner"
                     and isinstance(node.test.value, ast.Name) and node.test.value.id == "args")
        # Only source-pinned schema, command and note assignments for owner mode.
        exec(compile(ast.Module(body=owner.body, type_ignores=[]), path, "exec"), {"__builtins__": {}}, namespace)
        expected, record = namespace["report"], namespace["record"]
        expected["cleanup"] = dict.fromkeys(("container", "image", "build_completion"), "confirmed")
        record["ended_utc"] = "2026-10-24T17:41:45Z"
        record["notes"] = record["notes"].split(" Pending")[0]
        self.assertEqual(self.documents["cpu/output.log"], expected)
        self.assertEqual(self.documents["cpu/run.json"], record)
        self.assertEqual(expected["stage"], "inputs")
        self.assertEqual(expected["result"], "fail")
        self.assertIsNone(expected["runtime"])
        self.assertEqual(expected["sources"], {})
        self.assertEqual(self.report["cleanup"], "unconfirmed")
        self.assertIsNone(self.report["owner_reports"])


class SelectedTau1800FailureEvidenceTests(SelectedTauFailureEvidenceTests):
    case = "1800"

    def test_cpu_runtime_failure_is_source_bound_without_promoting_controller(self):
        run, report = self.documents["cpu/run.json"], self.documents["cpu/output.log"]
        self.assertEqual((run["git_sha"], report["git_sha"]), (PRODUCER, PRODUCER))
        self.assertEqual((run["started_utc"], run["ended_utc"]),
                         ("2026-10-24T17:59:09Z", "2026-10-24T18:02:31Z"))
        self.assertEqual((run["result"], report["result"], report["stage"]), ("fail", "fail", "runtime"))
        self.assertEqual(run["execution"], "software")
        self.assertIs(run["hardware_validated"], False)
        self.assertEqual(report["cleanup"], dict.fromkeys(("container", "image", "build_completion"), "confirmed"))
        self.assertEqual(report["source_archive_sha256"],
                         "acb408ae38ffa016e1f75bfea76dff7b78de4651aaaa677df061c5e099cf6292")
        self.assertTrue(all(report[key] is False for key in ("quality_eligible", "confidential", "live_admission")))
        with historical_cpu(PRODUCER, report) as (recorder, cpu, session):
            self.assertEqual(len(report["sources"]), 31)
            pins = json.loads((cpu.ROOT / "scripts/e2e/pins.json").read_bytes())
            self.assertEqual(report["base_images"], {key: pins[key] for key in ("go_image", "python_image")})
            self.assertIs(session.validate(report["runtime"]), report["runtime"])
            runtime = report["runtime"]
            self.assertEqual((runtime["schema"], runtime["stage"], runtime["result"], runtime["cleanup"]),
                             ("nib.tau-cpu-session/v4", "parity", "fail", "confirmed"))
            self.assertFalse(runtime["acknowledged"])
            self.assertEqual(runtime["receipts"], {})
            self.assertIsNone(runtime["ready_sha256"])
            self.assertIsNone(runtime["owner"])
            self.assertEqual((runtime["progress"]["model_index"], runtime["progress"]["substage"]), (0, "health"))
            parity = runtime["progress"]["parity"]
            self.assertEqual((parity["result"], parity["stage"]), ("fail", "readiness"))
            self.assertEqual(len(parity["models"]), 1)
            self.assertEqual(parity["models"][0]["cases"], [])
            self.assertIsNone(parity["models"][0]["process"])
            self.assertIsNone(parity["models"][0]["ready_ms"])
            self.assertEqual((runtime["failure"]["kind"], runtime["failure"]["errno"], runtime["failure"]["server_exit"]),
                             ("other", None, -6))
            self.assertEqual([(frame["source"], frame["line"]) for frame in runtime["failure"]["frames"]],
                             [("scripts/tau_cpu_session.py", 209), ("scripts/pilot/run.py", 128), ("scripts/pilot/run.py", 47)])
            for frame in runtime["failure"]["frames"]:
                self.assertEqual(frame["source_sha256"], report["sources"][frame["source"]])
            for mutate in (lambda row: row.update(result="pass"), lambda row: row.update(quality_eligible=True),
                           lambda row: row["failure"].update(message="private-canary"),
                           lambda row: row["failure"]["frames"][0].update(source_sha256="0" * 64)):
                changed = copy.deepcopy(runtime)
                mutate(changed)
                with self.assertRaises((ValueError, cpu.llama.worker.Abort)):
                    session.validate(changed)
        self.assertEqual(self.report["cleanup"], "unconfirmed")
        self.assertIsNone(self.report["owner_reports"])


if __name__ == "__main__":
    unittest.main()
