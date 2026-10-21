# SPDX-License-Identifier: Apache-2.0
"""Synthetic identity-map tests. No host mappings or ownership are changed."""
import ast
import os
from pathlib import Path
import stat
import tempfile
import types
import unittest
from unittest import mock

import mkosi_ownership as owner


class SubordinateTests(unittest.TestCase):
    def test_named_or_numeric_owner_and_minimum_range(self):
        for key in ("runner", "1001"):
            self.assertEqual(owner.subordinate((key + ":100000:65535\n").encode(), "runner", 1001), 100000)

    def test_ambiguous_short_overlapping_root_and_malformed_ranges_fail(self):
        cases = (b"", b"runner:0:65536", b"runner:1000:65536", b"runner:100000:65534",
                 b"runner:100000:65536\n1001:300000:65536", b"other:100000:65536",
                 b"runner:100000:65536\nother:150000:65536", b"runner:4294967294:65536",
                 b"runner:notanid:65536", b"runner:100000:65536:extra", b"\xff")
        for data in cases:
            with self.subTest(data=data), self.assertRaises(ValueError):
                owner.subordinate(data, "runner", 1001)

    def test_host_reads_only_existing_files_and_does_not_install_or_allocate(self):
        def read(path, *, executable=False):
            if path.startswith("/etc/sub"):
                self.assertFalse(executable)
                return b"runner:100000:65536\n"
            self.assertIn(path, ("/usr/bin/newuidmap", "/usr/bin/newgidmap"))
            self.assertTrue(executable)
            return b"synthetic-helper"
        with mock.patch.object(owner.os, "getuid", return_value=1001), \
             mock.patch.object(owner.os, "geteuid", return_value=1001), \
             mock.patch.object(owner.os, "getgid", return_value=1002), \
             mock.patch.object(owner.os, "getegid", return_value=1002), \
             mock.patch.object(owner.pwd, "getpwuid", return_value=types.SimpleNamespace(pw_name="runner")), \
             mock.patch.object(owner, "trusted_bytes", side_effect=read):
            expected, hashes = owner.host_inputs()
        self.assertEqual(expected, {"uid": 1001, "gid": 1002, "subuid": 100000, "subgid": 100000})
        self.assertEqual(len(hashes), 2)
        self.assertTrue(all(len(value) == 64 for value in hashes.values()))

    def test_metadata_ignores_atime_but_not_owner_or_content_changes(self):
        info = dict(st_dev=1, st_ino=2, st_mode=0o100644, st_uid=0, st_gid=0,
                    st_nlink=1, st_size=10, st_mtime_ns=4, st_ctime_ns=5, st_atime_ns=6)
        self.assertEqual(owner.identity(types.SimpleNamespace(**info)),
                         owner.identity(types.SimpleNamespace(**{**info, "st_atime_ns": 7})))
        for key in ("st_uid", "st_mtime_ns", "st_size", "st_ctime_ns"):
            self.assertNotEqual(owner.identity(types.SimpleNamespace(**info)),
                                owner.identity(types.SimpleNamespace(**{**info, key: 9})))

    def test_reader_rejects_symlinks_and_unowned_or_writable_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "file"
            target.write_bytes(b"synthetic")
            link = root / "link"
            link.symlink_to(target)
            with self.assertRaises(OSError):
                owner.trusted_bytes(link)
            with self.assertRaises(ValueError):
                owner.trusted_bytes(target, executable=True)
            real = os.fstat
            def changed(fd):
                info = real(fd)
                values = list(info)
                values[0] = stat.S_IFREG | 0o666
                values[4] = 0
                return os.stat_result(values)
            with mock.patch.object(owner.os, "fstat", side_effect=changed), self.assertRaises(ValueError):
                owner.trusted_bytes(target)


class MappingTests(unittest.TestCase):
    def test_complete_nonroot_host_mapping(self):
        owner.mapping("0 1001 1\n1 100000 65535\n", 1001, 100000)
        owner.mapping("1 100000 65535\n0 1001 1\n", 1001, 100000)

    def test_root_identity_single_uid_extra_and_wrong_range_fail(self):
        for text in ("0 0 1\n1 100000 65535\n", "0 1001 1\n", "0 1001 65536\n",
                     "0 1001 1\n1 200000 65535\n", "0 1001 1\n1 100000 65534\n",
                     "0 1001 1\n1 100000 65535\n65536 300000 1\n", "0 1001 -1\n", "x" * 4096):
            with self.subTest(text=text), self.assertRaises(ValueError):
                owner.mapping(text, 1001, 100000)

    def test_real_chown_results_are_checked_and_only_private_file_is_touched(self):
        expected = {"uid": 1001, "gid": 1002, "subuid": 100000, "subgid": 200000}
        current = [0, 0]
        def chown(fd, uid, gid):
            self.assertEqual(fd, 91)
            current[:] = uid, gid
        with mock.patch.object(owner.os, "geteuid", return_value=0), \
             mock.patch.object(owner.os, "getegid", return_value=0), \
             mock.patch.object(owner.Path, "read_text", side_effect=["0 1001 1\n1 100000 65535\n", "0 1002 1\n1 200000 65535\n"]), \
             mock.patch.object(owner.os, "open", return_value=91) as opened, \
             mock.patch.object(owner.os, "fchown", side_effect=chown) as changed, \
             mock.patch.object(owner.os, "fstat", side_effect=lambda _: types.SimpleNamespace(st_uid=current[0], st_gid=current[1], st_nlink=1)), \
             mock.patch.object(owner.os, "close") as closed, \
             mock.patch.object(owner.Path, "unlink") as removed:
            owner.check(expected)
        self.assertEqual(opened.call_args.args[0], Path("/tmp/nib-ownership-proof"))
        self.assertEqual(changed.call_count, 5)
        closed.assert_called_once_with(91)
        removed.assert_called_once()

    def test_script_uses_python_310_and_no_execution_escape_hatches(self):
        source = Path(owner.__file__).read_text()
        ast.parse(source, feature_version=(3, 10))
        for forbidden in ("subprocess", "shell=True", "sudo", "suppress-chown", "chmod("):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
