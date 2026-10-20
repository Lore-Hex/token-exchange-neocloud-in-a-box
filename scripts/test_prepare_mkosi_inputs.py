# SPDX-License-Identifier: Apache-2.0
"""Nonexecuting synthetic source/package fixtures; no upstream runtime imports."""
import copy
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tarfile
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    value = types.ModuleType(name)
    value.__file__ = str(path)
    exec(compile(path.read_bytes(), str(path), "exec", dont_inherit=True), value.__dict__)
    return value


tool = module("mkosi_test_tool", ROOT / "scripts/prepare_mkosi_inputs.py")
fixtures = module("mkosi_deb_fixtures", ROOT / "scripts/test_prepare_guest_packages.py")


def source(entries=None, transform=None):
    entries = entries or [
        ("pyproject.toml", b'[project]\nversion="27.1"\n', "100644"),
        ("mkosi/__main__.py", b'raise RuntimeError("must not execute upstream")\n', "100644"),
        ("LICENSES/LGPL-2.1-or-later.txt", b"synthetic license fixture\n", "100644"),
        ("entry", b"mkosi/__main__.py", "120000"),
    ]
    prefix = "mkosi-" + tool.COMMIT + "/"
    pin = {"schema": "nib.mkosi-source/v1", "version": "27.1", "git_sha": tool.COMMIT,
           "repository": "https://github.com/systemd/mkosi",
           "archive_url": "https://codeload.github.com/systemd/mkosi/tar.gz/" + tool.COMMIT,
           "archive_prefix": prefix, "required_python": ">=3.9",
           "executed": False, "build_verified": False, "deployable": False,
           "source_files": {}, "license_files": sorted(n for n, _, _ in entries if n.startswith("LICENSES/"))}
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for name, raw, mode in entries:
            pin["source_files"][name] = {
                "bytes": len(raw), "sha256": tool.base.digest(raw), "mode": mode,
                "git_blob": hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()}
            member = tarfile.TarInfo(prefix + name)
            member.mode = 0o775 if mode == "100755" else 0o664
            if mode == "120000":
                member.type, member.linkname = tarfile.SYMTYPE, raw.decode()
            else:
                member.size = len(raw)
            if transform:
                transform(member)
            archive.addfile(member, io.BytesIO(raw) if member.isfile() else None)
    raw = gzip.compress(buf.getvalue(), mtime=0)
    pin.update(archive_bytes=len(raw), archive_sha256=tool.base.digest(raw))
    return raw, pin


