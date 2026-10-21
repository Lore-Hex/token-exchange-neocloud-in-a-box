# SPDX-License-Identifier: Apache-2.0
import importlib.util
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "host" / "prepare-lab.py"
spec = importlib.util.spec_from_file_location("lab_preparation", SOURCE)
lab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lab)


class LabPreparationTests(unittest.TestCase):
    def test_ram_capacity_requires_complete_non_swapping_tmpfs_headroom(self):
        mem = "MemTotal: 2100000000 kB\nMemAvailable: 1800000000 kB\nSwapTotal: 0 kB\n"
        mounts = "42 11 0:30 / /dev/shm rw,nosuid,nodev shared:5 - tmpfs tmpfs rw,size=1024G\n"
        result = lab.ram_capacity(mem, mounts, 900 << 30)
        self.assertEqual(result["storage"], "existing-tmpfs")
        self.assertFalse(result["confidential_qualified"])
        for bad in (mem.replace("1800000000", "1000000000"), mem.replace("SwapTotal: 0", "SwapTotal: 1"),
                    mem.replace("kB", "MB"), mem + "MemTotal: 2100000000 kB\n", "MemTotal: 1 kB\n"):
            with self.subTest(mem=bad), self.assertRaises(ValueError):
                lab.ram_capacity(bad, mounts, 900 << 30)
        for bad in (mounts.replace("tmpfs tmpfs", "ext4 /dev/test"), mounts + mounts,
                    mounts.replace("rw,nosuid,nodev", "rw,nodev"), mounts.replace("/dev/shm", "/other")):
            with self.subTest(mount=bad), self.assertRaises(ValueError):
                lab.ram_capacity(mem, bad, 900 << 30)
        for available in (599 << 30, True, -1):
            with self.assertRaises(ValueError):
                lab.ram_capacity(mem, mounts, available)

    def test_ram_stage_rejects_other_paths_before_system_reads(self):
        with patch.object(lab.sys, "platform", "linux"), patch.object(lab.os, "getuid", return_value=0), \
                patch.object(lab.os, "open") as opened:
            for path in ("/tmp/weights", "/dev/shm/../private", "/dev/shm/nib-", "/dev/shm/nib-one/child"):
                with self.assertRaises(ValueError):
                    lab.prepare_ram_stage(path)
            opened.assert_not_called()

    def test_private_init_is_repeatable(self):
        with tempfile.TemporaryDirectory() as parent:
            target = Path(parent).resolve() / "lab"
            self.assertEqual(lab.private_directory(target, create=True), target)
            self.assertEqual(lab.private_directory(target, create=True), target)
            self.assertEqual(target.stat().st_mode & 0o777, 0o700)

    def test_relative_rejected(self):
        with self.assertRaises(ValueError):
            lab.private_directory("relative", create=True)

    def test_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as parent:
            link = Path(parent) / "link"
            link.symlink_to(parent, target_is_directory=True)
            with self.assertRaises(ValueError):
                lab.private_directory(link, create=True)

    def test_nonprivate_parent_rejected(self):
        with tempfile.TemporaryDirectory() as parent:
            Path(parent).chmod(0o755)
            with self.assertRaises(ValueError):
                lab.private_directory(Path(parent).resolve() / "lab", create=True)

    def test_invalid_bundle_never_executes(self):
        with tempfile.TemporaryDirectory() as parent:
            (Path(parent) / "source-45e7a09.bundle").write_bytes(b"not a bundle")
            with patch.object(lab, "command") as command:
                with self.assertRaises(ValueError):
                    lab.checkout(Path(parent))
                command.assert_not_called()

    def test_symlink_bundle_never_executes(self):
        with tempfile.TemporaryDirectory() as parent:
            (Path(parent) / "source-45e7a09.bundle").symlink_to(SOURCE)
            with patch.object(lab, "command") as command:
                with self.assertRaises(ValueError):
                    lab.checkout(Path(parent))
                command.assert_not_called()

    def test_prerequisites_are_booleans(self):
        with patch.object(lab.shutil, "which", return_value=None):
            self.assertTrue(all(value is False for value in lab.prerequisites().values()))


class ExactCheckoutTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.parent = Path(self.temporary.name).resolve()
        self.root = self.parent / "lab"
        self.root.mkdir(mode=0o700)
        self.repo = self.parent / "public-source"
        self.repo.mkdir(mode=0o700)
        templates = self.parent / "empty-templates"
        templates.mkdir()
        self.env = {**lab.ENV, "GIT_TEMPLATE_DIR": str(templates),
                    "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
                    "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}
        self.git("init", "--quiet")
        (self.repo / "source.txt").write_bytes(b"first\r\n")
        (self.repo / "entry.py").write_bytes(b"raise AssertionError('must-not-execute')\n")
        (self.repo / "entry.py").chmod(0o700)
        self.commit()
        self.first = self.git("rev-parse", "HEAD").decode().strip()
        (self.repo / "source.txt").write_bytes(b"second\n")
        self.commit()
        self.revision = self.git("rev-parse", "HEAD").decode().strip()
        self.bundle = self.root / "source.bundle"
        self.make_bundle()

    def git(self, *args):
        return subprocess.run(["/usr/bin/git", "-c", "core.hooksPath=/dev/null", "-C", str(self.repo), *args],
                              env=self.env, capture_output=True, check=True, timeout=15).stdout

    def commit(self):
        self.git("add", ".")
        self.git("commit", "--quiet", "-m", "synthetic source")

    def make_bundle(self, *revisions):
        self.bundle.unlink(missing_ok=True)
        self.git("bundle", "create", str(self.bundle), *(revisions or ("HEAD",)))
        self.digest = hashlib.sha256(self.bundle.read_bytes()).hexdigest()

    def test_exact_ancestor_not_head_and_private_regular_bytes(self):
        row = lab.checkout(self.root, self.first, self.digest)
        self.assertEqual(row, {"source_revision": self.first, "source_bundle_sha256": self.digest})
        source = self.root / "source"
        self.assertEqual((source / "source.txt").read_bytes(), b"first\r\n")
        self.assertEqual((source / "entry.py").stat().st_mode & 0o777, 0o700)
        self.assertEqual((source / "source.txt").stat().st_mode & 0o777, 0o600)
        self.assertEqual(source.stat().st_mode & 0o777, 0o700)
        self.assertEqual((source / ".git/HEAD").read_text(), self.first + "\n")
        self.assertFalse(list(self.root.glob(".checkout-*")))
        self.assertFalse((source / ".git/hooks").exists())
        self.assertNotIn("[remote", (source / ".git/config").read_text())
        result = subprocess.run(["/usr/bin/git", "-C", str(source), "status", "--porcelain"],
                                env=self.env, capture_output=True, check=True, timeout=15)
        self.assertEqual(result.stdout, b"")

    def test_historical_default_keeps_its_name_and_pins(self):
        self.bundle.rename(self.root / "source-45e7a09.bundle")
        with patch.object(lab, "REVISION", self.revision), patch.object(lab, "BUNDLE_SHA256", self.digest):
            self.assertEqual(lab.checkout(self.root)["source_revision"], self.revision)

    def test_partial_pair_and_nonliteral_pins_never_run_git(self):
        cases = [(self.revision, None), (None, self.digest), ("HEAD", self.digest),
                 (self.revision[:12], self.digest), (self.revision.upper(), self.digest),
                 ("0" * 40, self.digest), (self.revision + "\n", self.digest),
                 (self.revision, self.digest.upper()), (self.revision, self.digest[:-1])]
        with patch.object(lab, "command") as command:
            for revision, digest in cases:
                with self.subTest(revision=revision, digest=digest), self.assertRaises(ValueError):
                    lab.checkout(self.root, revision, digest)
            command.assert_not_called()
        self.assertFalse((self.root / "source").exists())

    def test_wrong_hash_and_unsafe_transfer_never_run_git(self):
        original = self.bundle.read_bytes()
        for variant in ("wrong-hash", "symlink", "hardlink", "fifo", "oversize"):
            with self.subTest(variant=variant):
                self.bundle.unlink()
                other = self.parent / "transfer"
                other.unlink(missing_ok=True)
                other.write_bytes(original)
                if variant == "symlink":
                    self.bundle.symlink_to(other)
                elif variant == "hardlink":
                    os.link(other, self.bundle)
                elif variant == "fifo":
                    os.mkfifo(self.bundle)
                else:
                    self.bundle.write_bytes(original + b"changed")
                    if variant == "oversize":
                        with self.bundle.open("r+b") as stream:
                            stream.truncate(lab.MAX_BUNDLE + 1)
                with patch.object(lab, "command") as command:
                    with self.assertRaises((ValueError, OSError)):
                        lab.checkout(self.root, self.revision, self.digest)
                    command.assert_not_called()
        self.assertFalse((self.root / "source").exists())

    def test_transfer_replaced_after_verification_cannot_supply_git_bytes(self):
        original_command = lab.command
        def replace_then_command(argv, **kwargs):
            self.bundle.write_bytes(b"substituted transfer")
            return original_command(argv, **kwargs)
        with patch.object(lab, "command", side_effect=replace_then_command):
            row = lab.checkout(self.root, self.revision, self.digest)
        self.assertEqual(row["source_bundle_sha256"], self.digest)
        self.assertEqual((self.root / "source/source.txt").read_bytes(), b"second\n")

    def test_changed_open_file_identity_is_rejected(self):
        original_stat = lab.os.stat
        def replaced(*args, **kwargs):
            if args[0] == "source.bundle":
                self.bundle.unlink()
                self.bundle.write_bytes(b"replacement")
            return original_stat(*args, **kwargs)
        with lab.directory(self.root) as fd, patch.object(lab.os, "stat", side_effect=replaced):
            with self.assertRaises(ValueError):
                lab.read_file(fd, "source.bundle", lab.MAX_BUNDLE)

    def test_existing_destination_and_lock_never_run_git(self):
        source = self.root / "source"
        source.mkdir()
        marker = source / "keep"
        marker.write_bytes(b"owned")
        with patch.object(lab, "command") as command, self.assertRaises(FileExistsError):
            lab.checkout(self.root, self.revision, self.digest)
        command.assert_not_called()
        self.assertEqual(marker.read_bytes(), b"owned")
        with lab.directory(self.root) as fd:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(lab, "command") as command, self.assertRaises(BlockingIOError):
                lab.checkout(self.root, self.revision, self.digest)
            command.assert_not_called()

    def test_missing_commit_and_incremental_bundle_cannot_complete(self):
        with self.assertRaises(ValueError):
            lab.checkout(self.root, "1" * 40, self.digest)
        self.assertFalse((self.root / "source/source.txt").exists())
        second_root = self.parent / "incremental"
        second_root.mkdir(mode=0o700)
        self.bundle = second_root / "source.bundle"
        self.make_bundle("HEAD", "^" + self.first)
        with self.assertRaises(ValueError):
            lab.checkout(second_root, self.revision, self.digest)
        self.assertFalse((second_root / "source/source.txt").exists())

    def test_symlink_gitlink_and_excess_tree_rejected_before_materialization(self):
        oid = "1" * 40
        for row in (f"120000 blob {oid} 4\tlink\0", f"160000 commit {oid} -\tmodule\0",
                    f"100644 blob {oid} 1\t../escape\0", f"100644 blob {oid} 1\t.GIT/config\0",
                    f"100644 blob {oid} {lab.MAX_BUNDLE + 1}\tlarge\0"):
            with self.subTest(row=row), self.assertRaises(ValueError):
                lab.expected_tree(row.encode())
        (self.repo / "link").symlink_to("/outside")
        self.commit()
        self.revision = self.git("rev-parse", "HEAD").decode().strip()
        self.make_bundle()
        with self.assertRaises(ValueError):
            lab.checkout(self.root, self.revision, self.digest)
        self.assertFalse((self.root / "source/link").exists())
        self.assertFalse((self.root / "source/source.txt").exists())

    def test_environment_templates_attributes_filters_and_replacements_not_imported(self):
        marker = self.parent / "must-not-run"
        hook = self.parent / "hook"
        hook.write_text("#!/bin/sh\ntouch " + str(marker) + "\n")
        hook.chmod(0o700)
        template = self.parent / "hostile-template"
        (template / "hooks").mkdir(parents=True)
        (template / "hooks/post-checkout").write_bytes(hook.read_bytes())
        (template / "hooks/post-checkout").chmod(0o700)
        config = self.parent / "hostile-config"
        config.write_text(f"[core]\n hooksPath = {template / 'hooks'}\n[filter \"evil\"]\n smudge = {hook}\n required = true\n")
        (self.repo / ".gitattributes").write_text("source.txt filter=evil\n")
        self.commit()
        selected = self.git("rev-parse", "HEAD").decode().strip()
        self.git("replace", selected, self.first)
        self.make_bundle("--all")
        with patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(config), "GIT_TEMPLATE_DIR": str(template),
                                     "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.hooksPath",
                                     "GIT_CONFIG_VALUE_0": str(template / "hooks"), "GIT_ALLOW_PROTOCOL": "all"}):
            row = lab.checkout(self.root, selected, self.digest)
        self.assertEqual(row["source_revision"], selected)
        self.assertEqual((self.root / "source/source.txt").read_bytes(), b"second\n")
        self.assertFalse(marker.exists())
        self.assertFalse((self.root / "source/.git/refs/replace").exists())
        self.assertFalse((self.root / "source/.git/hooks").exists())

    def test_archive_attribute_omissions_and_substitution_fail_closed(self):
        for number, attribute in enumerate(("export-ignore", "text eol=crlf", "export-subst")):
            with self.subTest(attribute=attribute):
                (self.repo / ".gitattributes").write_text("source.txt " + attribute + "\n")
                (self.repo / "source.txt").write_bytes(b"$Format:%H$\n")
                self.commit()
                self.revision = self.git("rev-parse", "HEAD").decode().strip()
                root = self.parent / ("attributes-" + str(number))
                root.mkdir(mode=0o700)
                self.bundle = root / "source.bundle"
                self.make_bundle()
                with self.assertRaises(ValueError):
                    lab.checkout(root, self.revision, self.digest)
                self.assertNotEqual((root / "source/.git/HEAD").read_text(), self.revision + "\n")

    def test_archive_links_duplicates_wrong_bytes_and_missing_files_rejected(self):
        data = b"original"
        oid = hashlib.sha1(b"blob 8\0" + data).hexdigest()
        expected = {"file": ("100644", oid, len(data))}
        for variant in ("link", "duplicate", "wrong-bytes", "missing"):
            with self.subTest(variant=variant):
                raw = io.BytesIO()
                with tarfile.open(fileobj=raw, mode="w") as archive:
                    info = tarfile.TarInfo("file")
                    info.size = len(data)
                    if variant == "link":
                        info.type, info.linkname, info.size = tarfile.SYMTYPE, "/outside", 0
                        archive.addfile(info)
                    elif variant != "missing":
                        archive.addfile(info, io.BytesIO(b"modified" if variant == "wrong-bytes" else data))
                        if variant == "duplicate":
                            archive.addfile(info, io.BytesIO(data))
                target = self.parent / variant
                target.mkdir(mode=0o700)
                with self.assertRaises(ValueError):
                    lab.materialize(target, raw.getvalue(), expected, time.monotonic() + 10)

    def test_root_replaced_during_git_is_not_reported_successful(self):
        original_command = lab.command
        def replace_after_command(argv, **kwargs):
            result = original_command(argv, **kwargs)
            self.root.rename(self.parent / "original-root")
            self.root.mkdir(mode=0o700)
            return result
        with patch.object(lab, "command", side_effect=replace_after_command):
            with self.assertRaises((ValueError, OSError)):
                lab.checkout(self.root, self.revision, self.digest)
        self.assertFalse((self.root / "source").exists())

    def test_tree_counts_sizes_and_case_collisions_are_bounded(self):
        oid = "1" * 40
        one = f"100644 blob {oid} 1\tfile\0".encode()
        for raw in (one * (lab.MAX_FILES + 1), one + one.replace(b"file", b"FILE"),
                    one.replace(b"file", b"Dir/a") + one.replace(b"file", b"dir/b"),
                    b"".join(f"100644 blob {oid} {lab.MAX_BUNDLE}\tf{i}\0".encode() for i in range(5))):
            with self.assertRaises(ValueError):
                lab.expected_tree(raw)

    def test_helper_hash_and_shared_timeout_and_output_bounds(self):
        with patch.object(lab, "PROBE_SHA256", "0" * 64), self.assertRaises(ValueError):
            lab.command([sys.executable, "-c", "raise AssertionError('must-not-run')"])
        with self.assertRaises(ValueError):
            lab.command([sys.executable, "-c", "import os; os.write(1,b'x'*65536)"], limit=1024)
        with self.assertRaises(ValueError):
            lab.command([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.1)
        with patch.object(lab, "CHECKOUT_SECONDS", 0), patch.object(lab, "command") as command:
            with self.assertRaises(ValueError):
                lab.checkout(self.root, self.revision, self.digest)
            command.assert_not_called()

    def test_late_git_result_cannot_reset_the_shared_deadline(self):
        clock = type("Clock", (), {"now": 0.0, "monotonic": lambda self: self.now})()
        original_command = lab.command
        def late(argv, **kwargs):
            self.assertEqual(kwargs["timeout"], 60)
            result = original_command(argv, **kwargs)
            clock.now = lab.CHECKOUT_SECONDS + 1
            return result
        with patch.object(lab, "time", clock), patch.object(lab, "command", side_effect=late) as command:
            with self.assertRaises(ValueError):
                lab.checkout(self.root, self.revision, self.digest)
            self.assertEqual(command.call_count, 1)
        self.assertFalse((self.root / "source/source.txt").exists())

    def test_cli_pair_scope_and_sanitized_errors(self):
        for args in (("checkout", "--revision", self.revision),
                     ("init", "--revision", self.revision, "--bundle-sha256", self.digest),
                     ("checkout", "--unexpected-private-value")):
            result = subprocess.run([sys.executable, "-I", "-S", str(SOURCE), *args, "--root", str(self.root)],
                                    capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stderr, b"")
            self.assertEqual(json.loads(result.stdout), {"result": "fail", "reason": "lab-preparation-rejected"})

    def test_real_cli_current_source_receipt_output(self):
        result = subprocess.run([sys.executable, "-I", "-S", str(SOURCE), "checkout", "--root", str(self.root),
                                 "--revision", self.revision, "--bundle-sha256", self.digest],
                                capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, b"")
        self.assertEqual(json.loads(result.stdout), {"operation": "checkout", "confidential_qualified": False,
                         "source_revision": self.revision, "source_bundle_sha256": self.digest,
                         "script_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest()})
