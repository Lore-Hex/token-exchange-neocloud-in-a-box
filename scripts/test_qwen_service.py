# SPDX-License-Identifier: Apache-2.0
import importlib.util
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location("qwen_service", Path(__file__).parent / "pilot/serve.py")
service = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(service)


class ServiceTests(unittest.TestCase):
    def test_shutdown_waits_and_reports_forced_or_failed_exit(self):
        child = Mock(returncode=0)
        with patch.object(service.pilot, "stop", return_value=True) as stop:
            service.stop_serving(child)
            stop.assert_called_once_with(child, timeout=140)
        with patch.object(service.pilot, "stop", return_value=False):
            with self.assertRaisesRegex(service.pilot.PilotError, "service-shutdown"):
                service.stop_serving(child)
        child.returncode = 1
        with patch.object(service.pilot, "stop", return_value=True):
            with self.assertRaisesRegex(service.pilot.PilotError, "service-shutdown"):
                service.stop_serving(child)

    def test_private_inputs_snapshot_and_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "key"
            source.write_bytes(b"x" * 32)
            source.chmod(0o600)
            target = root / "copy"
            self.assertEqual(service.private_input(source, target, secret=True, limit=64), b"x" * 32)
            source.write_bytes(b"y" * 32)
            self.assertEqual(target.read_bytes(), b"x" * 32)
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            source.chmod(0o644)
            with self.assertRaises(service.pilot.PilotError):
                service.private_input(source, root / "bad", secret=True, limit=64)
            self.assertFalse((root / "bad").exists())

    def test_special_empty_oversized_and_link_inputs_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input"
            for value in (b"", b"x" * 65):
                source.write_bytes(value)
                with self.assertRaises(service.pilot.PilotError):
                    service.private_input(source, root / "copy", secret=False, limit=64)
            (root / "link").symlink_to(source)
            with self.assertRaises(OSError):
                service.private_input(root / "link", root / "copy", secret=False, limit=64)
            os.mkfifo(root / "fifo")
            with self.assertRaises(service.pilot.PilotError):
                service.private_input(root / "fifo", root / "copy", secret=False, limit=64)

    def test_auth_requires_one_nontrivial_key(self):
        for key in (b"x" * 32, b"aB9_-" * 20 + b"\n", b"x" * 256):
            service.validate_api_key(key)
        for key in (b"", b"short", b"x" * 257, b" " + b"x" * 32,
                    b"x" * 32 + b"\r\n", b"x" * 32 + b",another-key", b"x" * 32 + b"\n\n"):
            with self.assertRaises(service.pilot.PilotError):
                service.validate_api_key(key)

    def test_public_mode_changes_only_tls_front_door(self):
        front = service.pilot.shim_command("trcs", 8443, 8000, "cert", "key", host="0.0.0.0")
        self.assertIn("0.0.0.0:8443", front)
        self.assertIn("http://127.0.0.1:8000", front)
        self.assertEqual(front[front.index("--max-request-bytes") + 1], "65536")
        self.assertEqual(front[front.index("--tee") + 1], "none")
        engine = service.pilot.engine_command("llama", "weights", "model", 8000, "key")
        self.assertEqual(engine[engine.index("--host") + 1], "127.0.0.1")
        with self.assertRaises(service.pilot.PilotError):
            service.pilot.shim_command("trcs", 8443, 8000, "cert", "key", host="example.com")

    def test_child_failure_stops_supervisor(self):
        for index in (0, 1):
            children = [Mock(), Mock()]
            for child in children:
                child.poll.return_value = None
            children[index].poll.return_value = 0
            with self.assertRaisesRegex(service.pilot.PilotError, "service-exited"):
                service.supervise(children, threading.Event(), max_seconds=60, health_check=lambda: True)

    def test_health_failure_or_deadline_stops_supervisor(self):
        child = Mock()
        child.poll.return_value = None
        with self.assertRaisesRegex(service.pilot.PilotError, "service-unhealthy"):
            service.supervise([child], threading.Event(), max_seconds=60, health_check=lambda: False)
        with self.assertRaisesRegex(service.pilot.PilotError, "run-duration-exceeded"):
            service.supervise([child], threading.Event(), max_seconds=-1, health_check=lambda: True)
        with patch.object(service, "tls_health", side_effect=OSError("sensitive")):
            with self.assertRaisesRegex(service.pilot.PilotError, "service-unhealthy"):
                service.supervise([child], threading.Event(), max_seconds=60,
                                  health_check=lambda: service.tls_health(1, "example.com", None))

    def test_signal_during_readiness_does_not_start_public_listener(self):
        shutdown = threading.Event()
        shutdown.set()
        child = Mock()
        check = Mock()
        service.wait_ready(child, check, shutdown)
        check.assert_not_called()
        service.supervise([child], shutdown, max_seconds=60, health_check=check)
        child.poll.assert_not_called()

    def test_readiness_failure_is_bounded(self):
        child = Mock()
        child.poll.return_value = 1
        with self.assertRaisesRegex(service.pilot.PilotError, "service-exited"):
            service.wait_ready(child, lambda: False, threading.Event())
        child.poll.return_value = None
        with self.assertRaisesRegex(service.pilot.PilotError, "readiness-timeout"):
            service.wait_ready(child, lambda: False, threading.Event(), timeout=0.01)


if __name__ == "__main__":
    unittest.main()
