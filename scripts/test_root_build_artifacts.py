# SPDX-License-Identifier: Apache-2.0
"""Synthetic finalization tests, not native image or boot qualification."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import mkosi_verity as verity
import compare_native_exports as compare


class BuildArtifactTests(unittest.TestCase):
    def test_actual_original_diagnostics_localize_three_changed_buckets(self):
        root = Path(__file__).resolve().parents[1] / "evidence/p3/2026-10-22-native-root-tree-mismatch"
        raws = [{name: (root / f"attempt-{i}" / name).read_bytes() for name in compare.NAMES}
                for i in (1, 2)]
        report = compare.compare(*raws)
        self.assertEqual(compare.export.encoded(report), (root / "comparison.json").read_bytes())
        self.assertEqual(report["source"]["git_sha"], "ad1ab45e6b96a06a5972bedfcb5d4b5f5dcf5c9c")
        self.assertEqual(report["result"], "fail")
        self.assertFalse(any(report["matches"].values()))
        for raw, digest in zip(raws, (
            "807192caa0f754de8236737746c78666b2136cb29d4cd81a172a60765480d6dd",
            "9008154c7c5084d38e6753e0ee5fa601def5e7da90956af7e56b69617bbddb6a")):
            self.assertEqual(hashlib.sha256(raw["result.json"]).hexdigest(), digest)
        a, b = (json.loads(raw["result.json"])["worker"]["verity_root"]["source_tree_diagnostics"] for raw in raws)
        self.assertEqual({name for name in a["buckets"] if a["buckets"][name] != b["buckets"][name]},
                         {"var/log", "var/lib/dkms", "var"})
        self.assertNotEqual(a["selected_files"][verity.BUILD_ARTIFACTS[0]],
                            b["selected_files"][verity.BUILD_ARTIFACTS[0]])
        for index, raw in enumerate(raws, 1):
            self.assertEqual((root / f"attempt-{index}/output.log").read_bytes(), raw["validation.json"])

    def populate(self, root):
        for index, name in enumerate(verity.BUILD_ARTIFACTS):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"private-build-canary" + bytes([index]))

    def test_exact_three_paths_not_loader_cache_or_package_database(self):
        self.assertEqual(verity.BUILD_ARTIFACTS, (
            "var/lib/dkms/nvidia/595.91.07/6.8.0-147-generic/x86_64/log/make.log",
            "var/log/alternatives.log", "var/cache/ldconfig/aux-cache"))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            self.populate(root)
            untouched = ("etc/ld.so.cache", "var/lib/dpkg/status", "var/lib/dpkg/alternatives/editor",
                         "var/log/unknown.log", "usr/lib/modules/driver.ko")
            for name in untouched:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"keep")
            report = verity.remove_build_artifacts(root)
            for index, name in enumerate(verity.BUILD_ARTIFACTS):
                raw = b"private-build-canary" + bytes([index])
                self.assertEqual(report[name], {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
                self.assertFalse((root / name).exists())
            for name in untouched:
                self.assertEqual((root / name).read_bytes(), b"keep")
            self.assertNotIn("private-build-canary", json.dumps(report))
            self.assertEqual(verity.remove_build_artifacts(root), dict.fromkeys(verity.BUILD_ARTIFACTS))

    def test_missing_is_optional_but_root_must_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            self.assertEqual(verity.remove_build_artifacts(root), dict.fromkeys(verity.BUILD_ARTIFACTS))
            with self.assertRaises(FileNotFoundError):
                verity.remove_build_artifacts(root / "absent")

    def test_bad_later_candidate_cannot_remove_valid_earlier_file(self):
        for bad in ("symlink", "hardlink", "directory", "fifo", "oversized"):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                self.populate(root)
                path = root / verity.BUILD_ARTIFACTS[-1]
                path.unlink()
                outside = root / "keep"
                outside.write_bytes(b"sentinel")
                if bad == "symlink": path.symlink_to(outside)
                elif bad == "hardlink": os.link(outside, path)
                elif bad == "directory": path.mkdir()
                elif bad == "fifo": os.mkfifo(path)
                else:
                    with path.open("wb") as stream:
                        stream.truncate(verity.BUILD_ARTIFACT_MAX_BYTES + 1)
                with self.assertRaises((OSError, ValueError)):
                    verity.remove_build_artifacts(root)
                self.assertEqual(outside.read_bytes(), b"sentinel")
                self.assertTrue((root / verity.BUILD_ARTIFACTS[0]).is_file())

    def test_symlinked_parent_never_accesses_external_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            root, other = base / "root", base / "other"
            root.mkdir()
            other.mkdir()
            self.populate(other)
            (root / "var").symlink_to(other / "var", target_is_directory=True)
            with self.assertRaises(OSError): verity.remove_build_artifacts(root)
            self.assertTrue(all((other / name).is_file() for name in verity.BUILD_ARTIFACTS))

    def test_path_identity_change_rejects_before_removal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            self.populate(root)
            real_stat = verity.os.stat
            def changed(path, *args, **kwargs):
                if path == "aux-cache" and "dir_fd" in kwargs:
                    return real_stat(root / verity.BUILD_ARTIFACTS[0])
                return real_stat(path, *args, **kwargs)
            with mock.patch.object(verity.os, "stat", side_effect=changed), self.assertRaises(ValueError):
                verity.remove_build_artifacts(root)
            self.assertTrue(all((root / name).is_file() for name in verity.BUILD_ARTIFACTS))

    def test_empty_file_and_unlink_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            self.populate(root)
            (root / verity.BUILD_ARTIFACTS[0]).write_bytes(b"")
            with mock.patch.object(verity.os, "unlink", side_effect=OSError("fixture")), self.assertRaises(OSError):
                verity.remove_build_artifacts(root)
            report = verity.remove_build_artifacts(root)
            self.assertEqual(report[verity.BUILD_ARTIFACTS[0]],
                             {"bytes": 0, "sha256": hashlib.sha256(b"").hexdigest()})


if __name__ == "__main__":
    unittest.main()
