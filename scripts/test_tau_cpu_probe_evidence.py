# SPDX-License-Identifier: Apache-2.0
"""Replay original CPU prompt/parser receipts, never a live admission or model run."""
import ast
import copy
import datetime
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

import tau_cpu_probe
import tau_cpu_session as session
import tau_support_probe as policy
import test_tau_cpu_controller as controller
import test_tau_trial as trial
from test_evidence_history import git

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "evidence/p2/2026-10-22-tau-cpu-prompt-fit-pass"
PRODUCER = "4fe0bd7df8f6ce3293e6dca609106b37dd9676a3"
PLAN = "06247d35d8022a7f91fcb64d52c138baadb4567f58adabaa4e73251e621d52de"
HASHES = {
    "run.json": "a079971ec07f45eb357d4b39737be34ccf5ceb51fcd03b9bda0ce22529ef468f",
    "output.log": "da93b8aa62efb751a0fce19182cef6dd5baffcd72cf5caa818571b946ac5dc4e",
    "setup.json": "65ba2db57e02ee37daeaf4707988e86621158730d0f074807a29c340461d3ad6",
    "pair/run.json": "6e12578922dfcdd78b96caab19020b343f52d0d7a64901c4ed93fb462c2c15a8",
    "pair/output.log": "81c079071597e2f085c3f3265a8932625e556c0d8f81d73181be712de36e6ace",
    "pair/simulator-runtime.json": "f109aa60eb5a84f22a7eba41615fbe35fdf1e734c449d36fb468a9188f3d1ffd",
    "pair/evaluator-runtime.json": "7eefcd8547fa16bcb0089a7269adc04687a7c4100659c39ddbc6d7c36a8ccb22",
    "pair/scorer/run.json": "dc3e4ace3ab57c5b55646f9a7565dbb94e5e4b45b92999f85c7169d78f9b2076",
    "pair/scorer/output.log": "fad9ab360324d2c8bf14a93acee0ce510737f2fc1ec33b8d071cc723c82d77f1",
}
HASHES.update({"pair/" + role + "-tokenizer.json": sha for role, sha in (
    ("simulator", "a51a69f903338d9651a6853f3f452bcb63bfad0a279eb721c17d6557f20165b4"),
    ("evaluator", "dae972a868e9cd829062348c9bc4e51b1fc3d4b51dd18fc122818c2ae2e83b8c"),
)})


def document(name):
    return session.llama.worker.decode((EVIDENCE / name).read_bytes())


def historical_sources():
    source = git(ROOT, "show", PRODUCER + ":scripts/record-tau-cpu-parity.py")
    assignments = {target.id: node.value for node in ast.parse(source).body if isinstance(node, ast.Assign)
                   for target in node.targets if isinstance(target, ast.Name)}
    return tuple(ast.literal_eval(assignments[node.id] if isinstance(node, ast.Name) else node)
                 for node in assignments["SOURCES"].elts)


def ready_packet():
    runtime = document("pair/simulator-runtime.json")
    return {"schema": "nib.tau-cpu-pair-ready/v1", "producer": runtime["producer"],
            "image_sha256": runtime["serving"]["image_sha256"],
            "expires_unix": document("output.log")["ready"]["expires_unix"],
            "receipts": {name: (EVIDENCE / "pair" / name).read_text(encoding="ascii")
                         for name in session.FILES}}


