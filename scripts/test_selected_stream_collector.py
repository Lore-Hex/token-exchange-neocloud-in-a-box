# SPDX-License-Identifier: Apache-2.0
"""Synthetic loopback HTTP only; no serving-profile or hardware qualification."""

import json
import signal
import socket
import time
import unittest
from unittest import mock

import selected_combined_capacity as combined
import stream_capacity as capacity
import stream_transport as stream
import test_selected_combined_capacity as combined_fixture
import test_stream_capacity as capacity_fixture
import test_stream_transport as transport_fixture


class SelectedStreamTests(unittest.TestCase):
    def test_exact_opt_in_and_legacy_wire_identity(self):
        self.assertEqual(stream.SELECTED_MODEL, combined.service.MODEL)
        args = (transport_fixture.MESSAGES, 7, 2)
        legacy = stream.request_body(*args)
        explicit = stream.request_body(*args, selected_model=False)
        selected = stream.request_body(*args, model=stream.SELECTED_MODEL, selected_model=True)
        self.assertEqual(legacy, explicit)
        self.assertEqual(json.loads(selected), {**json.loads(legacy), "model": stream.SELECTED_MODEL})
        ep = capacity_fixture.endpoint()
        for name, flag in ((stream.SELECTED_MODEL, False), (stream.MODEL, True),
                           ("other/model", True), (stream.SELECTED_MODEL.upper(), True),
                           (stream.SELECTED_MODEL + "/extra", True), (stream.SELECTED_MODEL, 1),
                           (stream.SELECTED_MODEL, "true"), (None, True)):
            with self.subTest(model=name, flag=flag), mock.patch.object(socket, "socket") as connect:
                with self.assertRaises(ValueError): stream.request_body(*args, model=name, selected_model=flag)
                with self.assertRaises(ValueError): stream.OwnedStreamHTTP(ep, model=name, selected_model=flag)
                connect.assert_not_called()

    def test_actual_selected_sse_keeps_short_bounds_and_owned_peer_checks(self):
        raw = b"".join(transport_fixture.pieces()).replace(stream.MODEL.encode(), stream.SELECTED_MODEL.encode())
        ep = capacity_fixture.endpoint()
        with transport_fixture.server(raw) as seen, mock.patch.object(ep, "check") as checks:
            client = stream.OwnedStreamHTTP(ep, model=stream.SELECTED_MODEL, selected_model=True)
            result = transport_fixture.collect(client)
        self.assertEqual(result.status, "completed")
        self.assertEqual((result.prompt_tokens, result.completion_tokens), (7, 2))
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][0], "/v1/chat/completions")
        self.assertEqual(seen[0][2]["model"], stream.SELECTED_MODEL)
        self.assertNotIn("Authorization", seen[0][1])
        self.assertEqual(checks.call_count, 3)
        self.assertFalse(client.busy.locked())
        self.assertNotIn(transport_fixture.CANARY, repr(result) + repr(vars(client)))
        for prompt, output in ((4096, 1), (7, 513), (True, 2)):
            with self.assertRaises(ValueError):
                stream.request_body(transport_fixture.MESSAGES, prompt, output,
                                    model=stream.SELECTED_MODEL, selected_model=True)
        self.assertEqual((stream.CONTEXT, stream.MAX_OUTPUT, stream.REQUEST_SECONDS), (4096, 512, 60))

    def test_no_response_model_rewriting_or_identity_bypass(self):
        ep = capacity_fixture.endpoint()
        with transport_fixture.server(b"".join(transport_fixture.pieces())) as seen, mock.patch.object(ep, "check"):
            client = stream.OwnedStreamHTTP(ep, model=stream.SELECTED_MODEL, selected_model=True)
            self.assertEqual(transport_fixture.collect(client).status, "protocol-error")
        self.assertEqual(len(seen), 1)
        with transport_fixture.server(b"".join(transport_fixture.pieces())) as seen, \
                mock.patch.object(ep, "check", side_effect=ValueError("identity changed")):
            client = stream.OwnedStreamHTTP(ep, model=stream.SELECTED_MODEL, selected_model=True)
            self.assertEqual(transport_fixture.collect(client).status, "protocol-error")
        self.assertEqual(seen, [])
        self.assertFalse(client.busy.locked())


