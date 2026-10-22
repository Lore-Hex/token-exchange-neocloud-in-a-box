# SPDX-License-Identifier: Apache-2.0
"""Offline fixture tests; never execute vendor binaries or change host state."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

import mkosi_appraisal_probe as probe
import mkosi_confinement as guard
import mkosi_dkms as dkms


class AppraisalProbeTests(unittest.TestCase):
    def test_original_failure_is_codec_import_not_driver_or_gpu_failure(self):
        root = Path(__file__).resolve().parents[1] / "evidence/p3/2026-10-22-appraisal-codec-failure"
        expected = {
            "appraisal-cli-help.stderr": "488f7a2fe2a8aa3b72a410f1d14347098996d060337d91de89c5bd31c44b2089",
            "evidence-resources.json": "d28746c24a37601a955402e7ad31efd6f76d11730ea24ca59ea460fcf19142b4",
            "result.json": "6313ba734bf0e1d99be921b41ff44489f90a478216d08c4128d21dc0164ae6ef",
            "run.json": "feb47feb1d186ddf768c05eadbf08b2f6e9c7dc62584384864bdd5106e26364d",
        }
        for name, digest in expected.items():
            self.assertEqual(hashlib.sha256((root / name).read_bytes()).hexdigest(), digest)
        self.assertIn("LookupError: unknown encoding: ascii", (root / "appraisal-cli-help.stderr").read_text())
        result = json.loads((root / "result.json").read_bytes())
        worker = result["worker"]
        self.assertEqual(worker["stage"], "appraisal-cli")
        self.assertEqual(worker["installed_packages"], 212)
        self.assertEqual(worker["driver_build"]["result"], "pass")
        self.assertTrue(worker["driver_build"]["installed"])
        self.assertEqual(worker["runtime_policy"]["result"], "pass")
        self.assertTrue(result["process_cleanup_confirmed"])
        self.assertTrue(result["payload_removed"])
        self.assertEqual(result["result"], "fail")
        self.assertFalse(result["bootable"])
        self.assertFalse(result["deployable"])

    @contextmanager
    def fixture(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            pins = {}
            for name in probe.FILES:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                data = ("fixture:" + name).encode()
                path.write_bytes(data)
                pins[name] = (len(data), hashlib.sha256(data).hexdigest())
            (root / probe.ALIAS).symlink_to("libnvat.so.2.0.0")
            (root / "etc").mkdir()
            with mock.patch.object(probe, "ROOTFS", root), mock.patch.object(probe, "FILES", pins):
                yield root

    def test_exact_payload_and_alias(self):
        with self.fixture() as root:
            result = probe.verify_files(dkms.regular, root)
            for name, (size, digest) in probe.FILES.items():
                self.assertEqual(result[name], {"bytes": size, "sha256": digest})
            alias = root / probe.ALIAS
            alias.unlink()
            alias.symlink_to("../../../../outside")
            with self.assertRaises(ValueError):
                probe.verify_files(dkms.regular, root)

    def test_malformed_payloads_and_preload_reject(self):
        for kind in ("bytes", "symlink", "hardlink", "fifo", "preload", "preload-link"):
            with self.subTest(kind=kind), self.fixture() as root:
                path = root / "usr/bin/nvattest"
                if kind == "bytes":
                    path.write_bytes(b"changed")
                elif kind in ("symlink", "hardlink", "fifo"):
                    target = root / "sentinel"
                    path.rename(target)
                    if kind == "symlink":
                        path.symlink_to(target)
                    elif kind == "hardlink":
                        os.link(target, path)
                    else:
                        os.mkfifo(path)
                elif kind == "preload":
                    (root / "etc/ld.so.preload").write_bytes(b"")
                else:
                    (root / "etc/ld.so.preload").symlink_to("missing")
                with self.assertRaises((ValueError, OSError)):
                    probe.verify_files(dkms.regular, root)

    def test_wrong_root_and_parent_symlink_reject(self):
        reader = mock.Mock()
        with self.assertRaises(ValueError):
            probe.verify_files(reader, Path("/"))
        reader.assert_not_called()
        with self.fixture() as root:
            directory = root / "usr/lib/x86_64-linux-gnu"
            directory.rename(root / "outside")
            directory.symlink_to(root / "outside")
            with self.assertRaises(ValueError):
                probe.verify_files(dkms.regular, root)

    def test_exact_help_operations_and_readonly_boundary(self):
        expected = {
            "help": ["/usr/bin/nvattest", "--help"],
            "attest-help": ["/usr/bin/nvattest", "attest", "--help"],
            "token-help": ["/usr/bin/nvattest", "verify-token", "--help"],
        }
        for name, argv in expected.items():
            self.assertEqual(probe.operation(name), argv)
            command = probe.command(name, guard.PREFIX)
            self.assertNotIn("--bind", command)
            self.assertIn("--ro-bind", command)
            self.assertIn(str(probe.ROOTFS) + "/dev", command)
            self.assertIn(str(probe.ROOTFS) + "/tmp", command)
            start = command.index(guard.PREFIX[0])
            self.assertEqual(command[start:start + len(guard.PREFIX)], list(guard.PREFIX))
            self.assertEqual(command[-2:], ["/nib/appraisal-probe.py", name])
        for name in ("attest", "collect-evidence", "verify-token", "--help", "../help", "", None):
            with self.subTest(name=name), self.assertRaises(ValueError):
                probe.operation(name)

    def test_help_validation_requires_real_options_not_substrings(self):
        for name, (_, flags) in probe.OPERATIONS.items():
            raw = "".join("  " + flag + " VALUE\n" for flag in flags).encode()
            result = probe.validate_help(name, raw)
            self.assertEqual(result, {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
            for bad in (b"", b"x" * 65537, b"\xff", raw.replace(flags[0].encode(), b"--wrong"),
                        raw.replace(b"\n  --", b"\n  prefix--").replace(b"  --", b"  prefix--")):
                with self.subTest(name=name, bad=bad[:30]), self.assertRaises(ValueError):
                    probe.validate_help(name, bad)

    def test_probe_bounds_output_time_and_claims(self):
        def execute(argv, **kwargs):
            self.assertEqual(kwargs["timeout"], 10)
            self.assertEqual(kwargs["max_output_bytes"], 65536)
            self.assertEqual(kwargs["max_stderr_bytes"], 65536)
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
            self.assertEqual(set(kwargs["env"]), {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL"})
            flags = probe.OPERATIONS[argv[-1]][1]
            return subprocess.CompletedProcess(argv, 0, "".join("  " + f + " X\n" for f in flags).encode(), b"")
        processes = types.SimpleNamespace(execute_process_group=mock.Mock(side_effect=execute))
        with mock.patch.object(probe, "verify_files", return_value={"fixture": {}}) as verify:
            result = probe.probe(processes, guard.PREFIX, dkms.regular)
        self.assertEqual(processes.execute_process_group.call_count, 3)
        self.assertEqual(verify.call_count, 2)
        self.assertEqual(result["result"], "pass")
        for name in ("appraisal_performed", "gpu_access", "network_access", "boot_verified"):
            self.assertIs(result[name], False)

    def test_failure_stops_without_retry_or_positive_receipt(self):
        for value in (subprocess.CompletedProcess([], 1, b"", b"private error"),
                      subprocess.CompletedProcess([], 0, b"--format", b"unexpected"),
                      subprocess.TimeoutExpired("fixed", 10)):
            execute = mock.Mock(side_effect=value if isinstance(value, Exception) else None,
                                return_value=value)
            with mock.patch.object(probe, "verify_files", return_value={}) as verify:
                with self.assertRaises((ValueError, subprocess.SubprocessError)):
                    probe.probe(types.SimpleNamespace(execute_process_group=execute), guard.PREFIX, dkms.regular)
            self.assertEqual(execute.call_count, 1)
            self.assertEqual(verify.call_count, 1)

    def test_failed_fixed_help_retains_bounded_build_diagnostics(self):
        result = subprocess.CompletedProcess([], 7, b"public help", b"public package failure")
        processes = types.SimpleNamespace(execute_process_group=mock.Mock(return_value=result))
        diagnostics = {}
        with mock.patch.object(probe, "verify_files", return_value={}), \
                mock.patch.object(Path, "write_bytes") as write:
            with self.assertRaises(subprocess.CalledProcessError):
                probe.probe(processes, guard.PREFIX, dkms.regular,
                            report=diagnostics, output=Path("/out"))
        self.assertEqual(diagnostics, {
            "operation": "help", "exit_code": 7,
            "stdout_bytes": len(result.stdout), "stderr_bytes": len(result.stderr),
            "stdout_sha256": hashlib.sha256(result.stdout).hexdigest(),
            "stderr_sha256": hashlib.sha256(result.stderr).hexdigest(),
        })
        self.assertEqual(write.call_args_list, [mock.call(result.stdout), mock.call(result.stderr)])
        processes.execute_process_group.assert_called_once()

    def test_diagnostics_reject_other_output_directory(self):
        processes = types.SimpleNamespace(execute_process_group=mock.Mock(
            return_value=subprocess.CompletedProcess([], 7, b"", b"failure")))
        with mock.patch.object(probe, "verify_files", return_value={}), \
                mock.patch.object(Path, "write_bytes") as write:
            with self.assertRaises(ValueError):
                probe.probe(processes, guard.PREFIX, dkms.regular, output=Path("/tmp"))
        write.assert_not_called()

    def test_child_drops_all_capabilities_and_uses_clean_environment(self):
        fakeguard = types.SimpleNamespace(check=mock.Mock(), CAP_MASK=guard.CAP_MASK)
        fakefiles = types.SimpleNamespace(regular=mock.Mock())
        order = []
        with mock.patch.object(probe.sys, "platform", "linux"), \
                mock.patch.object(probe.sys, "version_info", (3, 12)), \
                mock.patch.object(probe.sys, "flags", types.SimpleNamespace(isolated=1, no_site=1, dont_write_bytecode=1)), \
                mock.patch.object(probe.sys, "argv", ["worker", "help"]), \
                mock.patch.object(probe.os, "geteuid", return_value=0), \
                mock.patch.object(probe.os, "getegid", return_value=0), \
                mock.patch.object(probe.socket, "if_nameindex", return_value=[(1, "lo")]), \
                mock.patch.object(probe, "module", side_effect=[fakeguard, fakefiles]), \
                mock.patch.object(probe, "verify_files") as verify, \
                mock.patch.object(probe, "prepare_chroot_runtime", side_effect=lambda: order.append("codec")) as prepare, \
                mock.patch.object(probe.os, "chroot", side_effect=lambda _: order.append("chroot")) as chroot, \
                mock.patch.object(probe.os, "chdir") as chdir, \
                mock.patch.object(probe, "drop_privileges") as drop, \
                mock.patch.object(probe.os, "execve") as execute:
            probe.main()
        fakeguard.check.assert_called_once_with()
        verify.assert_called_once_with(fakefiles.regular)
        prepare.assert_called_once_with()
        self.assertEqual(order, ["codec", "chroot"])
        chroot.assert_called_once_with(probe.ROOTFS)
        chdir.assert_called_once_with("/")
        drop.assert_called_once_with(guard.CAP_MASK)
        path, argv, environment = execute.call_args.args
        self.assertEqual(path, "/usr/bin/nvattest")
        self.assertEqual(argv, [path, "--help"])
        self.assertNotIn("LD_LIBRARY_PATH", environment)

    def test_ascii_io_without_importable_stdlib_requires_preparation(self):
        # Separate fresh interpreters prove the original lazy-import failure,
        # then the fix, without chroot privileges or borrowing warmed codecs.
        script = r'''
import codecs, encodings, io, runpy, sys
probe = runpy.run_path(sys.argv[1], run_name="fixture")
codecs.unregister(encodings.search_function)
codecs.register(encodings.search_function)
sys.modules.pop("encodings.ascii", None)
if sys.argv[2] == "warm":
    probe["prepare_chroot_runtime"]()
sys.meta_path.clear()
sys.path.clear()
try:
    stream = io.TextIOWrapper(io.BytesIO(b"Uid:\t65534\n"), encoding="ascii")
    assert stream.read() == "Uid:\t65534\n"
except LookupError:
    print("unavailable")
else:
    print("available")
'''
        for mode, expected in (("cold", b"unavailable\n"), ("warm", b"available\n")):
            with self.subTest(mode=mode):
                result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", script,
                                         str(Path(probe.__file__).resolve()), mode],
                                        capture_output=True, timeout=10, check=True)
                self.assertEqual(result.stdout, expected)
                self.assertEqual(result.stderr, b"")

    def test_unexpected_codec_identity_rejects(self):
        with mock.patch.object(probe.codecs, "lookup", return_value=types.SimpleNamespace(name="wrong")), \
                self.assertRaises(ValueError):
            probe.prepare_chroot_runtime()

    def status(self):
        return ("Uid:\t65534\t65534\t65534\t65534\nGid:\t65534\t65534\t65534\t65534\nGroups:\t\n"
                "CapInh:\t0000000000000000\nCapPrm:\t0000000000000000\nCapEff:\t0000000000000000\n"
                f"CapBnd:\t{guard.CAP_MASK:016x}\nCapAmb:\t0000000000000000\nNoNewPrivs:\t1\nSeccomp:\t2\n")

    def test_privilege_drop_requires_kernel_observed_unprivileged_state(self):
        text = self.status()
        probe.unprivileged_status(text, guard.CAP_MASK)
        for field in ("CapInh", "CapPrm", "CapEff", "CapAmb"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                probe.unprivileged_status(text.replace(field + ":\t0000000000000000",
                                                       field + ":\t0000000000000001"), guard.CAP_MASK)
        for before, after in (("Uid:\t65534", "Uid:\t0"), ("Gid:\t65534", "Gid:\t0"),
                              ("Groups:\t", "Groups:\t0"), ("NoNewPrivs:\t1", "NoNewPrivs:\t0"),
                              ("Seccomp:\t2", "Seccomp:\t0"), ("CapBnd:", "missing:")):
            with self.subTest(before=before), self.assertRaises(ValueError):
                probe.unprivileged_status(text.replace(before, after), guard.CAP_MASK)
        with self.assertRaises(ValueError):
            probe.unprivileged_status(text + "CapEff:\t0000000000000000\n", guard.CAP_MASK)

    def test_drop_order_is_groups_then_all_gids_then_all_uids_then_kernel_check(self):
        calls = []
        def record(name):
            return lambda *args: calls.append((name, args))
        with mock.patch.object(probe.os, "setgroups", side_effect=record("groups")), \
                mock.patch.object(probe.os, "setresgid", side_effect=record("gid"), create=True), \
                mock.patch.object(probe.os, "setresuid", side_effect=record("uid"), create=True), \
                mock.patch.object(probe.os, "getresuid", return_value=(65534,) * 3, create=True), \
                mock.patch.object(probe.os, "getresgid", return_value=(65534,) * 3, create=True), \
                mock.patch.object(probe.os, "getgroups", return_value=[]), \
                mock.patch("builtins.open", mock.mock_open(read_data=self.status())) as read:
            probe.drop_privileges(guard.CAP_MASK)
        self.assertEqual(calls, [("groups", ([],)), ("gid", (65534, 65534, 65534)), ("uid", (65534, 65534, 65534))])
        read.assert_called_once_with("/proc/self/status", encoding="ascii")


if __name__ == "__main__":
    unittest.main()
