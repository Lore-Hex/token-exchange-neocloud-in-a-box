# SPDX-License-Identifier: Apache-2.0
"""Synthetic archives and files only; no package installation or payload execution."""

import copy
import configparser
import gzip
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
tool = types.ModuleType("guest_preparation_test")
tool.__file__ = str(ROOT / "scripts/prepare_guest_packages.py")
exec(compile(Path(tool.__file__).read_bytes(), tool.__file__, "exec"), tool.__dict__)


def ar_member(name, raw):
    header = f"{name + '/':<16}{0:<12}{0:<6}{0:<6}{'100644':<8}{len(raw):<10}`\n".encode()
    return header + raw + (b"\n" if len(raw) % 2 else b"")


def deb(entries=None, control=None):
    if control is None:
        control = b"Package: synthetic\nVersion: 1.0\nArchitecture: amd64\nDescription: public fixture\n"
    entries = entries if entries is not None else [("control", control, None), ("postinst", b"exit 99\n", None)]
    buf = io.BytesIO()
    with tool.tarfile.open(fileobj=buf, mode="w", format=tool.tarfile.USTAR_FORMAT) as archive:
        for name, data, kind in entries:
            row = tool.tarfile.TarInfo(name)
            row.type = kind or tool.tarfile.REGTYPE
            row.linkname = "/PRIVATE" if kind == tool.tarfile.SYMTYPE else ""
            row.size = len(data) if row.isfile() else 0
            archive.addfile(row, io.BytesIO(data) if row.isfile() else None)
    packed = gzip.compress(buf.getvalue(), mtime=0)
    return b"!<arch>\n" + ar_member("debian-binary", b"2.0\n") + ar_member("control.tar.gz", packed) + ar_member("data.tar", b"payload-never-executed")


def row(raw):
    result = dict.fromkeys(tool.inputs.FIELDS, "")
    result.update(Package="synthetic", Version="1.0", Architecture="amd64",
                  Filename="pool/synthetic.deb", Size=str(len(raw)), SHA256=tool.digest(raw), index="fixture")
    return result


