# SPDX-License-Identifier: Apache-2.0
"""Synthetic clients only; no live tau, process admission or quality claims."""

import os
import threading
import time
import unittest
from unittest import mock

import tau_selected_session as selected
import test_tau_owner_channel as fixtures

policy, owners = selected.policy, selected.owners
transport, worker, tau = owners.transport, owners.worker, owners.tau


def manifest():
    config = policy.configuration()
    framing = {role: {"served_model": config["model_profile"]["served_model_name"] if role == "candidate"
                      else config["support"][role]["model"], "context_tokens": context,
                      "parallel_tool_calls": False, "reasoning_effort": "none",
                      "framing_sha256": "c" * 64, "tokenizer_sha256": tau.VOCABULARY_DIGEST}
               for role, context in policy.CONTEXTS.items()}
    return {"schema": policy.TRANSPORT, "input": config, "settings": policy.settings(config), "framing": framing,
            "owners": {name: {"ready_sha256": char * 64, "roles": {
                role: {"model": framing[role]["served_model"], "context_tokens": policy.CONTEXTS[role]}
                for role in roles}} for name, char, roles in (
                    ("candidate", "a", ("candidate",)), ("support", "b", ("simulator", "evaluator")))}}


class PolicyTests(unittest.TestCase):
    def test_fixed_checkpoint_task_roles_and_original_rejection(self):
        config = policy.configuration()
        self.assertEqual(policy.validate(config), config)
        self.assertEqual(config["model_profile"]["hf_revision"], "c19ca5ca4eaeb79d29fdf08aea8327da95b1116a")
        self.assertEqual(config["selection"], {"domain": "retail", "task_id": "2", "trial": 0,
                                               "seed": 42, "role": "candidate"})
        self.assertEqual(len({row["model"] for row in config["support"].values()}), 2)
        with self.assertRaises(ValueError):
            tau.settings(config)
        with self.assertRaises(ValueError):
            policy.support.validate(config)
        frozen = manifest()
        self.assertEqual(policy.transport_manifest(frozen, tau.digest(tau.encoded(frozen))), frozen)
        with self.assertRaises(worker.Abort):
            transport.smoke_manifest(frozen, tau.digest(tau.encoded(frozen)))

    def test_input_mutations_fail_even_with_recomputed_hash(self):
        mutations = [lambda row: row.update(schema=policy.support.SCHEMA),
                     lambda row: row.update(live_admission=True), lambda row: row.update(quality_eligible=True),
                     lambda row: row.update(confidential=True), lambda row: row.update(pricing="free"),
                     lambda row: row["selection"].update(seed=True), lambda row: row["selection"].update(task_id="3"),
                     lambda row: row["selection"].update(role="reference"),
                     lambda row: row["model_profile"].update(hf_revision=tau.WEIGHTS),
                     lambda row: row["support"].update(evaluator=row["support"]["simulator"]),
                     lambda row: row["contexts"].update(candidate=4096),
                     lambda row: row["generation"].update(max_tokens=512),
                     lambda row: row["limits"].update(requests=33), lambda row: row["limits"].update(retries=1),
                     lambda row: row.update(endpoint="https://not-allowed.example")]
        for mutate in mutations:
            row = policy.configuration()
            mutate(row)
            with self.subTest(row=row), self.assertRaises(ValueError):
                policy.validate(row, tau.digest(tau.encoded(row)))

    def test_transport_mutations_rejected(self):
        mutations = [lambda row: row["settings"]["limits"].update(turns=100),
                     lambda row: row["framing"]["candidate"].update(context_tokens=4096),
                     lambda row: row["framing"]["candidate"].update(tokenizer_sha256="d" * 64),
                     lambda row: row["framing"]["evaluator"].update(served_model="other"),
                     lambda row: row["framing"]["simulator"].update(parallel_tool_calls=True),
                     lambda row: row["owners"]["candidate"].update(ready_sha256="b" * 64),
                     lambda row: row["owners"]["support"]["roles"].pop("evaluator"),
                     lambda row: row["owners"]["candidate"]["roles"]["candidate"].update(context_tokens=True),
                     lambda row: row.update(url="https://not-allowed.example")]
        for mutate in mutations:
            row = manifest()
            mutate(row)
            with self.subTest(row=row), self.assertRaises((ValueError, worker.Abort)):
                policy.transport_manifest(row, tau.digest(tau.encoded(row)))

    def test_resource_budget_shared_sticky_and_bounded(self):
        now = [1]
        budget = policy.ResourceBudget(policy.configuration(), clock=lambda: now[0])
        budget.begin_trial()
        for i in range(32):
            role = ("candidate", "simulator", "evaluator")[i % 3]
            self.assertEqual(budget.reserve(role)["max_tokens"], 256)
            budget.settle({"prompt_tokens": 7, "completion_tokens": 1, "total_tokens": 8})
        self.assertEqual((budget.requests, budget.tokens, budget.spent), (32, 256, 0))
        with self.assertRaises(ValueError):
            budget.reserve("candidate")
        budget = policy.ResourceBudget(policy.configuration(), clock=lambda: now[0])
        budget.begin_trial()
        budget.reserve("candidate")
        budget.abandon()
        self.assertEqual(budget.tokens, 8448)
        self.assertTrue(budget.failed)
        with self.assertRaises(ValueError):
            budget.reserve("candidate")
        budget = policy.ResourceBudget(policy.configuration(), clock=lambda: now[0])
        budget.begin_trial()
        now[0] = 902
        with self.assertRaises(ValueError):
            budget.reserve("candidate")

    def test_mutually_exclusive_explicit_modes(self):
        for flags in ({"support_probe": True, "selected_operational": True},
                      {"selected_operational": 1}, {"support_probe": 1}):
            with self.assertRaises(ValueError):
                worker.Bridge(tau, policy.configuration(), "candidate", None, **flags)
        with self.assertRaises(ValueError):
            worker.Bridge(tau, policy.configuration(), "reference", None, selected_operational=True)


