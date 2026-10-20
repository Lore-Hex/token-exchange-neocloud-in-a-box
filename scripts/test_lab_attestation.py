# SPDX-License-Identifier: Apache-2.0
"""Software-only fakes; these tests do not attest GPUs or authenticate real JWTs."""

import base64
import copy
import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TEMP_ROOT = Path(tempfile.gettempdir()).resolve()
SPEC = importlib.util.spec_from_file_location("lab_attestation", ROOT / "host/attest-lab-gpus.py")
lab = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lab)


def claim(index, nonce):
    row = dict.fromkeys(lab.TRUE_CLAIMS, True)
    row.update(ueid=str(index + 1), eat_nonce=nonce, measres="success", dbgstat="disabled")
    row.update({"x-nvidia-device-type": "gpu", "x-nvidia-gpu-claims-version": "4.0",
                "x-nvidia-mismatch-measurement-records": None,
                "x-nvidia-mismatch-opaque-data-records": []})
    for name in lab.CHAINS:
        row["x-nvidia-gpu-" + name + "-cert-chain"] = {
            "x-nvidia-cert-status": "valid", "x-nvidia-cert-ocsp-status": "good",
            "x-nvidia-cert-expiration-date": "2099-01-01T00:00:00Z"}
    return row


def token(claims):
    def encoded(row):
        payload = base64.urlsafe_b64encode(json.dumps(row).encode()).rstrip(b"=").decode()
        return "synthetic-header." + payload + ".synthetic-signature"
    return [["JWT", "synthetic-overall"], {str(i): encoded(row) for i, row in enumerate(claims)}]


class FakeNvml:
    def __init__(self):
        self.rows = {str(i + 1): (b"PRIVATE_UUID_" + bytes([i]), b"PRIVATE_CERT", 1)
                     for i in range(8)}
        self.state = 0
        self.queries = 0
        self.transitions = 0

    def inventory(self):
        self.queries += 1
        return copy.deepcopy(self.rows)

    def ready(self):
        return self.state

    def close(self):
        pass

    def enable_ready(self):
        self.transitions += 1
        self.state = 1


class FakeCapture:
    def __init__(self, mutation=None, verified_mutation=None):
        self.calls = []
        self.mutation = mutation
        self.verified_mutation = verified_mutation
        self.token_path = None

    def __call__(self, args, **options):
        self.calls.append((args, options))
        nonce = args[args.index("--nonce") + 1]
        claims = [claim(i, nonce) for i in range(8)]
        if "attest" in args:
            if self.mutation:
                self.mutation(claims)
            return json.dumps({"result_code": 0, "claims": claims,
                               "detached_eat": token(claims)}).encode()
        self.token_path = Path(args[args.index("--token-file") + 1])
        assert self.token_path.stat().st_mode & 0o777 == 0o600
        if self.verified_mutation:
            self.verified_mutation(claims)
        return json.dumps({"result_code": 0, "claims": claims}).encode()