class SourceTests(unittest.TestCase):
    def test_complete_source_and_license_preserved_without_execution(self):
        raw, pin = source()
        result = tool.verify_source(raw, pin)
        self.assertEqual(result["files"], 4)
        self.assertEqual(result["source_bytes"], sum(r["bytes"] for r in pin["source_files"].values()))
        self.assertTrue(result["git_blob_correspondence"])
        self.assertTrue(result["license_files_preserved"])
        self.assertFalse(result["upstream_executed"])
        self.assertEqual(tool.verify_source(raw, pin), result)

    def test_archive_and_every_source_hash_git_blob_size_and_mode_bound(self):
        raw, pin = source()
        for key, value in (("sha256", "0" * 64), ("git_blob", "0" * 40),
                           ("bytes", 1), ("mode", "100755")):
            bad = copy.deepcopy(pin)
            bad["source_files"]["mkosi/__main__.py"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                tool.verify_source(raw, bad)
        with self.assertRaises(ValueError):
            tool.verify_source(raw + b"x", pin)
        for key, value in (("version", "latest"), ("git_sha", "0" * 40),
                           ("archive_url", "https://example.invalid/private"),
                           ("required_python", "any"), ("executed", True)):
            bad = {**pin, key: value}
            with self.subTest(key=key), self.assertRaises(ValueError):
                tool.verify_source(raw, bad)

    def test_missing_extra_duplicate_license_and_unsafe_members_reject(self):
        raw, pin = source()
        bad = copy.deepcopy(pin)
        del bad["source_files"]["entry"]
        with self.assertRaises(ValueError):
            tool.verify_source(raw, bad)
        bad = copy.deepcopy(pin)
        bad["license_files"] = []
        with self.assertRaises(ValueError):
            tool.verify_source(raw, bad)
        entries = [("mkosi/__main__.py", b"never", "100644")] * 2
        raw, pin = source(entries)
        with self.assertRaises(ValueError):
            tool.verify_source(raw, pin)
        def change_name(m):
            m.name = "mkosi-" + tool.COMMIT + "/../private"
        def change_fifo(m):
            m.type, m.size = tarfile.FIFOTYPE, 0
        def change_hardlink(m):
            m.type, m.size, m.linkname = tarfile.LNKTYPE, 0, "private"
        def change_suid(m):
            m.mode = 0o4755
        def change_pax(m):
            m.pax_headers = {"path": "private"}
        for change in (change_name, change_fifo, change_hardlink, change_suid, change_pax):
            raw, pin = source(transform=change)
            with self.subTest(change=change.__name__), self.assertRaises(ValueError):
                tool.verify_source(raw, pin)

    def test_source_links_cannot_escape_dangle_or_cycle(self):
        for target in (b"/private/secret", b"../../private", b"missing", b"entry"):
            raw, pin = source([("entry", target, "120000")])
            with self.subTest(target=target), self.assertRaises(ValueError):
                tool.verify_source(raw, pin)

    def test_source_expansion_and_entry_count_are_bounded(self):
        raw, pin = source()
        with mock.patch.object(tool, "MAX_SOURCE", len(raw) + 1):
            with self.assertRaises(ValueError):
                tool.verify_source(raw, pin)
        many = [("entry" + str(n), b"x", "100644") for n in range(513)]
        raw, pin = source(many)
        with self.assertRaises(ValueError):
            tool.verify_source(raw, pin)

    def test_bootstrap_rejects_fifo_links_size_without_waiting(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "scripts").mkdir()
            path = root / "scripts/prepare_guest_tools.py"
            for kind in ("fifo", "symlink", "hardlink", "oversize"):
                other = root / "other"
                other.write_bytes(b"must not execute")
                if kind == "fifo":
                    os.mkfifo(path)
                elif kind == "symlink":
                    path.symlink_to(other)
                elif kind == "hardlink":
                    os.link(other, path)
                else:
                    path.write_bytes(b"x" * (1 << 20))
                with mock.patch.object(tool, "ROOT", root), self.subTest(kind=kind):
                    with self.assertRaises((ValueError, OSError)):
                        tool.load()
                path.unlink()


class PreparationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.archives = self.root / "archives"
        self.metadata = self.root / "metadata"
        self.archives.mkdir()
        self.metadata.mkdir()
        self.raw, pin = source()
        self.deb = fixtures.deb()
        self.row = fixtures.row(self.deb)
        self.archive = self.archives / (self.row["SHA256"] + ".deb")
        self.archive.write_bytes(self.deb)
        self.index = b"synthetic public index"
        (self.metadata / "fixture.packages").write_bytes(self.index)
        self.lock = {"archive_bytes": len(self.deb), "packages": [self.row], "indexes": [
            {"file": "fixture.packages", "bytes": len(self.index), "sha256": tool.base.digest(self.index)}]}
        self.sources = {tool.EXTRA[0]: b"bound test code", tool.EXTRA[1]: tool.base.encoded(pin),
                        tool.EXTRA[2]: tool.base.encoded(self.lock),
                        tool.tools.FILES[-1]: tool.base.encoded(self.lock)}

    def prepare(self):
        with mock.patch.object(tool, "validate_tools_lock"), mock.patch.object(tool.base.inputs, "check"):
            return tool.prepare(self.metadata, self.archives, self.raw, "a" * 40, self.sources)

    def test_real_writer_verifier_deterministic_and_no_execution_claims(self):
        files, report = self.prepare()
        self.assertEqual((files, report), self.prepare())
        self.assertEqual(report["unique_archives"], 1)
        self.assertEqual(files["mkosi-source.tar.gz"], self.raw)
        self.assertEqual(files["repo/pool/" + tool.base.package_name(self.row)], self.deb)
        self.assertNotIn(b"exit 99", files["maintainer-inventory.json"])
        control = json.loads(files["maintainer-inventory.json"])["packages"][0]
        self.assertEqual(control["maintainer_members"], ["postinst"])
        self.assertEqual(control["control_members"]["postinst"]["sha256"], tool.base.digest(b"exit 99\n"))
        for key in ("execution_review_complete", "apt_rehearsal_passed", "upstream_executed",
                    "packages_executed", "image_built", "reproducibility_verified", "bootable", "deployable"):
            self.assertIs(report[key], False)
        contract = json.loads(files["build-contract.json"])
        self.assertIs(contract["execution_permitted"], False)
        self.assertIs(contract["bounds_runtime_validated"], False)
        self.assertEqual(contract["clean_builds_required"], 2)
        self.assertIn("matching-apt-rehearsal-pass", contract["required_before_execution"])
        self.assertNotIn(str(self.root), json.dumps(report))
        destination = self.root / "output"
        tool.base.publish(destination, files)
        tool.base.verify(destination, files)
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o700)
        self.assertEqual(json.loads((destination / "receipt.json").read_bytes()), report)
        with self.assertRaises(FileExistsError):
            tool.base.publish(destination, files)
        (destination / "mkosi-source.tar.gz").write_bytes(b"changed")
        with self.assertRaises(ValueError):
            tool.base.verify(destination, files)

    def test_package_union_conflict_missing_hash_and_special_files_reject(self):
        bad = copy.deepcopy(self.lock)
        bad["packages"][0]["Version"] = "2.0"
        self.sources[tool.EXTRA[2]] = tool.base.encoded(bad)
        with self.assertRaises(ValueError):
            self.prepare()
        self.sources[tool.EXTRA[2]] = tool.base.encoded(self.lock)
        self.archive.unlink()
        for kind in ("missing", "changed", "fifo", "symlink", "hardlink"):
            if kind == "changed":
                self.archive.write_bytes(self.deb + b"x")
            elif kind == "fifo":
                os.mkfifo(self.archive)
            elif kind == "symlink":
                self.archive.symlink_to(self.metadata / "fixture.packages")
            elif kind == "hardlink":
                os.link(self.metadata / "fixture.packages", self.archive)
            with self.subTest(kind=kind), self.assertRaises((ValueError, OSError)):
                self.prepare()
            self.archive.unlink(missing_ok=True)

    def test_metadata_rechecked_and_unrelated_cache_bytes_never_read(self):
        # Shared download cache may contain other tasks' packages; bundle is exact.
        os.mkfifo(self.archives / "not-selected.deb")
        self.prepare()
        (self.metadata / "fixture.packages").write_bytes(b"changed")
        with self.assertRaises(ValueError):
            self.prepare()

    def test_input_validation_and_one_time_budget_precede_publication(self):
        with mock.patch.object(tool, "validate_tools_lock", side_effect=ValueError("fixed")):
            with self.assertRaises(ValueError):
                tool.prepare(self.metadata, self.archives, self.raw, "a" * 40, self.sources)
        with mock.patch.object(tool.time, "monotonic", side_effect=[1000, 1601]):
            with self.assertRaises(ValueError):
                self.prepare()
        with mock.patch.object(tool, "MAX_BYTES", len(self.deb) - 1):
            with self.assertRaises(ValueError):
                self.prepare()
        self.assertEqual(set(self.root.iterdir()), {self.archives, self.metadata})

    def test_virtual_upstream_root_requires_exact_existing_provider(self):
        lock = {"provided_root_packages": {"systemd-repart": "systemd"},
                "upstream_requested_packages": ["systemd-repart", "systemd"],
                "roots": ["systemd"], "packages": [
                    {"Package": "systemd", "Version": "255.4", "Provides": "systemd-repart",
                     "Multi-Arch": "foreign"}]}
        with mock.patch.object(tool.base.inputs, "check") as check:
            tool.validate_tools_lock(lock, self.metadata)
            self.assertEqual(check.call_args.kwargs["scope"], "mkosi-build-tools-dependency-coverage")
            for mutate in ("missing", "unknown", "duplicate", "not-provider"):
                bad = copy.deepcopy(lock)
                if mutate == "missing":
                    bad["provided_root_packages"] = {}
                elif mutate == "unknown":
                    bad["upstream_requested_packages"].append("unknown")
                elif mutate == "duplicate":
                    bad["upstream_requested_packages"].append("systemd")
                else:
                    bad["packages"][0]["Provides"] = ""
                with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                    tool.validate_tools_lock(bad, self.metadata)


class BindingTests(unittest.TestCase):
    def test_extra_committed_inputs_are_exact_and_old_default_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            paths = (*tool.tools.FILES, *tool.EXTRA)
            for name in paths:
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((ROOT / name).read_bytes())
            env = {**tool.base.GIT_ENV, "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
                   "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}
            def git(*args):
                subprocess.run(["git", "-C", str(root), *args], env=env, check=True,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
            git("init", "-q")
            git("add", ".")
            git("-c", "commit.gpgsign=false", "commit", "-qm", "fixture")
            with mock.patch.object(tool.tools, "ROOT", root):
                _, old = tool.tools.committed()
                _, new = tool.tools.committed(tool.EXTRA)
                self.assertEqual(set(old), set(tool.tools.FILES))
                self.assertEqual(set(new), set(paths))
                for extra in (("../private",), ("/private",), ("-option",), ["not-tuple"]):
                    with self.subTest(extra=extra), self.assertRaises(ValueError):
                        tool.tools.committed(extra)
                (root / tool.EXTRA[1]).write_bytes(b"uncommitted")
                with self.assertRaises(ValueError):
                    tool.tools.committed(tool.EXTRA)


if __name__ == "__main__":
    unittest.main()
