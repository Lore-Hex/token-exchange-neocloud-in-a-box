# SPDX-License-Identifier: Apache-2.0
"""Original 1.7B simulator diagnostic: transport/literals, not qualification."""
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

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/p2/2026-10-25-tau-cpu-identity-17b"
PRODUCER = "19ece8918ed4f89121bbe704bf5466559e3805a1"
PLAN = "48952e2805c3c5cbbb0ef4861ac4b1035b945d57502ee2a3b9096da591344a87"
ARCHIVE = "01021506956ba241296a9d300876befc894ddf62e258ed6ed5716b036cbb51e3"
HASHES = {
    "run.json": "bb6831aece16b15fca4f853ab16cbfb91d43d9c6a56cec41d7dab63bafbf1fd6",
    "output.log": "79b6a16ee615b9e0ac4167fd049643d0eac4c4d4c371e08aa0bf44d3d1c1c033",
    "setup.json": "7a86df34a8f6034389384280a2d0b1ce27e60487f52cfc4ed5aa77d5a38ed853",
    "pair/run.json": "c39eac7faa8137b339f763758435aab8e1c316017a8b6f338a12039236c32d64",
    "pair/output.log": "885e41dc5a1086d6d2f5e5ab75932a966d52fb780983e7953da9e9ddaed0d74d",
    "pair/simulator-runtime.json": "9480357fcd406b1e933b2a34e8067b054ccc5a81501bd8575c139267e53c412e",
    "pair/evaluator-runtime.json": "86ea3a54558050dc7afb056be87f427c62a030564b3a1195b886923a81045112",
    "pair/scorer/run.json": "4df8940145b9b922063731a8b0a878193666490a3d1aa88a819ac6204afb80d7",
    "pair/scorer/output.log": "a36ef9d17b65edd89f6cd72a7e104d78589976b7698084b00ac2970a77518eee",
}
HASHES.update({"pair/" + role + "-tokenizer.json": sha for role, sha in (
    ("simulator", "60f179bdd6dccdc4502cac6a7d7e244540077f3467d6659c747467518222ec76"),
    ("evaluator", "1876eaef26f004664a5e0282c2f810d49cf232e3e2d788f0d17f8f80512cf797"),
)})


def document(name):
    return session.llama.worker.decode((EVIDENCE / name).read_bytes())


