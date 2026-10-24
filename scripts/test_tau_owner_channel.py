# SPDX-License-Identifier: Apache-2.0
"""Synthetic owner clients and private pipes; no inference or admission claims."""

import copy
import os
import threading
import time
import unittest
from unittest import mock

import tau_owner_channel as rpc
import test_tau_transport as fixtures


def admitted(cpu=False):
    roles = ("simulator", "evaluator") if cpu else ("candidate",)
    return {"ready_sha256": "a" * 64, "roles": {
        role: {"model": "synthetic-" + role, "context_tokens": 8192 if role == "candidate" else 4096}
        for role in roles}}


def body(role="candidate"):
    result = rpc.transport.request_body(fixtures.manifest(), "candidate", "agent", fixtures.arguments())[1]
    result["model"] = "synthetic-" + role
    return result


def completion():
    return {**fixtures.response(), "usage": {"prompt_tokens": 7, "completion_tokens": 1, "total_tokens": 8}}


class FakeChannel:
    def __init__(self, packets=()):
        self.packets, self.sent, self.closed = list(packets), [], False

    def receive(self, deadline):
        if self.closed or not self.packets:
            raise rpc.worker.Abort("protocol_error")
        return copy.deepcopy(self.packets.pop(0))

    def send(self, packet, deadline):
        if self.closed:
            raise rpc.worker.Abort("protocol_error")
        self.sent.append(copy.deepcopy(packet))

    def close(self):
        self.closed = True


class FakeClient:
    def __init__(self):
        self.endpoint = rpc.identity.Endpoint(8000, {"process": {}})
        self.calls, self.ids = [], None

    def count(self, payload, context, deadline):
        self.calls.append(("count", copy.deepcopy(payload), context, deadline))
        return 7

    def complete(self, payload, count, deadline):
        self.calls.append(("complete", copy.deepcopy(payload), count, deadline))
        return completion()


