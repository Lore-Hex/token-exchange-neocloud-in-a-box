# SPDX-License-Identifier: Apache-2.0
"""Privilege/filter policy tests; never constrain the test runner itself."""
import errno
import json
from pathlib import Path
import runpy
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
guard = types.SimpleNamespace(**runpy.run_path(str(ROOT / "scripts/mkosi_confinement.py")))


def status():
    mask = format(guard.CAP_MASK, "016x")
    return "\n".join(("CapInh:\t0000000000000000", "CapPrm:\t" + mask, "CapEff:\t" + mask,
                       "CapBnd:\t" + mask, "CapAmb:\t0000000000000000", "NoNewPrivs:\t1", "Seccomp:\t2")) + "\n"


class ConfinementTests(unittest.TestCase):
    def test_exact_caps_and_nnp_required(self):
        guard.status_check(status(), filtered=True)
        for value in ("", status() + "CapEff:\t00000000800400db\n",
                      status().replace("NoNewPrivs:\t1", "NoNewPrivs:\t0"),
                      status().replace("Seccomp:\t2", "Seccomp:\t0"),
                      status().replace("CapAmb:\t0000000000000000", "CapAmb:\t0000000000000001"),
                      status().replace(format(guard.CAP_MASK, "016x"), "000001ffffffffff")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                guard.status_check(value, filtered=True)
        self.assertFalse(guard.CAP_MASK & (1 << 21))
        self.assertFalse(guard.CAP_MASK & (1 << 8))
        self.assertEqual(guard.PREFIX[0], "/usr/bin/setpriv")
        self.assertIn("--no-new-privs", guard.PREFIX)

    def library(self):
        names, rows = [], []
        def resolve(name):
            names.append(name.decode())
            return len(names)
        def add(context, action, number, count, comparisons):
            rows.append((names[number - 1], action, [(c.arg, c.op, c.datum_a, c.datum_b) for c in comparisons]))
            self.assertEqual(count, len(comparisons))
            return 0
        lib = types.SimpleNamespace(seccomp_init=mock.Mock(return_value=123),
            seccomp_release=mock.Mock(), seccomp_syscall_resolve_name=mock.Mock(side_effect=resolve),
            seccomp_rule_add_array=mock.Mock(side_effect=add), seccomp_load=mock.Mock(return_value=0))
        return lib, rows

    def test_filter_denies_namespaces_and_preserves_ordinary_clone(self):
        lib, rows = self.library()
        guard.filter_install(lib)
        lib.seccomp_init.assert_called_once_with(0x7fff0000)
        lib.seccomp_load.assert_called_once_with(123)
        lib.seccomp_release.assert_called_once_with(123)
        self.assertEqual(rows[:len(guard.DENIED)], [(name, 0x50000 | errno.EPERM, []) for name in guard.DENIED])
        self.assertIn(("clone3", 0x50000 | errno.ENOSYS, []), rows)
        self.assertEqual([row for row in rows if row[0] == "clone"],
            [("clone", 0x50000 | errno.EPERM, [(0, 7, flag, flag)]) for flag in guard.CLONE_NAMESPACES])
        for name in ("fork", "vfork", "execve", "chown", "chroot", "setuid", "setgid"):
            self.assertNotIn(name, [row[0] for row in rows])

    def test_unknown_syscall_or_failed_rule_never_loads_partial_policy(self):
        for operation in ("seccomp_syscall_resolve_name", "seccomp_rule_add_array", "seccomp_load"):
            lib, _ = self.library()
            setattr(lib, operation, mock.Mock(return_value=-1))
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                guard.filter_install(lib)
            lib.seccomp_release.assert_called_once_with(123)
            if operation != "seccomp_load":
                lib.seccomp_load.assert_not_called()
        lib, _ = self.library()
        lib.seccomp_init.return_value = None
        with self.assertRaises(ValueError):
            guard.filter_install(lib)
        lib.seccomp_load.assert_not_called()

    def test_unexpected_inherited_fd_rejects(self):
        entries = mock.MagicMock()
        entries.__enter__.return_value = iter(types.SimpleNamespace(name=str(n)) for n in (0, 1, 2, 3))
        with mock.patch.object(guard.os, "scandir", return_value=entries), \
                mock.patch.object(guard.os, "stat", return_value=types.SimpleNamespace(st_mode=0o100600)), \
                self.assertRaises(ValueError):
            guard.file_descriptors_check()

    def test_transient_closed_scandir_fd_is_not_an_inherited_handle(self):
        entries = mock.MagicMock()
        entries.__enter__.return_value = iter(types.SimpleNamespace(name=str(n)) for n in (0, 1, 2, 3))
        with mock.patch.object(guard.os, "scandir", return_value=entries), \
                mock.patch.object(guard.os, "stat", side_effect=FileNotFoundError), \
                mock.patch.object(guard.os, "fstat", return_value=types.SimpleNamespace(st_mode=0o100600)):
            guard.file_descriptors_check()

    def test_actual_ownership_record_is_honest_about_scope(self):
        result = json.loads((ROOT / "evidence/p3/2026-10-21-native-mapped-ownership/result.json").read_bytes())
        self.assertEqual(result["result"], "pass")
        self.assertTrue(result["mapped_ownership_validated"])
        self.assertTrue(result["pid_namespace_validated"])
        self.assertTrue(result["payload_removed"])
        self.assertTrue(result["process_cleanup_confirmed"])
        for field in ("package_installation_executed", "maintainer_scripts_executed", "mkosi_build_executed",
                      "abi_validated", "bootable", "deployable"):
            self.assertFalse(result[field])


if __name__ == "__main__":
    unittest.main()
