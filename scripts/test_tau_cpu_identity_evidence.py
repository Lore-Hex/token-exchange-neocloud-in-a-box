# SPDX-License-Identifier: Apache-2.0
"""Original three-call transport evidence, not simulator qualification."""
import copy
import datetime
import hashlib
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

import tau_cpu_identity as identity
import tau_cpu_probe as probe
import tau_cpu_session as session
import test_tau_cpu_probe_evidence as previous
from test_evidence_history import git

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "evidence/p2/2026-10-25-tau-cpu-identity-followup"
PRODUCER = "fa59772be64e61147058e448d28cc1f3eb34ae03"
PLAN = "5db8bd56b1c657d180703153238fcd14f2ddcca6d10b1e6c3b53ad250165c46a"
ARCHIVE = "8ed51589300fdf2e3b411f9b833e498da73c7d45150defcfcad98bffbc8b7a07"
HASHES = {
    "run.json": "0a67b237a8824d95046b045e03712901276373376ef5462b26d931f680ccadb8",
    "output.log": "23d40540dcab40fbf0c94f50638c5a1ee23eaa0010decd09bd41f726f6612b27",
    "pair/run.json": "d5aaec29d5c843ee2fcf6d2cf41645ba87699db49ae19c833c2bb221e3fb229e",
    "pair/output.log": "6eb6e44371f1eef63b697ecc54ea6074088d1ee46ffe21c11eb0843d5ae00044",
    "pair/simulator-runtime.json": "4fdf73791fe9a465c05c6fb96daa788089222dbcb63c621a2d719cf76024b062",
    "pair/evaluator-runtime.json": "5f00d514c54498bd9b7d6be085b0931b53457aebecef148ba9351d0155b80cdd",
    "setup.json": "367add20f1c3d2bba1dc79b2fbfa2af887664e34a151eff7826f745facf7e29c",
    "pair/scorer/output.log": "d8d341ab15b40ad620b3888c2bf6d25d2f4ea9c8bf9bc0adb4705dcc357c80c6",
    "pair/scorer/run.json": "8b79e127daa42b553f4f390d52080091ae7db0573689f0c2b681091e1fe50a68",
}
HASHES.update({"pair/" + role + "-tokenizer.json": sha for role, sha in (
    ("simulator", "d00605c53bcc2f82eaefcaa59b1ee1aaee43ce9698f0606441bbb3c5f7e74bd5"),
    ("evaluator", "092cb8e872d01648096dc113b225e4cdca377794593fe899822ea88b2e73a1c6"),
)})


def document(name):
    return session.llama.worker.decode((EVIDENCE / name).read_bytes())


