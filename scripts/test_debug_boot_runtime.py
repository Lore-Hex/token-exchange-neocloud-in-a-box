# SPDX-License-Identifier: Apache-2.0
import copy
import hashlib
import json
import lzma
from pathlib import Path
import runpy
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).absolute().parent.parent
runtime = types.SimpleNamespace(**runpy.run_path(str(ROOT / "scripts/prepare_debug_boot_runtime.py")))


class DebugRuntimeTests(unittest.TestCase):
    def fixture(self, directory):
        lock = json.loads((ROOT / runtime.LOCK).read_bytes())
        rows = []
        for name in runtime.ROOTS:
            row = {field: "" for field in runtime.base.inputs.FIELDS}
            row.update(Package=name, Version="1", Architecture="amd64", Filename="pool/synthetic.deb",
                       Size="1", SHA256=hashlib.sha256(name.encode()).hexdigest(), index="jammy-main")
            rows.append(row)
        lock.update(packages=rows, archive_bytes=3)
        for index in lock["indexes"]:
            raw = b""
            for row in rows if index["id"] == "jammy-main" else []:
                raw += ("".join(k + ": " + v + "\n" for k, v in row.items() if k != "index" and v) + "\n").encode()
            packed = lzma.compress(raw)
            (directory / index["file"]).write_bytes(packed)
            index.update(bytes=len(packed), sha256=runtime.base.digest(packed))
        return lock

    def test_committed_lock_is_complete_bounded_and_pins_exact_vendor_runtime(self):
        lock = json.loads((ROOT / runtime.LOCK).read_bytes())
        self.assertEqual(len(lock["packages"]), 123)
        self.assertEqual(runtime.base.inputs.check_graph(lock["packages"], runtime.ROOTS, []), 55311182)
        rows = {r["Package"]: r for r in lock["packages"]}
        self.assertEqual(rows["qemu-system-x86"]["Version"], "1:6.2+dfsg-2ubuntu6.31")
        self.assertEqual(rows["ovmf"]["Version"], "2022.02-3ubuntu0.22.04.7")
        self.assertTrue(all(r["signer_fingerprint"] == runtime.SIGNER for r in lock["indexes"]))
        self.assertFalse(lock["boot_verified"])

    def test_index_rows_hashes_dependency_graph_and_paths_are_rechecked(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp).resolve()
            lock = self.fixture(directory)
            runtime.metadata_check(lock, directory)
            for mutation in ("index-hash", "index-size", "index-path", "index-url", "signer", "row-version",
                             "dependency", "archive-total", "duplicates", "too-many", "extra-root", "fake-boot"):
                bad = copy.deepcopy(lock)
                if mutation == "index-hash": bad["indexes"][0]["sha256"] = "0" * 64
                elif mutation == "index-size": bad["indexes"][0]["bytes"] += 1
                elif mutation == "index-path": bad["indexes"][0]["file"] = "../jammy-main.xz"
                elif mutation == "index-url": bad["indexes"][0]["url"] += "?unknown"
                elif mutation == "signer": bad["indexes"][0]["signer_fingerprint"] = "0" * 40
                elif mutation == "row-version": bad["packages"][0]["Version"] = "2"
                elif mutation == "dependency": bad["packages"][0]["Depends"] = "missing-package"
                elif mutation == "archive-total": bad["archive_bytes"] += 1
                elif mutation == "duplicates": bad["packages"].append(bad["packages"][0])
                elif mutation == "too-many": bad["packages"] *= 43
                elif mutation == "extra-root": bad["roots"].append("unexpected")
                elif mutation == "fake-boot": bad["boot_verified"] = True
                with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                    runtime.metadata_check(bad, directory)

    def test_index_symlink_and_hardlink_reject(self):
        import os
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp).resolve()
            lock = self.fixture(directory)
            path = directory / lock["indexes"][0]["file"]
            original = directory / "original"
            path.rename(original)
            for linked in (False, True):
                if linked: os.link(original, path)
                else: path.symlink_to(original)
                with self.assertRaises((OSError, ValueError)):
                    runtime.metadata_check(lock, directory)
                path.unlink()

    def test_archive_rejects_before_parser_or_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp).resolve()
            digest = hashlib.sha256(b"archive").hexdigest()
            lock = {"packages": [{"SHA256": digest, "Size": "7"}], "archive_bytes": 7}
            path = directory / (digest + ".deb")
            path.write_bytes(b"changed")
            with mock.patch.object(runtime.base, "controls") as controls, \
                 mock.patch.object(runtime.tools, "payload") as payload:
                with self.assertRaises(ValueError): runtime.assemble(lock, directory)
                controls.assert_not_called()
                payload.assert_not_called()
            path.unlink()
            outside = directory / "outside"
            outside.write_bytes(b"archive")
            path.symlink_to(outside)
            with self.assertRaises((OSError, ValueError)): runtime.assemble(lock, directory)

    def test_member_identity_and_timeout_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp).resolve()
            raw = b"synthetic archive"
            digest = hashlib.sha256(raw).hexdigest()
            (directory / (digest + ".deb")).write_bytes(raw)
            lock = {"packages": [{"SHA256": digest, "Size": str(len(raw))}], "archive_bytes": len(raw)}
            with mock.patch.object(runtime.base, "controls"), \
                 mock.patch.object(runtime.tools, "payload", return_value={}), \
                 mock.patch.object(runtime.tools, "executable") as executable:
                with self.assertRaises(ValueError): runtime.assemble(lock, directory)
                executable.assert_not_called()
            with mock.patch.object(runtime.time, "monotonic", side_effect=[0, 121]), \
                 mock.patch.object(runtime.base, "read_at") as read:
                with self.assertRaises(ValueError): runtime.assemble(lock, directory)
                read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
