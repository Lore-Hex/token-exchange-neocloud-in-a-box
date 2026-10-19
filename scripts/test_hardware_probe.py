# SPDX-License-Identifier: Apache-2.0
import importlib.util
import json
from pathlib import Path
import sys
import unittest

spec = importlib.util.spec_from_file_location("hardware_probe", Path(__file__).resolve().parents[1] / "host/probe.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class HardwareProbeTests(unittest.TestCase):
    def collect(self, gpu=None, vendor=b"AuthenticAMD"):
        def run(argv):
            self.assertEqual(argv, ["/usr/bin/nvidia-smi", "--query-gpu=name,memory.total,driver_version",
                                    "--format=csv,noheader,nounits"])
            return gpu if gpu is not None else b"NVIDIA B200, 183359, 595.45.04\n" * 8
        def read(path, limit):
            if str(path) == "/proc/meminfo":
                return b"MemTotal:       1400000000 kB\n"
            return b"vendor_id : " + vendor + b"\n"
        return probe.collect(run, read, lambda path: str(path) == "/dev/sev-guest")

    def test_inventory_success_never_means_attestation_or_runtime_ready(self):
        report = self.collect()
        self.assertEqual(report["result"], "pass")
        self.assertEqual(report["checks"]["cpu_vendor"], "amd")
        self.assertTrue(report["checks"]["attestation_interfaces_present"]["amd_sev_guest"])
        for key in ("confidential_qualified", "hardware_attestation_verified", "runtime_ready"):
            self.assertIs(report[key], False)
        self.assertEqual(self.collect(vendor=b"GenuineIntel")["checks"]["cpu_vendor"], "intel")

    def test_wrong_gpu_counts_and_models_fail(self):
        for data in (b"", b"NVIDIA B200, 180000, 595.45.04\n" * 9,
                     b"NVIDIA B200, 180000, 595.45.04\n" * 7,
                     b"NVIDIA H100, 80000, 595.45.04\n" * 8,
                     b"NVIDIA B200, N/A, 595.45.04\n" * 8):
            self.assertEqual(self.collect(gpu=data)["result"], "fail")

    def test_raw_diagnostics_and_unrecognized_strings_are_not_returned(self):
        secret = "PRIVATE-DIAGNOSTIC-CANARY"
        report = self.collect(gpu=(secret + ", 180000, 595.45.04\n").encode() * 8,
                              vendor=secret.encode())
        self.assertNotIn(secret, json.dumps(report))
        def fail(*_):
            raise OSError(secret)
        report = probe.collect(fail, fail, fail)
        self.assertEqual(len(report["errors"]), 4)
        self.assertNotIn(secret, json.dumps(report))
        self.assertEqual(report["result"], "fail")

    def test_bad_memory_and_driver_rejected(self):
        for data in (b"NVIDIA B200, -1, 595.45.04\n", b"NVIDIA B200, 2, secret\n",
                     b"NVIDIA B200, 1048577, 595.45.04\n", b"\xff"):
            with self.assertRaises(ValueError):
                probe.gpu_inventory(data)
        for data in (b"MemTotal: secret kB\n", b"MemTotal: 0 kB\n", b"MemTotal: 2 kB\nMemTotal: 3 kB\n"):
            with self.assertRaises(ValueError):
                probe.memory_kib(data)

    def test_command_output_timeout_and_failure_are_bounded(self):
        self.assertEqual(probe.capture([sys.executable, "-I", "-c", "print('ok')"]), b"ok\n")
        for program, timeout in (("import time; time.sleep(5)", 0.1),
                                 ("import sys; sys.stderr.write('x'*100000)", 3),
                                 ("print('x'*100000)", 3), ("raise SystemExit(1)", 3)):
            with self.assertRaises((ValueError, probe.subprocess.TimeoutExpired)):
                probe.capture([sys.executable, "-I", "-c", program], timeout=timeout)
