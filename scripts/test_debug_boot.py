# SPDX-License-Identifier: Apache-2.0
"""Local contracts/failure tests; no namespace, cgroup or QEMU execution."""
from contextlib import contextmanager
import configparser
import copy
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

import yaml

import debug_boot_qemu as qemu
import debug_boot_uki as uki
import mkosi_boot as boot
import mkosi_confinement as guard
import fetch_debug_boot_runtime as fetch
from test_mkosi_boot import fixture_cpio
from test_evidence_history import producer as evidence_producer

ROOT = Path(__file__).absolute().parent.parent


def load(path):
    value = types.ModuleType(path.replace("/", "_"))
    value.__file__ = str(ROOT / path)
    exec(compile((ROOT / path).read_bytes(), value.__file__, "exec"), value.__dict__)
    return value


host = load("host/debug-boot.py")
wrapper = load("host/run-debug-boot.py")
PRODUCER = {"schema": "nib.debug-producer/v1", "source": {"git_sha": "a" * 40, "sources": {}},
            "debug_source": {"git_sha": "a" * 40, "sources": {wrapper.SCRIPT: wrapper.base.digest(wrapper.SELF)}}}


class QemuTests(unittest.TestCase):
    def test_network_failure_frames_fit_original_cap_and_never_pass(self):
        self.assertEqual(qemu.MAX_PROOF_BYTES, 62)
        self.assertEqual(qemu.NETWORK_STAGES, tuple("path filesystem interfaces config-read config policy executable "
            "initial-command initial-ruleset check create readback-command readback deadline".split()))
        for status in (*range(64, 78), 0, 203, 226, 255):
            raw = qemu.NETWORK_PREFIX + f"2 1 {status}\n".encode("ascii")
            expected = {"service_result": "exit-code", "exit_code": "exited", "exit_status": status,
                        "stage": qemu.NETWORK_STAGES[status - 64] if 64 <= status <= 77 else None}
            self.assertLessEqual(len(raw), 62)
            for split in range(1, len(raw)):
                connection = mock.Mock()
                connection.recv.side_effect = [raw[:split], raw[split:], b""]
                listener = mock.Mock()
                listener.accept.return_value = (connection, None)
                state = {"initrd": False, "root": False, "complete": False}
                qemu.receive(listener, threading.Event(), time.monotonic() + 1, state)
                self.assertEqual(state["network_failure"], expected)
                self.assertEqual(state["guest_failure"], "NETWORK_SERVICE")
                self.assertTrue(state["initrd"])
                self.assertFalse(state["root"] or state["complete"])
                self.assertNotIn("protocol_error", state)
        for result in range(10):
            for code in range(4):
                prefix, value = qemu.network_frame(qemu.NETWORK_PREFIX + f"{result} {code} 0\n".encode())
                self.assertTrue(prefix)
                self.assertEqual(value["service_result"], qemu.NETWORK_RESULTS[result])
                self.assertIsNone(value["stage"])

    def test_network_failure_rejects_noncanonical_content_and_missing_eof(self):
        for body in (b"10 1 0\n", b"1 4 0\n", b"1 1 256\n", b"01 1 0\n", b"1 1 00\n", b"1 1 -1\n",
                     b"1 2 9\n", b"1 1 0 extra\n", b"1 1 0\r\n", b"1  0 0\n", b"1 1 private-canary\n",
                     b"2 1 70\nPASS\n", b"2 1 70\n\n"):
            raw = qemu.NETWORK_PREFIX + body
            self.assertEqual(qemu.network_frame(raw), (False, None))
            connection = mock.Mock()
            connection.recv.side_effect = [raw, b""]
            listener = mock.Mock()
            listener.accept.return_value = (connection, None)
            state = {"initrd": False, "root": False, "complete": False}
            qemu.receive(listener, threading.Event(), time.monotonic() + 1, state)
            self.assertFalse(state["complete"])
            self.assertNotIn("network_failure", state)
            self.assertNotIn("private-canary", json.dumps(state))
        self.assertEqual(qemu.network_frame(b"NIB_DEBUG_BOOT_V1 N 2 1 70\n"), (False, None))
        raw = qemu.NETWORK_PREFIX + b"2 1 70\n"
        for chunks, expected in (([raw[:-1], b""], "incomplete"), ([raw, socket.timeout()], "missing-eof")):
            connection = mock.Mock()
            connection.recv.side_effect = chunks
            listener = mock.Mock()
            listener.accept.return_value = (connection, None)
            stop = threading.Event(); stop.set()
            state = {"initrd": False, "root": False, "complete": False}
            qemu.receive(listener, stop, time.monotonic() + 1, state)
            self.assertFalse(state["complete"])
            self.assertEqual(state["protocol_error"], expected)

    def test_network_failure_survives_clean_qemu_exit_and_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / "proof.sock"
            def execute(args, **kwargs):
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.connect(str(path))
                    client.sendall(qemu.NETWORK_PREFIX + b"1 0 0\n")
                return subprocess.CompletedProcess(args, 0, b"", b"")
            with mock.patch.object(qemu, "SOCKET", path), mock.patch.object(qemu, "TIMEOUT", 1):
                state, terminal = {"initrd": False, "root": False, "complete": False}, {}
                with self.assertRaises(ValueError): qemu.trial(execute, state, terminal)
            self.assertEqual(state["network_failure"], {"service_result": "resources", "exit_code": "unknown", "exit_status": 0, "stage": None})
            self.assertFalse(state["complete"])
            self.assertEqual(terminal["exit_code"], 0)
            self.assertTrue(terminal["process_group_cleaned"])
            self.assertFalse(path.exists())

    def test_root_read_counters_fit_unchanged_cap_and_never_pass(self):
        self.assertEqual(qemu.MAX_PROOF_BYTES, 62)
        self.assertEqual(qemu.MAX_ROOT_BYTES, 1536 << 20)
        longest = qemu.ROOT_READ_PREFIX + f"8 {qemu.MAX_ROOT_BYTES} 145\n".encode("ascii")
        self.assertEqual(len(longest), qemu.MAX_PROOF_BYTES)
        self.assertEqual(qemu.root_read_frame(longest + b"x"), (False, None))
        self.assertEqual(qemu.ROOT_READ_REASONS, {1: "size", 2: "deadline", 3: "short-read", 4: "io",
            5: "extra-data", 6: "eof", 7: "late-eof", 8: "close"})
        for code, reason in qemu.ROOT_READ_REASONS.items():
            for count, seconds in ((0, 0), (3, 1), (qemu.MAX_ROOT_BYTES, 25),
                                   (qemu.MAX_ROOT_BYTES, 26), (qemu.MAX_ROOT_BYTES, 99),
                                   (qemu.MAX_ROOT_BYTES, 100), (qemu.MAX_ROOT_BYTES, 145)):
                raw = qemu.ROOT_READ_PREFIX + f"{code} {count} {seconds}\n".encode("ascii")
                self.assertLessEqual(len(raw), 62)
                for split in range(1, len(raw)):
                    connection = mock.Mock()
                    connection.recv.side_effect = [raw[:split], raw[split:], b""]
                    listener = mock.Mock()
                    listener.accept.return_value = (connection, None)
                    state = {"initrd": False, "root": False, "complete": False}
                    qemu.receive(listener, threading.Event(), time.monotonic() + 1, state)
                    self.assertEqual(state["root_read"], {"reason": reason, "bytes_read": count, "elapsed_seconds": seconds})
                    self.assertEqual(state["guest_failure"], "ROOT_READ")
                    self.assertTrue(state["initrd"])
                    self.assertFalse(state["root"] or state["complete"])
                    self.assertNotIn("protocol_error", state)

    def test_root_read_rejects_unknown_noncanonical_and_excess_fields(self):
        for body in (b"0 0 0\n", b"9 0 0\n", b"01 0 0\n", b"1 00 0\n", b"1 0 00\n",
                     b"1 -1 0\n", b"1 +1 0\n", b"1 1610612737 0\n", b"1 0 146\n", b"1 0 1000\n",
                     b"1 0 0 extra\n", b"1 0 0\nprivate-canary", b"1  0 0\n", b"1 0 0\r\n",
                     b"1 99999999999 0\n", b"1 0 private-canary\n", b"1 0 0\n\n"):
            raw = qemu.ROOT_READ_PREFIX + body
            self.assertEqual(qemu.root_read_frame(raw), (False, None))
            connection = mock.Mock()
            connection.recv.side_effect = [raw, b""]
            listener = mock.Mock()
            listener.accept.return_value = (connection, None)
            state = {"initrd": False, "root": False, "complete": False}
            qemu.receive(listener, threading.Event(), time.monotonic() + 1, state)
            self.assertFalse(state["complete"])
            self.assertNotIn("root_read", state)
            self.assertNotIn("private-canary", json.dumps(state))
        self.assertEqual(qemu.root_read_frame(b"NIB_DEBUG_BOOT_V1 R 1 0 0\n"), (False, None))

    def test_root_read_still_requires_newline_and_eof(self):
        raw = qemu.ROOT_READ_PREFIX + b"2 65536 23\n"
        for chunks, expected in (([raw[:-1], b""], "incomplete"), ([raw, socket.timeout()], "missing-eof")):
            connection = mock.Mock()
            connection.recv.side_effect = chunks
            listener = mock.Mock()
            listener.accept.return_value = (connection, None)
            stop = threading.Event(); stop.set()
            state = {"initrd": False, "root": False, "complete": False}
            qemu.receive(listener, stop, time.monotonic() + 1, state)
            self.assertFalse(state["complete"])
            self.assertEqual(state["protocol_error"], expected)

    def test_numeric_root_failure_survives_clean_process_exit(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / "proof.sock"
            def execute(args, **kwargs):
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.connect(str(path))
                    client.sendall(qemu.ROOT_READ_PREFIX + b"4 65543 2\n")
                return subprocess.CompletedProcess(args, 0, b"", b"")
            with mock.patch.object(qemu, "SOCKET", path), mock.patch.object(qemu, "TIMEOUT", 1):
                state, terminal = {"initrd": False, "root": False, "complete": False}, {}
                with self.assertRaises(ValueError):
                    qemu.trial(execute, state, terminal)
            self.assertEqual(state["root_read"], {"reason": "io", "bytes_read": 65543, "elapsed_seconds": 2})
            self.assertFalse(state["complete"])
            self.assertEqual(terminal["exit_code"], 0)
            self.assertTrue(terminal["process_group_cleaned"])
            self.assertFalse(path.exists())

    def test_failure_stage_contract_is_finite_and_separate_from_success(self):
        self.assertEqual(qemu.FAILURE_STAGES, tuple("INITRD_STAGE INITRD_MARKER KERNEL PID1 CMDLINE "
            "PID1_MOUNT SELF_MOUNT MOUNT_MATCH DM_NAME DM_SUSPENDED DM_READONLY TABLE_QUERY TABLE_FORMAT "
            "ROOT_OPEN ROOT_READ TABLE_RECHECK STATUS_QUERY STATUS_FORMAT DEADLINE".split()))
        self.assertEqual(len(qemu.FAILURES), 21)
        self.assertNotIn(qemu.EXPECTED, qemu.FAILURES)
        self.assertLessEqual(qemu.MAX_PROOF_BYTES, 128)
        self.assertEqual(qemu.TIMEOUT, 180)
        service = (ROOT / "guest/debugboot/nib-debug-boot.service").read_text()
        for directive in ("CapabilityBoundingSet=CAP_SYS_ADMIN\n", "TimeoutStartSec=150\n",
                          "MemoryMax=64M\n", "StandardOutput=null\n", "StandardError=null\n"):
            self.assertIn(directive, service)

    def test_root_budget_keeps_initrd_and_outer_ceilings(self):
        units = []
        for name in ("nib-debug-boot-stage.service", "nib-debug-boot.service"):
            config = configparser.ConfigParser(interpolation=None)
            config.read_string((ROOT / "guest/debugboot" / name).read_text())
            units.append((config.getint("Service", "TimeoutStartSec"), config.getint("Service", "TimeoutStopSec")))
        self.assertEqual(units, [(30, 2), (150, 2)])
        self.assertEqual(qemu.MAX_ROOT_READ_SECONDS, 145)
        self.assertEqual(units[1][0] - qemu.MAX_ROOT_READ_SECONDS, 5)
        self.assertEqual(qemu.TIMEOUT - sum(units[1]), 28)
        self.assertEqual(qemu.TIMEOUT, 180)
        self.assertEqual(wrapper.TIMEOUT, 900)
        workflow = yaml.safe_load((ROOT / ".github/workflows/debug-boot.yml").read_text())
        self.assertEqual(workflow["jobs"]["boot"]["timeout-minutes"], 35)

    def test_recognized_failures_survive_every_fragment_boundary_but_never_pass(self):
        for raw, stage in qemu.FAILURES.items():
            for split in range(1, len(raw)):
                with self.subTest(stage=stage, split=split):
                    connection = mock.Mock()
                    connection.recv.side_effect = [raw[:split], raw[split:], b""]
                    listener = mock.Mock()
                    listener.accept.return_value = (connection, None)
                    state = {"initrd": False, "root": False, "complete": False}
                    qemu.receive(listener, threading.Event(), time.monotonic() + 1, state)
                    self.assertEqual(state["guest_failure"], stage)
                    self.assertEqual(state["initrd"], raw.startswith(qemu.INITRD))
                    self.assertFalse(state["root"] or state["complete"])
                    self.assertNotIn("protocol_error", state)
                    connection.close.assert_called_once()

    def test_unknown_out_of_order_truncated_and_extended_failures_are_rejected(self):
        known = qemu.INITRD + b"NIB_DEBUG_BOOT_V1 FAIL ROOT_READ\n"
        for raw in (known[:-1], known + b"private-canary", known + known,
                    b"NIB_DEBUG_BOOT_V1 FAIL ROOT_READ\n", qemu.INITRD * 2,
                    qemu.INITRD + b"NIB_DEBUG_BOOT_V1 FAIL private-canary\n",
                    qemu.INITRD + b"NIB_DEBUG_BOOT_V1 FAIL INITRD_STAGE\n"):
            connection = mock.Mock()
            connection.recv.side_effect = [raw, b""]
            listener = mock.Mock()
            listener.accept.return_value = (connection, None)
            state = {"initrd": False, "root": False, "complete": False}
            qemu.receive(listener, threading.Event(), time.monotonic() + 1, state)
            self.assertFalse(state["complete"])
            self.assertIn(state["protocol_error"], ("invalid-frame", "incomplete"))
            self.assertNotIn("guest_failure", state)
            self.assertNotIn("private-canary", json.dumps(state))

    def test_clean_qemu_exit_does_not_hide_a_guest_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / "proof.sock"
            def execute(args, **kwargs):
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.connect(str(path))
                    client.sendall(qemu.INITRD + b"NIB_DEBUG_BOOT_V1 FAIL ROOT_READ\n")
                return subprocess.CompletedProcess(args, 0, b"", b"")
            with mock.patch.object(qemu, "SOCKET", path), mock.patch.object(qemu, "TIMEOUT", 1):
                state, terminal = {"initrd": False, "root": False, "complete": False}, {}
                with self.assertRaises(ValueError):
                    qemu.trial(execute, state, terminal)
            self.assertEqual(state["guest_failure"], "ROOT_READ")
            self.assertFalse(state["root"] or state["complete"])
            self.assertEqual(terminal["exit_code"], 0)
            self.assertTrue(terminal["process_group_cleaned"])
            self.assertFalse(path.exists())

    def test_complete_frame_without_eof_still_fails(self):
        connection = mock.Mock()
        connection.recv.side_effect = [qemu.EXPECTED, socket.timeout()]
        listener = mock.Mock()
        listener.accept.return_value = (connection, None)
        stopped = threading.Event()
        stopped.set()
        state = {"initrd": False, "root": False, "complete": False}
        qemu.receive(listener, stopped, time.monotonic() + 1, state)
        self.assertFalse(state["complete"])
        self.assertEqual(state["protocol_error"], "missing-eof")

    def test_all_capability_sets_are_zero_after_the_one_way_reexec(self):
        status = "".join(name + ":\t0000000000000000\n" for name in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"))
        status += "NoNewPrivs:\t1\nSeccomp:\t2\n"
        with mock.patch.object(guard, "CAP_MASK", 0):
            guard.status_check(status, filtered=True)
            for field in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"):
                with self.subTest(field=field), self.assertRaises(ValueError):
                    guard.status_check(status.replace(field + ":\t0000000000000000", field + ":\t0000000000000001"), filtered=True)

    def test_mapping_probe_precedes_irreversible_all_caps_drop(self):
        class Reexec(BaseException):
            pass
        ownership = mock.Mock()
        checker = mock.Mock()
        api = types.SimpleNamespace(read=lambda *args: b"{}")
        modules = {"boot-uki": api, "ownership": ownership, "namespace-check": checker, "pid-check": checker}
        flags = types.SimpleNamespace(isolated=1, no_site=1, dont_write_bytecode=1)
        def execute(path, args, env):
            ownership.check.assert_called_once_with({})
            self.assertEqual(path, "/usr/bin/setpriv")
            self.assertEqual(args[:len(qemu.DROP_CAPS)], list(qemu.DROP_CAPS))
            self.assertEqual(args[-1], "--confined")
            self.assertEqual(env, qemu.environment())
            raise Reexec
        with mock.patch.object(qemu, "module", side_effect=modules.__getitem__), \
             mock.patch.object(qemu.sys, "platform", "linux"), mock.patch.object(qemu.sys, "version_info", (3, 12)), \
             mock.patch.object(qemu.sys, "flags", flags), mock.patch.object(qemu.sys, "argv", ["worker", *(["namespace"] * 5)]), \
             mock.patch.object(qemu.os, "execve", side_effect=execute), self.assertRaises(Reexec):
            qemu.main()

    def test_reader_drains_queued_frames_after_child_exit(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = str(Path(temporary).resolve() / "proof.sock")
            state = {"initrd": False, "root": False, "complete": False}
            stopped = threading.Event()
            stopped.set()
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                server.bind(path)
                server.listen(1)
                server.settimeout(0.1)
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.connect(path)
                    client.sendall(qemu.EXPECTED)
                qemu.receive(server, stopped, time.monotonic() + 1, state)
            self.assertEqual(state, {"initrd": True, "root": True, "complete": True})

    def test_fixed_command_has_no_acceleration_network_console_or_user_paths(self):
        args = qemu.command()
        self.assertEqual(args[args.index("-accel") + 1], "tcg")
        for key in ("-nic", "-monitor", "-serial", "-display"):
            self.assertEqual(args[args.index(key) + 1], "none")
        self.assertEqual(args[args.index("-smp") + 1], "2")
        self.assertEqual(args[args.index("-m") + 1], "1024")
        self.assertIn("-nodefaults", args)
        self.assertIn("-no-user-config", args)
        self.assertIn("--bounding-set=-all", qemu.DROP_CAPS)
        self.assertEqual(args[:5], [qemu.LOADER, "--inhibit-cache", "--library-path", qemu.LIBRARIES, qemu.QEMU])
        self.assertIn("virtio-blk-pci,drive=root,logical_block_size=4096,physical_block_size=4096", args)
        self.assertIn("if=none,id=root,format=raw,readonly=on,file=/input/root.raw", args)
        self.assertFalse(any(term in " ".join(args) for term in ("kvm", "vfio", "vhost", "hostfwd", "ssh", "-append")))
        self.assertEqual(set(qemu.environment()), {"PATH", "HOME", "LANG", "LC_ALL", "QEMU_MODULE_DIR"})

    def test_startup_gate_rejects_missing_properties_stderr_failure_or_overflow(self):
        outputs = (b"QEMU emulator version 6.2.0\n", b"tcg\n", b"logical_block_size physical_block_size\n",
                   b"chardev name\n", b"hpet smm\n")
        for bad in (None, "missing", "stderr", "exit", "overflow"):
            calls = []
            def execute(args, **kwargs):
                index = len(calls)
                calls.append(args)
                self.assertEqual(kwargs["timeout"], 10)
                self.assertEqual(kwargs["max_output_bytes"], 65536)
                if bad == "overflow": raise ValueError("synthetic overflow")
                return subprocess.CompletedProcess(args, 1 if bad == "exit" else 0,
                    b"unknown" if bad == "missing" else outputs[index], b"diagnostic" if bad == "stderr" else b"")
            if bad is None:
                self.assertEqual(len(qemu.startups(execute, [])), 5)
            else:
                with self.subTest(bad=bad), self.assertRaises((ValueError, subprocess.SubprocessError)):
                    qemu.startups(execute, [])

    def test_real_unix_socket_requires_exact_frames_and_clean_exit(self):
        for bad in (None, "wrong", "truncated", "extra", "duplicate", "no-connect", "exit", "stderr", "timeout"):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary).resolve() / "proof.sock"
                def execute(args, **kwargs):
                    if bad == "no-connect": return subprocess.CompletedProcess(args, 1, b"", b"")
                    if bad == "timeout": raise subprocess.TimeoutExpired(args, 0.1)
                    raw = {"wrong": b"private arbitrary output", "truncated": qemu.EXPECTED[:-1],
                           "extra": qemu.EXPECTED + b"X", "duplicate": qemu.EXPECTED * 2}.get(bad, qemu.EXPECTED)
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                        client.connect(str(path))
                        client.sendall(raw)
                    return subprocess.CompletedProcess(args, 1 if bad == "exit" else 0,
                                                       b"", b"diagnostic" if bad == "stderr" else b"")
                with mock.patch.object(qemu, "SOCKET", path), mock.patch.object(qemu, "TIMEOUT", 1):
                    state = {"initrd": False, "root": False, "complete": False}
                    terminal = {}
                    if bad is None:
                        self.assertEqual(qemu.trial(execute, state, terminal), {"initrd": True, "root": True, "complete": True})
                    else:
                        with self.assertRaises((ValueError, subprocess.SubprocessError)): qemu.trial(execute, state, terminal)
                    if bad in ("exit", "stderr"):
                        self.assertEqual(state, {"initrd": True, "root": True, "complete": True})
                        self.assertTrue(terminal["process_group_cleaned"])
                    if bad == "timeout":
                        self.assertEqual(terminal["failure_kind"], "timeout")
                self.assertFalse(path.exists())


class UkiTests(unittest.TestCase):
    def test_network_failure_observer_is_explicit_debug_only_and_failclosed(self):
        debug = {name: (ROOT / name).read_bytes() for name in host.GUEST}
        default = host.initrd_units(debug, False)
        selected = host.initrd_units(debug, True)
        self.assertEqual(default["nib-debug-boot-stage.service"], debug["guest/debugboot/nib-debug-boot-stage.service"])
        self.assertEqual(selected["nib-debug-boot-stage.service"], default["nib-debug-boot-stage.service"].replace(
            b" --stage-initrd\n", b" --stage-initrd-network\n"))
        self.assertEqual(selected["10-nib-debug-boot.conf"], default["10-nib-debug-boot.conf"])
        for bad in (b"unknown", default["nib-debug-boot-stage.service"] * 2):
            with self.assertRaises(ValueError): host.initrd_units({**debug, "guest/debugboot/nib-debug-boot-stage.service": bad}, True)
        config = configparser.ConfigParser(interpolation=None)
        config.read_string(debug["guest/debugboot/nib-debug-network-failure.service"].decode())
        self.assertEqual(config["Unit"]["OnSuccess"], "nib-failclosed.target")
        self.assertEqual(config["Unit"]["OnFailure"], "nib-failclosed.target")
        self.assertEqual(config["Unit"]["Before"], "nib-failclosed.target")
        self.assertNotIn("Requires", config["Unit"])
        self.assertNotIn("After", config["Unit"])
        service = config["Service"]
        self.assertEqual(service["ExecStart"], "/run/nib-debug-boot --network-failure")
        self.assertEqual(service["CapabilityBoundingSet"], "")
        self.assertEqual(service["NoNewPrivileges"], "yes")
        self.assertEqual(service["TimeoutStartSec"], "2")
        self.assertEqual(service["TimeoutStopSec"], "2")
        for stream in ("StandardInput", "StandardOutput", "StandardError"): self.assertEqual(service[stream], "null")
        self.assertEqual(debug["guest/debugboot/90-nib-debug-network-failure.conf"].decode().split("[Unit]\n")[1],
                         "OnFailure=\nOnFailure=nib-debug-network-failure.service\n")
        self.assertIn("OnFailure=nib-failclosed.target", (ROOT / "guest/systemd/nib-network.service").read_text())

    @contextmanager
    def fixture(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            payload, output = root / "payload", root / "out"
            payload.mkdir(); output.mkdir()
            probe = b"\x7fELF\x02\x01\x01synthetic"
            (payload / "probe").write_bytes(probe)
            units = {}
            for name in uki.UNITS:
                raw = (ROOT / "guest/debugboot" / name).read_bytes()
                (payload / name).write_bytes(raw)
                units[name] = boot.identity(raw)
            config = {"schema": "nib.debug-uki-input/v1", "probe": boot.identity(probe), "units": units,
                      "root_hash": "ab" * 32, "original": {"uki": {"image": boot.identity(b"original")}}}
            with mock.patch.object(boot, "WORK", root / "work"), \
                 mock.patch.object(boot, "ROOTFS", root / "output/rootfs"), \
                 mock.patch.object(uki, "PAYLOAD", payload), mock.patch.object(uki, "OUTPUT", output):
                yield root, config

    def test_supplement_exact_files_modes_and_existing_cpio_verifier(self):
        with self.fixture() as (_, config):
            expected = uki.supplement(boot, config)
            regular = {name for name, row in expected.items() if row["kind"] == "file"}
            self.assertEqual(regular, {"usr/libexec/nib-debug-boot", *uki.UNITS.values()})
            self.assertEqual(expected["usr/libexec/nib-debug-boot"]["mode"], 0o500)
            raw = fixture_cpio(boot.WORK / "tree", expected)
            boot.check_cpio(raw, expected)
            self.assertEqual((boot.WORK / "file-list").read_bytes(), b"".join(n.encode() + b"\0" for n in sorted(expected)))
            with self.assertRaises(FileExistsError): uki.supplement(boot, config)

    def test_changed_probe_or_unit_rejected(self):
        for name in ("probe", *uki.UNITS):
            with self.subTest(name=name), self.fixture() as (_, config):
                (uki.PAYLOAD / name).write_bytes(b"changed")
                with self.assertRaises(ValueError): uki.supplement(boot, config)

    def test_failed_cpio_or_inspection_never_publishes_debug_image(self):
        for stage in ("cpio", "ukify", "inspect"):
            with self.subTest(stage=stage), self.fixture() as (_, config):
                def operation(processes, api, name):
                    if name == stage:
                        raise subprocess.TimeoutExpired([], 1)
                    if name == "cpio":
                        (boot.WORK / "initrd.cpio").write_bytes(fixture_cpio(boot.WORK / "tree", boot.snapshot()))
                    return subprocess.CompletedProcess([], 0, b"{}", b"")
                report = {"result": "fail"}
                with mock.patch.object(uki, "original", return_value={".initrd": b"original", ".osrel": b"ID=ubuntu\n"}), \
                     mock.patch.object(uki, "operation", side_effect=operation), self.assertRaises(subprocess.TimeoutExpired):
                    uki.build(boot, None, None, config, report)
                self.assertEqual(report["result"], "fail")
                self.assertEqual(report["stage"], "supplement" if stage == "cpio" else stage)
                self.assertFalse((uki.OUTPUT / "debug.efi").exists())

    def test_original_rejects_wrong_receipt_root_image_kernel_and_cmdline(self):
        for bad in ("root", "image", "kernel", "cmdline", "schema", "sections"):
            with self.subTest(bad=bad), self.fixture() as (_, config):
                report = {"schema": "nib.private-boot-uki/v1", "result": "pass", "root_hash": config["root_hash"],
                          "sections_checked": True, "uki": {"image": boot.identity(b"original")}}
                config["original"] = report
                parts = {".cmdline": boot.cmdline(config["root_hash"]), ".uname": boot.KERNEL.encode(), ".linux": b"kernel"}
                if bad == "root": report["root_hash"] = "cd" * 32
                if bad == "image": report["uki"]["image"] = boot.identity(b"other")
                if bad == "cmdline": parts[".cmdline"] += b" rw"
                if bad == "schema": report["schema"] = "unknown"
                if bad == "sections": report["sections_checked"] = False
                with mock.patch.object(boot, "read", return_value=b"original"), \
                     mock.patch.object(uki, "sections", return_value=parts), self.assertRaises(ValueError):
                    uki.original(boot, None, config)

    def test_build_appends_original_bytes_reinspects_and_exports_distinct_image(self):
        with self.fixture() as (_, config):
            parts = {".initrd": b"original initrd", ".osrel": b"ID=ubuntu\n"}
            sections = {}
            def operation(processes, api, name):
                if name == "cpio":
                    expected = boot.snapshot()
                    (boot.WORK / "initrd.cpio").write_bytes(fixture_cpio(boot.WORK / "tree", expected))
                elif name == "ukify":
                    self.assertTrue((boot.WORK / "initrd.cpio").read_bytes().startswith(parts[".initrd"]))
                    self.assertEqual((boot.WORK / "cmdline").read_bytes(), boot.cmdline(config["root_hash"]) + uki.SUFFIX)
                    (boot.WORK / "nib.efi").write_bytes(b"distinct debug image")
                    sections.update(schema="nib.uki-sections/v1", image=boot.identity(b"distinct debug image"),
                        sections={".initrd": boot.identity((boot.WORK / "initrd.cpio").read_bytes()),
                                  ".cmdline": boot.identity((boot.WORK / "cmdline").read_bytes())}, signed=False)
                return subprocess.CompletedProcess([], 0, json.dumps(sections).encode(), b"")
            with mock.patch.object(uki, "original", return_value=parts), \
                 mock.patch.object(uki, "operation", side_effect=operation), \
                 mock.patch.object(boot, "inspect_uki", side_effect=lambda pe: sections):
                report = uki.build(boot, None, None, config, {"boot_verified": False})
            self.assertEqual(report["result"], "pass")
            self.assertFalse(report["boot_verified"])
            self.assertEqual((uki.OUTPUT / "debug.efi").read_bytes(), b"distinct debug image")


class LifecycleTests(unittest.TestCase):
    def test_independent_producer_audit_runs_unprivileged_and_bounds_both_streams(self):
        for bad in (None, "exit", "stderr", "sha", "self", "timeout"):
            value = copy.deepcopy(PRODUCER)
            if bad == "sha": value["source"]["git_sha"] = None
            if bad == "self": value["debug_source"]["sources"][wrapper.SCRIPT] = "b" * 64
            child = mock.Mock(returncode=1 if bad == "exit" else 0)
            output = {"return_value": (json.dumps(value).encode(), b"diagnostic" if bad == "stderr" else b"")}
            if bad == "timeout": output = {"side_effect": subprocess.TimeoutExpired([], 120)}
            with self.subTest(bad=bad), mock.patch.object(wrapper.subprocess, "Popen", return_value=child) as popen, \
                 mock.patch.object(wrapper.base.processes, "bounded_output", **output) as bounded, \
                 mock.patch.object(wrapper.base.processes, "cleanup_process_group") as cleanup:
                if bad is None:
                    self.assertEqual(wrapper.capture_producer(1001, 1002), PRODUCER)
                else:
                    with self.assertRaises((ValueError, subprocess.SubprocessError)):
                        wrapper.capture_producer(1001, 1002)
            cleanup.assert_called_once_with(child)
            self.assertEqual(popen.call_args.args[0][-1], "--source-only")
            self.assertEqual(popen.call_args.kwargs["user"], 1001)
            self.assertEqual(popen.call_args.kwargs["group"], 1002)
            self.assertEqual(popen.call_args.kwargs["extra_groups"], [])
            self.assertEqual(bounded.call_args.args[2:], (120, None, 65536, 65536))

    def test_execute_checks_exit_oom_limits_and_independent_cleanup(self):
        for bad in (None, "exit", "timeout", "cancel", "oom", "limits", "empty", "removed"):
            @contextmanager
            def cgroup(state):
                state["limits_read_back"] = bad != "limits"
                try:
                    yield 19, "synthetic"
                finally:
                    state.update(empty_verified=bad != "empty", group_removed=bad != "removed",
                                 oom_kill=1 if bad == "oom" else 0)
            def launch(*args):
                if bad == "timeout": raise subprocess.TimeoutExpired([], 1)
                if bad == "cancel": raise KeyboardInterrupt
                return 1 if bad == "exit" else 0
            with self.subTest(bad=bad), mock.patch.object(wrapper.resources, "cgroup", cgroup), \
                 mock.patch.object(wrapper, "launch", side_effect=launch), mock.patch.object(wrapper.os, "fchmod"):
                state = wrapper.execute(1001, 1001, [])
            self.assertEqual(state["result"], "pass" if bad is None else "fail")
            self.assertEqual(state["empty_verified"], bad != "empty")

    def test_root_child_joins_cgroup_then_drops_privileges_and_environment(self):
        child = mock.Mock()
        child.wait.return_value = 0
        child.poll.return_value = 0
        with mock.patch.object(wrapper.os, "open", return_value=22), mock.patch.object(wrapper.os, "close"), \
             mock.patch.object(wrapper.subprocess, "Popen", return_value=child) as popen:
            self.assertEqual(wrapper.launch(19, 1001, 1002, ["--go", "/fixed/compiler"]), 0)
        args, kwargs = popen.call_args
        self.assertEqual(args[0][0:5], ["/usr/bin/python3.10", "-I", "-S", "-B", "-c"])
        self.assertIs(args[0][5], wrapper.build.CHILD)
        self.assertEqual(args[0][6:10], ["22", "1001", "1002", str(ROOT / "host/debug-boot.py")])
        self.assertEqual(kwargs["pass_fds"], (22,))
        self.assertTrue(kwargs["start_new_session"])
        self.assertEqual(set(kwargs["env"]), {"PATH", "HOME", "LANG", "LC_ALL"})
        child.wait.assert_called_once_with(timeout=900)

    def test_separate_limits_really_reach_reused_cgroup_globals(self):
        limits = wrapper.resources.cgroup.__wrapped__.__globals__["LIMITS"]
        self.assertEqual(limits, wrapper.resources.LIMITS)
        self.assertEqual(limits["memory.max"], str(2 << 30))
        self.assertEqual(limits["pids.max"], "128")
        self.assertEqual(wrapper.build.resources.LIMITS["memory.max"], str(8 << 30))

    def test_terminal_failure_keeps_valid_worker_diagnostic(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary).resolve()
            value = {"result": "fail", "stage": "boot", "proof": {"initrd": True, "root": False, "complete": False,
                "guest_failure": "NETWORK_SERVICE", "network_failure": {
                    "service_result": "exit-code", "exit_code": "exited", "exit_status": 70, "stage": "executable"}}}
            (output / "qemu.json").write_text(json.dumps(value))
            for failure in (subprocess.CompletedProcess([], 1, b"", b""), subprocess.TimeoutExpired([], 1),
                            ValueError("synthetic cleanup failure")):
                kwargs = {"side_effect": failure} if isinstance(failure, Exception) else {"return_value": failure}
                with self.subTest(failure=type(failure).__name__), mock.patch.object(host.base.processes, "execute_process_group", **kwargs):
                    self.assertEqual(host.execute_worker([], output, "qemu.json", 1), (value, False))

    def test_metadata_only_after_cleanup_on_success_and_failure(self):
        for failed in (False, True):
            with self.subTest(failed=failed), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                private, bundle = root / "private", root / "bundle"
                private.mkdir(mode=0o700); bundle.mkdir(mode=0o700)
                (private / "nested").mkdir()
                (private / "nested/image").write_bytes(b"image bytes")
                (bundle / "root.raw").write_bytes(b"original image")
                report = {"schema": "nib.debug-boot/v1", "result": "fail" if failed else "pass", "stage": "complete",
                          "boot_verified": not failed, "confidentiality_verified": False,
                          "source": PRODUCER["source"], "debug_source": PRODUCER["debug_source"]}
                (root / "worker.json").write_text(json.dumps(report))
                state = {"result": report["result"], "empty_verified": True, "group_removed": True}
                with mock.patch.object(wrapper, "DIRECTORY", root), mock.patch.object(wrapper, "PRIVATE", private), \
                     mock.patch.object(wrapper, "BUNDLE", bundle):
                    outcome = wrapper.finish(state, private.stat(), bundle.stat(), "2026-10-22T00:00:00Z", PRODUCER)
                self.assertEqual(outcome, report["result"])
                self.assertFalse(private.exists()); self.assertFalse(bundle.exists())
                self.assertEqual({p.name for p in (root / "metadata").iterdir()}, {"validation.json", "resources.json", "worker.json", "run.json"})
                self.assertEqual(evidence_producer((root / "metadata/run.json").read_bytes()), "a" * 40)

    def test_success_missing_and_killed_worker_envelopes_pass_repository_validator(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary).resolve()
            for outcome in ("pass", "missing", "killed", "source-mismatch"):
                root = fixture / outcome
                root.mkdir(mode=0o700)
                private, bundle = root / "private", root / "bundle"
                private.mkdir(mode=0o700); bundle.mkdir(mode=0o700)
                report = {"schema": "nib.debug-boot/v1", "result": "pass", "stage": "complete", "boot_verified": True,
                          "confidentiality_verified": False, "source": PRODUCER["source"], "debug_source": PRODUCER["debug_source"]}
                if outcome == "killed":
                    report.update(result="fail", stage="boot", boot_verified=False, qemu={"proof": {"initrd": True, "root": False}})
                if outcome == "source-mismatch": report["source"] = {"git_sha": "b" * 40, "sources": {}}
                raw = json.dumps(report, separators=(",", ":")).encode() + b"\n"
                if outcome != "missing": (root / "worker.json").write_bytes(raw)
                state = {"result": "fail" if outcome in ("missing", "killed") else "pass", "child_exit_code": -9 if outcome == "killed" else 0,
                         "empty_verified": True, "group_removed": True, "oom_kill": 0}
                with mock.patch.object(wrapper, "DIRECTORY", root), mock.patch.object(wrapper, "PRIVATE", private), \
                     mock.patch.object(wrapper, "BUNDLE", bundle):
                    result = wrapper.finish(state, private.stat(), bundle.stat(), "2026-10-22T00:00:00Z", PRODUCER)
                self.assertEqual(result, "pass" if outcome == "pass" else "fail")
                record = (root / "metadata/run.json").read_bytes()
                self.assertEqual(evidence_producer(record), "a" * 40)
                run = json.loads(record)
                self.assertEqual(run["result"], result)
                self.assertEqual((run["phase"], run["task"], run["execution"]), ("P3", "T1", "software"))
                self.assertFalse(run["hardware_validated"])
                self.assertEqual(json.loads((root / "metadata/resources.json").read_bytes())["producer"], PRODUCER)
                if outcome == "missing":
                    self.assertEqual(json.loads((root / "metadata/worker.json").read_bytes())["stage"], "worker-missing")
                else:
                    self.assertEqual((root / "metadata/worker.json").read_bytes(), raw)
                    self.assertEqual((root / "worker.json").read_bytes(), raw)
                destination = fixture / "evidence/p3" / outcome
                destination.mkdir(parents=True)
                (destination / "run.json").write_bytes(record)
            checked = subprocess.run(["go", "run", "./cmd/nib", "lint", str(fixture)], cwd=ROOT,
                env={**os.environ, "GOTOOLCHAIN": "local", "GOPROXY": "off", "GOSUMDB": "off"},
                capture_output=True, timeout=120)
            self.assertEqual(checked.returncode, 0, checked.stderr.decode())

    def test_unknown_cleanup_does_not_remove_images_or_authorize_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            private, bundle = root / "private", root / "bundle"
            private.mkdir(); bundle.mkdir()
            state = {"empty_verified": False, "group_removed": False}
            with mock.patch.object(wrapper, "DIRECTORY", root), mock.patch.object(wrapper, "PRIVATE", private), \
                 mock.patch.object(wrapper, "BUNDLE", bundle), self.assertRaises(ValueError):
                wrapper.finish(state, private.stat(), bundle.stat(), "synthetic", PRODUCER)
            self.assertTrue(private.exists()); self.assertTrue(bundle.exists())
            self.assertFalse((root / "metadata").exists())

    def test_replaced_directory_and_cleanup_failure_never_authorize_upload(self):
        for bad in ("replacement", "cleanup", "metadata-collision"):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                private, bundle = root / "private", root / "bundle"
                private.mkdir(mode=0o700); bundle.mkdir(mode=0o700)
                identity = private.stat()
                if bad == "replacement":
                    private.rename(root / "old-private")
                    private.mkdir(mode=0o700)
                if bad == "metadata-collision": (root / "metadata").mkdir()
                state = {"result": "fail", "empty_verified": True, "group_removed": True}
                delete = mock.patch.object(wrapper.shutil, "rmtree", side_effect=OSError("synthetic"))
                if bad == "cleanup":
                    delete.start()
                    wrapper.shutil.rmtree.avoids_symlink_attacks = True
                try:
                    with mock.patch.object(wrapper, "DIRECTORY", root), mock.patch.object(wrapper, "PRIVATE", private), \
                         mock.patch.object(wrapper, "BUNDLE", bundle), self.assertRaises((OSError, ValueError)):
                        wrapper.finish(state, identity, bundle.stat(), "synthetic", PRODUCER)
                    self.assertFalse((root / "metadata/validation.json").exists())
                finally:
                    if bad == "cleanup": delete.stop()

    def test_sandbox_uses_readonly_authenticated_roots_and_fixed_worker(self):
        with mock.patch.object(host.os, "readlink", side_effect=lambda path: "synthetic:[1]"), \
             mock.patch.object(host.native.probe.ownership, "prefix", return_value=["mapped-prefix"]):
            args = host.sandbox(Path("/tools"), Path("/payload"), Path("/input"), Path("/out"), {}, "debug-qemu.py", Path("/runtime"))
            with self.assertRaises(ValueError):
                host.sandbox(Path("/tools"), Path("/payload"), Path("/input"), Path("/out"), {}, "caller-command")
        pairs = [(args[i + 1], args[i + 2]) for i, v in enumerate(args) if v == "--ro-bind"]
        self.assertIn(("/runtime", "/runtime"), pairs)
        self.assertIn(("/tools", "/"), pairs)
        self.assertIn(("/payload", "/nib"), pairs)
        self.assertEqual([(args[i + 1], args[i + 2]) for i, v in enumerate(args) if v == "--bind"], [("/out", "/out")])
        for flag in ("--unshare-net", "--unshare-ipc", "--pid", "--mount-proc=/proc"):
            self.assertIn(flag, args)
        self.assertNotIn("--bounding-set=-all,+chown", " ".join(args))

    def test_committed_gate_binds_loaded_helpers_and_exact_workflow(self):
        debug = {host.SCRIPT: host.SELF, host.runtime.SCRIPT: host.RUNTIME_SOURCE, host.validator.SCRIPT: host.VALIDATOR_SOURCE}
        for bad in (None, "revision", "runtime", "validator", "self"):
            value = dict(debug)
            key = {"runtime": host.runtime.SCRIPT, "validator": host.validator.SCRIPT, "self": host.SCRIPT}.get(bad)
            if key is not None: value[key] += b"\n"
            with self.subTest(bad=bad), mock.patch.object(host.native, "committed_sources", return_value=("a" * 40, {})), \
                 mock.patch.object(host.tools, "committed", return_value=("b" * 40 if bad == "revision" else "a" * 40, value)), \
                 mock.patch.object(host, "workflow_source", return_value=b"workflow"):
                if bad is None:
                    self.assertEqual(host.committed()[2][host.WORKFLOW], b"workflow")
                else:
                    with self.assertRaises(ValueError): host.committed()

    def test_fetch_only_exact_public_snapshot_paths_and_bounds(self):
        lock = json.loads((ROOT / fetch.runtime.LOCK).read_bytes())
        items = fetch.plan(lock)
        self.assertEqual(len(items), 127)
        self.assertTrue(all(url.startswith(fetch.runtime.SNAPSHOT) for url, _, _ in items.values()))
        for mutation in ("metadata-url", "package-url", "digest", "size"):
            bad = copy.deepcopy(lock)
            if mutation == "metadata-url": bad["indexes"][0]["url"] += "?secret"
            elif mutation == "package-url": bad["packages"][0]["Filename"] = "../private.deb"
            elif mutation == "digest": bad["packages"][0]["SHA256"] = "../private"
            else: bad["packages"][0]["Size"] = str(97 << 20)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): fetch.plan(bad)


class WorkflowTests(unittest.TestCase):
    def test_explicit_hosted_one_shot_with_no_binary_or_recursive_upload(self):
        workflow = yaml.safe_load((ROOT / host.WORKFLOW).read_bytes())
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        self.assertIs(workflow[True]["workflow_dispatch"]["inputs"]["debug_boot"]["default"], False)
        job = workflow["jobs"]["boot"]
        self.assertEqual(job["if"], "github.ref == 'refs/heads/main' && inputs.debug_boot")
        self.assertEqual(job["runs-on"], "ubuntu-22.04")
        self.assertEqual(job["timeout-minutes"], 35)
        steps = job["steps"]
        build = next(s for s in steps if s.get("id") == "build")
        self.assertIn("600s", build["run"])
        for flag in ("--guest-runtime", "--compile-driver", "--install-driver", "--verity-root", "--boot-uki", "--export-boot"):
            self.assertIn(flag, build["run"])
        debug = next(s for s in steps if s.get("id") == "debug")
        self.assertIn("host/run-debug-boot.py", debug["run"])
        self.assertLess(debug["run"].index("case \"$result\""), debug["run"].index("metadata_ready=true"))
        self.assertIn('test "$result" = pass', debug["run"])
        self.assertNotIn("always()", debug.get("if", ""))
        cleanup = next(s for s in steps if s.get("id") == "unused_export")
        self.assertEqual(cleanup["if"], "${{ always() && steps.debug.outcome == 'skipped' }}")
        uploads = [s for s in steps if s.get("uses", "").startswith("actions/upload-artifact@")]
        self.assertEqual(len(uploads), 2)
        self.assertEqual(uploads[0]["with"]["path"].splitlines(), ["build/debug-boot/metadata/" + n + ".json"
                         for n in ("validation", "resources", "worker", "run")])
        for step in uploads:
            self.assertIn("metadata_ready == 'true'", step["if"])
            self.assertNotIn("*", step["with"]["path"])
            self.assertTrue(all(p.endswith(".json") for p in step["with"]["path"].splitlines()))
        raw = (ROOT / host.WORKFLOW).read_text()
        self.assertNotIn("secrets.", raw)
        self.assertNotIn("self-hosted", raw)


if __name__ == "__main__":
    unittest.main()