class ControlTests(unittest.TestCase):
    def test_authenticated_trailing_builder_signature_is_not_executed_or_trusted(self):
        raw = deb() + ar_member("_gpgbuilder", b"not an authentication claim")
        tool.controls(raw, row(raw))
        for extra in (ar_member("_gpgbuilder", b""), ar_member("_gpgbuilder", b"x" * 16385),
                      ar_member("_gpgbuilder", b"x") * 2):
            bad = deb() + extra
            with self.assertRaises(ValueError):
                tool.controls(bad, row(bad))

    def test_controls_preserve_public_metadata_and_hash_never_execute_scripts(self):
        raw = deb(control=b"Package: synthetic\nVersion: 1.0\nArchitecture: amd64\nReplaces: old\nDescription: first\n second\n")
        index, observations = tool.controls(raw, row(raw))
        self.assertIn(b"Replaces: old\n", index)
        self.assertIn(b"Description: first\n second\n", index)
        self.assertIn(("SHA256: " + tool.digest(raw)).encode(), index)
        self.assertEqual(observations["postinst"], {"bytes": 8, "sha256": tool.digest(b"exit 99\n")})
        self.assertNotIn(b"exit 99", index)

    def test_reject_ar_ambiguity_truncation_and_extra_members(self):
        raw = deb()
        for bad in (raw[1:], raw[:-1], raw + ar_member("extra", b"x"),
                    raw.replace(b"2.0\n", b"3.0\n", 1),
                    raw.replace(b"control.tar.gz", b"otherfile-name", 1),
                    raw.replace(b"`\n", b"xx", 1)):
            with self.subTest(prefix=bad[:30]), self.assertRaises(ValueError):
                tool.controls(bad, row(bad))

    def test_reject_control_links_duplicates_traversal_and_unknown_types(self):
        control = b"Package: synthetic\nVersion: 1.0\nArchitecture: amd64\n"
        for entries in (
            [("control", b"", tool.tarfile.SYMTYPE)],
            [("control", control, None), ("control", control, None)],
            [("../control", control, None)],
            [("/control", control, None)],
            [("subdir", b"", tool.tarfile.DIRTYPE)],
            [("control", b"", tool.tarfile.FIFOTYPE)],
            [("postinst", b"exit 99\n", None)],
        ):
            raw = deb(entries)
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                tool.controls(raw, row(raw))

    def test_reject_control_inconsistency_and_injected_archive_fields(self):
        base = b"Package: synthetic\nVersion: 1.0\nArchitecture: amd64\n"
        for text in (base + b"Package: other\n", base + b"Size: 12\n",
                     base + b"SHA256: forged\n", base + b"Filename: /PRIVATE\n",
                     base.replace(b"1.0", b"2.0"), base + b"bad header\n", base + b"\x00"):
            raw = deb(control=text)
            with self.subTest(text=text), self.assertRaises(ValueError):
                tool.controls(raw, row(raw))

    def test_control_decompression_is_bounded(self):
        with mock.patch.object(tool, "MAX_CONTROL", 1024):
            with self.assertRaises(ValueError):
                tool.decompress("control.tar.gz", gzip.compress(b"x" * 1025))
            value = subprocess.CompletedProcess([], 0, b"x" * 1025, b"")
            with mock.patch.object(tool.shutil, "which", return_value="/fixed/zstd"), \
                    mock.patch.object(tool.processes, "execute_process_group", return_value=value) as execute:
                with self.assertRaises(ValueError):
                    tool.decompress("control.tar.zst", b"fixed")
            self.assertEqual(execute.call_args.kwargs["max_output_bytes"], 1024)
            self.assertEqual(execute.call_args.kwargs["timeout"], 20)
            self.assertEqual(execute.call_args.kwargs["env"], tool.TOOL_ENV)
            self.assertNotIn("LD_PRELOAD", execute.call_args.kwargs["env"])

    def test_directory_entries_count_toward_tar_budget(self):
        entries = [(".", b"", tool.tarfile.DIRTYPE)] * 64
        entries.append(("control", b"Package: synthetic\nVersion: 1.0\nArchitecture: amd64\n", None))
        raw = deb(entries)
        with self.assertRaises(ValueError):
            tool.controls(raw, row(raw))


class PreparationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.archives = self.root / "archives"
        self.archives.mkdir()
        self.raw = deb()
        self.row = row(self.raw)
        self.path = self.archives / (self.row["SHA256"] + ".deb")
        self.path.write_bytes(self.raw)
        self.lock = {"archive_bytes": len(self.raw), "packages": [self.row], "indexes": []}

    def prepare(self):
        with mock.patch.object(tool.inputs, "check"):
            return tool.prepare(self.lock, self.root, self.archives, "a" * 40, {"public": "b" * 64})

    def test_deterministic_complete_repository_and_nonqualification_receipt(self):
        files, report = self.prepare()
        self.assertEqual((files, report), self.prepare())
        self.assertEqual(files["repo/pool/" + tool.package_name(self.row)], self.raw)
        self.assertEqual(tool.package_name(self.row).partition("_")[0], self.row["Package"])
        self.assertIn(b"Packages=synthetic=1.0\n", files["mkosi.conf.candidate"])
        for setting in (b"Bootable=no", b"WithNetwork=no", b"CacheOnly=always", b"ToolsTree=tools-tree"):
            self.assertIn(setting, files["mkosi.conf.candidate"])
        config = configparser.ConfigParser()
        config.read_string(files["mkosi.conf.candidate"].decode())
        self.assertEqual(config["Content"]["SourceDateEpoch"], "1792454400")
        self.assertEqual(config["Build"]["ToolsTree"], "tools-tree")
        self.assertEqual(config["Output"]["Format"], "directory")
        for key in ("maintainer_scripts_executed", "apt_simulation_executed", "installable_verified",
                    "image_built", "boot_validated", "hardware_validated", "deployable"):
            self.assertIs(report[key], False)
        for name, metadata in report["files"].items():
            self.assertEqual(metadata, {"bytes": len(files[name]), "sha256": tool.digest(files[name])})
        self.assertNotIn(str(self.root), json.dumps(report))
        self.assertNotIn("postinst", files["repo/Packages"].decode())

    def test_mismatch_missing_extra_archive_and_total_reject(self):
        for mutation in ("hash", "missing", "extra", "size", "total"):
            with self.subTest(mutation=mutation):
                original = copy.deepcopy(self.lock)
                self.path.write_bytes(self.raw)
                if mutation == "hash":
                    self.path.write_bytes(b"x" + self.raw[1:])
                elif mutation == "missing":
                    self.path.unlink()
                elif mutation == "extra":
                    (self.archives / "extra.deb").write_bytes(self.raw)
                elif mutation == "size":
                    self.row["Size"] = str(len(self.raw) + 1)
                else:
                    self.lock["archive_bytes"] += 1
                with self.assertRaises(ValueError):
                    self.prepare()
                (self.archives / "extra.deb").unlink(missing_ok=True)
                self.lock = original
                self.row = self.lock["packages"][0]

    def test_links_fifo_ancestor_and_changed_identity_reject(self):
        for kind in ("symlink", "hardlink", "fifo"):
            self.path.unlink()
            target = self.root / ("target-" + kind)
            target.write_bytes(self.raw)
            if kind == "symlink":
                self.path.symlink_to(target)
            elif kind == "hardlink":
                os.link(target, self.path)
            else:
                os.mkfifo(self.path)
            with self.subTest(kind=kind), self.assertRaises((ValueError, OSError)):
                self.prepare()
        self.path.unlink()
        self.path.write_bytes(self.raw)
        alias = self.root / "alias"
        alias.symlink_to(self.archives, target_is_directory=True)
        with self.assertRaises(OSError):
            with tool.directory(alias):
                self.fail("ancestor symlink admitted")
        real_stat = tool.os.stat
        def changed(*args, **kwargs):
            value = real_stat(*args, **kwargs)
            fields = {name: getattr(value, name) for name in
                      ("st_dev", "st_ino", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")}
            fields["st_ino"] += 1
            return types.SimpleNamespace(**fields)
        with tool.directory(self.archives) as fd, mock.patch.object(tool.os, "stat", side_effect=changed):
            with self.assertRaises(ValueError):
                tool.read_at(fd, self.path.name, tool.MAX_ARCHIVE)

    def test_invalid_metadata_and_expired_budget_reject_before_publication(self):
        with mock.patch.object(tool.inputs, "check", side_effect=ValueError("fixed")):
            with self.assertRaises(ValueError):
                tool.prepare(self.lock, self.root, self.archives, "a" * 40, {})
        with mock.patch.object(tool.time, "monotonic", side_effect=[0, 601]):
            with self.assertRaises(ValueError):
                self.prepare()
        self.assertEqual(set(self.root.iterdir()), {self.archives})

    def test_metadata_copied_exactly_and_rechecked(self):
        raw = b"public authenticated fixture"
        path = self.root / "fixture.packages"
        path.write_bytes(raw)
        self.lock["indexes"] = [{"file": path.name, "bytes": len(raw), "sha256": tool.digest(raw)}]
        files, _ = self.prepare()
        self.assertEqual(files["metadata/" + path.name], raw)
        for bad in (b"changed", raw + b"x"):
            path.write_bytes(bad)
            with self.assertRaises(ValueError):
                self.prepare()
        path.unlink()
        path.symlink_to(self.path)
        with self.assertRaises(OSError):
            self.prepare()

    def test_private_publication_noclobber_and_receipt_last(self):
        files, report = self.prepare()
        destination = self.root / "bundle"
        tool.publish(destination, files)
        tool.verify(destination, files)
        with mock.patch.object(tool.inputs, "check"):
            again = tool.prepare(self.lock, self.root, destination / "repo/pool", "a" * 40,
                                 {"public": "b" * 64}, from_bundle=True)
        self.assertEqual(again, (files, report))
        self.assertEqual(json.loads((destination / "receipt.json").read_bytes()), report)
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o700)
        self.assertTrue(all(stat.S_IMODE(p.stat().st_mode) == 0o600
                            for p in destination.rglob("*") if p.is_file()))
        with self.assertRaises(FileExistsError):
            tool.publish(destination, files)
        second = self.root / "partial"
        with mock.patch.object(tool.os, "fsync", side_effect=OSError("fixed")):
            with self.assertRaises(OSError):
                tool.publish(second, files)
        self.assertFalse((second / "receipt.json").exists())
        with self.assertRaises(FileExistsError):
            tool.publish(second, files)

    def test_publication_rejects_unsafe_parent_and_relative_output(self):
        files, _ = self.prepare()
        alias = self.root / "alias"
        alias.symlink_to(self.archives, target_is_directory=True)
        with self.assertRaises(OSError):
            tool.publish(alias / "bundle", files)
        with self.assertRaises(ValueError):
            tool.publish(Path("relative"), files)
        self.archives.chmod(0o777)
        with self.assertRaises(ValueError):
            tool.publish(self.archives / "bundle", files)

    def test_verify_rejects_extra_missing_mutated_links_and_mode(self):
        files, _ = self.prepare()
        destination = self.root / "bundle"
        tool.publish(destination, files)
        target = destination / "packages.txt"
        original = target.read_bytes()
        for kind in ("extra", "missing", "mutated", "receipt", "symlink", "hardlink", "fifo", "mode"):
            with self.subTest(kind=kind):
                extra = destination / "extra"
                if kind == "extra":
                    extra.write_text("private")
                elif kind == "receipt":
                    (destination / "receipt.json").write_text("{}")
                elif kind == "mode":
                    target.chmod(0o644)
                else:
                    target.unlink()
                    if kind == "mutated":
                        target.write_text("private")
                        target.chmod(0o600)
                    elif kind == "symlink":
                        target.symlink_to(self.path)
                    elif kind == "hardlink":
                        os.link(self.path, target)
                    elif kind == "fifo":
                        os.mkfifo(target, 0o600)
                with self.assertRaises((ValueError, OSError)):
                    tool.verify(destination, files)
                extra.unlink(missing_ok=True)
                target.unlink(missing_ok=True)
                target.write_bytes(original)
                target.chmod(0o600)
                (destination / "receipt.json").write_bytes(files["receipt.json"])
        tool.verify(destination, files)


class SourceTests(unittest.TestCase):
    def test_committed_source_binding_and_offline_git_controls(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        for name in tool.SOURCE_FILES:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((ROOT / name).read_bytes())
        env = {**tool.GIT_ENV, "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
               "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}
        def git(*args):
            return subprocess.run(["git", "-C", str(root), *args], env=env, check=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15).stdout
        git("init", "-q")
        git("add", ".")
        git("-c", "commit.gpgsign=false", "commit", "-qm", "fixture")
        with mock.patch.object(tool, "ROOT", root), \
                mock.patch.object(tool.processes, "execute_process_group",
                                  wraps=tool.processes.execute_process_group) as execute:
            revision, sources = tool.committed_inputs()
            self.assertEqual(revision, git("rev-parse", "HEAD").decode().strip())
            self.assertEqual(set(sources), set(tool.SOURCE_FILES))
            for call in execute.call_args_list:
                self.assertEqual(call.kwargs["env"]["GIT_NO_LAZY_FETCH"], "1")
                self.assertEqual(call.kwargs["env"]["GIT_ALLOW_PROTOCOL"], "")
                self.assertEqual(call.kwargs["env"]["GIT_NO_REPLACE_OBJECTS"], "1")
                self.assertEqual(call.kwargs["env"]["GIT_TERMINAL_PROMPT"], "0")
            (root / tool.SOURCE_FILES[0]).write_bytes(b"# changed\n")
            with self.assertRaises(ValueError):
                tool.committed_inputs()


if __name__ == "__main__":
    unittest.main()
