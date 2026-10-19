# SPDX-License-Identifier: Apache-2.0
import importlib.util
import datetime
import os
from pathlib import Path
import ssl
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location("qwen_service", Path(__file__).parent / "pilot/serve.py")
service = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(service)


class ServiceTests(unittest.TestCase):
    def test_public_retirement_is_required_canonical_and_bounded(self):
        now = datetime.datetime(2026, 10, 19, 12, tzinfo=datetime.timezone.utc).timestamp()
        with patch.object(service.time, "time", return_value=now), \
                patch.object(service.time, "monotonic", return_value=100):
            self.assertIsNone(service.retirement_deadline(None, public=False))
            for value in (None, "", "not-a-time", "2026-02-30T12:00:00Z", "2026-10-19T13:00:00+00:00",
                          "2026-10-19T13:00:00.1Z", "2026-10-19T12:00:00Z",
                          "2026-10-19T11:59:59Z", "2026-10-20T12:00:01Z"):
                with self.subTest(value=value), self.assertRaises(service.pilot.PilotError):
                    service.retirement_deadline(value, public=True)
            self.assertEqual(service.retirement_deadline("2026-10-20T12:00:00Z", public=True),
                             (now + 86400, 86500))
            self.assertEqual(service.retirement_deadline("2026-10-19T13:00:00Z", public=True),
                             (now + 3600, 3700))
        with patch.object(service.time, "time", return_value=now + 7200):
            with self.assertRaisesRegex(service.pilot.PilotError, "retirement-window"):
                service.retirement_deadline("2026-10-19T13:00:00Z", public=True)

    def test_retirement_drains_on_either_clock_without_extra_health_work(self):
        child, health = Mock(), Mock()
        for wall, monotonic in ((1000, 500), (999, 501), (1001, 499)):
            with self.subTest(wall=wall, monotonic=monotonic), \
                    patch.object(service.time, "time", return_value=wall), \
                    patch.object(service.time, "monotonic", return_value=monotonic):
                service.supervise([child], threading.Event(), max_seconds=60,
                                  health_check=health, retirement=(1000, 500))
        child.poll.assert_not_called()
        health.assert_not_called()

    def test_health_probe_crossing_retirement_is_normal_drain(self):
        child = Mock()
        child.poll.return_value = None
        for raises in (False, True):
            with self.subTest(raises=raises), patch.object(service.time, "time", return_value=999) as clock, \
                    patch.object(service.time, "monotonic", return_value=499):
                def check():
                    clock.return_value = 1000
                    if raises:
                        raise OSError("probe ended at cutoff")
                    return False
                service.supervise([child], threading.Event(), max_seconds=60,
                                  health_check=check, retirement=(1000, 500))

    def test_public_missing_retirement_fails_before_inputs_or_child_start(self):
        args = Mock(public=True, retirement_at=None)
        with patch.object(service.pilot, "prepared_inputs") as prepared, \
                patch.object(service.subprocess, "Popen") as spawn:
            with self.assertRaisesRegex(service.pilot.PilotError, "public-retirement-required"):
                service.serve(args, threading.Event())
        prepared.assert_not_called()
        spawn.assert_not_called()

    def test_public_cannot_advertise_beyond_process_lifetime(self):
        args = Mock(public=True, retirement_at="reviewed-cutoff", max_seconds=60)
        with patch.object(service, "retirement_deadline", return_value=(2000, 2000)), \
                patch.object(service.time, "monotonic", return_value=1000), \
                patch.object(service.pilot, "prepared_inputs") as prepared:
            with self.assertRaisesRegex(service.pilot.PilotError, "retirement-run-duration"):
                service.serve(args, threading.Event())
        prepared.assert_not_called()

    def test_public_fullchain_uses_system_root_and_verifies_hostname(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def openssl(*args):
                subprocess.run(["openssl", *args], cwd=root, check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            openssl("req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256",
                    "-nodes", "-keyout", "root.key", "-out", "root.pem", "-days", "1",
                    "-subj", "/CN=Test Root", "-addext", "basicConstraints=critical,CA:TRUE",
                    "-addext", "keyUsage=critical,keyCertSign,cRLSign")
            for name, issuer, ca in (("intermediate", "root", True), ("leaf", "intermediate", False)):
                openssl("req", "-new", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256",
                        "-nodes", "-keyout", name + ".key", "-out", name + ".csr",
                        "-subj", "/CN=" + name)
                extensions = ("basicConstraints=critical,CA:TRUE,pathlen:0\n"
                              "keyUsage=critical,keyCertSign,cRLSign\n" if ca else
                              "basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\n"
                              "extendedKeyUsage=serverAuth\nsubjectAltName=DNS:api.example.test\n")
                (root / "extensions").write_text(extensions + "subjectKeyIdentifier=hash\n"
                                                "authorityKeyIdentifier=keyid:always\n")
                openssl("x509", "-req", "-in", name + ".csr", "-CA", issuer + ".pem",
                        "-CAkey", issuer + ".key", "-CAcreateserial", "-out", name + ".pem",
                        "-days", "1", "-extfile", "extensions")
            fullchain = root / "fullchain.pem"
            fullchain.write_bytes((root / "leaf.pem").read_bytes() + (root / "intermediate.pem").read_bytes())
            server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            server.load_cert_chain(fullchain, root / "leaf.key")
            (root / "empty").mkdir()
            with patch.dict(os.environ, {"SSL_CERT_FILE": str(root / "root.pem"),
                                         "SSL_CERT_DIR": str(root / "empty")}):
                client = service.tls_client_context(fullchain)
            self.assertTrue(client.check_hostname)
            self.assertEqual(client.verify_mode, ssl.CERT_REQUIRED)
            self.assertIn(ssl.PEM_cert_to_DER_cert((root / "root.pem").read_text()),
                          client.get_ca_certs(binary_form=True))
            self.handshake(client, server, "api.example.test")
            with self.assertRaises(ssl.SSLCertVerificationError):
                self.handshake(client, server, "wrong.example.test")
            # Reproduce the pinned Python 3.12 image's fullchain-only failure,
            # including on newer Python releases that default to partial chains.
            fullchain_only = ssl.create_default_context(cafile=str(fullchain))
            fullchain_only.verify_flags &= ~getattr(ssl, "VERIFY_X509_PARTIAL_CHAIN", 0)
            with self.assertRaises(ssl.SSLCertVerificationError):
                self.handshake(fullchain_only, server, "api.example.test")

    def handshake(self, client, server, hostname):
        incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
        peer_in, peer_out = ssl.MemoryBIO(), ssl.MemoryBIO()
        connection = client.wrap_bio(incoming, outgoing, server_side=False, server_hostname=hostname)
        peer = server.wrap_bio(peer_in, peer_out, server_side=True)
        for _ in range(10):
            try:
                connection.do_handshake()
                return
            except ssl.SSLWantReadError:
                pass
            peer_in.write(outgoing.read())
            try:
                peer.do_handshake()
            except ssl.SSLWantReadError:
                pass
            incoming.write(peer_out.read())
        self.fail("TLS handshake did not complete")

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