class OwnerReplies(fixtures.FakeChannel):
    """Counted response fixture, not a network endpoint or readiness proof."""
    def __init__(self, binding):
        super().__init__()
        self.binding = binding
        self.count = 7
        self.error = None
        self.reply_count = None
        self.finish_reason = "stop"

    def send(self, packet, deadline):
        super().send(packet, deadline)
        operation = packet["kind"]
        reply = {key: packet[key] for key in ("schema", "binding_sha256", "sequence")}
        reply.update(kind=operation + "-result", request_sha256=tau.digest(tau.encoded(packet["body"])))
        if self.error and operation == "complete":
            reply.update(kind="complete-error", status=self.error)
        elif operation == "count":
            reply["prompt_tokens"] = self.count
        else:
            result = fixtures.completion()
            if packet["role"] == "evaluator":
                result["choices"][0]["message"]["content"] = '{"results":[{"expectedOutcome":"assertion","metExpectation":true,"reasoning":"synthetic"}]}'
            result["choices"][0]["finish_reason"] = self.finish_reason
            result["usage"] = {"prompt_tokens": self.count if self.reply_count is None else self.reply_count,
                               "completion_tokens": 1,
                               "total_tokens": (self.count if self.reply_count is None else self.reply_count) + 1}
            reply["response"] = result
        self.packets.append(reply)


