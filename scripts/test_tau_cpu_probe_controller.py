# SPDX-License-Identifier: Apache-2.0
"""Original metadata joins and real pipe ordering; no Docker or model run."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
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
    def test_relay_keeps_original_cpu_report_and_waits_for_second_bound_frame(self):
        f = control_tests.fixtures
        for failed in (False, True):
            value = f.packet()
            raw = session.probes.encoded(value)
            with mock.patch.object(session.identity, "source_receipt"):
                _, held = session.accept(raw, f.PRODUCER, f.IMAGE, time.monotonic() + 5)
            diagnostic = fixtures.passing()
            if failed:
                diagnostic.update(result="fail", status="truncated")
                diagnostic["observations"][-1]["status"] = "not-completed"
            final = f.result(held)
            final.update(schema="nib.tau-cpu-session/v3", result=diagnostic["result"], probe=diagnostic)
            scorer = {"result": diagnostic["result"], "cleanup": "confirmed", "diagnostic": diagnostic}
            script = ("import sys;sys.stdout.buffer.write(" + repr(session.READY + raw + b"\n") + ");sys.stdout.flush();"
                "assert sys.stdin.buffer.readline().startswith(" + repr(session.DONE) + ");"
                "sys.stdout.buffer.write(" + repr(session.probes.encoded(final)) + ");sys.stdout.flush();"
                "sys.exit(" + str(int(failed)) + ")")
            with tempfile.TemporaryDirectory() as temp, f.pipe() as (rd, wr), f.pipe() as (out_rd, out_wr):
                fd = os.open(temp, os.O_DIRECTORY)
                observed, failures = [], []
                def controller():
                    try:
                        with os.fdopen(os.dup(out_rd), "rb", buffering=0) as stream:
                            observed.append(stream.readline(4096))
                            observed.append(stream.readline(16384))
                        os.write(wr, session.DONE + session.probes.encoded({
                            "ready_sha256": held["ready_sha256"], "released": True}) + b"\n")
                    except BaseException as error:
                        failures.append(error)
                thread = threading.Thread(target=controller, daemon=True)
                thread.start()
                spec = {"payload": Path("/private/payload"), "sha": diagnostic["plan_sha256"],
                        "output": Path(temp) / "scorer", "socket": Path("/private/docker.sock"), "record": None}
                runner = mock.Mock()
                runner.run.return_value = scorer
                try:
                    with mock.patch.object(session.identity, "source_receipt"), mock.patch.object(session.cpu, "module", return_value=runner):
                        actual = session.relay([sys.executable, "-I", "-S", "-B", "-c", script], {},
                            control_tests.snapshot, f.PRODUCER, f.IMAGE, fd, read_fd=rd, write_fd=out_wr, probe=spec)
                    self.assertEqual(actual, final)
                    self.assertEqual(spec["record"], scorer)
                    self.assertEqual(json.loads(observed[0][len(session.READY):]), held)
                    notice = json.loads(observed[1][len(b"NIB-TAU-CPU-PROBE "):])
                    self.assertEqual(notice["diagnostic"], diagnostic)
                    self.assertEqual(notice["scorer_sha256"], session.tau.digest(session.tau.encoded(scorer)))
                    self.assertEqual(runner.run.call_args.kwargs["support_probe"]["ready_sha256"], held["ready_sha256"])
                    for name in session.FILES:
                        self.assertEqual((Path(temp) / name).read_bytes(), value["receipts"][name].encode())
                finally:
                    os.close(fd)
                    thread.join(timeout=2)
                self.assertFalse(thread.is_alive())
                self.assertEqual(failures, [])

    def test_terminal_originals_join_failed_probe_without_turning_cleanup_into_pass(self):
        rec = control.rec
        for failed in (False, True):
            with self.subTest(failed=failed), tempfile.TemporaryDirectory() as temp:
                directory = Path(temp).resolve()
                held, spec, notice, scorer = setup(directory, failed=failed)
                spec.update(notice=notice, scorer=scorer)
                sources = {name: rec.digest((control_tests.ROOT / name).read_bytes()) for name in rec.SOURCES}
                record = {"schema": "nib.evidence/v1", "phase": "P2", "task": "T3", "git_sha": "a" * 40,
                    "result": scorer["result"], "execution": "software", "hardware_validated": False,
                    "host": rec.host("local"), "started_utc": rec.now(), "ended_utc": rec.now(),
                    "command": "python3 -I -S -B scripts/record-tau-cpu-parity.py --archive PRIVATE --model-06 PRIVATE --model-17 PRIVATE --cmake-wheel PRIVATE --output PRIVATE --probe-payload PRIVATE --probe-plan-sha256 " + spec["sha"],
                    "notes": "Fixed original tau CPU support probe with scripted target only. No full tau trial, target quality, live admission or confidential claim."}
                runtime = control_tests.fixtures.result(held)
                runtime.update(schema="nib.tau-cpu-session/v3", result=scorer["result"], probe=scorer["diagnostic"])
                pins = json.loads((control_tests.ROOT / "scripts/e2e/pins.json").read_bytes())
                report = {"schema": "nib.tau-cpu-probe-run/v1", "result": scorer["result"],
                    "stage": "runtime" if failed else "complete", "git_sha": "a" * 40,
                    "source_archive_sha256": "b" * 64, "context_sha256": "d" * 64,
                    "runtime": runtime, "sources": sources, "scorer": scorer,
                    "image_id": "sha256:" + control_tests.fixtures.IMAGE,
                    "base_images": {key: pins[key] for key in ("go_image", "python_image")},
                    "cleanup": dict.fromkeys(("container", "image", "build_completion"), "confirmed"),
                    "quality_eligible": False, "confidential": False, "live_admission": False}
                control_tests.write(directory, "run.json", session.tau.encoded(record))
                for mutation in (None, lambda r: r.update(result="pass" if failed else "fail"),
                                 lambda r: r["runtime"].update(acknowledged=False),
                                 lambda r: r["runtime"]["probe"].update(status="timeout"),
                                 lambda r: r["cleanup"].update(container="unknown"),
                                 lambda r: r.update(scorer={}), lambda r: r.update(quality_eligible=True)):
                    value = copy.deepcopy(report)
                    if mutation: mutation(value)
                    control_tests.write(directory, "output.log", session.tau.encoded(value))
                    args = (session, directory, held, control_tests.fixtures.IMAGE, "a" * 40, "b" * 64,
                            sources, time.monotonic() + 5)
                    if mutation is None:
                        hashes = control.final_receipts(*args, probe=spec)
                        self.assertEqual(hashes["output.log"], rec.digest(session.tau.encoded(report)))
                        self.assertEqual(report["result"], "fail" if failed else "pass")
                    else:
                        with self.assertRaises((ValueError, session.llama.worker.Abort)):
                            control.final_receipts(*args, probe=spec)

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
