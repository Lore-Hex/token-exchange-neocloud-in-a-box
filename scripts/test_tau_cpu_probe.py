# SPDX-License-Identifier: Apache-2.0
"""Fixed-mode failure controls; no model execution or upstream import."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest import mock

import tau_cpu_probe as probe
import test_tau_cpu_admission as admission

ROOT = Path(__file__).resolve().parent.parent


def prepared():
    _, docs = admission.documents()
    manifest = probe.policy.configuration()
    plan = {"support_probe": True, "transport": "scripted-parent-pipe",
            "manifest_sha256": probe.tau.digest(probe.tau.encoded(manifest)),
            "selection": dict(probe.policy.SELECTION)}
    return probe.inputs(plan, manifest, ["Synthetic assertion"]), docs


def passing():
    selected, _ = prepared()
    return {"schema": "nib.tau-cpu-probe/v1", "result": "pass", "status": "evaluated",
            "plan_sha256": selected["plan_sha256"], "selection": selected["selection"],
            "upstream_revision": probe.tau.REVISION,
            "observations": [{"role": role, "request_sha256": "b" * 64, "prompt_tokens": 100,
                              "completion_tokens": 10, "status": "pass"} for role in ("simulator", "evaluator")],
            "roles": {"agent": 1, "simulator": 1, "evaluator": 1}, "target": "scripted", "pricing": "not-measured",
            "quality_eligible": False, "confidential": False, "live_admission": False}


CHILD = r'''
import os,sys,time
sys.path.insert(0,sys.argv[1])
import tau_transport as transport
channel=transport.Channel(os.dup(0),os.dup(1))
with open(os.devnull,'r+b',buffering=0) as sink:
    for fd in (0,1,2): os.dup2(sink.fileno(),fd)
proxy=transport.WorkerProxy(channel,time.monotonic()+4)
proxy.assertions=['Synthetic assertion']
try:
    for role in ('simulator','agent','evaluator'):
        proxy.complete(model=transport.ALIASES[role],messages=[{'role':'user','content':'private-probe-canary'}])
    proxy.finish('evaluated',0.0)
finally:
    channel.close()
'''


class SupportProbeTests(unittest.TestCase):
    def test_fixed_input_contract_rejects_other_tasks_models_and_larger_caps(self):
        original, _ = prepared()
        for mutate in (
            lambda row: row["selection"].update(task_id="3"),
            lambda row: row["selection"].update(role="reference"),
            lambda row: row["selection"].update(domain="airline"),
            lambda row: row.update(assertions=[]),
            lambda row: row["manifest"]["support"]["simulator"].update(model="unapproved"),
            lambda row: row["manifest"]["generation"].update(max_tokens=257),
            lambda row: row["selection"].update(trial=False),
            lambda row: row["manifest"].update(reference={}),
            lambda row: row["manifest"].update(input_usd_per_million="0"),
            lambda row: row.update(extra="private-probe-canary"),
        ):
            changed = copy.deepcopy(original)
            mutate(changed)
            with self.subTest(mutation=mutate), self.assertRaises((ValueError, probe.worker.Abort)):
                probe.prepared_input(changed)

    def test_real_completion_pipes_use_two_models_and_strict_evaluator_parser(self):
        for mode in ("pass", "truncated", "bad-json", "wrong-assertion"):
            selected, docs = prepared()
            runtimes = {role: docs[role + "-runtime.json"] for role in ("simulator", "evaluator")}
            endpoints = {role: mock.Mock(port=row["endpoint"]["port"], expected=row["process"])
                         for role, row in runtimes.items()}
            clients = []
            def client(port, model, endpoint):
                result = mock.Mock(ids=None)
                result.count.return_value = 100
                content = "Synthetic simulator reply"
                finish = "stop"
                if model == probe.cpu.MODEL_IDS[1]:
                    content = json.dumps({"results": [{"expectedOutcome": "wrong" if mode == "wrong-assertion" else
                        "Synthetic assertion", "metExpectation": False, "reasoning": "Synthetic parser control."}]})
                    if mode == "bad-json":
                        content = "not JSON"
                    if mode == "truncated":
                        finish = "length"
                result.complete.return_value = {"choices": [{"message": {"role": "assistant", "content": content},
                    "finish_reason": finish}], "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110}}
                clients.append((model, result))
                return result
            child = subprocess.Popen([sys.executable, "-I", "-S", "-B", "-c", CHILD, str(ROOT / "scripts")],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            try:
                with self.subTest(mode=mode), mock.patch.object(probe, "ProbeClient", side_effect=client):
                    report = probe.execute(selected, endpoints, runtimes, [], os.dup(child.stdout.fileno()),
                                           os.dup(child.stdin.fileno()), time.monotonic() + 3)
                self.assertEqual(report["result"], "pass" if mode == "pass" else "fail")
                self.assertEqual(report["status"], "evaluated" if mode == "pass" else
                                 "truncated" if mode == "truncated" else "protocol_error")
                self.assertEqual([model for model, _ in clients], list(probe.cpu.MODEL_IDS))
                self.assertEqual(report["roles"], {"agent": 1, "simulator": 1, "evaluator": 1})
                self.assertNotIn("private-probe-canary", json.dumps(report))
                for model, instance in clients:
                    instance.complete.assert_called_once()
                    body = instance.complete.call_args.args[0]
                    self.assertEqual(body["model"], model)
                    self.assertFalse(body["parallel_tool_calls"])
                    self.assertLessEqual(body["max_completion_tokens"], 256)
                    self.assertIsNone(instance.ids)
            finally:
                child.stdin.close()
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=2)
                child.stdout.close()

    def test_early_finished_preserves_original_failure_without_observations(self):
        for status in ("infrastructure_error", "protocol_error", "timeout", "cancelled"):
            session = mock.Mock(sequence=0, observations=[])
            session.serve_once.return_value = True
            session.result = {"status": status}
            session.bridge.counts = {"agent": 0, "simulator": 0, "evaluator": 0}
            prepared = {"plan_sha256": "a" * 64,
                        "selection": {"domain": "retail", "task_id": "2", "trial": 0, "seed": 7, "role": "candidate"}}
            with self.subTest(status=status), mock.patch.object(probe.transport, "Channel"), \
                    mock.patch.object(probe, "ProbeSession", return_value=session):
                report = probe.execute(prepared, {}, {}, [], 3, 4, time.monotonic() + 2)
            self.assertEqual(report["status"], status)
            self.assertEqual(report["result"], "fail")
            self.assertEqual(report["observations"], [])
            self.assertEqual(report["roles"], dict.fromkeys(("agent", "simulator", "evaluator"), 0))
            session.close.assert_called_once()

    def test_count_above_context_reports_context_failure_without_retaining_ids(self):
        for mode in ("over-context", "malformed-token", "malformed-response", "timeout", "output-reserve"):
            session = object.__new__(probe.ProbeSession)
            session.sequence, session.observations = 0, []
            client = mock.Mock(ids=None)
            def count(*args):
                self.assertEqual(args[1], 4096)
                if mode == "output-reserve":
                    return 4096
                client.ids = [1] * 4097
                client.token_shape = mode != "malformed-response"
                if mode == "malformed-token":
                    client.ids[-1] = True
                raise probe.worker.Abort("timeout" if mode == "timeout" else "protocol_error")
            client.count.side_effect = count
            session.clients = {"simulator": client}
            body = {"max_completion_tokens": 256}
            with self.subTest(mode=mode), self.assertRaises(probe.worker.Abort) as raised:
                session.prepare_record("simulator", body, time.monotonic() + 2)
            context = mode in ("over-context", "output-reserve")
            self.assertEqual(raised.exception.status, "budget_abort" if context else
                             "timeout" if mode == "timeout" else "protocol_error")
            self.assertEqual(session.observations[0]["status"], "context-exceeded" if context else "not-completed")
            self.assertEqual(session.observations[0]["prompt_tokens"], 4096 if mode == "output-reserve" else None)
            self.assertIsNone(client.ids)
            client.complete.assert_not_called()

    def test_tokenizer_extra_keys_stay_protocol_error_even_with_valid_oversized_ids(self):
        for extra in (False, True):
            client = probe.ProbeClient(24001, probe.cpu.MODEL_IDS[0], mock.Mock())
            session = object.__new__(probe.ProbeSession)
            session.sequence, session.observations = 0, []
            session.clients = {"simulator": client}
            token_response = {"tokens": [1] * 4097}
            if extra:
                token_response["unapproved"] = True
            body = probe.cpu.body(probe.cpu.MODEL_IDS[0], probe.cpu.probes.probes()[0])
            with self.subTest(extra=extra), mock.patch.object(probe.cpu.llama.LocalChatHTTP, "post",
                    side_effect=[{"prompt": "synthetic"}, token_response]), \
                    self.assertRaises(probe.worker.Abort) as raised:
                session.prepare_record("simulator", body, time.monotonic() + 2)
            self.assertEqual(raised.exception.status, "protocol_error" if extra else "budget_abort")
            self.assertEqual(session.observations[0]["status"], "not-completed" if extra else "context-exceeded")
            self.assertIsNone(client.ids)

    def test_failed_completion_does_not_become_pass_or_repair_truncated_json(self):
        for status in ("truncated", "protocol_error", "budget_abort"):
            observation = {"role": "simulator", "request_sha256": "b" * 64,
                           "prompt_tokens": 100, "completion_tokens": 256, "status": "not-completed"}
            session = mock.Mock(sequence=0, observations=[observation])
            session.serve_once.side_effect = probe.worker.Abort(status)
            session.bridge.counts = {"agent": 0, "simulator": 1, "evaluator": 0}
            prepared = {"plan_sha256": "a" * 64,
                        "selection": {"domain": "retail", "task_id": "2", "trial": 0, "seed": 7, "role": "candidate"}}
            with self.subTest(status=status), mock.patch.object(probe.transport, "Channel"), \
                    mock.patch.object(probe, "ProbeSession", return_value=session):
                result = probe.execute(prepared, {}, {}, [], 3, 4, time.monotonic() + 2)
            self.assertEqual((result["result"], result["status"]), ("fail", status))
            self.assertEqual(result["observations"], [observation])
            self.assertTrue(all(result[key] is False for key in ("quality_eligible", "confidential", "live_admission")))

    def test_scorer_cross_connection_keeps_model_text_out_of_metadata(self):
        for mode in ("pass", "failed-probe", "bad-metadata", "controller-eof"):
            row = passing()
            if mode == "failed-probe":
                row.update(result="fail", status="truncated")
                row["observations"][-1]["status"] = "not-completed"
            frame = probe.REPORT + probe.cpu.probes.encoded(row) + b"\n"
            if mode == "bad-metadata":
                frame = b"private-model-canary\n"
            cpu_script = ("import sys,time;line=sys.stdin.buffer.readline();assert line==b'private-prompt-canary\\n';"
                          "sys.stdout.buffer.write(b'private-model-canary\\n');sys.stdout.flush();"
                          "sys.stderr.buffer.write(" + repr(frame) + ");sys.stderr.flush();time.sleep(5)")
            scorer_script = ("import sys;sys.stdout.buffer.write(b'private-prompt-canary\\n');sys.stdout.flush();"
                             "assert sys.stdin.buffer.readline()==b'private-model-canary\\n'")
            process = subprocess.Popen([sys.executable, "-I", "-S", "-B", "-c", cpu_script],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            rd, wr = os.pipe()
            if mode == "controller-eof":
                os.close(wr)
                wr = None
            try:
                held = {"process": process, "ready_sha256": "a" * 64, "controller_fd": rd}
                with self.subTest(mode=mode):
                    if mode in ("bad-metadata", "controller-eof"):
                        with self.assertRaises(probe.worker.Abort):
                            probe.driver.attach_probe_scorer([sys.executable, "-I", "-S", "-B", "-c", scorer_script],
                                                             {}, held, time.monotonic() + 2)
                    else:
                        observed = probe.driver.attach_probe_scorer(
                            [sys.executable, "-I", "-S", "-B", "-c", scorer_script], {}, held, time.monotonic() + 2)
                        self.assertEqual(observed, row)
                        self.assertNotIn("canary", json.dumps(observed))
                self.assertIsNone(process.poll())  # Scorer cleanup must not own the CPU process.
            finally:
                os.close(rd)
                if wr is not None:
                    os.close(wr)
                process.kill()
                process.wait(timeout=2)
                for stream in (process.stdin, process.stdout, process.stderr):
                    stream.close()


if __name__ == "__main__":
    unittest.main()