class OwnerTests(unittest.TestCase):
    def owner(self, cpu=False):
        channel, bindings = FakeChannel(), admitted(cpu)
        clients = {role: FakeClient() for role in bindings["roles"]}
        with mock.patch.object(rpc.identity, "GuardedClient", FakeClient), \
                mock.patch.object(rpc.identity, "GuardedLlamaClient", FakeClient):
            owner = rpc.Owner(channel, bindings, 100, clients, clock=lambda: 1)
        return owner, channel, clients

    def packet(self, owner, kind="count", role="candidate", sequence=1, deadline=90):
        return {**owner.envelope(kind, sequence), "role": role, "body": body(role), "deadline": deadline,
                **({"context_tokens": owner.binding["roles"][role]["context_tokens"]}
                   if kind == "count" else {"prompt_tokens": 7})}

    def assert_rejected(self, owner, channel, clients, packet):
        before = sum(len(client.calls) for client in clients.values())
        channel.packets.append(packet)
        with self.assertRaises(rpc.worker.Abort):
            owner.serve_once()
        self.assertTrue(channel.closed)
        self.assertIsNone(owner.pending)
        self.assertEqual(sum(len(client.calls) for client in clients.values()), before)
        with self.assertRaises(rpc.worker.Abort):
            owner.serve_once()

    def test_guarded_clients_only_and_fixed_role_sets(self):
        for roles in ({}, {"reference": {}}, {"candidate": {}, "simulator": {}}, {"evaluator": {}}):
            with self.subTest(roles=roles), self.assertRaises(rpc.worker.Abort):
                rpc.Owner(FakeChannel(), {**admitted(), "roles": roles}, 100, {}, clock=lambda: 1)
        with self.assertRaises(rpc.worker.Abort):
            rpc.Owner(FakeChannel(), admitted(), 100, {"candidate": FakeClient()}, clock=lambda: 1)

    def test_count_then_one_identical_completion(self):
        owner, channel, clients = self.owner()
        channel.packets = [self.packet(owner), self.packet(owner, "complete", sequence=2, deadline=80)]
        owner.serve_once()
        owner.serve_once()
        self.assertEqual([entry[0] for entry in clients["candidate"].calls], ["count", "complete"])
        self.assertEqual(channel.sent[0]["prompt_tokens"], 7)
        self.assertEqual(channel.sent[1]["response"], completion())
        self.assertIsNone(owner.pending)
        self.assert_rejected(owner, channel, clients, self.packet(owner, "complete", sequence=3))

    def test_every_binding_field_and_unexpected_field_fails_before_count(self):
        mutations = [lambda row: row.update(schema="wrong"), lambda row: row.update(binding_sha256="b" * 64),
                     lambda row: row.update(sequence=True), lambda row: row.update(sequence=2),
                     lambda row: row.update(role="reference"), lambda row: row.update(extra="not allowed"),
                     lambda row: row.update(context_tokens=4096), lambda row: row.update(context_tokens=True),
                     lambda row: row["body"].update(model="wrong"), lambda row: row["body"].update(stream=True),
                     lambda row: row["body"].update(n=True), lambda row: row["body"].update(reasoning_effort="high")]
        for mutation in mutations:
            owner, channel, clients = self.owner()
            packet = self.packet(owner)
            mutation(packet)
            self.assert_rejected(owner, channel, clients, packet)

    def test_recount_changed_body_role_count_or_extended_deadline_rejected(self):
        mutations = [lambda row: row.update(kind="count"), lambda row: row.update(role="evaluator"),
                     lambda row: row.update(prompt_tokens=8), lambda row: row.update(prompt_tokens=True),
                     lambda row: row.update(deadline=91), lambda row: row["body"].update(temperature=1)]
        for mutation in mutations:
            owner, channel, clients = self.owner()
            channel.packets = [self.packet(owner)]
            owner.serve_once()
            packet = self.packet(owner, "complete", sequence=2)
            mutation(packet)
            self.assert_rejected(owner, channel, clients, packet)

    def test_deadlines_are_finite_bounded_and_expire(self):
        for deadline in (True, float("nan"), float("inf"), 0, 101):
            owner, channel, clients = self.owner()
            self.assert_rejected(owner, channel, clients, self.packet(owner, deadline=deadline))
        for deadline in (True, float("nan"), float("inf"), 0, 1802):
            with self.assertRaises(rpc.worker.Abort):
                rpc.Coordinator(FakeChannel(), admitted(), deadline, clock=lambda: 1)

    def test_count_failure_or_context_exhaustion_never_generates(self):
        for value in (True, 0, -1, 8192, 8193):
            owner, channel, clients = self.owner()
            channel.packets = [self.packet(owner)]
            with mock.patch.object(clients["candidate"], "count", return_value=value), self.assertRaises(rpc.worker.Abort):
                owner.serve_once()
            self.assertTrue(channel.closed)
            self.assertFalse(clients["candidate"].calls)
            self.assertIsNone(owner.pending)

    def test_failed_completion_is_consumed_and_never_retried(self):
        owner, channel, clients = self.owner()
        channel.packets = [self.packet(owner), self.packet(owner, "complete", sequence=2)]
        owner.serve_once()
        with mock.patch.object(clients["candidate"], "complete", side_effect=RuntimeError(fixtures.CANARY)) as complete:
            with self.assertRaises(rpc.worker.Abort) as caught:
                owner.serve_once()
            with self.assertRaises(rpc.worker.Abort):
                owner.serve_once()
        self.assertEqual(complete.call_count, 1)
        self.assertNotIn(fixtures.CANARY, str(caught.exception))
        self.assertIsNone(owner.pending)

    def test_serial_cpu_roles_share_count_completion_state(self):
        owner, channel, clients = self.owner(cpu=True)
        for index, role in enumerate(("simulator", "evaluator")):
            channel.packets = [self.packet(owner, role=role, sequence=index * 2 + 1),
                self.packet(owner, "complete", role=role, sequence=index * 2 + 2)]
            owner.serve_once()
            owner.serve_once()
        self.assertTrue(all([row[0] for row in client.calls] == ["count", "complete"]
                            for client in clients.values()))

    def test_actual_private_pipe_roundtrip(self):
        r1, w1 = os.pipe()
        r2, w2 = os.pipe()
        owner_channel = rpc.transport.Channel(r1, w2)
        coordinator_channel = rpc.transport.Channel(r2, w1)
        deadline = time.monotonic() + 3
        client = FakeClient()
        with mock.patch.object(rpc.identity, "GuardedClient", FakeClient):
            owner = rpc.Owner(owner_channel, admitted(), deadline, {"candidate": client})
        coordinator = rpc.Coordinator(coordinator_channel, admitted(), deadline)
        failures = []

        def serve():
            try:
                owner.serve_once()
                owner.serve_once()
            except BaseException as error:
                failures.append(type(error).__name__)
            finally:
                owner.close()

        thread = threading.Thread(target=serve)
        thread.start()
        try:
            proxy = coordinator.client("candidate")
            count = proxy.count(body(), 8192, deadline)
            self.assertEqual(count, 7)
            self.assertEqual(proxy.complete(body(), count, deadline), completion())
        finally:
            coordinator.close()
            thread.join(4)
            owner.close()
        self.assertFalse(thread.is_alive())
        self.assertFalse(failures)