class IdentityEvidenceTests(unittest.TestCase):
    def test_original_bytes_and_historical_source_closures(self):
        self.assertEqual({p.relative_to(EVIDENCE).as_posix() for p in EVIDENCE.rglob("*") if p.is_file()},
                         set(HASHES) | {"README.md"})
        for name, sha in HASHES.items():
            raw = (EVIDENCE / name).read_bytes()
            self.assertLessEqual(len(raw), 65536)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), sha)
            document(name)
        closure = set(previous.historical_sources(PRODUCER))
        self.assertEqual(len(closure), 28)
        controller = closure | {"scripts/record-tau-cpu-session.py", ".github/workflows/tau-cpu-session-native.yml",
            "scripts/pilot/export.py", "scripts/e2e/run.py", "scripts/prepare-tau-cpu-probe-native.py"}
        for name, expected in (("pair/output.log", closure), ("output.log", controller)):
            row = document(name)
            self.assertEqual((row["git_sha"], row["source_archive_sha256"]), (PRODUCER, ARCHIVE))
            self.assertEqual(set(row["sources"]), expected)
            for path, sha in row["sources"].items():
                self.assertEqual(hashlib.sha256(git(ROOT, "show", PRODUCER + ":" + path)).hexdigest(), sha)
        toolkit = document("pair/scorer/output.log")["toolkit"]
        self.assertEqual(toolkit["git_sha"], PRODUCER)
        self.assertEqual(len(toolkit["files"]), 22)
        for path, sha in toolkit["files"].items():
            self.assertEqual(hashlib.sha256(git(ROOT, "show", PRODUCER + ":" + path)).hexdigest(), sha)
        setup = document("setup.json")
        lock = session.tau.json_value(git(ROOT, "show", PRODUCER + ":bench/tau-runtime-lock.json"))
        self.assertEqual(setup["source_archive_sha256"], lock["archive_sha256"])
        self.assertEqual(setup["runtime_lock_sha256"], hashlib.sha256(
            git(ROOT, "show", PRODUCER + ":bench/tau-runtime-lock.json")).hexdigest())
        self.assertEqual((setup["schema"], setup["git_sha"], setup["plan_sha256"], setup["wheels"]),
                         ("nib.tau-cpu-identity-probe-setup/v1", PRODUCER, PLAN, 74))
        self.assertIs(setup["upstream_executed"], False)

    def test_original_readiness_scorer_and_cleanup_joins_with_tamper_controls(self):
        control = previous.controller.control
        outer, inner, scorer = map(document, ("output.log", "pair/output.log", "pair/scorer/output.log"))
        runtime = document("pair/simulator-runtime.json")
        spec = {"sha": PLAN, "plan": {key: scorer[key] for key in ("image", "platform")},
                "toolkit": scorer["toolkit"], "runner": previous.trial.runner,
                "notice": outer["probe"], "identity_followup": True}
        spec["plan"]["selection"] = probe.policy.SELECTION
        self.assertEqual(spec["plan"]["image"], previous.trial.runner.runtime.TARGET["image"])
        self.assertEqual(spec["plan"]["platform"], "linux/amd64")
        observed = datetime.datetime.fromisoformat(scorer["started_utc"]).timestamp()
        with tempfile.TemporaryDirectory() as temporary:
            private = Path(temporary).resolve()
            for name in HASHES:
                if name.startswith("pair/"):
                    path = private / name.removeprefix("pair/")
                    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    path.write_bytes((EVIDENCE / name).read_bytes())
                    path.chmod(0o600)
            # Only freshness is replayed at the original time. Runtime/process/
            # tokenizer identities and original source hashes are not substituted.
            with mock.patch.object(session.time, "time", return_value=observed):
                image = control.originals(session, private, outer["ready"], runtime["producer"], time.monotonic() + 5)
                self.assertEqual(image, runtime["serving"]["image_sha256"])
                changed = copy.deepcopy(outer["ready"])
                changed["receipts"]["simulator-runtime.json"] = "0" * 64
                self.assertRaises(ValueError, control.originals, session, private, changed,
                                  runtime["producer"], time.monotonic() + 5)
            args = (session, private, outer["ready"], image, PRODUCER, ARCHIVE, outer["sources"])
            self.assertEqual(control.probe_receipts(session, private, outer["probe"], outer["ready"],
                                                   spec, time.monotonic() + 5), scorer)
            with mock.patch.object(control.rec, "SOURCES", previous.historical_sources(PRODUCER)):
                self.assertEqual(control.final_receipts(*args, time.monotonic() + 5, probe=spec),
                                 outer["producer_receipts"])
                self.assertEqual(spec["receipts"], outer["scorer_receipts"])
                for mutation in (lambda r: r.update(quality_eligible=True),
                                 lambda r: r["runtime"].update(acknowledged=False),
                                 lambda r: r["cleanup"].update(container="unknown"),
                                 lambda r: r["sources"].update({"scripts/tau_cpu_identity.py": "0" * 64})):
                    changed = copy.deepcopy(inner)
                    mutation(changed)
                    (private / "output.log").write_bytes(session.tau.encoded(changed))
                    with self.assertRaises((ValueError, session.llama.worker.Abort)):
                        control.final_receipts(*args, time.monotonic() + 5, probe=spec)
            for mutation in (lambda r: r.update(cleanup="failed"), lambda r: r.update(plan_sha256="0" * 64),
                             lambda r: r["diagnostic"]["observations"][0]["identity"].update(content="private-canary")):
                changed = copy.deepcopy(scorer)
                mutation(changed)
                (private / "scorer/output.log").write_bytes(session.tau.encoded(changed))
                with self.assertRaises((ValueError, session.llama.worker.Abort)):
                    control.probe_receipts(session, private, outer["probe"], outer["ready"], spec, time.monotonic() + 5)

    def test_transport_pass_missing_identity_literals_and_no_qualification(self):
        outer, inner, scorer, setup = map(document, ("output.log", "pair/output.log", "pair/scorer/output.log", "setup.json"))
        diagnostic = probe.validate(scorer["diagnostic"])
        self.assertEqual(diagnostic, outer["probe"]["diagnostic"])
        self.assertEqual(diagnostic, inner["runtime"]["probe"])
        self.assertEqual(diagnostic["roles"], {"agent": 0, "simulator": 3, "evaluator": 0})
        self.assertEqual((diagnostic["result"], diagnostic["status"], diagnostic["target"], diagnostic["plan_sha256"]),
                         ("pass", "evaluated", "scripted", PLAN))
        self.assertEqual([row["step"] for row in diagnostic["observations"]], list(identity.STEPS))
        self.assertEqual([(row["prompt_tokens"], row["completion_tokens"]) for row in diagnostic["observations"]],
                         [(382, 24), (425, 11), (458, 18)])
        for observation in diagnostic["observations"]:
            self.assertEqual(observation["identity"], dict.fromkeys(identity.FIELDS, False))
            self.assertEqual(observation["status"], "pass")
        for row in (outer, inner, inner["runtime"], scorer, diagnostic, setup):
            for key in ("quality_eligible", "confidential", "live_admission"):
                self.assertIs(row[key], False)
        self.assertEqual(outer["cleanup"], dict.fromkeys(("process_group", "producer"), "confirmed"))
        self.assertEqual(inner["cleanup"], dict.fromkeys(("container", "image", "build_completion"), "confirmed"))
        self.assertEqual((inner["runtime"]["cleanup"], scorer["cleanup"]), ("confirmed", "confirmed"))
        self.assertEqual((scorer["started_utc"], scorer["ended_utc"]), ("2026-10-25T06:41:30Z", "2026-10-25T06:41:43Z"))
        for name in ("run.json", "pair/run.json", "pair/scorer/run.json"):
            row = document(name)
            self.assertEqual((row["git_sha"], row["result"], row["execution"], row["host"]["gpus"]),
                             (PRODUCER, "pass", "software", "none"))
            self.assertIs(row["hardware_validated"], False)


if __name__ == "__main__":
    unittest.main()