class SessionTests(unittest.TestCase):
    def session(self):
        frozen = manifest()
        channels = {name: OwnerReplies(binding) for name, binding in frozen["owners"].items()}
        coordinators = {name: owners.Coordinator(channel, frozen["owners"][name], time.monotonic() + 30)
                        for name, channel in channels.items()}
        channel = fixtures.FakeChannel()
        session = selected.Session(channel, frozen, tau.digest(tau.encoded(frozen)), coordinators["candidate"],
                                   coordinators["support"], assertions=["assertion"])
        self.addCleanup(session.close)
        return session, channel, channels, coordinators

    def request(self, session, channel, category="agent", history=None):
        arguments = fixtures.fixtures.arguments()
        if history is not None:
            arguments["messages"] = history
        channel.packets.append(fixtures.fixtures.packet(session.sequence + 1, category, arguments))
        return session.serve_once()

    def test_all_roles_and_new_history_share_one_budget(self):
        session, channel, channels, coordinators = self.session()
        history = [{"role": "user", "content": fixtures.fixtures.CANARY}]
        for category in ("simulator", "agent", "simulator", "agent", "evaluator"):
            self.assertFalse(self.request(session, channel, category, history))
            history = history + [{"role": "assistant", "content": "synthetic next turn"}]
        self.assertEqual(session.bridge.budget.requests, 5)
        self.assertEqual(session.bridge.budget.tokens, 40)
        self.assertEqual(session.bridge.counts, {"agent": 2, "simulator": 2, "evaluator": 1})
        for name, expected in (("candidate", 4), ("support", 6)):
            packets = channels[name].sent
            self.assertEqual(len(packets), expected)
            self.assertEqual([row["kind"] for row in packets], ["count", "complete"] * (expected // 2))
            for count, completion in zip(packets[::2], packets[1::2]):
                self.assertEqual(count["body"], completion["body"])
                self.assertLessEqual(completion["deadline"], count["deadline"])
            self.assertEqual(len({tau.digest(tau.encoded(p["body"])) for p in packets[::2]}), expected // 2)
        channel.packets.append({"schema": transport.SCHEMA, "kind": "finished", "sequence": 6,
            "status": "evaluated", "reward": 1.0, "assertions_sha256": tau.digest(tau.encoded(["assertion"]))})
        self.assertTrue(session.serve_once())
        self.assertFalse(session.result["quality_eligible"])
        self.assertFalse(session.result["live_admission"])
        self.assertFalse(session.result["confidential"])
        self.assertEqual(session.result["pricing"], "not-measured")
        self.assertIsNone(session.result["usage"]["cost_usd"])
        self.assertNotIn(fixtures.fixtures.CANARY, repr(session.result))
        session.close()
        self.assertTrue(all(owner.channel.closed for owner in coordinators.values()))

    def test_context_overflow_is_terminal_and_never_generates(self):
        for category, name, count in (("agent", "candidate", 8000), ("simulator", "support", 4000)):
            session, channel, channels, _ = self.session()
            channels[name].count = count
            with self.assertRaises(worker.Abort):
                self.request(session, channel, category)
            with self.assertRaises(worker.Abort):
                self.request(session, channel, category)
            self.assertEqual([p["kind"] for p in channels[name].sent], ["count"])
            self.assertEqual(session.bridge.budget.requests, 0)

    def test_owner_failure_retains_unknown_reservation_without_retry(self):
        session, channel, channels, _ = self.session()
        channels["candidate"].error = "infrastructure_error"
        with self.assertRaises(worker.Abort) as caught:
            self.request(session, channel)
        self.assertEqual(caught.exception.status, "infrastructure_error")
        self.assertIsNone(session.bridge.usage())
        self.assertEqual(session.bridge.budget.tokens, 8448)
        with self.assertRaises(worker.Abort):
            self.request(session, channel)
        self.assertEqual(len(channels["candidate"].sent), 2)

    def test_count_usage_mismatch_and_truncation_cannot_score(self):
        for attribute, value, status in (("reply_count", 8, "protocol_error"), ("finish_reason", "length", "truncated")):
            session, channel, channels, _ = self.session()
            setattr(channels["candidate"], attribute, value)
            with self.assertRaises(worker.Abort) as caught:
                self.request(session, channel)
            self.assertEqual(caught.exception.status, status)
            self.assertIsNone(session.result)

    def test_stale_changed_or_shared_owner_rejected(self):
        for mode in ("binding", "sequence", "failed", "pending", "shared"):
            session, channel, _, coordinators = self.session()
            candidate, support = coordinators["candidate"], coordinators["support"]
            if mode == "binding":
                candidate.binding["ready_sha256"] = "d" * 64
            elif mode == "sequence":
                candidate.sequence = 1
            elif mode == "failed":
                candidate.failed = "timeout"
            elif mode == "pending":
                candidate.pending = ("unconsumed",)
            else:
                support = candidate
            with self.assertRaises((ValueError, worker.Abort)):
                selected.Session(channel, session.manifest, session.manifest_sha, candidate, support,
                                 assertions=["assertion"])

    def test_missing_evaluator_request_cannot_be_reported_as_evaluated(self):
        session, channel, _, _ = self.session()
        self.request(session, channel, "simulator")
        self.request(session, channel)
        channel.packets.append({"schema": transport.SCHEMA, "kind": "finished", "sequence": 3,
            "status": "evaluated", "reward": 1.0, "assertions_sha256": tau.digest(tau.encoded(["assertion"]))})
        with self.assertRaises(worker.Abort):
            session.serve_once()
        self.assertIsNone(session.result)

    def test_real_owner_pipes_keep_three_roles_under_one_budget(self):
        frozen, deadline = manifest(), time.monotonic() + 5
        coordinators, owner_instances, threads, failures = {}, [], [], []
        def serve(owner, count):
            try:
                for _ in range(count):
                    owner.serve_once()
            except BaseException as exc:
                failures.append(type(exc).__name__)
            finally:
                owner.close()
        def reply(role):
            result = fixtures.completion()
            if role == "evaluator":
                result["choices"][0]["message"]["content"] = '{"results":[{"expectedOutcome":"assertion","metExpectation":true,"reasoning":"synthetic"}]}'
            return result
        try:
            for name, binding in frozen["owners"].items():
                r1, w1 = os.pipe()
                r2, w2 = os.pipe()
                owner_channel, caller_channel = transport.Channel(r1, w2), transport.Channel(r2, w1)
                clients = {role: fixtures.FakeClient() for role in binding["roles"]}
                for role, client in clients.items():
                    client.complete = mock.Mock(return_value=reply(role))
                with mock.patch.object(owners.identity, "GuardedClient", fixtures.FakeClient), \
                        mock.patch.object(owners.identity, "GuardedLlamaClient", fixtures.FakeClient):
                    owner = owners.Owner(owner_channel, binding, deadline, clients)
                owner_instances.append(owner)
                coordinators[name] = owners.Coordinator(caller_channel, binding, deadline)
                thread = threading.Thread(target=serve, args=(owner, 2 * len(clients)))
                thread.start()
                threads.append(thread)
            channel = fixtures.FakeChannel()
            session = selected.Session(channel, frozen, tau.digest(tau.encoded(frozen)),
                coordinators["candidate"], coordinators["support"], assertions=["assertion"])
            try:
                for category in ("simulator", "agent", "evaluator"):
                    self.request(session, channel, category)
                self.assertEqual(session.bridge.budget.requests, 3)
                self.assertEqual(session.bridge.budget.tokens, 24)
                self.assertEqual(session.bridge.counts, {"agent": 1, "simulator": 1, "evaluator": 1})
            finally:
                session.close()
        finally:
            for owner in coordinators.values():
                owner.close()
            for thread in threads:
                thread.join(6)
            for owner in owner_instances:
                owner.close()
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertFalse(failures)


if __name__ == "__main__":
    unittest.main()
