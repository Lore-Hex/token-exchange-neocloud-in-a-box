# SPDX-License-Identifier: Apache-2.0
"""Original metadata joins and real pipe ordering; no Docker or model run."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

import test_tau_cpu_controller as control_tests
import test_tau_cpu_probe as fixtures
import test_tau_trial as trial

control, session = control_tests.control, control_tests.session


def setup(directory, *, failed=False):
    held = control_tests.ready(directory)
    diagnostic = fixtures.passing()
    if failed:
        diagnostic.update(result="fail", status="truncated")
        diagnostic["observations"][-1]["status"] = "not-completed"
    plan = {"image": trial.runner.runtime.TARGET["image"], "platform": "linux/amd64",
            "selection": diagnostic["selection"]}
    spec = {"sha": diagnostic["plan_sha256"], "plan": plan,
            "toolkit": {"git_sha": "a" * 40, "files": {"scripts/tau_trial.py": "b" * 64}},
            "runner": trial.runner}
    scorer = {"schema": "nib.tau-cpu-probe-scorer/v1", "toolkit": spec["toolkit"],
              "plan_sha256": spec["sha"], "cpu_ready_sha256": held["ready_sha256"],
              "image": plan["image"], "platform": plan["platform"],
              "started_utc": control.rec.now(), "ended_utc": control.rec.now(),
              "result": diagnostic["result"], "stage": "completed", "cleanup": "confirmed",
              "diagnostic": diagnostic, "quality_eligible": False, "confidential": False, "live_admission": False}
    notice = {"schema": "nib.tau-cpu-probe-held/v1", "ready_sha256": held["ready_sha256"],
              "plan_sha256": spec["sha"], "scorer_sha256": control.rec.digest(session.tau.encoded(scorer)),
              **{key: scorer[key] for key in ("result", "cleanup", "diagnostic")}}
    (directory / "scorer").mkdir(mode=0o700)
    control_tests.write(directory / "scorer", "output.log", session.tau.encoded(scorer))
    control_tests.write(directory / "scorer", "run.json", session.tau.encoded(trial.runner.probe_evidence_record(scorer)))
    return held, spec, notice, scorer


class ProbeControllerTests(unittest.TestCase):
    def test_second_frame_requires_exact_private_source_bound_originals(self):
        mutations = (None, lambda n: n.update(ready_sha256="e" * 64),
                     lambda n: n.update(plan_sha256="e" * 64), lambda n: n.update(scorer_sha256="e" * 64),
                     lambda n: n.update(result="fail"), lambda n: n.update(cleanup="failed"),
                     lambda n: n.update(body="private-canary"))
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp).resolve()
            held, spec, notice, scorer = setup(directory)
            for mutation in mutations:
                value = copy.deepcopy(notice)
                if mutation: mutation(value)
                with self.subTest(mutation=mutation):
                    if mutation is None:
                        result = control.probe_receipts(session, directory, value, held, spec, time.monotonic() + 5)
                        self.assertEqual(result, scorer)
                        self.assertEqual(set(spec["receipts"]), {"run.json", "output.log"})
                    else:
                        with self.assertRaises((ValueError, session.llama.worker.Abort)):
                            control.probe_receipts(session, directory, value, held, spec, time.monotonic() + 5)
            for mutation in (lambda r: r.update(quality_eligible=True), lambda r: r.update(toolkit={}),
                             lambda r: r.update(platform="linux/arm64"), lambda r: r.update(stage="image"),
                             lambda r: r.update(diagnostic=None), lambda r: r.update(cleanup="failed"),
                             lambda r: r.update(response="private-canary")):
                value = copy.deepcopy(scorer)
                mutation(value)
                control_tests.write(directory / "scorer", "output.log", session.tau.encoded(value))
                altered = {**notice, "scorer_sha256": control.rec.digest(session.tau.encoded(value)),
                           **{key: value[key] for key in ("result", "cleanup", "diagnostic")}}
                with self.assertRaises((ValueError, session.llama.worker.Abort)):
                    control.probe_receipts(session, directory, altered, held, spec, time.monotonic() + 5)

    def test_controller_waits_for_probe_before_release_and_retains_failed_probe(self):
        for failed in (False, True):
            with self.subTest(failed=failed), tempfile.TemporaryDirectory() as temp:
                directory = Path(temp).resolve()
                held, spec, notice, scorer = setup(directory, failed=failed)
                ready = session.READY + session.probes.encoded(held) + b"\n"
                second = b"NIB-TAU-CPU-PROBE " + session.probes.encoded(notice) + b"\n"
                ack = session.DONE + session.probes.encoded({"ready_sha256": held["ready_sha256"], "released": True}) + b"\n"
                script = ("import sys,select;sys.stdout.buffer.write(" + repr(ready) + ");sys.stdout.flush();"
                    "assert not select.select([sys.stdin],[],[],0.05)[0];"
                    "sys.stdout.buffer.write(" + repr(second) + ");sys.stdout.flush();"
                    "assert sys.stdin.buffer.readline()==" + repr(ack) + ";assert sys.stdin.buffer.read()==b'';"
                    "sys.stderr.write('CPU session: " + ("fail" if failed else "pass") + "; no qualification claim.\\n');"
                    "sys.exit(" + str(int(failed)) + ")")
                current = control_tests.report()
                with mock.patch.object(session.identity, "source_receipt"):
                    observed = control.supervise([sys.executable, "-I", "-S", "-B", "-c", script], {},
                        control_tests.snapshot, session, directory, control_tests.fixtures.PRODUCER,
                        current, time.monotonic() + 3, probe=spec)
                self.assertEqual(observed, (held, control_tests.fixtures.IMAGE))
                self.assertEqual(current["probe"], notice)
                self.assertEqual(spec["exit_code"], int(failed))
                self.assertEqual(spec["scorer"], scorer)
                self.assertEqual(current["cleanup"]["process_group"], "confirmed")
                self.assertNotIn("private-canary", json.dumps(current))


if __name__ == "__main__":
    unittest.main()
