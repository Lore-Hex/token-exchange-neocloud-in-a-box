# SPDX-License-Identifier: Apache-2.0
"""Network-proof envelope checks only; no Docker or network mutation."""

import copy
import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest import mock


PATH = Path(__file__).with_name("probe-selected-network.py")
SPEC = importlib.util.spec_from_file_location("selected_network_probe", PATH)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class NetworkProbeTests(unittest.TestCase):
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

    def test_partial_creation_retains_failure_and_attempts_owned_cleanup(self):
        docker = mock.Mock(cleaning=False)
        docker.call.side_effect = ValueError("creation outcome unknown")
        docker.inspect.return_value = None
        cases = []
        with mock.patch.object(probe.socket, "socket") as socket_factory:
            socket_factory.return_value.__enter__.return_value.getsockname.return_value = ("127.0.0.1", 49123)
            with self.assertRaises(ValueError):
                probe.case(docker, True, cases)
        self.assertEqual(len(cases), 1)
        self.assertTrue(cases[0]["cleanup_confirmed"])
        self.assertFalse(cases[0]["listener_ready"])
        self.assertIsNone(cases[0]["host_reachable"])
        self.assertEqual(docker.inspect.call_count, 4)

    def test_cleanup_does_not_remove_an_unowned_resource(self):
        docker = mock.Mock(cleaning=False)
        docker.call.side_effect = ValueError("creation failed")
        docker.inspect.return_value = {"Name": "/unrelated", "Config": {"Labels": {}}}
        with mock.patch.object(probe.socket, "socket") as socket_factory:
            socket_factory.return_value.__enter__.return_value.getsockname.return_value = ("127.0.0.1", 49123)
            with self.assertRaises(ValueError):
                probe.case(docker, True, [])
        self.assertEqual(docker.call.call_count, 1)


if __name__ == "__main__":
    unittest.main()
