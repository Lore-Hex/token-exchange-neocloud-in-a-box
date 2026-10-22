# SPDX-License-Identifier: Apache-2.0
"""Real local pipe/fork tests with synthetic replies, never a model benchmark."""

import contextlib
import json
import os
import threading
import time
import unittest
from unittest import mock

import tau_pipe_trial as trial
import test_tau_transport as fixtures

transport, worker = trial.transport, trial.worker


class TrialTests(unittest.TestCase):
    def setUp(self):
        parent_read, child_write = os.pipe()
        child_read, parent_write = os.pipe()
        self.parent_channel = transport.Channel(parent_read, parent_write)
        self.child_channel = transport.Channel(child_read, child_write)
        self.addCleanup(self.parent_channel.close)
        self.addCleanup(self.child_channel.close)
        self.manifest = fixtures.manifest()
        self.sha = transport.tau.digest(transport.tau.encoded(self.manifest))
        self.parent = transport.ParentSession(self.parent_channel, self.manifest, self.sha,
            "candidate", [fixtures.record(self.manifest), fixtures.record(self.manifest, "simulator")])
        self.child = transport.WorkerProxy(self.child_channel, time.monotonic() + 3)

    def exchange(self, operation):
        results = []
        def child():
            try:
                results.append(operation())
            except BaseException as exc:
                results.append(exc)
        thread = threading.Thread(target=child)
        thread.start()
        try:
            finished = self.parent.serve_once()
        finally:
            thread.join(4)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(results), 1)
        if isinstance(results[0], BaseException):
            raise results[0]
        return finished

    def complete(self):
        for category in ("agent", "simulator"):
            self.assertFalse(self.exchange(lambda: self.child.complete(
                model=transport.ALIASES[category], **fixtures.arguments()["kwargs"],
                messages=fixtures.arguments()["messages"])))

    def final_packet(self, **changes):
        packet = {"schema": transport.SCHEMA, "kind": "finished", "sequence": self.parent.sequence + 1,
                  "status": "evaluated", "reward": 0.0,
                  "assertions_sha256": transport.tau.digest(worker.encoded([]))}
        packet.update(changes)
        return packet

    def test_success_uses_only_parent_accounting_and_closes_trial(self):
        self.complete()
        self.assertTrue(self.exchange(lambda: self.child.finish("evaluated", 0.0)))
        value = self.parent.result
        self.assertEqual(value["requests"], 2)
        self.assertEqual(value["role_requests"], {"agent": 1, "simulator": 1, "evaluator": 0})
        self.assertEqual(value["usage"]["total_tokens"], 4)
        self.assertEqual(value["reward"], "0.0")
        self.assertFalse(value["quality_eligible"])
        self.assertFalse(value["confidential"])
        self.assertEqual(value["manifest_sha256"], self.sha)
        self.assertNotIn(fixtures.CANARY, json.dumps(value))
        self.assertIsNone(self.parent.bridge.budget.trial_deadline)
        self.assertFalse(hasattr(self.child, "budget"))
        self.assertTrue(self.child.finished)
        for operation in (lambda: self.child.finish("evaluated", 1.0),
                          lambda: self.child.complete(model=transport.ALIASES["agent"], messages=[]),
                          self.parent.serve_once):
            with self.assertRaises(worker.Abort):
                operation()
        self.assertEqual(self.parent.result, value)
        self.assertEqual(self.parent.bridge.budget.requests, 2)

    def test_worker_failure_cannot_report_reward_or_erase_known_usage(self):
        self.complete()
        self.assertTrue(self.exchange(lambda: self.child.finish("infrastructure_error", None)))
        self.assertEqual(self.parent.result["status"], "infrastructure_error")
        self.assertIsNone(self.parent.result["reward"])
        self.assertEqual(self.parent.result["usage"]["total_tokens"], 4)

    def test_success_requires_agent_user_and_required_evaluator(self):
        with self.assertRaises(worker.Abort):
            self.exchange(lambda: self.child.finish("evaluated", 1.0))
        self.assertIsNone(self.parent.result)

    def test_missing_evaluator_rejected_even_when_assertion_hash_matches(self):
        self.complete()
        self.parent.bridge.assertions = self.child.assertions = ["synthetic assertion"]
        with self.assertRaises(worker.Abort):
            self.exchange(lambda: self.child.finish("evaluated", 1.0))
        self.assertIsNone(self.parent.result)

    def test_assertion_disagreement_rejects_before_result_acceptance(self):
        self.complete()
        self.child.assertions = [fixtures.CANARY]
        with self.assertRaises(worker.Abort):
            self.exchange(lambda: self.child.finish("evaluated", 1.0))
        self.assertIsNone(self.parent.result)

    def test_valid_evaluator_uses_existing_assertion_validation(self):
        self.complete()
        self.parent.bridge.assertions = self.child.assertions = ["synthetic assertion"]
        result = fixtures.response(json.dumps({"results": [{"expectedOutcome": "synthetic assertion",
            "metExpectation": True, "reasoning": fixtures.CANARY}]}))
        self.parent.records.append(fixtures.record(self.manifest, "evaluator", result=result))
        self.exchange(lambda: self.child.complete(model=transport.ALIASES["evaluator"],
                      messages=fixtures.arguments()["messages"]))
        self.assertTrue(self.exchange(lambda: self.child.finish("evaluated", 1.0)))
        self.assertEqual(self.parent.result["role_requests"]["evaluator"], 1)
        self.assertNotIn(fixtures.CANARY, json.dumps(self.parent.result))

    def test_parent_rejects_forged_accounting_fields(self):
        self.complete()
        self.child_channel.send(self.final_packet(usage={"total_tokens": 0}), self.child.deadline)
        with self.assertRaises(worker.Abort):
            self.parent.serve_once()
        self.assertIsNone(self.parent.result)
        self.assertEqual(self.parent.bridge.budget.tokens, 4)

    def test_terminal_scalar_and_envelope_negative_controls(self):
        self.complete()
        # Validate without I/O, so each mutation starts with the same accounting.
        mutations = ({"sequence": True}, {"sequence": 1}, {"schema": "other"},
                     {"status": "success"}, {"reward": True}, {"reward": "1"},
                     {"reward": float("nan")}, {"reward": float("inf")}, {"reward": 1001.0},
                     {"status": "cancelled", "reward": 1.0}, {"assertions_sha256": "a" * 64})
        for changes in mutations:
            with self.subTest(changes=changes), self.assertRaises((worker.Abort, ValueError)):
                self.parent.accept_result(self.final_packet(**changes), self.child.deadline)
        self.assertEqual(self.parent.bridge.budget.requests, 2)
        self.assertIsNotNone(self.parent.bridge.budget.trial_deadline)

    def test_ack_mismatch_is_sticky_on_worker(self):
        def parent_reply():
            self.parent_channel.receive(self.child.deadline)
            self.parent_channel.send({"schema": transport.SCHEMA, "kind": "accepted", "sequence": 99},
                                     self.child.deadline)
        thread = threading.Thread(target=parent_reply)
        thread.start()
        try:
            with self.assertRaises(worker.Abort):
                self.child.finish("infrastructure_error", None)
        finally:
            thread.join(4)
        self.assertFalse(self.child.finished)
        self.assertEqual(self.child.failed, "protocol_error")

    def test_closed_worker_cannot_create_an_accepted_result(self):
        self.complete()
        self.child_channel.close()
        with self.assertRaises(worker.Abort):
            self.parent.serve_once()
        self.assertIsNone(self.parent.result)
        self.assertEqual(self.parent.bridge.usage()["total_tokens"], 4)

    def test_expired_terminal_does_not_get_a_new_deadline(self):
        self.complete()
        self.parent.bridge.budget.trial_deadline = time.monotonic() - 1
        with self.assertRaises(worker.Abort):
            self.parent.serve_once()
        self.assertIsNone(self.parent.result)

    def test_real_fork_runs_callback_through_single_parent_ledger(self):
        # Child owns the completion proxy, parent owns the Budget. No upstream
        # packages are imported by this synthetic subprocess integration test.
        def run(root, manifest, selection, proxy):
            for category in ("agent", "simulator"):
                proxy.complete(model=transport.ALIASES[category], messages=fixtures.arguments()["messages"])
            return 0.0
        pid = os.fork()
        if pid == 0:
            code = 1
            try:
                self.parent_channel.close()
                with mock.patch.object(transport.tau, "Budget", side_effect=AssertionError("second ledger")):
                    trial.execute(None, self.manifest, {"role": "candidate"}, self.child, run=run)
                code = 0
            except BaseException:
                pass
            finally:
                self.child_channel.close()
                os._exit(code)
        self.child_channel.close()
        try:
            self.assertFalse(self.parent.serve_once())
            self.assertFalse(self.parent.serve_once())
            self.assertTrue(self.parent.serve_once())
        finally:
            self.parent_channel.close()
            _, status = os.waitpid(pid, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 0)
        self.assertEqual(self.parent.result["status"], "evaluated")
        self.assertEqual(self.parent.bridge.budget.requests, 2)

    def test_worker_callback_failures_only_send_fixed_terminal_status(self):
        for exc, expected in ((RuntimeError(fixtures.CANARY), "infrastructure_error"),
                              (KeyboardInterrupt(), "cancelled"),
                              (worker.Abort("truncated"), "truncated")):
            with self.subTest(expected=expected):
                proxy = transport.WorkerProxy(self.child_channel, time.monotonic() + 2)
                with mock.patch.object(proxy, "finish") as finish:
                    trial.execute(None, self.manifest, {}, proxy, run=mock.Mock(side_effect=exc))
                finish.assert_called_once_with(expected, None)

    def test_parent_late_deadline_failure_never_publishes_evaluated_result(self):
        self.complete()
        @contextlib.contextmanager
        def expires_on_exit(*args):
            yield
            raise worker.Abort("timeout")
        with mock.patch.object(worker, "alarm", expires_on_exit), self.assertRaises(worker.Abort) as caught:
            self.exchange(lambda: self.child.finish("evaluated", 1.0))
        self.assertEqual(caught.exception.status, "timeout")
        self.assertIsNone(self.parent.result)
        self.assertEqual(self.parent.bridge.failed, "timeout")
        self.assertEqual(self.parent.bridge.usage()["total_tokens"], 4)

    def test_worker_late_deadline_failure_drops_reward(self):
        @contextlib.contextmanager
        def expires_on_exit(*args):
            yield
            raise worker.Abort("timeout")
        with mock.patch.object(worker, "alarm", expires_on_exit), mock.patch.object(self.child, "finish") as finish:
            trial.execute(None, self.manifest, {}, self.child, run=lambda *args: 1.0)
        finish.assert_called_once_with("timeout", None)


if __name__ == "__main__":
    unittest.main()
