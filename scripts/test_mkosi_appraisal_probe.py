# SPDX-License-Identifier: Apache-2.0
"""Offline fixture tests; never execute vendor binaries or change host state."""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest import mock

import mkosi_appraisal_probe as probe
import mkosi_confinement as guard
import mkosi_dkms as dkms


class AppraisalProbeTests(unittest.TestCase):
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

    def test_child_drops_all_capabilities_and_uses_clean_environment(self):
        fakeguard = types.SimpleNamespace(check=mock.Mock())
        fakefiles = types.SimpleNamespace(regular=mock.Mock())
        with mock.patch.object(probe.sys, "platform", "linux"), \
                mock.patch.object(probe.sys, "version_info", (3, 12)), \
                mock.patch.object(probe.sys, "flags", types.SimpleNamespace(isolated=1, no_site=1, dont_write_bytecode=1)), \
                mock.patch.object(probe.sys, "argv", ["worker", "help"]), \
                mock.patch.object(probe.os, "geteuid", return_value=0), \
                mock.patch.object(probe.os, "getegid", return_value=0), \
                mock.patch.object(probe.socket, "if_nameindex", return_value=[(1, "lo")]), \
                mock.patch.object(probe, "module", side_effect=[fakeguard, fakefiles]), \
                mock.patch.object(probe, "verify_files") as verify, \
                mock.patch.object(probe.os, "chroot") as chroot, \
                mock.patch.object(probe.os, "chdir") as chdir, \
                mock.patch.object(probe.os, "execve") as execute:
            probe.main()
        fakeguard.check.assert_called_once_with()
        verify.assert_called_once_with(fakefiles.regular)
        chroot.assert_called_once_with(probe.ROOTFS)
        chdir.assert_called_once_with("/")
        path, argv, environment = execute.call_args.args
        self.assertEqual(path, "/usr/bin/setpriv")
        self.assertEqual(argv, [path, "--no-new-privs", "--inh-caps=-all", "--ambient-caps=-all",
                                "--bounding-set=-all", "--", "/usr/bin/nvattest", "--help"])
        self.assertNotIn("LD_LIBRARY_PATH", environment)


if __name__ == "__main__":
    unittest.main()
