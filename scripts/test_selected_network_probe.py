# SPDX-License-Identifier: Apache-2.0
"""Network-proof envelope checks only; no Docker or network mutation."""

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest import mock

from test_evidence_history import git


PATH = Path(__file__).with_name("probe-selected-network.py")
SPEC = importlib.util.spec_from_file_location("selected_network_probe", PATH)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class NetworkProbeTests(unittest.TestCase):
    def test_original_observations_and_committed_producer_joins(self):
        root = PATH.parent.parent
        directory = root / "evidence/p2/2026-10-25-selected-network-probe"
        for name, expected in (
            ("preflight-failure.json", "a906e3e9dd9bb891d24567a5fe2dae26279e615086b311ecef7a3358a44d04ca"),
            ("observations.json", "41bd726bd95ca1a45016d3f891597e7bc13a7b5881b5a8f5ad956d4765287480"),
        ):
            raw = (directory / name).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), expected)
            row = json.loads(raw)
            self.assertEqual(row["engine_version"], "29.4.0")
            for field in ("gpu_used", "tls_verified", "egress_verified", "confidential", "deployment_eligible"):
                self.assertIs(row[field], False)
            self.assertEqual(set(row["source_sha256"]), {
                "scripts/probe-selected-network.py", "scripts/fixtures/selected_network_listener.py"})
            for source, sha in row["source_sha256"].items():
                self.assertEqual(hashlib.sha256(git(root, "show", row["git_sha"] + ":" + source)).hexdigest(), sha)
        self.assertEqual(row["result"], "recorded")
        self.assertEqual([case["host_reachable"] for case in row["cases"]], [True, False])
        self.assertEqual([case["operational_port_mapping"] for case in row["cases"]], [True, False])
        self.assertTrue(all(case["listener_ready"] and case["cleanup_confirmed"] for case in row["cases"]))

    def test_requested_mapping_does_not_prove_operational_mapping(self):
        requested = {"8443/tcp": [{"HostIp": "127.0.0.1", "HostPort": "49123"}]}
        row = {"HostConfig": {"PortBindings": requested}, "NetworkSettings": {"Ports": {}}}
        self.assertFalse(probe.publication(row, 49123))
        row["NetworkSettings"]["Ports"] = {"8443/tcp": None}
        self.assertFalse(probe.publication(row, 49123))
        row["NetworkSettings"]["Ports"] = copy.deepcopy(requested)
        self.assertTrue(probe.publication(row, 49123))

    def test_wrong_extra_or_public_mapping_is_rejected(self):
        for mapping in ({"8443/tcp": [{"HostIp": "0.0.0.0", "HostPort": "49123"}]},
                        {"8443/tcp": [{"HostIp": "127.0.0.1", "HostPort": "443"}]},
                        {"8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "49123"}]}):
            for target in ("HostConfig", "NetworkSettings"):
                row = {"HostConfig": {"PortBindings": {"8443/tcp": [
                    {"HostIp": "127.0.0.1", "HostPort": "49123"}]}}, "NetworkSettings": {"Ports": {}}}
                row[target]["PortBindings" if target == "HostConfig" else "Ports"] = mapping
                with self.subTest(target=target, mapping=mapping), self.assertRaises(ValueError):
                    probe.publication(row, 49123)

    def test_network_contact_is_fixed_loopback_and_synthetic(self):
        stream = mock.MagicMock()
        stream.__enter__.return_value = stream
        stream.recv.return_value = b"NIB-PROBE-OK\n"
        with mock.patch.object(probe.socket, "create_connection", return_value=stream) as connect:
            self.assertTrue(probe.probe(49123))
        connect.assert_called_once_with(("127.0.0.1", 49123), 1)
        stream.sendall.assert_called_once_with(b"NIB-PROBE\n")
        for data in (b"", b"not the controlled listener", b"NIB-PROBE-OK"):
            stream.recv.return_value = data
            with mock.patch.object(probe.socket, "create_connection", return_value=stream):
                self.assertFalse(probe.probe(49123))

    def test_remote_daemon_endpoints_rejected(self):
        for endpoint in ("tcp://127.0.0.1:2375", "ssh://example.invalid", "unix://relative", "unix:///a\n"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                probe.Docker(endpoint, Path("/nonexistent"))

    def test_cleanup_has_fixed_reserve_not_a_renewed_lifetime(self):
        with mock.patch.object(probe.time, "monotonic", return_value=100):
            docker = probe.Docker("unix:///socket", Path("/nonexistent"))
        docker.cleaning = True
        with mock.patch.object(probe.time, "monotonic", return_value=401), self.assertRaises(ValueError):
            docker.call("container", "ls")

    def test_failed_inspect_requires_successful_inventory_for_absence(self):
        docker = probe.Docker("unix:///socket", Path("/nonexistent"))
        missing = subprocess.CompletedProcess([], 1, b"", b"missing")
        with mock.patch.object(docker, "call", side_effect=[missing,
                subprocess.CompletedProcess([], 0, b"unrelated\n", b"")]):
            self.assertIsNone(docker.inspect("container", "owned"))
        with mock.patch.object(docker, "call", side_effect=[missing,
                subprocess.CompletedProcess([], 0, b"owned\n", b"")]), self.assertRaises(ValueError):
            docker.inspect("container", "owned")
        with mock.patch.object(docker, "call", side_effect=[missing, ValueError()]), self.assertRaises(ValueError):
            docker.inspect("network", "owned")

    def test_image_preflight_selects_exact_cached_amd64_variant(self):
        docker = probe.Docker("unix:///socket", Path("/nonexistent"))
        value = {"Os": "linux", "Architecture": "amd64"}
        with mock.patch.object(docker, "call", return_value=
                subprocess.CompletedProcess([], 0, json.dumps([value]).encode(), b"")) as call:
            self.assertEqual(docker.inspect("image", probe.IMAGE), value)
        call.assert_called_once_with("image", "inspect", "--platform", "linux/amd64", probe.IMAGE, check=False)
        with mock.patch.object(docker, "call", return_value=
                subprocess.CompletedProcess([], 1, b"", b"missing")) as call, self.assertRaises(ValueError):
            docker.inspect("image", probe.IMAGE)
        self.assertEqual(call.call_count, 1)

    def test_partial_creation_retains_failure_and_attempts_owned_cleanup(self):
        docker = mock.Mock(cleaning=False)
        docker.call.side_effect = ValueError("creation outcome unknown")
        docker.inspect.return_value = None
        cases = []
        with mock.patch.object(probe.socket, "socket") as socket_factory, mock.patch.object(probe.time, "sleep"):
            socket_factory.return_value.__enter__.return_value.getsockname.return_value = ("127.0.0.1", 49123)
            with self.assertRaises(ValueError):
                probe.case(docker, True, cases)
        self.assertEqual(len(cases), 1)
        self.assertFalse(cases[0]["cleanup_confirmed"])
        self.assertTrue(cases[0]["creation_outcome_unknown"])
        self.assertFalse(cases[0]["listener_ready"])
        self.assertIsNone(cases[0]["host_reachable"])
        self.assertEqual(docker.inspect.call_count, 10)

    def test_cleanup_does_not_remove_an_unowned_resource(self):
        docker = mock.Mock(cleaning=False)
        docker.call.side_effect = ValueError("creation failed")
        docker.inspect.return_value = {"Name": "/unrelated", "Config": {"Labels": {}}}
        with mock.patch.object(probe.socket, "socket") as socket_factory, mock.patch.object(probe.time, "sleep"):
            socket_factory.return_value.__enter__.return_value.getsockname.return_value = ("127.0.0.1", 49123)
            with self.assertRaises(ValueError):
                probe.case(docker, True, [])
        self.assertEqual(docker.call.call_count, 1)

    def test_sigterm_and_sigint_cancel_work_but_allow_bounded_cleanup(self):
        for number in (probe.signal.SIGTERM, probe.signal.SIGINT):
            previous = probe.signal.getsignal(number)
            with probe.cancellation() as cancelled:
                docker = probe.Docker("unix:///socket", Path("/nonexistent"), cancelled)
                os.kill(os.getpid(), number)
                self.assertTrue(cancelled["cancelled"])
                with self.assertRaises(ValueError):
                    docker.call("container", "create")
                docker.cleaning = True
                with mock.patch.object(probe.subprocess, "run", return_value=
                        subprocess.CompletedProcess([], 0, b"", b"")) as run:
                    docker.call("container", "ls")
                run.assert_called_once()
            self.assertEqual(probe.signal.getsignal(number), previous)

    def test_fragmented_host_response_and_listener_frames(self):
        stream = mock.MagicMock()
        stream.__enter__.return_value = stream
        stream.recv.side_effect = [b"N", b"IB-PRO", b"BE-OK\n"]
        with mock.patch.object(probe.socket, "create_connection", return_value=stream):
            self.assertTrue(probe.probe(49123))
        spec = importlib.util.spec_from_file_location("network_listener",
            PATH.parent / "fixtures/selected_network_listener.py")
        listener = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(listener)
        for expected in (b"NIB-PROBE\n", b"NIB-PROBE-OK\n"):
            stream.recv.side_effect = [expected[:1], expected[1:4], expected[4:]]
            self.assertTrue(listener.receive(stream, expected, probe.time.monotonic() + 1))
        stream.recv.side_effect = [b"N", b""]
        self.assertFalse(listener.receive(stream, b"NIB-PROBE\n", probe.time.monotonic() + 1))

    def test_slow_fragments_do_not_extend_absolute_deadline(self):
        stream = mock.MagicMock()
        stream.__enter__.return_value = stream
        stream.recv.return_value = b"N"
        with mock.patch.object(probe.socket, "create_connection", return_value=stream), \
                mock.patch.object(probe.time, "monotonic", side_effect=[100, 100.5, 101.1]):
            self.assertFalse(probe.probe(49123))


if __name__ == "__main__":
    unittest.main()