class CombinedCollectorTests(unittest.TestCase):
    def collect(self, *, seconds=3):
        ep = capacity_fixture.endpoint()
        with mock.patch.object(ep, "check") as checks:
            value = combined.collect(ep, time.monotonic() + seconds, execution_profile=combined.plan.PROFILE)
        self.assertEqual(checks.call_count, 3)
        self.assertIsNotNone(checks.call_args_list[1].args[1])
        return value

    def test_length_and_chunked_return_original_bytes_and_separate_policy(self):
        raw = combined_fixture.metrics()
        fixture = capacity_fixture.CapacityHTTPTests()
        with mock.patch.object(capacity_fixture, "metrics", return_value=raw):
            for mode in ("length", "chunked"):
                with self.subTest(mode=mode), fixture.server(mode) as seen:
                    original, observation = self.collect()
                self.assertEqual(original, raw)
                self.assertEqual(observation, combined_fixture.parse(raw))
                self.assertEqual(len(seen), 1)
                self.assertEqual(seen[0][0], "/metrics")
                self.assertEqual(seen[0][1]["Accept-Encoding"], "identity")
                self.assertNotIn("Authorization", seen[0][1])
                self.assertFalse(observation["runtime_qualified"] or observation["confidentiality_verified"])

    def test_http_bounds_and_deadline_unchanged_no_retry(self):
        fixture = capacity_fixture.CapacityHTTPTests()
        with mock.patch.object(capacity_fixture, "metrics", return_value=combined_fixture.metrics()):
            for mode in ("redirect", "duplicate", "large"):
                with self.subTest(mode=mode), fixture.server(mode) as seen, self.assertRaises(ValueError):
                    self.collect()
                self.assertEqual(len(seen), 1)
            with fixture.server("trickle"), self.assertRaises(stream.worker.Abort):
                self.collect(seconds=.15)
        self.assertEqual((capacity.LIMIT, capacity.SECONDS), (2 << 20, 5))

    def test_profile_and_endpoint_rejected_before_socket(self):
        ep = capacity_fixture.endpoint()
        with mock.patch.object(socket, "socket") as connect:
            for profile in (None, True, "long128k-graphs", "batch32-graphs"):
                with self.assertRaises(ValueError):
                    combined.collect(ep, time.monotonic() + 3, execution_profile=profile)
            for endpoint in (None, object(), stream.identity.Endpoint(80, {"process": ep.expected})):
                with self.assertRaises(ValueError):
                    combined.collect(endpoint, time.monotonic() + 3, execution_profile=combined.plan.PROFILE)
            for deadline in (None, True, "3", -1, float("nan"), float("inf"), 10**400):
                with self.subTest(deadline=deadline), self.assertRaises(ValueError):
                    combined.collect(ep, deadline, execution_profile=combined.plan.PROFILE)
            connect.assert_not_called()
        self.assertNotIn(combined.plan.PROFILE, combined.service.engine_entry.BOUND_PROFILES)

    def test_invalid_capacity_and_terminal_drift_cannot_pass(self):
        fixture = capacity_fixture.CapacityHTTPTests()
        for raw in (combined_fixture.metrics(262144, "64.0"), combined_fixture.metrics().replace(b'} 0\n', b'} 1\n')):
            with mock.patch.object(capacity_fixture, "metrics", return_value=raw), fixture.server("length"), \
                    self.assertRaises(ValueError):
                self.collect()
        ep = capacity_fixture.endpoint()
        with mock.patch.object(capacity_fixture, "metrics", return_value=combined_fixture.metrics()), \
                fixture.server("length"), mock.patch.object(ep, "check", side_effect=[None, None, ValueError("drift")]), \
                self.assertRaises(ValueError):
            combined.collect(ep, time.monotonic() + 3, execution_profile=combined.plan.PROFILE)

    def test_cancellation_restores_alarm_and_closes_socket(self):
        fixture = capacity_fixture.CapacityHTTPTests()
        ep = capacity_fixture.endpoint()
        previous = signal.getsignal(signal.SIGALRM)
        with mock.patch.object(capacity_fixture, "metrics", return_value=combined_fixture.metrics()), \
                fixture.server("length"), mock.patch.object(ep, "check", side_effect=[None, KeyboardInterrupt()]), \
                self.assertRaises(KeyboardInterrupt):
            combined.collect(ep, time.monotonic() + 3, execution_profile=combined.plan.PROFILE)
        self.assertIs(signal.getsignal(signal.SIGALRM), previous)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))
        # A second real connection succeeds after cancellation and cleanup.
        with mock.patch.object(capacity_fixture, "metrics", return_value=combined_fixture.metrics()), fixture.server("length"):
            raw, _ = self.collect()
        self.assertEqual(raw, combined_fixture.metrics())


if __name__ == "__main__":
    unittest.main()