class AttestationTests(unittest.TestCase):
    def test_offline_installer_checks_archives_before_exact_dpkg(self):
        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as temporary:
            root = Path(temporary)
            packages = root / "input"
            packages.mkdir()
            sizes, hashes = {}, {}
            for name in lab.PACKAGES:
                data = ("synthetic archive " + name).encode()
                (packages / (name + "_" + lab.VERSION + "_amd64.deb")).write_bytes(data)
                sizes[name], hashes[name] = len(data), hashlib.sha256(data).hexdigest()
            original = lab.read_regular

            def read(path, limit):
                if str(path) == "/usr/lib/os-release":
                    return b'ID=ubuntu\nVERSION_ID="26.04"\n'
                return original(path, limit)

            calls, private_files = [], []

            def capture(argv, **kwargs):
                calls.append(argv)
                self.assertLessEqual(kwargs["timeout"], 120)
                self.assertNotIn("LD_PRELOAD", kwargs["env"])
                if argv[0] == "/usr/bin/dpkg-query":
                    return b"base-files\n"
                self.assertEqual(argv[:3], ["/usr/bin/dpkg", "--no-triggers", "--install"])
                for path, name in zip(argv[3:], ("libnvat", "nvattest")):
                    private_files.append(Path(path))
                    self.assertNotEqual(Path(path).parent, packages)
                    self.assertEqual(Path(path).stat().st_mode & 0o777, 0o600)
                    self.assertEqual(hashlib.sha256(Path(path).read_bytes()).hexdigest(), hashes[name])
                return b""

            with mock.patch.object(lab.os, "geteuid", return_value=0), \
                 mock.patch.object(lab, "read_regular", side_effect=read), \
                 mock.patch.object(lab, "PACKAGE_SIZES", sizes), \
                 mock.patch.object(lab, "PACKAGES", hashes), \
                 mock.patch.object(lab.os.path, "lexists", return_value=False):
                lab.install_packages(capture, packages, temporary)
                self.assertEqual(len(calls), 2)
                self.assertTrue(all(not path.exists() for path in private_files))
                bad = packages / ("nvattest_" + lab.VERSION + "_amd64.deb")
                bad.write_bytes(b"mutated")
                calls.clear()
                with self.assertRaises(ValueError):
                    lab.install_packages(capture, packages, temporary)
                self.assertEqual(len(calls), 1)  # Query only; no install.
                for present in (b"nvattest\n", b"libnvat:amd64\n"):
                    query = mock.Mock(return_value=present)
                    with self.assertRaises(ValueError):
                        lab.install_packages(query, packages, temporary)
                    query.assert_called_once()

    def test_install_and_attest_are_separate_cli_operations(self):
        result = subprocess.run([sys.executable, "-I", "-S", "-B",
                                 str(ROOT / "host/attest-lab-gpus.py"),
                                 "--install-packages", "PRIVATE_PATH", "--attest"],
                                capture_output=True, timeout=5, check=False)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, b"Invalid appraisal arguments.\n")
        self.assertEqual(result.stdout, b"")

    def test_actual_cli_contract_exact_versions_and_required_help(self):
        metadata = "".join(name + "\t" + lab.VERSION + "\tamd64\tii \n"
                           for name in lab.PACKAGES).encode()
        # The pinned installed binary pads short aliases with spaces.
        helps = [b"  -f,     --format TEXT\n  -l,     --log-level TEXT\n",
                 b"  -d,     --device TEXT\n  --nonce TEXT\n  --verifier TEXT\n"
                 b"  --gpu-evidence-source TEXT\n  --nras-url TEXT\n",
                 b"  --token-file TEXT\n  --nonce TEXT\n  --nras-url TEXT\n"
                 b"  --token-type TEXT\n"]
        capture = mock.Mock(side_effect=[metadata, *helps])
        lab.cli_contract(capture, "/private-fixture", clock=lambda: 1024.0)
        self.assertEqual(capture.call_count, 4)
        for spacing in (b"", b"\t"):
            variants = [text.replace(b",     --", b"," + spacing + b"--") for text in helps]
            lab.cli_contract(mock.Mock(side_effect=[metadata, *variants]), "/private-fixture")
        for call in capture.call_args_list:
            self.assertEqual(call.kwargs["timeout"], 30)
            self.assertEqual(call.kwargs["limit"], 65536)
            self.assertEqual(set(call.kwargs["env"]),
                             {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "LD_LIBRARY_PATH"})
        for bad in (metadata.replace(lab.VERSION.encode(), b"old"),
                    metadata.replace(b"amd64", b"arm64"), metadata.replace(b"ii ", b"rc "),
                    metadata + metadata, metadata.replace(b"libnvat", b"nvattest")):
            with self.assertRaises(ValueError):
                lab.cli_contract(mock.Mock(return_value=bad), "/private-fixture")
        for bad in (b"See --format and --log-level in PRIVATE help",
                    helps[0].replace(b"--format", b"--formats"), b"\xff"):
            with self.assertRaises(ValueError):
                lab.cli_contract(mock.Mock(side_effect=[metadata, bad]), "/private-fixture")
        with self.assertRaises(ValueError):
            lab.cli_contract(mock.Mock(), "/private-fixture",
                             clock=mock.Mock(side_effect=[1024.0, 1054.0]))

    def test_inspection_does_not_claim_appraisal_or_call_nvml(self):
        with mock.patch.object(lab.sys, "platform", "linux"), \
             mock.patch.object(lab.platform, "machine", return_value="x86_64"), \
             mock.patch.object(lab, "verify_installation"), \
             mock.patch.object(lab, "cli_contract"), \
             mock.patch.object(lab, "load_capture"), \
             mock.patch.object(lab, "Nvml") as nvml:
            result = lab.worker(False, "/private-fixture")
        nvml.assert_not_called()
        self.assertEqual(result["result"], "fail")
        self.assertEqual(result["stage"], "inspection-complete-unappraised")
        self.assertTrue(result["cli_contract_verified"])
        self.assertFalse(result["gpu_appraisal_verified"])
        self.assertIsNone(result["claims"])

    def test_complete_bound_flow_and_private_token_cleanup(self):
        run, nvml, report = FakeCapture(), FakeNvml(), {}
        result = lab.run_attestation(run, nvml, report, check=lambda: None)
        self.assertEqual(result["authenticated_gpu_count"], 8)
        self.assertTrue(result["local_device_identity_matches"])
        self.assertEqual(len(run.calls), 2)
        self.assertEqual(nvml.queries, 2)
        self.assertEqual(report["ready_before"], 0)
        self.assertEqual(report["ready_after"], 0)
        self.assertFalse(run.token_path.exists())
        for argv, opts in run.calls:
            self.assertIn(lab.NRAS, argv)
            self.assertNotIn("--relying-party-policy", argv)
            self.assertEqual(opts["limit"], 4 << 20)
            self.assertLessEqual(opts["timeout"], 240)
            self.assertNotIn("NV_ATTESTATION_SERVICE_KEY", opts["env"])
            self.assertNotIn("https_proxy", opts["env"])
        public = json.dumps(report | result)
        self.assertNotIn("PRIVATE_", public)
        self.assertNotIn("synthetic-signature", public)
        self.assertNotIn(run.calls[0][0][run.calls[0][0].index("--nonce") + 1], public)

    def test_each_claim_must_be_true_and_exact_inventory(self):
        mutations = [
            lambda rows: rows.pop(),
            lambda rows: rows.append(copy.deepcopy(rows[0])),
            lambda rows: rows[1].update(ueid=rows[0]["ueid"]),
            lambda rows: rows[0].update(ueid="PRIVATE_WRONG_ID"),
            lambda rows: rows[0].update(eat_nonce="old"),
            lambda rows: rows[0].update(measres="failure"),
            lambda rows: rows[0].update(dbgstat="enabled"),
            lambda rows: rows[0].update({"x-nvidia-device-type": "nvswitch"}),
            lambda rows: rows[0].update({"x-nvidia-gpu-claims-version": "unknown"}),
            lambda rows: rows[0].update({"x-nvidia-mismatch-measurement-records": [{}]}),
        ]
        for name in lab.TRUE_CLAIMS:
            for value in (False, 1, None, "true"):
                mutations.append(lambda rows, n=name, v=value: rows[0].update({n: v}))
        for change in mutations:
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    lab.run_attestation(FakeCapture(change), FakeNvml(), {}, check=lambda: None)

    def test_signed_claims_and_cli_claims_are_both_required(self):
        for change in (lambda rows: rows[0].update(measres="failure"),
                       lambda rows: rows[0].update(ueid="different")):
            with self.assertRaises(ValueError):
                lab.run_attestation(FakeCapture(verified_mutation=change), FakeNvml(), {},
                                    check=lambda: None)

    def test_claim_failure_fixed_fields_never_include_untrusted_values(self):
        for source in ("verified-cli", "signed-token", "attested-cli"):
            for name in lab.TRUE_CLAIMS:
                rows = [claim(i, "private-nonce") for i in range(8)]
                rows[0][name] = "PRIVATE_TOKEN"
                with self.assertRaises(lab.ClaimRejection) as raised:
                    lab.claim_map(rows, "private-nonce", source)
                error = raised.exception
                self.assertEqual((error.code, error.source, error.field),
                                 ("claim-type", source, name))
                self.assertNotIn("PRIVATE", str(error))
                self.assertNotIn("private-nonce", str(error))
        for name in ("x-nvidia-device-type", "x-nvidia-gpu-claims-version",
                     "x-nvidia-mismatch-measurement-records",
                     "x-nvidia-mismatch-opaque-data-records"):
            rows = [claim(i, "nonce") for i in range(8)]
            del rows[0][name]
            with self.assertRaises(lab.ClaimRejection) as raised:
                lab.claim_map(rows, "nonce", "signed-token")
            self.assertEqual((raised.exception.code, raised.exception.source, raised.exception.field),
                             ("claim-missing", "signed-token", name))
        field = "x-nvidia-gpu-attestation-report-cert-chain"
        for value, expected in (("PRIVATEZ", "certificate-expiry-format"),
                                ("2001-01-01T00:00:00Z", "certificate-expired")):
            rows = [claim(i, "nonce") for i in range(8)]
            rows[0][field]["x-nvidia-cert-expiration-date"] = value
            with self.assertRaises(lab.ClaimRejection) as raised:
                lab.claim_map(rows, "nonce", "signed-token")
            self.assertEqual(raised.exception.code, expected)
            self.assertEqual(raised.exception.field, field + ".x-nvidia-cert-expiration-date")
        for payload, expected in (("PRIVATE", "token-jwt-shape"),
                                  ("h.%%%PRIVATE.s", "token-payload-encoding"),
                                  ("h.bm90LWpzb24.s", "token-payload-json")):
            envelope = [["JWT", "PRIVATE_OVERALL"], {str(i): payload for i in range(8)}]
            with self.assertRaises(lab.ClaimRejection) as raised:
                lab.token_claims(envelope)
            self.assertEqual(raised.exception.code, expected)
            self.assertNotIn("PRIVATE", str(raised.exception))

    def test_worker_keeps_claim_rejection_before_shutdown_failure(self):
        nvml = FakeNvml()
        with mock.patch.object(lab.sys, "platform", "linux"), \
             mock.patch.object(lab.platform, "machine", return_value="x86_64"), \
             mock.patch.object(lab, "verify_installation"), \
             mock.patch.object(lab, "cli_contract"), \
             mock.patch.object(lab, "Nvml", return_value=nvml), \
             mock.patch.object(lab, "load_capture"), \
             mock.patch.object(nvml, "close", side_effect=ValueError("PRIVATE_SHUTDOWN")):
            def reject(capture, instance, report, **kwargs):
                report["stage"] = "claims-policy"
                raise lab.ClaimRejection("claim-value", "verified-cli", "eat_nonce")
            with mock.patch.object(lab, "run_attestation", side_effect=reject):
                result = lab.worker(True, None)
        self.assertEqual(result["result"], "fail")
        self.assertEqual(result["stage"], "claims-policy")
        self.assertEqual(result["claim_failure"],
                         {"code": "claim-value", "source": "verified-cli", "field": "eat_nonce"})
        self.assertEqual(nvml.transitions, 0)
        self.assertFalse(result["nvml_shutdown"])
        self.assertNotIn("PRIVATE", json.dumps(result))

    def test_revoked_expired_and_unavailable_certificates_fail(self):
        for chain in lab.CHAINS:
            for key, value in (("x-nvidia-cert-status", "invalid"),
                               ("x-nvidia-cert-ocsp-status", "unknown"),
                               ("x-nvidia-cert-ocsp-status", "revoked"),
                               ("x-nvidia-cert-expiration-date", "2001-01-01T00:00:00Z")):
                rows = [claim(i, "00") for i in range(8)]
                rows[0]["x-nvidia-gpu-" + chain + "-cert-chain"][key] = value
                with self.assertRaises(ValueError):
                    lab.claim_map(rows, "00")

    def test_inventory_or_readiness_change_rejects(self):
        nvml = FakeNvml()
        calls = 0

        def inventory():
            nonlocal calls
            calls += 1
            rows = copy.deepcopy(nvml.rows)
            if calls > 1:
                rows["1"] = (b"CHANGED_UUID", b"PRIVATE_CERT", 1)
            return rows
        nvml.inventory = inventory
        with self.assertRaises(ValueError):
            lab.run_attestation(FakeCapture(), nvml, {}, check=lambda: None)
        nvml = FakeNvml()
        with mock.patch.object(nvml, "ready", side_effect=[0, 1]):
            with self.assertRaises(ValueError):
                lab.run_attestation(FakeCapture(), nvml, {}, check=lambda: None)

    def test_untrusted_json_and_nonzero_status_never_accept(self):
        for value in (b'{"result_code":0,"result_code":1}', b'{"result_code":false}',
                      b'{"result_code":0,"value":NaN}', b'{"result_code":1}',
                      b'[]', b'\xff', b'x' * (lab.LIMIT + 1)):
            with self.assertRaises((ValueError, TypeError)):
                lab.successful_output(value)
        with self.assertRaises(ValueError):
            lab.token_claims([["JWT", "x"], {"x": "h.p.s"}])

    def test_deadline_and_command_failure(self):
        with self.assertRaises(ValueError):
            lab.run_attestation(FakeCapture(), FakeNvml(), {}, check=lambda: None,
                                clock=mock.Mock(side_effect=[1024.0, 1264.0]))
        capture = mock.Mock(side_effect=ValueError("PRIVATE_STDERR"))
        with self.assertRaises(ValueError):
            lab.run_attestation(capture, FakeNvml(), {}, check=lambda: None)
        self.assertEqual(capture.call_count, 1)

    def test_worker_sanitizes_failures_and_reports_shutdown_separately(self):
        nvml = FakeNvml()
        with mock.patch.object(lab.sys, "platform", "linux"), \
             mock.patch.object(lab.platform, "machine", return_value="x86_64"), \
             mock.patch.object(lab, "verify_installation"), \
             mock.patch.object(lab, "cli_contract"), \
             mock.patch.object(lab, "Nvml", return_value=nvml), \
             mock.patch.object(lab, "load_capture", return_value=FakeCapture()), \
             mock.patch.object(lab, "run_attestation", side_effect=ValueError("PRIVATE_SECRET")), \
             mock.patch.object(nvml, "close", side_effect=ValueError("PRIVATE_SHUTDOWN")):
            result = lab.worker(True, None)
        self.assertEqual(result["result"], "fail")
        self.assertFalse(result["nvml_shutdown"])
        self.assertFalse(result["gpu_appraisal_verified"])
        self.assertNotIn("PRIVATE", json.dumps(result))

    def test_failed_cli_contract_stops_before_nvml_and_preserves_failure(self):
        with mock.patch.object(lab.sys, "platform", "linux"), \
             mock.patch.object(lab.platform, "machine", return_value="x86_64"), \
             mock.patch.object(lab, "verify_installation"), \
             mock.patch.object(lab, "load_capture"), \
             mock.patch.object(lab, "cli_contract", side_effect=ValueError("PRIVATE_HELP")), \
             mock.patch.object(lab, "Nvml") as nvml:
            result = lab.worker(True, "/private-fixture")
        nvml.assert_not_called()
        self.assertEqual(result["stage"], "cli-contract")
        self.assertFalse(result["cli_contract_verified"])
        self.assertEqual(result["result"], "fail")
        self.assertNotIn("PRIVATE", json.dumps(result))

    def test_installed_files_and_alias_are_exact(self):
        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as temporary:
            root = Path(temporary)
            pins = {}
            for path in lab.FILES:
                target = root / path.lstrip("/")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"fixture")
                pins[path] = (7, hashlib.sha256(b"fixture").hexdigest())
            alias = root / lab.LIB_ALIAS.lstrip("/")
            alias.symlink_to("libnvat.so.2.0.0")
            with mock.patch.object(lab, "FILES", pins):
                lab.verify_installation(root)
                alias.unlink()
                alias.symlink_to("wrong")
                with self.assertRaises(ValueError):
                    lab.verify_installation(root)
                alias.unlink()
                alias.symlink_to("libnvat.so.2.0.0")
                binary = root / "usr/bin/nvattest"
                binary.write_bytes(b"changed")
                with self.assertRaises(ValueError):
                    lab.verify_installation(root)

    def test_no_follow_fifo_hardlinks_bounds_and_helper_tamper(self):
        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as temporary:
            root = Path(temporary)
            file = root / "regular"
            file.write_bytes(b"abc")
            self.assertEqual(lab.read_regular(file, 3), b"abc")
            with self.assertRaises(ValueError):
                lab.read_regular(file, 2)
            link = root / "link"
            link.symlink_to(file)
            with self.assertRaises(OSError):
                lab.read_regular(link, 10)
            link.unlink()
            os.link(file, link)
            with self.assertRaises(ValueError):
                lab.read_regular(file, 10)
            fifo = root / "fifo"
            os.mkfifo(fifo)
            with self.assertRaises(ValueError):
                lab.read_regular(fifo, 10)
        lab.load_capture()
        with mock.patch.object(lab, "read_regular", return_value=b"raise Exception('PRIVATE')"):
            with self.assertRaises(ValueError):
                lab.load_capture()

    def test_alias_read_atime_change_is_not_identity_change(self):
        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as temporary:
            root = Path(temporary)
            alias = root / lab.LIB_ALIAS.lstrip("/")
            alias.parent.mkdir(parents=True)
            alias.symlink_to("libnvat.so.2.0.0")
            actual = alias.lstat()
            fields = ("st_dev", "st_ino", "st_mode", "st_nlink", "st_size",
                      "st_mtime_ns", "st_ctime_ns", "st_atime", "st_atime_ns")
            before = types.SimpleNamespace(**{name: getattr(actual, name) for name in fields})
            after = copy.copy(before)
            after.st_atime += 1
            after.st_atime_ns += 1000000000
            with mock.patch.object(lab, "FILES", {}), \
                 mock.patch.object(Path, "lstat", side_effect=[before, after]):
                lab.verify_installation(root)
            for name in fields[:7]:
                changed = copy.copy(after)
                setattr(changed, name, getattr(changed, name) + 1)
                with self.subTest(field=name), mock.patch.object(lab, "FILES", {}), \
                     mock.patch.object(Path, "lstat", side_effect=[before, changed]):
                    with self.assertRaises(ValueError):
                        lab.verify_installation(root)

    def test_certificate_serial_prefix_is_bounded(self):
        def pem(serial, version=b"\xa0\x03\x02\x01\x02"):
            tbs = version + b"\x02" + bytes([len(serial)]) + serial
            cert = b"\x30" + bytes([len(tbs) + 2]) + b"\x30" + bytes([len(tbs)]) + tbs
            return b"-----BEGIN CERTIFICATE-----\n" + base64.b64encode(cert) + b"\n-----END CERTIFICATE-----"
        self.assertEqual(lab.certificate_serial(pem(b"\x01")), "1")
        self.assertEqual(lab.certificate_serial(pem(b"\x00\x80")), "128")
        self.assertEqual(lab.certificate_serial(pem(b"\x01", version=b"")), "1")
        for serial in (b"", b"\x00", b"\x80", b"\x00\x01", b"\x01" * 21):
            with self.assertRaises(ValueError):
                lab.certificate_serial(pem(serial))
        for value in (b"PRIVATE", pem(b"\x01")[:20], b"x" * 5121):
            with self.assertRaises(ValueError):
                lab.certificate_serial(value)

    def test_no_setter_is_referenced(self):
        class Function:
            def __call__(self, *args):
                return 0
        names = []

        class Library:
            def __getattr__(self, name):
                names.append(name)
                return Function()
        with mock.patch.object(lab.C, "CDLL", return_value=Library()):
            nvml = lab.Nvml()
            nvml.close()
        self.assertFalse(any(name.startswith(("nvmlSystemSet", "nvmlDeviceSet")) for name in names))
        self.assertEqual(ctypes.sizeof(lab.Settings), 20)
        self.assertEqual(ctypes.sizeof(lab.Certificate), 9224)

    def test_real_nvml_inventory_functions_with_fake_c_abi(self):
        class Function:
            def __init__(self, name, state):
                self.name, self.state = name, state

            def __call__(self, *args):
                name, state = self.name, self.state
                state["calls"].append(name)
                if name == state.get("fail"):
                    return 3
                if name == "nvmlDeviceGetCount_v2":
                    args[0]._obj.value = state.get("count", 8)
                elif name == "nvmlSystemGetConfComputeSettings":
                    row = args[0]._obj
                    assert row.version == 0x01000014
                    row.environment, row.feature, row.devtools, row.multigpu = state.get(
                        "settings", (2, 1, 0, 2))
                elif name == "nvmlSystemGetConfComputeGpusReadyState":
                    args[0]._obj.value = state.get("ready", 0)
                elif name == "nvmlDeviceGetHandleByIndex_v2":
                    args[1]._obj.value = args[0] + 1
                elif name == "nvmlDeviceGetPersistenceMode":
                    args[1]._obj.value = state.get("persistence", 1)
                elif name == "nvmlDeviceGetUUID":
                    index = 1 if state.get("duplicate_uuid") else args[0].value
                    args[1].value = ("PRIVATE_UUID_" + str(index)).encode()
                elif name == "nvmlDeviceGetConfComputeGpuCertificate":
                    row = args[1]._obj
                    row.cert_size = 0
                    row.attestation_size = state.get("cert_size", 1)
                    row.attestation[0] = 1 if state.get("duplicate_cert") else args[0].value
                return 0

        class Library:
            def __init__(self, state):
                self.state = state

            def __getattr__(self, name):
                return Function(name, self.state)

        for changes in ({}, {"count": 4}, {"count": 9}, {"settings": (2, 1, 1, 2)},
                        {"settings": (0, 1, 0, 2)}, {"settings": (2, 0, 0, 2)},
                        {"settings": (2, 1, 0, 0)}, {"duplicate_uuid": True},
                        {"duplicate_cert": True}, {"cert_size": 5121}, {"cert_size": 0},
                        {"persistence": 2}, {"fail": "nvmlDeviceGetConfComputeGpuCertificate"}):
            state = {"calls": [], **changes}
            with mock.patch.object(lab.C, "CDLL", return_value=Library(state)), \
                 mock.patch.object(lab, "certificate_serial",
                                   side_effect=lambda pem: str(pem[0])):
                nvml = lab.Nvml()
                try:
                    if changes:
                        with self.assertRaises(ValueError):
                            nvml.inventory()
                    else:
                        self.assertEqual(len(nvml.inventory()), 8)
                        self.assertEqual(nvml.ready(), 0)
                        state["settings"] = (2, 1, 0, 1)
                        with self.assertRaises(ValueError):
                            nvml.inventory()
                finally:
                    nvml.close()
            self.assertEqual(state["calls"][-1], "nvmlShutdown")

    def test_unknown_cli_input_is_not_echoed(self):
        result = subprocess.run([sys.executable, "-I", "-S", "-B",
                                 str(ROOT / "host/attest-lab-gpus.py"), "--PRIVATE_SECRET"],
                                capture_output=True, timeout=5, check=False)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, b"Invalid appraisal arguments.\n")
        self.assertEqual(result.stdout, b"")

    def test_enable_is_after_appraisal_persistence_and_rechecks_only(self):
        nvml, report = FakeNvml(), {}
        capture = FakeCapture()
        original = nvml.enable_ready

        def transition():
            self.assertEqual(len(capture.calls), 2)
            self.assertGreaterEqual(nvml.queries, 3)
            self.assertTrue(report["gpu_appraisal_verified"])
            self.assertEqual(report["readiness_transition"], "attempted-unverified")
            original()
        nvml.enable_ready = transition
        lab.run_attestation(capture, nvml, report, check=lambda: None, enable_readiness=True)
        self.assertEqual(nvml.transitions, 1)
        self.assertEqual(report["readiness_transition"], "enabled-readback-verified")
        self.assertEqual(report["ready_after"], 1)
        self.assertEqual(nvml.queries, 4)

    def test_all_prerequisite_failures_prevent_setter(self):
        for failure in ("nonce", "count", "persistence", "inventory", "deadline"):
            nvml, report, capture = FakeNvml(), {}, FakeCapture()
            options = {}
            if failure == "nonce":
                capture = FakeCapture(lambda rows: rows[0].update(eat_nonce="PRIVATE"))
            if failure == "count":
                capture = FakeCapture(lambda rows: rows.pop())
            if failure == "persistence":
                nvml.rows["1"] = (b"PRIVATE", b"CERT", 0)
            if failure == "inventory":
                original = nvml.inventory
                def inventory():
                    rows = original()
                    if nvml.queries == 3:
                        rows["1"] = (b"CHANGED", b"CERT", 1)
                    return rows
                nvml.inventory = inventory
            if failure == "deadline":
                options["clock"] = mock.Mock(side_effect=[1024.0, 1025.0, 1026.0,
                                                         1027.0, 1028.0, 1264.0])
            with self.subTest(failure=failure), self.assertRaises(ValueError):
                lab.run_attestation(capture, nvml, report, check=lambda: None,
                                    enable_readiness=True, **options)
            self.assertEqual(nvml.transitions, 0)

    def test_already_ready_does_not_set_and_missing_persistence_still_rejects(self):
        nvml, report = FakeNvml(), {}
        nvml.state = 1
        lab.run_attestation(FakeCapture(), nvml, report, check=lambda: None, enable_readiness=True)
        self.assertEqual(nvml.transitions, 0)
        self.assertEqual(report["readiness_transition"], "already-ready-verified")
        nvml.rows["1"] = (b"PRIVATE", b"CERT", 0)
        with self.assertRaises(ValueError):
            lab.run_attestation(FakeCapture(), nvml, {}, check=lambda: None, enable_readiness=True)
        self.assertEqual(nvml.transitions, 0)

    def test_set_failure_and_bad_readback_are_not_success_or_retried(self):
        for failure in ("setter", "readback", "identity"):
            nvml, report = FakeNvml(), {}
            def transition():
                nvml.transitions += 1
                if failure == "setter":
                    raise ValueError("PRIVATE_NVML")
                nvml.state = 0 if failure == "readback" else 1
                if failure == "identity":
                    nvml.rows["1"] = (b"CHANGED", b"CERT", 1)
            nvml.enable_ready = transition
            with self.subTest(failure=failure), self.assertRaises(ValueError):
                lab.run_attestation(FakeCapture(), nvml, report, check=lambda: None,
                                    enable_readiness=True)
            self.assertEqual(nvml.transitions, 1)
            self.assertEqual(report["readiness_transition"], "attempted-unverified")
            self.assertTrue(report["gpu_appraisal_verified"])
            self.assertNotIn("PRIVATE", json.dumps(report))

    def test_transition_lock_is_private_nonblocking_and_no_follow(self):
        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as temporary:
            root = Path(temporary)
            fd = lab.readiness_lock(root)
            try:
                with self.assertRaises(BlockingIOError):
                    lab.readiness_lock(root)
            finally:
                os.close(fd)
            path = root / "nib-lab-gpu-readiness.lock"
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            path.unlink()
            path.symlink_to(root / "PRIVATE")
            with self.assertRaises(OSError):
                lab.readiness_lock(root)
            path.unlink()
            os.mkfifo(path, 0o600)
            with self.assertRaises(ValueError):
                lab.readiness_lock(root)

    def test_enable_cli_requires_attest_and_default_remains_read_only(self):
        result = subprocess.run([sys.executable, "-I", "-S", "-B",
                                 str(ROOT / "host/attest-lab-gpus.py"), "--enable-readiness"],
                                capture_output=True, timeout=5, check=False)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, b"Invalid appraisal arguments.\n")
        nvml = FakeNvml()
        lab.run_attestation(FakeCapture(), nvml, {}, check=lambda: None)
        self.assertEqual(nvml.transitions, 0)

    def test_exact_readiness_setter_abi_and_return_failure(self):
        names, values = [], []
        class Function:
            def __init__(self, name, result):
                self.name, self.result = name, result
            def __call__(self, *args):
                if self.name == "nvmlSystemSetConfComputeGpusReadyState":
                    values.extend(args)
                    self_test.assertEqual(self.argtypes, [ctypes.c_uint])
                    self_test.assertEqual(self.restype, ctypes.c_int)
                    return self.result
                return 0
        class Library:
            def __init__(self, result):
                self.result = result
            def __getattr__(self, name):
                names.append(name)
                return Function(name, self.result)
        self_test = self
        for code in (0, 3):
            with mock.patch.object(lab.C, "CDLL", return_value=Library(code)):
                nvml = lab.Nvml()
                if code:
                    with self.assertRaises(ValueError):
                        nvml.enable_ready()
                else:
                    nvml.enable_ready()
                nvml.close()
        self.assertEqual(values, [1, 1])
        self.assertNotIn("nvmlDeviceSetPersistenceMode", names)

    def test_worker_transition_shutdown_failure_preserves_observations_and_unlocks(self):
        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as temporary:
            root = Path(temporary)
            nvml = FakeNvml()
            original_lock = lab.readiness_lock
            original_attestation = lab.run_attestation
            with mock.patch.object(lab.sys, "platform", "linux"), \
                 mock.patch.object(lab.platform, "machine", return_value="x86_64"), \
                 mock.patch.object(lab.os, "geteuid", return_value=0), \
                 mock.patch.object(lab, "readiness_lock", side_effect=lambda: original_lock(root)), \
                 mock.patch.object(lab, "verify_installation"), \
                 mock.patch.object(lab, "cli_contract"), \
                 mock.patch.object(lab, "Nvml", return_value=nvml), \
                 mock.patch.object(lab, "load_capture", return_value=FakeCapture()), \
                 mock.patch.object(lab, "run_attestation", side_effect=lambda *args, **kwargs:
                                   original_attestation(*args, check=lambda: None, **kwargs)), \
                 mock.patch.object(nvml, "close", side_effect=ValueError("PRIVATE_SHUTDOWN")):
                # The fake root account must agree with the real temporary directory's owner.
                with mock.patch.object(lab.os, "geteuid", side_effect=[0, root.stat().st_uid,
                                                                     root.stat().st_uid]):
                    result = lab.worker(True, temporary, enable_readiness=True)
            self.assertEqual(result["result"], "fail")
            self.assertEqual(result["stage"], "nvml-shutdown")
            self.assertTrue(result["gpu_appraisal_verified"])
            self.assertEqual(result["readiness_transition"], "enabled-readback-verified")
            self.assertFalse(result["nvml_shutdown"])
            self.assertNotIn("PRIVATE", json.dumps(result))
            fd = original_lock(root)
            os.close(fd)

    def test_capture_isolated_no_site_flags_timeout_overflow(self):
        capture = lab.load_capture()
        command = [sys.executable, "-I", "-S", "-B", "-c", "print('ok')"]
        self.assertEqual(capture(command), b"ok\n")
        with self.assertRaises(ValueError):
            capture([sys.executable, "-I", "-S", "-c", "print('x'*1000)"], limit=100)
        with self.assertRaises(ValueError):
            capture([sys.executable, "-I", "-S", "-c", "import time;time.sleep(5)"], timeout=0.1)
        source = (ROOT / "host/probe.py").read_text()
        self.assertIn('"-I", "-S", "-B", "-c", SUPERVISOR', source)


if __name__ == "__main__":
    unittest.main()
