# SPDX-License-Identifier: Apache-2.0
"""Closed venue admission and private resolver setup; no mounts or sockets."""

from contextlib import ExitStack
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import time
import types
import unittest
from unittest import mock
import test_selected_network_policy as core_tests

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("nat_venue_proof", ROOT / "scripts/prove-selected-nat.py")
proof = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(proof)


class VenueTests(unittest.TestCase):
    def test_new_venue_uses_same_complete_control_and_cleanup_gates(self):
        for failure in (None, "create", "policy", "readback", "cleanup", "reap"):
            with self.subTest(failure=failure):
                status, report, _, reaps, _ = core_tests.ProofTests().inner_case(
                    venue=proof.ORDINARY_VENUE, failure=failure)
                self.assertEqual(status, 0 if failure is None else 1)
                self.assertEqual(report["result"], "pass" if failure is None else "fail")
                self.assertEqual(report["venue_profile"], proof.ORDINARY_VENUE)
                self.assertEqual(reaps, 2)
                proof.check_report(report)
                with self.assertRaises(ValueError):
                    proof.check_report({**report, "venue_profile": "arbitrary"})

    def test_exact_opt_in_preserves_legacy_defaults(self):
        self.assertEqual(proof.venue_settings(proof.LEGACY_VENUE),
                         {"python": "3.12.13", "nftables": "1.0.9", "unshare": "2.39.3"})
        settings = proof.venue_settings(proof.ORDINARY_VENUE)
        self.assertEqual(settings["python"], "3.14.4")
        self.assertEqual(settings["nftables"], "1.1.6")
        self.assertEqual(len(settings["files"]), 38)
        self.assertEqual(settings["packages"]["zlib1g:amd64"],
                         "1:1.3.dfsg+really1.3.1-1ubuntu3.2")
        self.assertEqual(settings["files"]["/usr/lib/x86_64-linux-gnu/libz.so.1.3.1"],
                         "27fa6c6cb94fb5b099ead61758a300dbc392d1dbf307fff6c16a9b8e57fdc9de")
        self.assertIn(proof.VENUE_FILE, proof.SOURCES)
        for unknown in (None, "auto", "ubuntu26", "ubuntu26-ordinary\n", True, 1):
            with self.subTest(unknown=unknown), self.assertRaises(ValueError):
                proof.venue_settings(unknown)
        legacy = proof.namespace_command(Path("/tmp/work"), Path("/tmp/image"), 1, 2, 100)
        ordinary = proof.namespace_command(Path("/tmp/work"), Path("/tmp/image"), 1, 2, 100,
                                           proof.ORDINARY_VENUE)
        self.assertNotIn("--venue-profile", legacy)
        self.assertEqual(ordinary, legacy + ["--venue-profile", proof.ORDINARY_VENUE])

    def test_pinned_files_reject_changes_links_permissions_and_expiry(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "tool"
            path.write_bytes(b"synthetic immutable tool")
            path.chmod(0o644)
            original = path.lstat()
            info = types.SimpleNamespace(**{key: getattr(original, key) for key in (
                "st_mode", "st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")}, st_uid=0)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            with mock.patch.object(Path, "lstat", return_value=info):
                proof.pinned_file(path, digest, time.monotonic() + 5)
                with self.assertRaises(ValueError):
                    proof.pinned_file(path, "0" * 64, time.monotonic() + 5)
                with self.assertRaises(ValueError):
                    proof.pinned_file(path, digest, time.monotonic() - 1)
            for change in ({"st_uid": 1000}, {"st_mode": original.st_mode | 0o020},
                           {"st_mode": 0o120777}, {"st_size": 0}, {"st_size": 129 << 20}):
                changed = types.SimpleNamespace(**{**vars(info), **change})
                with mock.patch.object(Path, "lstat", return_value=changed), self.assertRaises(ValueError):
                    proof.pinned_file(path, digest, time.monotonic() + 5)

    def test_only_exact_resolver_link_is_admitted(self):
        valid = {"row": types.SimpleNamespace(st_mode=0o120777, st_uid=0),
                 "link": Path("../run/systemd/resolve/stub-resolv.conf"), "target": proof.RESOLVER_TARGET}
        for change in ({}, {"row": types.SimpleNamespace(st_mode=0o100644, st_uid=0)},
                       {"row": types.SimpleNamespace(st_mode=0o120777, st_uid=1)},
                       {"link": Path("/tmp/resolv.conf")}, {"target": Path("/etc/passwd")}):
            values = {**valid, **change}
            with mock.patch.object(Path, "lstat", return_value=values["row"]), \
                    mock.patch.object(Path, "readlink", return_value=values["link"]), \
                    mock.patch.object(Path, "resolve", return_value=values["target"]):
                if change:
                    with self.assertRaises(ValueError):
                        proof.resolver_target(proof.ORDINARY_VENUE)
                else:
                    self.assertEqual(proof.resolver_target(proof.ORDINARY_VENUE), proof.RESOLVER_TARGET)
        with mock.patch.object(Path, "is_symlink", return_value=True), self.assertRaises(ValueError):
            proof.resolver_target(proof.LEGACY_VENUE)

    def test_resolver_target_created_only_after_private_run_mount(self):
        events = []
        commands = mock.Mock()
        commands.run.side_effect = lambda argv: events.append(("command", argv))
        with mock.patch.object(proof, "resolver_target", return_value=proof.RESOLVER_TARGET), \
                mock.patch.object(Path, "readlink", return_value=Path("../run/systemd/resolve/stub-resolv.conf")), \
                mock.patch.object(Path, "resolve", return_value=proof.RESOLVER_TARGET), \
                mock.patch.object(Path, "mkdir", side_effect=lambda **kw: events.append(("mkdir", kw))), \
                mock.patch.object(Path, "touch", side_effect=lambda **kw: events.append(("touch", kw))):
            proof.isolate_resolver(commands, Path("/tmp/private"), proof.ORDINARY_VENUE)
        self.assertEqual([kind for kind, _ in events], ["command", "mkdir", "touch", "command"])
        self.assertEqual(events[0][1][-1], "/run")
        self.assertEqual(events[1][1], {"mode": 0o755, "parents": True, "exist_ok": False})
        self.assertEqual(events[2][1], {"mode": 0o600, "exist_ok": False})
        self.assertEqual(events[3][1], ["mount", "--bind", "/tmp/private/resolv.conf",
                                      "/run/systemd/resolve/stub-resolv.conf"])
        commands.run.reset_mock()
        commands.run.side_effect = ValueError("mount failed")
        with mock.patch.object(proof, "resolver_target", return_value=proof.RESOLVER_TARGET), \
                mock.patch.object(Path, "mkdir") as mkdir, self.assertRaises(ValueError):
            proof.isolate_resolver(commands, Path("/tmp/private"), proof.ORDINARY_VENUE)
        mkdir.assert_not_called()

    def test_host_admission_checks_kernel_packages_all_files_and_tool_paths(self):
        settings = proof.venue_settings(proof.ORDINARY_VENUE)
        output = "".join(name + "\t" + version + "\n" for name, version in sorted(settings["packages"].items()))
        for failure in (None, "kernel", "python", "interpreter", "relative-interpreter",
                        "package", "zlib-package", "zlib-missing", "duplicate", "file", "zlib-file", "path"):
            with self.subTest(failure=failure), ExitStack() as stack:
                commands = mock.Mock(end=time.monotonic() + 60, env=proof.ENV)
                package_output = output + output.splitlines(True)[0] if failure == "duplicate" else output
                if failure == "package":
                    package_output = output.replace("29.1.3-0ubuntu4.1", "29.1.4")
                if failure == "zlib-package":
                    package_output = output.replace("1:1.3.dfsg+really1.3.1-1ubuntu3.2",
                                                    "1:1.3.dfsg+really1.3.1-1ubuntu3.1")
                if failure == "zlib-missing":
                    package_output = "".join(line for line in output.splitlines(True)
                                             if not line.startswith("zlib1g:amd64\t"))
                commands.run.side_effect = [
                    subprocess.CompletedProcess([], 0, b"unshare from util-linux 2.41.3\n", b""),
                    subprocess.CompletedProcess([], 0, package_output.encode(), b"")]
                stack.enter_context(mock.patch.object(proof.sys, "version_info",
                    (3, 12, 13) if failure == "python" else (3, 14, 4)))
                stack.enter_context(mock.patch.object(proof.sys, "executable",
                    {"interpreter": "/opt/alternate/python3.14", "relative-interpreter": "python3.14"}
                    .get(failure, "/usr/bin/python3.14")))
                stack.enter_context(mock.patch.object(proof.platform, "release",
                    return_value="changed" if failure == "kernel" else settings["kernel"]))
                stack.enter_context(mock.patch.object(proof, "resolver_target", return_value=proof.RESOLVER_TARGET))
                check = stack.enter_context(mock.patch.object(proof, "pinned_file"))
                if failure == "file":
                    check.side_effect = ValueError("changed file")
                if failure == "zlib-file":
                    def changed_zlib(path, digest, end):
                        if path.name == "libz.so.1.3.1":
                            raise ValueError("old or unknown zlib content")
                    check.side_effect = changed_zlib
                targets = {"python3": "/usr/bin/python3.14", "iptables": "/usr/sbin/xtables-nft-multi",
                           "nft": "/usr/sbin/nft", "runc": "/usr/sbin/runc"}
                stack.enter_context(mock.patch.object(proof.shutil, "which", side_effect=lambda name, **kw:
                    "/tmp/untrusted" if failure == "path" else targets.get(name, "/usr/bin/" + name)))
                stack.enter_context(mock.patch.object(Path, "resolve", lambda path: path))
                if failure is None:
                    proof.check_venue(commands, proof.ORDINARY_VENUE)
                    self.assertEqual(check.call_count, 38)
                    self.assertEqual({str(c.args[0]): c.args[1] for c in check.call_args_list}, settings["files"])
                    self.assertIn("zlib1g:amd64", commands.run.call_args.args[0])
                else:
                    with self.assertRaises(ValueError):
                        proof.check_venue(commands, proof.ORDINARY_VENUE)


if __name__ == "__main__":
    unittest.main()
