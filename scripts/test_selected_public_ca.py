# SPDX-License-Identifier: Apache-2.0
"""Ephemeral test CAs and real in-memory TLS; no listener or external request."""

from contextlib import ExitStack, contextmanager, nullcontext, redirect_stdout
import hashlib
import io
import os
from pathlib import Path
import ssl
import subprocess
import tempfile
import unittest
from unittest import mock

import test_qwen_service as qwen
import test_selected_external_smoke as external
import test_selected_health_wait as startup

probe, waiter = external.probe, startup.waiter
CANARY = "PRIVATE_CA_CANARY"


def args(**changes):
    return external.args(**{"tls_mode": "system-roots", "certificate": None,
                            "certificate_sha256": None, **changes})


class ContractTests(unittest.TestCase):
    def test_closed_modes_reject_missing_pins_and_mixed_trust_before_io(self):
        probe.validate_target(external.args())
        probe.validate_target(args())
        for value in (args(tls_mode="unknown"), args(tls_mode=None), args(tls_mode="pinned"),
                      args(certificate="/private/" + CANARY), args(certificate_sha256="a" * 64),
                      external.args(certificate=None), external.args(certificate_sha256=None)):
            for module in (probe, waiter):
                with self.subTest(module=module.__name__, mode=value.tls_mode if hasattr(value, "tls_mode") else "default"), \
                        mock.patch.object(module, "source_identity") as source, \
                        mock.patch.object(probe.socket, "socket") as socket, \
                        mock.patch.object(probe.service.identity, "private_read") as read:
                    report = module.run(value)
                    self.assertEqual(report["result"], "fail")
                    self.assertEqual(report["failure"], "contract")
                    source.assert_not_called()
                    socket.assert_not_called()
                    read.assert_not_called()
                    self.assertNotIn(CANARY, probe.encoded(report).decode())

    def test_no_trust_store_environment_override_or_error_fallback(self):
        paths = ssl.get_default_verify_paths()
        names = {"SSL_CERT_FILE", "SSL_CERT_DIR", paths.openssl_cafile_env, paths.openssl_capath_env}
        for name in names:
            for value in ("", "/private/" + CANARY):
                with self.subTest(name=name, value=value), mock.patch.dict(os.environ, {name: value}), \
                        mock.patch.object(ssl.SSLContext, "load_default_certs") as load, \
                        mock.patch.object(probe.service.identity, "private_read") as read:
                    with self.assertRaises(probe.inference.Rejected):
                        probe.trust_input(args(), probe.time.monotonic() + 5)
                    load.assert_not_called()
                    read.assert_not_called()
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(ssl.SSLContext, "load_default_certs", side_effect=OSError(CANARY)) as load, \
                mock.patch.object(probe.service.identity, "private_read") as read:
            with self.assertRaises(OSError):
                probe.trust_input(args(), probe.time.monotonic() + 5)
            load.assert_called_once_with(ssl.Purpose.SERVER_AUTH)
            read.assert_not_called()

    def test_no_verification_or_pin_bypass_before_socket_creation(self):
        for mode in ("pinned", "system-roots"):
            value = external.args() if mode == "pinned" else args()
            for fault in ("hostname", "verification", "protocol", "keylog", "digest"):
                context = mock.Mock(check_hostname=True, verify_mode=ssl.CERT_REQUIRED,
                                    minimum_version=ssl.TLSVersion.TLSv1_2, keylog_filename=None)
                digest = b"a" * 32 if mode == "pinned" else None
                if fault == "hostname":
                    context.check_hostname = False
                elif fault == "verification":
                    context.verify_mode = ssl.CERT_NONE
                elif fault == "protocol":
                    context.minimum_version = ssl.TLSVersion.MINIMUM_SUPPORTED
                elif fault == "keylog":
                    context.keylog_filename = "/private/" + CANARY
                else:
                    digest = None if mode == "pinned" else b"a" * 32
                with self.subTest(mode=mode, fault=fault), mock.patch.object(probe.socket, "socket") as socket:
                    with self.assertRaises(probe.inference.Rejected):
                        with probe.pinned_tls(value, context, digest, probe.time.monotonic() + 5):
                            self.fail("invalid TLS policy yielded a connection")
                    socket.assert_not_called()

    def test_cli_requires_opt_in_and_never_accepts_ca_or_insecure_switches(self):
        base = ["--run", "--address", "8.8.8.8", "--port", "8443", "--hostname", "selected.test",
                "--deadline", external.args().deadline]
        for module in (probe, waiter):
            command = base + (["--api-key-file", "/private/key"] if module is probe else [])
            for flags, mode in ((["--certificate", "/private/cert", "--certificate-sha256", "a" * 64], "pinned"),
                                (["--tls-mode", "system-roots"], "system-roots")):
                with mock.patch.object(module, "run", return_value={"result": "pass"}) as run, redirect_stdout(io.StringIO()):
                    self.assertEqual(module.main(command + flags), 0)
                    self.assertEqual(run.call_args.args[0].tls_mode, mode)
            for flag in ("--insecure", "--ca-file", "--cafile", "--capath", "--no-check-hostname", "--tls-mod"):
                output = io.StringIO()
                with mock.patch.object(module, "run") as run, redirect_stdout(output):
                    self.assertEqual(module.main(command + [flag, "/private/" + CANARY]), 1)
                    run.assert_not_called()
                self.assertNotIn(CANARY, output.getvalue())

    def test_explicit_receipts_and_startup_target_binding_keep_pinned_v1(self):
        check = external.ExternalTests()
        for selected in (external.args(), args()):
            with check.fixture() as (_, read):
                report = probe.run(selected)
            self.assertEqual(report["result"], "pass")
            self.assertEqual(read.call_count, 1)
            self.assertEqual(report["max_authenticated_requests"], 1)
            self.assertEqual(report["retries"], 0)
            if probe.tls_mode(selected) == "pinned":
                self.assertEqual(report["schema"], "nib.selected-external-smoke/v1")
                self.assertNotIn("tls_mode", report)
            else:
                self.assertEqual(report["schema"], "nib.selected-external-smoke/v2")
                self.assertEqual(report["tls_mode"], "system-roots")
        check = startup.WaitTests()
        with check.fixture() as (clock, sockets, _, _):
            selected = clock.args(tls_mode="system-roots", certificate=None, certificate_sha256=None)
            report = waiter.run(selected)
            self.assertEqual(report["result"], "pass")
            self.assertEqual(report["schema"], "nib.selected-external-startup/v2")
            self.assertEqual(report["tls_mode"], "system-roots")
            self.assertEqual(report["target_sha256"], hashlib.sha256(probe.encoded({
                "address": selected.address, "port": selected.port, "hostname": selected.hostname,
                "tls_mode": "system-roots"})).hexdigest())
            self.assertEqual(len(sockets), 1)
            self.assertFalse(report["credential_accessed"])
            self.assertEqual(report["model_requests"], 0)

    def test_system_mode_retains_deadlines_cancellation_and_permanent_tls_stop(self):
        check = startup.WaitTests()
        for event, expected in ((ssl.SSLCertVerificationError(CANARY), "identity"),
                                 (KeyboardInterrupt(), "cancelled")):
            with check.fixture((event, startup.GOOD)) as (clock, _, connects, _):
                result = waiter.run(clock.args(tls_mode="system-roots", certificate=None, certificate_sha256=None))
                self.assertEqual(result["failure"], expected)
                self.assertEqual(len(connects), 1)
                self.assertFalse(clock.sleeps)
        with check.fixture((startup.DOWN,)) as (clock, _, connects, _):
            result = waiter.run(clock.args(seconds=4, tls_mode="system-roots", certificate=None, certificate_sha256=None))
            self.assertEqual(result["failure"], "deadline")
            self.assertEqual(sum(clock.sleeps), 4)
            self.assertTrue(all(deadline <= 1004 for _, deadline in connects))

    def test_authenticated_handshake_cannot_outlive_deadline_before_key_read(self):
        clock = [1000.0]
        denied = probe.encoded({"error": {"message": "invalid_api_key", "type": "invalid_api_key"}})
        replies = [startup.GOOD, external.response(401, denied), external.response(401, denied), external.response()]
        opened, closed = [], []
        raw = mock.MagicMock()
        raw.__enter__.return_value = raw
        class Connection(external.Socket):
            def __enter__(self):
                return self
            def __exit__(self, *_):
                closed.append(self)
        def wrap(*_, **kwargs):
            index = len(opened)
            connection = Connection(replies[index])
            opened.append(connection)
            if index == 3:
                clock[0] += 121
            return connection
        trust = mock.Mock(check_hostname=True, verify_mode=ssl.CERT_REQUIRED,
                          minimum_version=ssl.TLSVersion.TLSv1_2, keylog_filename=None)
        trust.wrap_socket.side_effect = wrap
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(probe, "source_identity", return_value=("b" * 40, {})))
            stack.enter_context(mock.patch.object(probe, "trust_input", return_value=(trust, None)))
            stack.enter_context(mock.patch.object(probe.time, "monotonic", side_effect=lambda: clock[0]))
            stack.enter_context(mock.patch.object(probe.service.identity.worker, "alarm", side_effect=lambda *_: nullcontext()))
            stack.enter_context(mock.patch.object(probe.socket, "socket", return_value=raw))
            read = stack.enter_context(mock.patch.object(probe.service.identity, "private_read"))
            report = probe.run(args())
            read.assert_not_called()
        self.assertEqual(report["result"], "fail")
        self.assertEqual(report["failure"], "deadline")
        self.assertEqual(len(opened), 4)
        self.assertEqual(opened, closed)
        self.assertFalse(report["tls_checks"]["stream"])
        self.assertFalse(report["attempts"]["stream"])
        self.assertIsNone(report["usage"])


class TLSChainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)

        def openssl(*argv):
            subprocess.run(["openssl", *argv], cwd=cls.root, check=True, timeout=10,
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        openssl("req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256",
                "-nodes", "-keyout", "root.key", "-out", "root.pem", "-days", "1",
                "-subj", "/CN=Synthetic Test Root", "-addext", "basicConstraints=critical,CA:TRUE",
                "-addext", "keyUsage=critical,keyCertSign,cRLSign")
        for name, issuer, ca in (("intermediate", "root", True),
                                 ("leaf", "intermediate", False), ("expired", "intermediate", False)):
            openssl("req", "-new", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256",
                    "-nodes", "-keyout", name + ".key", "-out", name + ".csr", "-subj", "/CN=" + name)
            extensions = ("basicConstraints=critical,CA:TRUE,pathlen:0\nkeyUsage=critical,keyCertSign,cRLSign\n"
                          if ca else "basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\n"
                          "extendedKeyUsage=serverAuth\nsubjectAltName=DNS:selected.test\n")
            (cls.root / "extensions").write_text(extensions + "subjectKeyIdentifier=hash\n"
                                               "authorityKeyIdentifier=keyid:always\n")
            if name == "expired":
                (cls.root / "index").write_text("")
                (cls.root / "serial").write_text("01\n")
                (cls.root / "ca.conf").write_text(
                    "[ca]\ndefault_ca=issuer\n[issuer]\ndatabase=index\nnew_certs_dir=.\n"
                    "serial=serial\ndefault_md=sha256\npolicy=subject\n[subject]\ncommonName=supplied\n"
                    "[leaf]\n" + (cls.root / "extensions").read_text())
                openssl("ca", "-batch", "-notext", "-config", "ca.conf", "-cert", "intermediate.pem",
                        "-keyfile", "intermediate.key", "-in", "expired.csr", "-out", "expired.pem",
                        "-startdate", "20000101000000Z", "-enddate", "20000102000000Z", "-extensions", "leaf")
            else:
                openssl("x509", "-req", "-in", name + ".csr", "-CA", issuer + ".pem", "-CAkey", issuer + ".key",
                        "-CAcreateserial", "-out", name + ".pem", "-days", "1", "-extfile", "extensions")
        cls.servers = {}
        for kind, name, chain in (("fullchain", "leaf", True), ("missing-intermediate", "leaf", False),
                                  ("expired", "expired", True)):
            pem = (cls.root / (name + ".pem")).read_bytes()
            if chain:
                pem += (cls.root / "intermediate.pem").read_bytes()
            path = cls.root / (kind + "-chain.pem")
            path.write_bytes(pem)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(path, cls.root / (name + ".key"))
            cls.servers[kind] = context

    @contextmanager
    def installed_roots(self, *, trusted=True):
        loaded = []
        def load(context, purpose):
            self.assertEqual(purpose, ssl.Purpose.SERVER_AUTH)
            self.assertEqual(context.cert_store_stats()["x509"], 0)
            loaded.append(context)
            if trusted:
                context.load_verify_locations(cafile=str(self.root / "root.pem"))
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(ssl.SSLContext, "load_default_certs", load):
            yield loaded

    def test_real_fullchain_trust_hostname_expiry_and_missing_issuer(self):
        for kind, hostname, trusted, success in (("fullchain", "selected.test", True, True),
                ("fullchain", "wrong.test", True, False), ("expired", "selected.test", True, False),
                ("missing-intermediate", "selected.test", True, False), ("fullchain", "selected.test", False, False)):
            with self.subTest(kind=kind, hostname=hostname, trusted=trusted), self.installed_roots(trusted=trusted) as loaded, \
                    mock.patch.object(probe.service.identity, "private_read") as read:
                context, digest = probe.trust_input(args(), probe.time.monotonic() + 5)
                self.assertEqual(loaded, [context])
                self.assertIsNone(digest)
                self.assertTrue(context.check_hostname)
                self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
                self.assertIsNone(context.keylog_filename)
                if success:
                    qwen.ServiceTests().handshake(context, self.servers[kind], hostname)
                else:
                    with self.assertRaises(ssl.SSLCertVerificationError):
                        qwen.ServiceTests().handshake(context, self.servers[kind], hostname)
                read.assert_not_called()

    def test_pinned_input_never_adds_system_roots_or_uses_keylog_environment(self):
        pem = (self.root / "root.pem").read_bytes()
        keylog = self.root / "forbidden-keylog"
        with mock.patch.object(probe.service.identity, "private_read", return_value=pem), \
                mock.patch.object(ssl.SSLContext, "load_default_certs") as load, \
                mock.patch.dict(os.environ, {"SSLKEYLOGFILE": str(keylog)}):
            context, digest = probe.trust_input(external.args(certificate_sha256=hashlib.sha256(pem).hexdigest()),
                                                probe.time.monotonic() + 5)
        load.assert_not_called()
        self.assertEqual(digest, hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem.decode())).digest())
        self.assertIsNone(context.keylog_filename)
        self.assertFalse(keylog.exists())

    def test_system_mode_ignores_keylog_environment_without_creating_a_file(self):
        keylog = self.root / "forbidden-system-keylog"
        with self.installed_roots(), mock.patch.dict(os.environ, {"SSLKEYLOGFILE": str(keylog)}):
            context, digest = probe.trust_input(args(), probe.time.monotonic() + 5)
            qwen.ServiceTests().handshake(context, self.servers["fullchain"], "selected.test")
        self.assertIsNone(digest)
        self.assertIsNone(context.keylog_filename)
        self.assertFalse(keylog.exists())

    def test_full_run_verifies_each_actual_chain_before_secret_read_and_no_retry(self):
        denied = probe.encoded({"error": {"message": "invalid_api_key", "type": "invalid_api_key"}})
        replies = [startup.GOOD, external.response(401, denied), external.response(401, denied), external.response()]
        cases = [(probe, failed) for failed in (None, 0, 1, 2, 3)] + [(waiter, None), (waiter, 0)]
        for module, failed in cases:
            client = module if module is probe else module.probe
            count = 4 if module is probe else 1
            for failure in (("expired", "hostname", "missing-intermediate", "untrusted") if failed is not None else ("none",)):
                verified, sockets, closed, attempts = [], [], [], []
                raw = mock.MagicMock()
                raw.__enter__.return_value = raw
                class Connection(external.Socket):
                    def __enter__(self):
                        return self
                    def __exit__(self, *_):
                        closed.append(self)
                def wrap(context, raw, *, server_hostname):
                    index = len(attempts)
                    attempts.append(index)
                    if index == failed and failure == "untrusted":
                        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                    server = self.servers[failure] if index == failed and failure in self.servers else self.servers["fullchain"]
                    hostname = "wrong.test" if index == failed and failure == "hostname" else server_hostname
                    qwen.ServiceTests().handshake(context, server, hostname)
                    verified.append(index)
                    sock = Connection(replies[index])
                    sockets.append(sock)
                    return sock
                def key(*_):
                    self.assertEqual(verified, [0, 1, 2, 3])
                    return external.KEY
                with self.subTest(module=module.__name__, failed=failed, failure=failure), self.installed_roots(), ExitStack() as stack:
                    stack.enter_context(mock.patch.object(module, "source_identity", return_value=("b" * 40, {})))
                    stack.enter_context(mock.patch.object(client.socket, "socket", return_value=raw))
                    stack.enter_context(mock.patch.object(ssl.SSLContext, "wrap_socket", wrap))
                    read = stack.enter_context(mock.patch.object(client.service.identity, "private_read", side_effect=key))
                    result = module.run(args())
                    self.assertEqual(result["result"], "pass" if failed is None else "fail", result)
                    self.assertEqual(read.call_count, int(module is probe and failed is None))
                self.assertEqual(len(attempts), count if failed is None else failed + 1)
                self.assertEqual(sockets, closed)
                self.assertEqual(len(sockets), count if failed is None else failed)
                if module is probe:
                    self.assertEqual(result["attempts"]["stream"], failed is None)
                else:
                    self.assertEqual(result["health_requests"], int(failed is None))
                    self.assertEqual(result["health_retries"], 0)
                    self.assertEqual(result["model_requests"], 0)
                    self.assertFalse(result["credential_accessed"])
                text = probe.encoded(result).decode()
                for private in (external.KEY.decode(), external.CANARY, "selected.test", "8.8.8.8"):
                    self.assertNotIn(private, text)


if __name__ == "__main__":
    unittest.main()
