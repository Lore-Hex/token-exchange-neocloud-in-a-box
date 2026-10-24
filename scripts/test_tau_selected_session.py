# SPDX-License-Identifier: Apache-2.0
"""Synthetic clients only; no live tau, process admission or quality claims."""

import copy
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
        self.response = None

    def send(self, packet, deadline):
        super().send(packet, deadline)
        operation = packet["kind"]
        reply = {key: packet[key] for key in ("schema", "binding_sha256", "sequence")}
        if operation == "finish":
            self.packets.append({**reply, "kind": "finish-result", "status": packet["status"],
                                 "completed_requests": (packet["sequence"] - 1) // 2})
            return
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
            if self.response is not None:
                reply["response"] = copy.deepcopy(self.response)
        self.packets.append(reply)


def capped_tool_response(tokens=256, finish="tool_calls"):
    # Synthetic valid tool call: JSON validity alone cannot prove completion.
    return {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
        {"id": "cap-call", "type": "function", "function": {"name": "lookup", "arguments": '{"key": "blue"}'}}]},
        "finish_reason": finish}],
        "usage": {"prompt_tokens": 7, "completion_tokens": tokens, "total_tokens": 7 + tokens}}


class ToolCapTests(unittest.TestCase):
    def bridge(self, response, mode="selected"):
        if mode == "selected":
            config, flags = policy.configuration(), {"selected_operational": True}
        elif mode == "probe":
            config, flags = policy.support.configuration(), {"support_probe": True}
        else:
            config, flags = fixtures.fixtures.manifest()["settings"], {}
        return worker.Bridge(tau, config, "candidate", mock.Mock(return_value=response), **flags)

    def complete(self, bridge, role="agent"):
        return bridge.complete(model=transport.ALIASES[role], messages=[{"role": "user", "content": "synthetic"}])

    def test_selected_candidate_cap_is_ambiguous_and_usage_stays_known(self):
        bridge = self.bridge(capped_tool_response())
        with self.assertRaises(worker.Abort) as caught:
            self.complete(bridge)
        self.assertEqual(caught.exception.status, "protocol_error")
        self.assertTrue(bridge.usage_known)
        self.assertEqual(bridge.usage()["total_tokens"], 263)
        self.assertEqual((bridge.budget.requests, bridge.budget.tokens), (1, 263))
        self.assertIsNone(bridge.budget.pending)
        with self.assertRaises(worker.Abort) as caught:
            self.complete(bridge, "evaluator")
        self.assertEqual(caught.exception.status, "protocol_error")
        bridge.transport.assert_called_once()

    def test_below_cap_cpu_role_support_probe_and_text_stop_are_unaffected(self):
        for role, mode, tokens, tools in (("agent", "selected", 255, True),
                                         ("simulator", "selected", 256, True),
                                         ("agent", "probe", 256, True),
                                         ("agent", "legacy", 50, True),
                                         ("agent", "selected", 256, False),
                                         ("evaluator", "selected", 256, False)):
            with self.subTest(role=role, mode=mode, tokens=tokens, tools=tools):
                response = capped_tool_response(tokens)
                if not tools:
                    response["choices"][0].update(finish_reason="stop", message={"role": "assistant",
                        "content": '{"results":[]}' if role == "evaluator" else "synthetic"})
                bridge = self.bridge(response, mode)
                self.assertEqual(self.complete(bridge, role), response)
                self.assertEqual(bridge.budget.tokens, 7 + tokens)
                self.assertIsNone(bridge.failed)

    def test_explicit_length_still_reports_truncated_with_known_usage(self):
        for role in ("agent", "simulator"):
            for tokens in (255, 256):
                with self.subTest(role=role, tokens=tokens):
                    bridge = self.bridge(capped_tool_response(tokens, "length"))
                    with self.assertRaises(worker.Abort) as caught:
                        self.complete(bridge, role)
                    self.assertEqual(caught.exception.status, "truncated")
                    self.assertTrue(bridge.usage_known)
                    self.assertEqual(bridge.usage()["total_tokens"], 7 + tokens)

    def test_invalid_usage_is_not_settled_by_cap_guard(self):
        for mutate in (lambda r: r["usage"].update(completion_tokens=257, total_tokens=264),
                       lambda r: r["usage"].update(completion_tokens=True, total_tokens=8),
                       lambda r: r["usage"].update(total_tokens=262)):
            response = capped_tool_response()
            mutate(response)
            bridge = self.bridge(response)
            with self.assertRaises(worker.Abort) as caught:
                self.complete(bridge)
            self.assertEqual(caught.exception.status, "protocol_error")
            self.assertIsNone(bridge.usage())
            self.assertEqual(bridge.budget.tokens, 8448)


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
            "status": "evaluated", "reward": 1.0, "assertions_sha256": tau.digest(tau.encoded(["assertion"])),
            "upstream_termination": {"reason": "user_stop", "tool_errors": 0}})
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

    def finish_packet(self, session, upstream, *, status="protocol_error"):
        return {"schema": transport.SCHEMA, "kind": "finished", "sequence": session.sequence + 1,
                "status": status, "reward": None, "assertions_sha256": tau.digest(tau.encoded(["assertion"])),
                "upstream_termination": upstream}

    def test_failed_upstream_terminal_preserves_known_usage(self):
        for upstream in (None, {"reason": "too_many_errors", "tool_errors": 1},
                         {"reason": "too_many_errors", "tool_errors": 16}):
            session, channel, _, _ = self.session()
            self.request(session, channel)
            channel.packets.append(self.finish_packet(session, upstream))
            self.assertTrue(session.serve_once())
            self.assertEqual(session.result["upstream_termination"], upstream)
            self.assertEqual(session.result["status"], "protocol_error")
            self.assertEqual(session.result["usage"]["total_tokens"], 8)
            self.assertIsNone(session.result["reward"])
            self.assertEqual(channel.sent[-1]["kind"], "accepted")

    def test_termination_tampering_is_not_acknowledged_or_retained(self):
        bad = ("PRIVATE_CANARY", {}, {"reason": "PRIVATE_CANARY", "tool_errors": 1},
               {"reason": "too_many_errors", "tool_errors": True},
               {"reason": "too_many_errors", "tool_errors": -1},
               {"reason": "too_many_errors", "tool_errors": 0},
               {"reason": "too_many_errors", "tool_errors": 17},
               {"reason": "too_many_errors", "tool_errors": 1.0},
               {"reason": "too_many_errors", "tool_errors": "PRIVATE_CANARY"},
               {"reason": "user_stop", "tool_errors": 1},
               {"reason": "too_many_errors", "tool_errors": 1, "exception": "PRIVATE_CANARY"})
        for upstream in bad:
            with self.subTest(upstream=upstream):
                session, channel, _, _ = self.session()
                self.request(session, channel)
                channel.packets.append(self.finish_packet(session, upstream))
                with self.assertRaises(worker.Abort):
                    session.serve_once()
                self.assertIsNone(session.result)
                self.assertEqual(session.bridge.usage()["total_tokens"], 8)
                self.assertTrue(channel.closed)
                self.assertFalse(any(packet["kind"] == "accepted" for packet in channel.sent))
                self.assertNotIn("PRIVATE_CANARY", repr(channel.sent))

    def test_termination_requires_field_and_cannot_invent_tool_errors_without_responses(self):
        for mutation in (lambda p: p.pop("upstream_termination"),
                         lambda p: p.update(extra="PRIVATE_CANARY"), lambda p: None):
            session, channel, _, _ = self.session()
            packet = self.finish_packet(session, {"reason": "too_many_errors", "tool_errors": 1})
            mutation(packet)
            channel.packets.append(packet)
            with self.assertRaises(worker.Abort):
                session.serve_once()
            self.assertIsNone(session.result)
            self.assertEqual(channel.sent, [])

    def test_failed_reason_cannot_promote_evaluated_reward(self):
        for upstream in (None, {"reason": "too_many_errors", "tool_errors": 1}):
            session, channel, _, _ = self.session()
            for role in ("simulator", "agent", "evaluator"):
                self.request(session, channel, role)
            packet = self.finish_packet(session, upstream, status="evaluated")
            packet["reward"] = 1.0
            channel.packets.append(packet)
            with self.assertRaises(worker.Abort):
                session.serve_once()
            self.assertIsNone(session.result)
            self.assertEqual(session.bridge.usage()["total_tokens"], 24)

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

    def test_cap_saturated_tool_response_never_reaches_scorer(self):
        session, channel, channels, coordinators = self.session()
        channels["candidate"].response = capped_tool_response()
        with self.assertRaises(worker.Abort) as caught:
            self.request(session, channel)
        self.assertEqual(caught.exception.status, "protocol_error")
        self.assertEqual(channel.sent, [])
        self.assertTrue(channel.closed)
        self.assertEqual(coordinators["candidate"].sequence, 2)
        self.assertIsNone(coordinators["candidate"].pending)
        self.assertEqual(session.bridge.usage()["total_tokens"], 263)
        self.assertIsNone(session.result)
        with self.assertRaises(worker.Abort):
            self.request(session, channel)
        self.assertEqual(len(channels["candidate"].sent), 2)

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
            "status": "evaluated", "reward": 1.0, "assertions_sha256": tau.digest(tau.encoded(["assertion"])),
            "upstream_termination": {"reason": "user_stop", "tool_errors": 0}})
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