class Identity17EvidenceTests(unittest.TestCase):
    def test_original_bytes_and_exact_producer_source_closures(self):
        git(ROOT, "merge-base", "--is-ancestor", PRODUCER, "HEAD")
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
        lock_raw = git(ROOT, "show", PRODUCER + ":bench/tau-runtime-lock.json")
        self.assertEqual(setup["source_archive_sha256"], session.tau.json_value(lock_raw)["archive_sha256"])
        self.assertEqual(setup["runtime_lock_sha256"], hashlib.sha256(lock_raw).hexdigest())
        self.assertEqual((setup["schema"], setup["git_sha"], setup["plan_sha256"], setup["wheels"]),
                         ("nib.tau-cpu-identity-17b-probe-setup/v1", PRODUCER, PLAN, 74))
        self.assertIs(setup["upstream_executed"], False)

    def test_original_readiness_scorer_cleanup_and_tamper_replay(self):
        control = previous.controller.control
        outer, inner, scorer = map(document, ("output.log", "pair/output.log", "pair/scorer/output.log"))
        runtime = document("pair/evaluator-runtime.json")
        spec = {"sha": PLAN, "plan": {key: scorer[key] for key in ("image", "platform")},
                "toolkit": scorer["toolkit"], "runner": previous.trial.runner,
                "notice": outer["probe"], "report_schema": probe.policy.IDENTITY_17_REPORT}
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
            # Only freshness is historical. Keep original process/model/source identities.
            with mock.patch.object(session.time, "time", return_value=observed):
                image = control.originals(session, private, outer["ready"], runtime["producer"], time.monotonic() + 5)
                self.assertEqual(image, runtime["serving"]["image_sha256"])
                changed = copy.deepcopy(outer["ready"])
                changed["receipts"]["evaluator-runtime.json"] = "0" * 64
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
                                 lambda r: r["sources"].update({"scripts/tau_support_probe.py": "0" * 64})):
                    changed = copy.deepcopy(inner)
                    mutation(changed)
                    (private / "output.log").write_bytes(session.tau.encoded(changed))
                    with self.assertRaises((ValueError, session.llama.worker.Abort)):
                        control.final_receipts(*args, time.monotonic() + 5, probe=spec)
            # Rehash mutated scorer envelopes so these controls reach semantic and
            # runtime-binding checks, not just the outer digest comparison.
            mutations = [lambda r: r.update(cleanup="failed"), lambda r: r.update(plan_sha256="0" * 64),
                lambda r: r["diagnostic"]["observations"][0]["identity"].update(content="private-canary"),
                lambda r: r["diagnostic"]["roles"].update(evaluator=1),
                lambda r: r["diagnostic"]["roles"].update(agent=1),
                lambda r: r["diagnostic"]["simulator_binding"].update(owner_role="simulator")]
            mutations += [lambda r, key=key: r["diagnostic"]["simulator_binding"].update({key: "0" * 64})
                          for key in ("runtime_sha256", "process_sha256", "model_files_sha256",
                                      "tokenizer_sha256", "framing_sha256")]
            for mutation in mutations:
                changed = copy.deepcopy(scorer)
                mutation(changed)
                (private / "scorer/output.log").write_bytes(session.tau.encoded(changed))
                (private / "scorer/run.json").write_bytes(session.tau.encoded(
                    spec["runner"].probe_evidence_record(changed)))
                notice = dict(outer["probe"], scorer_sha256=session.tau.digest(session.tau.encoded(changed)),
                              **{key: changed[key] for key in ("result", "cleanup", "diagnostic")})
                with self.assertRaises((ValueError, session.llama.worker.Abort)):
                    control.probe_receipts(session, private, notice, outer["ready"], spec, time.monotonic() + 5)

    def test_actual_17b_owner_binding_and_transport_only_observations(self):
        outer, inner, scorer, setup = map(document, ("output.log", "pair/output.log", "pair/scorer/output.log", "setup.json"))
        diagnostic = probe.validate(scorer["diagnostic"])
        self.assertEqual(diagnostic, outer["probe"]["diagnostic"])
        self.assertEqual(diagnostic, inner["runtime"]["probe"])
        runtime = document("pair/evaluator-runtime.json")
        self.assertEqual(diagnostic["simulator_binding"], probe.policy.runtime_binding(runtime))
        self.assertEqual((runtime["role"], runtime["model"], runtime["revision"]),
                         ("evaluator", "qwen/qwen3-1.7b", "90862c4b9d2787eaed51d12237eafdfe7c5f6077"))
        pins = session.tau.json_value(git(ROOT, "show", PRODUCER + ":scripts/tau_cpu_support_models.json"))
        self.assertEqual(runtime["gguf"], {key: pins["models"][runtime["model"]][key]
                                          for key in ("filename", "sha256", "bytes")})
        self.assertEqual(diagnostic["roles"], {"agent": 0, "simulator": 3, "evaluator": 0})
        self.assertEqual((diagnostic["result"], diagnostic["status"], diagnostic["target"], diagnostic["plan_sha256"]),
                         ("pass", "evaluated", "scripted", PLAN))
        rows = diagnostic["observations"]
        self.assertEqual([row["step"] for row in rows], list(identity.STEPS))
        self.assertEqual([(row["prompt_tokens"], row["completion_tokens"]) for row in rows],
                         [(382, 10), (411, 17), (450, 26)])
        self.assertEqual((sum(r["prompt_tokens"] for r in rows), sum(r["completion_tokens"] for r in rows)), (1243, 53))
        for index, row in enumerate(rows):
            self.assertEqual(row["identity"], {key: index == 2 and key != "email_like_literal" for key in identity.FIELDS})
            self.assertEqual((row["role"], row["status"]), ("simulator", "pass"))
        for row in (outer, inner, inner["runtime"], scorer, diagnostic, setup):
            for key in ("quality_eligible", "confidential", "live_admission"):
                self.assertIs(row[key], False)
        self.assertEqual(outer["cleanup"], dict.fromkeys(("process_group", "producer"), "confirmed"))
        self.assertEqual(inner["cleanup"], dict.fromkeys(("container", "image", "build_completion"), "confirmed"))
        self.assertEqual((inner["runtime"]["stage"], inner["runtime"]["cleanup"], inner["runtime"]["acknowledged"]),
                         ("released", "confirmed", True))
        self.assertEqual(scorer["cleanup"], "confirmed")
        self.assertEqual((scorer["started_utc"], scorer["ended_utc"]), ("2026-10-25T08:01:38Z", "2026-10-25T08:02:01Z"))
        for name in ("run.json", "pair/run.json", "pair/scorer/run.json"):
            row = document(name)
            self.assertEqual((row["git_sha"], row["result"], row["execution"], row["host"]["gpus"]),
                             (PRODUCER, "pass", "software", "none"))
            self.assertIs(row["hardware_validated"], False)


if __name__ == "__main__":
    unittest.main()