class CoordinatorTests(unittest.TestCase):
    def coordinator(self, cpu=False):
        channel = FakeChannel()
        return rpc.Coordinator(channel, admitted(cpu), 100, clock=lambda: 1), channel

    def counted(self, coordinator, role="candidate"):
        return {**coordinator.envelope("count-result", 1), "prompt_tokens": 7,
                "request_sha256": rpc.tau.digest(rpc.tau.encoded(body(role)))}

    def test_bad_count_reply_is_terminal_and_does_not_dispatch(self):
        mutations = [lambda row: row.update(binding_sha256="b" * 64), lambda row: row.update(sequence=True),
                     lambda row: row.update(sequence=2), lambda row: row.update(prompt_tokens=True),
                     lambda row: row.update(prompt_tokens=8192), lambda row: row.update(extra=1),
                     lambda row: row.update(request_sha256="b" * 64)]
        for mutation in mutations:
            coordinator, channel = self.coordinator()
            reply = self.counted(coordinator)
            mutation(reply)
            channel.packets = [reply]
            with self.assertRaises(rpc.worker.Abort):
                coordinator.client("candidate").count(body(), 8192, 90)
            with self.assertRaises(rpc.worker.Abort):
                coordinator.client("candidate").complete(body(), 7, 90)
            self.assertTrue(channel.closed)
            self.assertEqual(len(channel.sent), 1)

    def test_missing_count_changed_body_count_deadline_or_role_never_sent(self):
        for mutation in (lambda row: row.update(temperature=1), lambda row: row.update(model="other")):
            coordinator, channel = self.coordinator()
            channel.packets = [self.counted(coordinator)]
            client = coordinator.client("candidate")
            client.count(body(), 8192, 90)
            changed = body()
            mutation(changed)
            with self.assertRaises(rpc.worker.Abort):
                client.complete(changed, 7, 90)
            self.assertEqual(len(channel.sent), 1)
        for count, deadline in ((True, 90), (8, 90), (7, 91)):
            coordinator, channel = self.coordinator()
            channel.packets = [self.counted(coordinator)]
            client = coordinator.client("candidate")
            client.count(body(), 8192, 90)
            with self.assertRaises(rpc.worker.Abort):
                client.complete(body(), count, deadline)
            self.assertEqual(len(channel.sent), 1)
        coordinator, channel = self.coordinator()
        with self.assertRaises(rpc.worker.Abort):
            coordinator.client("candidate").complete(body(), 7, 90)
        self.assertFalse(channel.sent)

    def test_attempted_recount_or_cross_cpu_role_cannot_replace_pending(self):
        for next_role in ("simulator", "evaluator"):
            coordinator, channel = self.coordinator(cpu=True)
            channel.packets = [self.counted(coordinator, "simulator")]
            coordinator.client("simulator").count(body("simulator"), 4096, 90)
            with self.assertRaises(rpc.worker.Abort):
                coordinator.client(next_role).count(body(next_role), 4096, 90)
            self.assertEqual(len(channel.sent), 1)
            self.assertIsNone(coordinator.pending)

    def test_busy_channel_fails_closed_without_dispatch(self):
        coordinator, channel = self.coordinator()
        with coordinator.busy, self.assertRaises(rpc.worker.Abort):
            coordinator.client("candidate").count(body(), 8192, 90)
        self.assertTrue(channel.closed)
        self.assertFalse(channel.sent)

    def test_usage_must_still_match_count_across_the_owner_boundary(self):
        for count in (True, 6, None):
            coordinator, channel = self.coordinator()
            wrong = completion()
            wrong["usage"]["prompt_tokens"] = count
            channel.packets = [self.counted(coordinator), {**coordinator.envelope("complete-result", 2),
                "request_sha256": rpc.tau.digest(rpc.tau.encoded(body())), "response": wrong}]
            client = coordinator.client("candidate")
            client.count(body(), 8192, 90)
            with self.assertRaises(rpc.worker.Abort):
                client.complete(body(), 7, 90)
            self.assertTrue(channel.closed)
            self.assertIsNone(coordinator.pending)

    def test_existing_session_retains_one_budget_and_exact_usage(self):
        frozen = fixtures.manifest()
        frozen["framing"]["candidate"]["context_tokens"] = 8192
        deadline = time.monotonic() + 10
        owner_pipe = FakeChannel()
        coordinator = rpc.Coordinator(owner_pipe, admitted(), deadline)
        owner_pipe.packets = [self.counted(coordinator), {**coordinator.envelope("complete-result", 2),
            "request_sha256": rpc.tau.digest(rpc.tau.encoded(body())), "response": completion()}]
        scorer_pipe = FakeChannel([fixtures.packet()])
        with mock.patch.object(rpc.tau, "Budget", wraps=rpc.tau.Budget) as budget:
            session = rpc.identity.local.LocalVllmSession(scorer_pipe, frozen, rpc.tau.digest(rpc.tau.encoded(frozen)),
                "candidate", {"candidate": 8000, "reference": 8001, "simulator": 8002, "evaluator": 8003})
            session.endpoints["candidate"] = coordinator.client("candidate")
            session.bridge.budget.trial_deadline = deadline
            try:
                self.assertFalse(session.serve_once())
                self.assertEqual(budget.call_count, 1)
                self.assertEqual(session.bridge.budget.requests, 1)
                self.assertEqual(session.bridge.usage()["total_tokens"], 8)
                self.assertEqual(len(owner_pipe.sent), 2)
            finally:
                session.close()
                coordinator.close()

    def test_bindings_are_copied_and_readiness_is_not_a_success_claim(self):
        original = admitted()
        coordinator = rpc.Coordinator(FakeChannel(), original, 100, clock=lambda: 1)
        original["roles"]["candidate"]["model"] = "changed"
        self.assertEqual(coordinator.binding, admitted())
        self.assertFalse(hasattr(coordinator, "budget"))
        self.assertFalse(hasattr(coordinator, "confidential"))


if __name__ == "__main__":
    unittest.main()