class CPUProbeEvidenceTests(unittest.TestCase):
    def test_eleven_originals_and_exact_historical_source_closures(self):
        self.assertEqual({p.relative_to(EVIDENCE).as_posix() for p in EVIDENCE.rglob("*")
                          if p.is_file()}, set(HASHES) | {"README.md"})
        for name, sha in HASHES.items():
            raw = (EVIDENCE / name).read_bytes()
            self.assertLessEqual(len(raw), 65536)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), sha)
            document(name)
        closure = set(historical_sources())
        self.assertEqual(len(closure), 27)
        controller_closure = closure | {"scripts/record-tau-cpu-session.py",
            ".github/workflows/tau-cpu-session-native.yml", "scripts/pilot/export.py",
            "scripts/e2e/run.py", "scripts/prepare-tau-cpu-probe-native.py"}
        for name, expected in (("pair/output.log", closure), ("output.log", controller_closure)):
            row = document(name)
            self.assertEqual(row["git_sha"], PRODUCER)
            self.assertEqual(set(row["sources"]), expected)
            self.assertEqual(row["source_archive_sha256"],
                             "eeb5f1fc322122b298040d0e3765941747347d653dac9dbb4ea7ce6a0facca17")
            for path, sha in row["sources"].items():
                self.assertEqual(hashlib.sha256(git(ROOT, "show", PRODUCER + ":" + path)).hexdigest(), sha)
        toolkit = document("pair/scorer/output.log")["toolkit"]
        self.assertEqual(toolkit["git_sha"], PRODUCER)
        self.assertEqual(len(toolkit["files"]), 20)
        for path, sha in toolkit["files"].items():
            self.assertEqual(hashlib.sha256(git(ROOT, "show", PRODUCER + ":" + path)).hexdigest(), sha)
        lock = json.loads(git(ROOT, "show", PRODUCER + ":bench/tau-runtime-lock.json"))
        self.assertEqual(document("setup.json")["source_archive_sha256"], lock["archive_sha256"])

    def test_original_ready_frame_and_receipts_replay_without_provenance_bypass(self):
        packet = ready_packet()
        held = document("output.log")["ready"]
        # Only wall time is historical. Source, schema, pin and digest checks run normally.
        observed = datetime.datetime.fromisoformat(document("pair/scorer/output.log")["started_utc"]).timestamp()
        def accept(value):
            return session.accept(session.probes.encoded(value), packet["producer"], packet["image_sha256"],
                                  time.monotonic() + 10)
        with mock.patch.object(session.time, "time", return_value=observed):
            originals, checked = accept(packet)
            self.assertEqual(checked, held)
            for name, raw in originals.items():
                self.assertEqual(raw, (EVIDENCE / "pair" / name).read_bytes())
            for mutation in (lambda p: p.update(image_sha256="0" * 64),
                             lambda p: p["producer"].update(source_sha256="0" * 64),
                             lambda p: p.update(expires_unix=int(observed) - 1)):
                changed = copy.deepcopy(packet)
                mutation(changed)
                with self.assertRaises((ValueError, session.llama.worker.Abort)):
                    accept(changed)
            changed = copy.deepcopy(packet)
            runtime = json.loads(changed["receipts"]["evaluator-runtime.json"])
            runtime["process"]["pid"] = document("pair/simulator-runtime.json")["process"]["pid"]
            changed["receipts"]["evaluator-runtime.json"] = session.tau.encoded(runtime).decode("ascii")
            with self.assertRaises((ValueError, session.llama.worker.Abort)):
                accept(changed)
        with mock.patch.object(session.time, "time", return_value=held["expires_unix"] + 1):
            with self.assertRaises((ValueError, session.llama.worker.Abort)):
                accept(packet)

    def test_original_scorer_and_terminal_joins_reject_mutated_claims(self):
        control = controller.control
        outer, inner, scorer = (document(name) for name in
            ("output.log", "pair/output.log", "pair/scorer/output.log"))
        spec = {"sha": PLAN, "plan": {key: scorer[key] for key in ("image", "platform")},
                "toolkit": scorer["toolkit"], "runner": trial.runner, "notice": outer["probe"]}
        spec["plan"]["selection"] = policy.SELECTION
        with tempfile.TemporaryDirectory() as temporary:
            private = Path(temporary).resolve()
            for name in HASHES:
                if not name.startswith("pair/"):
                    continue
                path = private / name.removeprefix("pair/")
                path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                path.write_bytes((EVIDENCE / name).read_bytes())
                path.chmod(0o600)
            actual = control.probe_receipts(session, private, outer["probe"], outer["ready"],
                                           spec, time.monotonic() + 5)
            self.assertEqual(actual, scorer)
            self.assertEqual(spec["receipts"], outer["scorer_receipts"])
            args = (session, private, outer["ready"], inner["image_id"].removeprefix("sha256:"),
                    PRODUCER, outer["source_archive_sha256"], outer["sources"])
            historical = historical_sources()
            self.assertEqual(len(historical), 27)
            self.assertEqual(set(control.rec.SOURCES), set(historical) | {"scripts/tau_cpu_identity.py"})
            # Old originals are not current-source evidence. Only this replay
            # uses the exact producer's closure; every receipt byte stays old.
            with self.assertRaises((KeyError, ValueError, session.llama.worker.Abort)):
                control.final_receipts(*args, time.monotonic() + 5, probe=spec)
            with mock.patch.object(control.rec, "SOURCES", historical):
                self.assertEqual(control.final_receipts(*args, time.monotonic() + 5, probe=spec),
                                 outer["producer_receipts"])
                for mutation in (lambda r: r.update(quality_eligible=True),
                                 lambda r: r["runtime"].update(acknowledged=False),
                                 lambda r: r["cleanup"].update(container="unknown"),
                                 lambda r: r["runtime"]["probe"].update(status="timeout"),
                                 lambda r: r["runtime"]["probe"]["roles"].update(agent=0),
                                 lambda r: r["sources"].pop(historical[0]),
                                 lambda r: r["sources"].update({historical[0]: "0" * 64}),
                                 lambda r: r["sources"].update({"scripts/tau_cpu_identity.py": "0" * 64})):
                    changed = copy.deepcopy(inner)
                    mutation(changed)
                    (private / "output.log").write_bytes(session.tau.encoded(changed))
                    with self.assertRaises((ValueError, session.llama.worker.Abort)):
                        control.final_receipts(*args, time.monotonic() + 5, probe=spec)
            self.assertEqual(set(control.rec.SOURCES), set(historical) | {"scripts/tau_cpu_identity.py"})
            for mutation in (lambda r: r.update(cleanup="failed"),
                             lambda r: r.update(plan_sha256="0" * 64),
                             lambda r: r["diagnostic"]["observations"][0].update(prompt_tokens=4097)):
                changed = copy.deepcopy(scorer)
                mutation(changed)
                (private / "scorer/output.log").write_bytes(session.tau.encoded(changed))
                with self.assertRaises((ValueError, session.llama.worker.Abort)):
                    control.probe_receipts(session, private, outer["probe"], outer["ready"],
                                           spec, time.monotonic() + 5)

    def test_measured_counts_bounds_cleanup_and_no_target_quality_claim(self):
        outer, inner, scorer, setup = (document(name) for name in
            ("output.log", "pair/output.log", "pair/scorer/output.log", "setup.json"))
        diagnostic = scorer["diagnostic"]
        tau_cpu_probe.validate(diagnostic)
        self.assertEqual(diagnostic, outer["probe"]["diagnostic"])
        self.assertEqual(diagnostic, inner["runtime"]["probe"])
        self.assertEqual(diagnostic["selection"], policy.SELECTION)
        self.assertEqual(diagnostic["roles"], {"agent": 1, "simulator": 1, "evaluator": 1})
        self.assertEqual([(row["role"], row["prompt_tokens"], row["completion_tokens"], row["status"])
                          for row in diagnostic["observations"]],
                         [("simulator", 382, 24, "pass"), ("evaluator", 281, 88, "pass")])
        self.assertEqual((diagnostic["result"], diagnostic["status"], diagnostic["target"]),
                         ("pass", "evaluated", "scripted"))
        for row in (outer, inner, inner["runtime"], scorer, diagnostic, setup):
            for key in ("quality_eligible", "confidential", "live_admission"):
                self.assertIs(row[key], False)
        for row in (setup, diagnostic):
            self.assertEqual(row["plan_sha256"], PLAN)
            self.assertEqual(row["pricing"], "not-measured")
        self.assertIs(setup["upstream_executed"], False)
        self.assertEqual(setup["wheels"], 74)
        self.assertEqual(outer["cleanup"], dict.fromkeys(("process_group", "producer"), "confirmed"))
        self.assertEqual(inner["cleanup"], dict.fromkeys(("container", "image", "build_completion"), "confirmed"))
        self.assertEqual((inner["runtime"]["stage"], inner["runtime"]["cleanup"], inner["runtime"]["acknowledged"]),
                         ("released", "confirmed", True))
        self.assertEqual(scorer["cleanup"], "confirmed")
        self.assertEqual((scorer["started_utc"], scorer["ended_utc"]),
                         ("2026-10-22T23:43:57Z", "2026-10-22T23:44:13Z"))
        for name in ("run.json", "pair/run.json", "pair/scorer/run.json"):
            record = document(name)
            self.assertEqual((record["git_sha"], record["result"], record["execution"]),
                             (PRODUCER, "pass", "software"))
            self.assertIs(record["hardware_validated"], False)
            self.assertEqual(record["host"]["gpus"], "none")
        parity = inner["runtime"]["progress"]["parity"]
        session.cpu.validate(parity)
        self.assertEqual([row["ready_ms"] for row in parity["models"]], [1490, 1195])
        for model in parity["models"]:
            self.assertEqual([row["local"]["count"] for row in model["cases"]], [20, 28, 160, 198])


if __name__ == "__main__":
    unittest.main()
