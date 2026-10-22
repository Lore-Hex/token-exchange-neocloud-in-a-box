# SPDX-License-Identifier: Apache-2.0
"""Private local export fixtures, never a native build, boot or cgroup proof."""
from contextlib import contextmanager, ExitStack, nullcontext
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import time
import unittest
from unittest import mock

import mkosi_export as export
from test_mkosi_build import host, launcher, worker, load, ROOT
import test_mkosi_boot as bt
import test_mkosi_verity as vt

publication = load("scripts/pilot/export.py")
base = host.base


def identity(raw):
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def proof(raws):
    source = {"git_sha": "ab" * 20, "sources": {host.EXPORT: base.digest(b"fixture source")}}
    root_hash = base.digest(b"fixture root hash, not a measured image")
    files = {name: identity(raw) for name, raw in raws.items()}
    root = {"schema": "nib.private-verity-root/v1", "result": "pass", "root_hash": root_hash,
            "images": {"root.raw": files["root.raw"]}}
    root.update(dict.fromkeys(("erofs_checked", "userspace_verity_verified", "wrong_root_rejected",
                              "boot_partuuids_checked", "private_images_removed"), True))
    root.update(dict.fromkeys(("images_exported", "boot_verified", "kernel_verity_verified",
                              "reproducibility_verified", "confidentiality_verified"), False))
    uki = {"schema": "nib.private-boot-uki/v1", "result": "pass", "root_hash": root_hash,
           "uki": {"image": files["nib.efi"]}}
    uki.update(dict.fromkeys(("archive_checked", "sections_checked", "private_images_removed"), True))
    uki.update(dict.fromkeys(("signed", "images_exported", "boot_verified",
                             "reproducibility_verified", "confidentiality_verified"), False))
    staged = {"schema": "nib.boot-artifact-stage/v1", "source": source,
              "root_hash": root_hash, "files": files, "published": False}
    report = {"schema": "nib.mkosi-installation/v1", "result": "pass", "stage": "complete",
        "git_sha": source["git_sha"], "payload_removed": True, "process_cleanup_confirmed": True,
        "bootable": False, "deployable": False, "worker": {
            "schema": "nib.mkosi-install-worker/v1", "result": "pass", "stage": "boot-uki",
            "configured_state_verified": True, "installed_packages": 212, "dkms_built": True,
            "bootable": False, "deployable": False,
            "driver_build": {"result": "pass", "installed": True}, "runtime_policy": {"result": "pass"},
            "appraisal_cli": {"result": "pass"}, "verity_root": root, "boot_uki": uki,
            "artifact_stage": staged}}
    return report, source


class ExportTests(unittest.TestCase):
    @contextmanager
    def fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp).resolve()
            parent.chmod(0o700)
            evidence, stage = parent / "evidence", parent / "stage"
            stage.mkdir(mode=0o700)
            raws = {"root.raw": b"synthetic GPT fixture\n", "nib.efi": b"synthetic unsigned UKI fixture\n"}
            for name, raw in raws.items():
                (stage / name).write_bytes(raw)
            report, source = proof(raws)
            run = {"result": "pass", "git_sha": source["git_sha"], "hardware_validated": False}
            base.publish(evidence, {"run.json": export.encoded(run), "result.json": export.encoded(report)})
            resources = {"schema": "nib.mkosi-build-resources/v1", "result": "pass",
                "child_exit_code": 0, "oom_kill": 0, "limits_read_back": True,
                "empty_verified": True, "group_removed": True, "limits": launcher.resources.LIMITS}
            evidence.with_name(evidence.name + "-resources.json").write_bytes(export.encoded(resources))
            yield evidence, stage, raws, report, source

    def prepare(self, evidence, stage, report):
        with export.Pending(evidence, base, publication) as pending:
            pending.capture(stage, report["worker"]["artifact_stage"])
            pending.accept(report, evidence)

    def test_success_keeps_exact_bytes_and_receipts_without_qualification(self):
        with self.fixture() as (evidence, stage, raws, report, source):
            self.prepare(evidence, stage, report)
            pending, final = export.paths(evidence)
            self.assertFalse(final.exists())
            self.assertEqual(export.finish(evidence, base, publication, source), final)
            self.assertFalse(pending.exists())
            self.assertEqual({p.name for p in final.iterdir()},
                             {"root.raw", "nib.efi", "manifest.json", "run.json", "result.json", "resources.json"})
            manifest = json.loads((final / "manifest.json").read_bytes())
            self.assertEqual(manifest["source"], source)
            self.assertEqual(manifest["root_hash"], report["worker"]["verity_root"]["root_hash"])
            self.assertEqual(manifest["files"], {n: identity(r) for n, r in raws.items()})
            for name, raw in raws.items():
                self.assertEqual((final / name).read_bytes(), raw)
                self.assertNotEqual((final / name).stat().st_ino, (stage / name).stat().st_ino)
            for name in ("run.json", "result.json"):
                self.assertEqual((final / name).read_bytes(), (evidence / name).read_bytes())
            for name, row in manifest["receipts"].items():
                self.assertEqual(row, identity((final / name).read_bytes()))
            for name in ("signed", "boot_verified", "reproducibility_verified", "confidentiality_verified"):
                self.assertIs(manifest[name], False)
            self.assertIs(manifest["local_only"], True)
            self.assertEqual(stat.S_IMODE(final.stat().st_mode), 0o700)
            self.assertTrue(all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in final.iterdir()))
            self.assertFalse(any(p.name in export.FILES for p in evidence.iterdir()))

    def test_every_stage_and_root_source_binding_is_required(self):
        paths = [("result",), ("stage",), ("git_sha",), ("payload_removed",), ("process_cleanup_confirmed",),
            ("worker", "result"), ("worker", "stage"), ("worker", "configured_state_verified"),
            ("worker", "installed_packages"), ("worker", "dkms_built"),
            ("worker", "driver_build", "result"), ("worker", "driver_build", "installed"),
            ("worker", "runtime_policy", "result"), ("worker", "appraisal_cli", "result"),
            ("worker", "verity_root", "root_hash"), ("worker", "boot_uki", "root_hash"),
            ("worker", "artifact_stage", "source", "git_sha")]
        for name in ("verity_root", "boot_uki"):
            paths += [("worker", name, "result"), ("worker", name, "private_images_removed")]
        paths += [("worker", "verity_root", name) for name in
                  ("erofs_checked", "userspace_verity_verified", "wrong_root_rejected", "boot_partuuids_checked")]
        paths += [("worker", "boot_uki", name) for name in ("archive_checked", "sections_checked")]
        for path in paths:
            with self.subTest(path=path), self.fixture() as (evidence, stage, _, report, source):
                changed = copy.deepcopy(report)
                row = changed
                for key in path[:-1]: row = row[key]
                row[path[-1]] = None
                # Failures cannot even survive the child-side pending context.
                with self.assertRaises((ValueError, KeyError, TypeError)):
                    self.prepare(evidence, stage, changed)
                self.assertFalse(any(p.exists() for p in export.paths(evidence)))

    def test_finalization_rejects_resource_receipt_failures_and_removes_pending(self):
        changes = [("result", "fail"), ("child_exit_code", 1), ("child_exit_code", False),
                   ("oom_kill", 1), ("limits_read_back", False), ("empty_verified", None),
                   ("group_removed", False), ("limits", {}), ("schema", "wrong")]
        for key, value in changes:
            with self.subTest(key=key), self.fixture() as (evidence, stage, _, report, source):
                self.prepare(evidence, stage, report)
                path = evidence.with_name(evidence.name + "-resources.json")
                resources = json.loads(path.read_bytes())
                resources[key] = value
                path.write_bytes(export.encoded(resources))
                with self.assertRaises(ValueError): export.finish(evidence, base, publication, source)
                self.assertFalse(any(p.exists() for p in export.paths(evidence)))

    def test_finalization_rejects_missing_changed_receipts_source_images_and_metadata(self):
        for bad in ("missing", "run", "result", "source", "image", "mode", "member", "symlink"):
            with self.subTest(bad=bad), self.fixture() as (evidence, stage, _, report, source):
                self.prepare(evidence, stage, report)
                pending, final = export.paths(evidence)
                if bad == "missing": evidence.with_name(evidence.name + "-resources.json").unlink()
                elif bad in ("run", "result"): (evidence / (bad + ".json")).write_bytes(b"{}\n")
                elif bad == "source": source = {**source, "git_sha": "cd" * 20}
                elif bad == "image": (pending / "root.raw").write_bytes(b"changed")
                elif bad == "mode": (pending / "root.raw").chmod(0o644)
                elif bad == "member": (pending / "extra").write_bytes(b"bad")
                else:
                    (pending / "root.raw").unlink()
                    (pending / "root.raw").symlink_to(stage / "root.raw")
                with self.assertRaises((OSError, ValueError)):
                    export.finish(evidence, base, publication, source)
                self.assertFalse(pending.exists())
                self.assertFalse(final.exists())

    def test_pending_capture_copy_failure_and_late_child_failure_cleanup(self):
        for bad in ("copy", "accept", "late", "member"):
            with self.subTest(bad=bad), self.fixture() as (evidence, stage, _, report, _):
                if bad == "copy": (stage / "nib.efi").write_bytes(b"changed")
                if bad == "member": (stage / "extra").write_bytes(b"extra")
                with self.assertRaises(ValueError), export.Pending(evidence, base, publication) as pending:
                    pending.capture(stage, report["worker"]["artifact_stage"])
                    if bad == "accept": report["payload_removed"] = False
                    pending.accept(report, evidence)
                    raise ValueError("late fixture failure")
                self.assertFalse(any(p.exists() for p in export.paths(evidence)))

    def test_collisions_preserved_at_creation_and_atomic_publication(self):
        for which in (0, 1):
            with self.subTest(which=which), self.fixture() as (evidence, stage, _, report, source):
                paths = export.paths(evidence)
                paths[which].mkdir()
                (paths[which] / "sentinel").write_bytes(b"keep")
                with self.assertRaises(ValueError):
                    with export.Pending(evidence, base, publication): self.fail("adopted collision")
                self.assertEqual((paths[which] / "sentinel").read_bytes(), b"keep")
        with self.fixture() as (evidence, stage, _, report, source):
            self.prepare(evidence, stage, report)
            pending, final = export.paths(evidence)
            final.mkdir()
            (final / "sentinel").write_bytes(b"keep")
            with self.assertRaises(OSError): export.finish(evidence, base, publication, source)
            self.assertFalse(pending.exists())
            self.assertEqual((final / "sentinel").read_bytes(), b"keep")

    def test_publication_failure_before_and_after_rename_removes_our_artifacts(self):
        for when in ("before", "after"):
            with self.subTest(when=when), self.fixture() as (evidence, stage, _, report, source):
                self.prepare(evidence, stage, report)
                original = publication.rename_exclusive
                renamed = False
                def rename(*args):
                    nonlocal renamed
                    if when == "before": raise OSError("fixture rename failure")
                    original(*args)
                    renamed = True
                fsync = os.fsync
                def sync(fd):
                    if renamed: raise OSError("fixture post-rename fsync failure")
                    fsync(fd)
                with mock.patch.object(publication, "rename_exclusive", side_effect=rename), \
                        mock.patch.object(export.os, "fsync", side_effect=sync), self.assertRaises(OSError):
                    export.finish(evidence, base, publication, source)
                self.assertFalse(any(p.exists() for p in export.paths(evidence)))

    def test_cleanup_failure_does_not_publish_or_delete_a_replacement(self):
        with self.fixture() as (evidence, stage, _, report, source):
            self.prepare(evidence, stage, report)
            pending, final = export.paths(evidence)
            (pending / "root.raw").write_bytes(b"changed")
            with mock.patch.object(base.inputs, "remove_flat_directory", side_effect=OSError("cleanup failed")), \
                    self.assertRaises(OSError): export.finish(evidence, base, publication, source)
            self.assertFalse(final.exists())
            self.assertTrue(pending.exists())  # Failure is explicit, never claimed clean.
        with self.fixture() as (evidence, _, _, _, _):
            pending, _ = export.paths(evidence)
            with self.assertRaises(ValueError):
                with export.Pending(evidence, base, publication):
                    pending.rename(pending.with_name("original"))
                    pending.mkdir()
                    (pending / "sentinel").write_bytes(b"keep")
                    raise ValueError("fixture abort")
            self.assertEqual((pending / "sentinel").read_bytes(), b"keep")

    def test_flat_cleanup_unlinks_leaf_links_and_specials_without_following(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp).resolve()
            outside, target = parent / "outside", parent / "owned"
            outside.mkdir()
            (outside / "sentinel").write_bytes(b"keep")
            target.mkdir(mode=0o700)
            (target / "regular").write_bytes(b"remove")
            (target / "link").symlink_to(outside, target_is_directory=True)
            os.mkfifo(target / "fifo")
            cwd = os.getcwd()
            with base.directory(parent) as fd:
                base.inputs.remove_flat_directory(fd, target.name, target.stat())
            self.assertFalse(target.exists())
            self.assertEqual((outside / "sentinel").read_bytes(), b"keep")
            self.assertEqual(os.getcwd(), cwd)

    def test_flat_cleanup_rejects_nested_and_excess_entries_before_unlinking(self):
        for bad in ("nested", "excess"):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as tmp:
                parent = Path(tmp).resolve()
                target = parent / "owned"
                target.mkdir(mode=0o700)
                (target / "sentinel").write_bytes(b"keep")
                if bad == "nested": (target / "unexpected").mkdir()
                else:
                    for i in range(16): (target / str(i)).write_bytes(b"keep")
                with base.directory(parent) as fd, self.assertRaises(ValueError):
                    base.inputs.remove_flat_directory(fd, target.name, target.stat())
                self.assertEqual((target / "sentinel").read_bytes(), b"keep")

    def test_flat_cleanup_rejects_replaced_or_linked_directory_and_traversal(self):
        for bad in ("replacement", "symlink", "traversal"):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as tmp:
                parent = Path(tmp).resolve()
                target, moved = parent / "owned", parent / "moved"
                target.mkdir(mode=0o700)
                (target / "sentinel").write_bytes(b"keep original")
                original = target.stat()
                target.rename(moved)
                if bad == "symlink": target.symlink_to(moved, target_is_directory=True)
                else:
                    target.mkdir(mode=0o700)
                    (target / "sentinel").write_bytes(b"keep replacement")
                with base.directory(parent) as fd, self.assertRaises((OSError, ValueError)):
                    base.inputs.remove_flat_directory(fd, "../moved" if bad == "traversal" else target.name, original)
                self.assertEqual((moved / "sentinel").read_bytes(), b"keep original")
                if bad != "symlink": self.assertEqual((target / "sentinel").read_bytes(), b"keep replacement")

    def test_flat_cleanup_checks_final_directory_identity_after_unlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp).resolve()
            target, moved = parent / "owned", parent / "moved"
            target.mkdir(mode=0o700)
            (target / "ours").write_bytes(b"remove")
            original, unlink = target.stat(), os.unlink
            def replace(leaf, *, dir_fd):
                unlink(leaf, dir_fd=dir_fd)
                target.rename(moved)
                target.mkdir(mode=0o700)
                (target / "sentinel").write_bytes(b"keep")
            with base.directory(parent) as fd, mock.patch.object(base.inputs.os, "unlink", side_effect=replace), \
                    self.assertRaises(ValueError):
                base.inputs.remove_flat_directory(fd, target.name, original)
            self.assertEqual((target / "sentinel").read_bytes(), b"keep")
            self.assertTrue(moved.exists())

    def test_flat_cleanup_remains_anchored_when_parent_path_is_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            parent, moved = root / "parent", root / "moved"
            target = parent / "owned"
            target.mkdir(parents=True, mode=0o700)
            (target / "ours").write_bytes(b"remove")
            original = target.stat()
            with base.directory(parent) as fd:
                parent.rename(moved)
                target.mkdir(parents=True, mode=0o700)
                (target / "sentinel").write_bytes(b"keep")
                base.inputs.remove_flat_directory(fd, "owned", original)
            self.assertFalse((moved / "owned").exists())
            self.assertEqual((target / "sentinel").read_bytes(), b"keep")

    def test_stream_copy_rejects_links_specials_bounds_hashes_and_parent_links(self):
        for bad in ("symlink", "hardlink", "fifo", "directory", "size", "hash", "parent", "deadline"):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                path, other = root / "input", root / "sentinel"
                other.write_bytes(b"keep")
                expected = identity(b"keep")
                if bad == "symlink": path.symlink_to(other)
                elif bad == "hardlink": os.link(other, path)
                elif bad == "fifo": os.mkfifo(path)
                elif bad == "directory": path.mkdir()
                else: path.write_bytes(b"keep")
                if bad == "size": expected["bytes"] += 1
                if bad == "hash": expected["sha256"] = base.digest(b"bad")
                if bad == "parent":
                    (root / "alias").symlink_to(root)
                    path = root / "alias/input"
                deadline = time.monotonic() + (120 if bad != "deadline" else -1)
                with self.assertRaises((OSError, ValueError)):
                    export.transfer(path, expected, 4, deadline, base.directory)
                self.assertEqual(other.read_bytes(), b"keep")

    def test_stream_handles_short_writes_and_detects_mutation_after_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp).resolve() / "input"
            raw = b"fixture bytes" * 1000
            path.write_bytes(raw)
            sink = io.BytesIO()
            class ShortWrites:
                def write(self, view): return sink.write(view[:3])
            export.transfer(path, identity(raw), len(raw), time.monotonic() + 120, base.directory, ShortWrites())
            self.assertEqual(sink.getvalue(), raw)
            class Mutates:
                def write(self, view):
                    path.write_bytes(b"x" * len(raw))
                    return len(view)
            with self.assertRaises(ValueError):
                export.transfer(path, identity(raw), len(raw), time.monotonic() + 120, base.directory, Mutates())

    def test_json_and_source_limits_reject_duplicates_constants_paths_and_excess(self):
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b"x" * 65537):
            with self.assertRaises(ValueError): export.parsed(raw)
        for count in (0, 43):
            with self.assertRaises(ValueError):
                export.source_identity({"git_sha": "ab" * 20,
                    "sources": {f"scripts/{i}.py": base.digest(b"x") for i in range(count)}})
        for name in ("/absolute", "../escape", "a//b", "a/./b", "a/../b"):
            with self.assertRaises(ValueError):
                export.source_identity({"git_sha": "ab" * 20, "sources": {name: base.digest(b"x")}})
        report, source = proof({"root.raw": b"fixture disk", "nib.efi": b"fixture uki"})
        report["worker"]["artifact_stage"]["published"] = 0
        with self.assertRaises(ValueError): export.validate_proof(report, source)

    def test_deadline_clears_alarm_and_finalization_timeout_cleans_pending(self):
        with self.fixture() as (evidence, stage, _, report, source):
            self.prepare(evidence, stage, report)
            with mock.patch.object(export, "TIMEOUT", 0.01), \
                    mock.patch.object(export, "transfer", side_effect=lambda *a: time.sleep(1)), \
                    self.assertRaisesRegex(ValueError, "timeout"):
                export.finish(evidence, base, publication, source)
            self.assertEqual(export.signal.getitimer(export.signal.ITIMER_REAL), (0.0, 0.0))
            self.assertFalse(any(p.exists() for p in export.paths(evidence)))

    def test_exact_opt_in_closure_and_default_command_are_unchanged(self):
        good = ["--install-driver", "--verity-root", "--boot-uki", "--stage-boot"]
        self.assertEqual(worker.options(good), (True,) * 5)
        for bad in (["--stage-boot"], good[:-2] + good[-1:], good[::-1], good + ["--stage-boot"]):
            with self.assertRaises(ValueError): worker.options(bad)
        with mock.patch.object(host.tools, "committed", return_value=("ab" * 20, {})) as committed:
            host.committed_sources(True, True, True, True)
            extra = committed.call_args.args[0]
            self.assertEqual(len(extra), 34)
            self.assertEqual(extra[-2:], (host.EXPORT, host.PUBLICATION))
            self.assertEqual(len(set((*host.tools.FILES, *extra))), 42)
        with mock.patch.object(host.os, "readlink", return_value="ns:[1]"), \
                mock.patch.object(host.probe.ownership, "prefix", return_value=[]):
            args = (Path("/root"), Path("/payload"), Path("/out"), {}, True, True, True, True)
            self.assertEqual(host.command(*args, stage_boot=True), host.command(*args) + ["--stage-boot"])
        for entry, flag in ((host, "--stage-boot"), (launcher, "--export-boot")):
            with mock.patch.object(entry.sys, "argv", ["host", "--downloads", "/downloads", "--archive",
                    "/archive", "--output", "/output", flag]), self.assertRaises(ValueError): entry.main()

    def test_launcher_finishes_only_after_receipt_is_closed_and_forwards_explicit_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp).resolve() / "evidence"
            events = []
            @contextmanager
            def receipt(*args):
                events.append("receipt-open")
                yield 99
                events.append("receipt-durable")
            args = ["host", "--downloads", "/downloads", "--archive", "/archive", "--output", str(output),
                    "--compile-driver", "--install-driver", "--guest-runtime", "--verity-root", "--boot-uki"]
            for enabled in (False, True):
                events.clear()
                with mock.patch.object(launcher.sys, "argv", args + (["--export-boot"] if enabled else [])), \
                        mock.patch.object(launcher, "identity", return_value=(os.getuid(), os.getgid())), \
                        mock.patch.object(launcher, "receipt_file", receipt), \
                        mock.patch.object(launcher.os, "write", side_effect=lambda fd, raw: len(raw)), \
                        mock.patch.object(launcher, "execute", return_value={"result": "pass"}) as execute, \
                        mock.patch.object(launcher, "finish_export", side_effect=lambda *a: events.append("finish")):
                    self.assertEqual(launcher.main(), 0)
                    self.assertEqual(events, ["receipt-open", "receipt-durable"] + (["finish"] if enabled else []))
                    self.assertEqual("--stage-boot" in execute.call_args.args[2], enabled)

    def test_launcher_refuses_unsafe_parent_and_collisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp).resolve()
            output = parent / "evidence"
            parent.chmod(0o755)
            with self.assertRaises(ValueError): launcher.export_preflight(output, os.getuid())
            parent.chmod(0o700)
            for path in export.paths(output):
                path.symlink_to(parent / "absent")
                with self.assertRaises(ValueError): launcher.export_preflight(output, os.getuid())
                self.assertTrue(path.is_symlink())
                path.unlink()

    def test_launcher_drops_privileges_before_loading_and_finalizing(self):
        events = []
        uid, gid = os.getuid(), os.getgid()
        sources = {host.EXPORT: b"export", host.PUBLICATION: b"publication"}
        native = mock.Mock()
        native.committed_sources.return_value = ("ab" * 20, sources)
        native.export_helpers.return_value = (mock.Mock(), publication)
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            evidence = Path(tmp).resolve() / "evidence"
            export.paths(evidence)[0].mkdir(mode=0o700)
            for name, value in (("getuid", uid), ("geteuid", uid), ("getgid", gid),
                                ("getegid", gid), ("getgroups", [])):
                stack.enter_context(mock.patch.object(launcher.os, name, return_value=value))
            for name in ("setgroups", "setgid", "setuid"):
                stack.enter_context(mock.patch.object(launcher.os, name, side_effect=lambda *a, n=name: events.append(n)))
            stack.enter_context(mock.patch.object(launcher.base, "helper",
                side_effect=lambda *a: (events.append("load") or native, b"source")))
            launcher.finish_export(evidence, uid, gid)
        self.assertEqual(events, ["setgroups", "setgid", "setuid", "load"])
        native.committed_sources.assert_called_once_with(True, True, True, True)
        native.export_helpers.return_value[0].finish.assert_called_once()

    def test_source_reaudit_failure_and_timeout_remove_pending_without_finalizing(self):
        for fail in ("changed-source", "timeout"):
            with self.subTest(fail=fail), self.fixture() as (evidence, stage, _, report, source), ExitStack() as stack:
                self.prepare(evidence, stage, report)
                uid, gid = os.getuid(), os.getgid()
                for name in ("setgroups", "setgid", "setuid"):
                    stack.enter_context(mock.patch.object(launcher.os, name))
                stack.enter_context(mock.patch.object(launcher.os, "getgroups", return_value=[]))
                native = mock.Mock()
                if fail == "changed-source":
                    native.committed_sources.side_effect = ValueError("fixture changed source")
                else:
                    native.committed_sources.side_effect = lambda *a: time.sleep(1)
                    timer = launcher.signal.setitimer
                    stack.enter_context(mock.patch.object(launcher.signal, "setitimer",
                        side_effect=lambda which, seconds: timer(which, 0.01 if seconds else 0)))
                stack.enter_context(mock.patch.object(launcher.base, "helper", return_value=(native, b"source")))
                with self.assertRaises(ValueError): launcher.finish_export(evidence, uid, gid)
                self.assertFalse(any(p.exists() for p in export.paths(evidence)))
                native.export_helpers.assert_not_called()
                self.assertEqual(export.signal.getitimer(export.signal.ITIMER_REAL), (0.0, 0.0))

    def test_host_handoff_checks_all_stages_and_cleans_private_payload_before_accepting(self):
        for bad in (None, "process", "source", "image", "appraisal", "policy", "evidence"):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp).resolve() / "evidence"
                raws = {"root.raw": b"synthetic disk", "nib.efi": b"synthetic UKI"}
                report, _ = proof(raws)
                sources = {name: (ROOT / name).read_bytes() for name in (host.EXPORT, host.PUBLICATION)}
                source = host.source_identity(report["git_sha"], sources)
                report["worker"]["artifact_stage"]["source"] = copy.deepcopy(source)
                if bad == "source": report["worker"]["artifact_stage"]["source"]["git_sha"] = "cd" * 20
                if bad == "appraisal": report["worker"]["appraisal_cli"]["result"] = "fail"
                if bad == "policy": report["worker"]["runtime_policy"]["result"] = "fail"
                payload_dirs = []
                def execute(argv, **kwargs):
                    self.assertEqual(kwargs["timeout"], 1900)
                    result = Path(argv[0])
                    payload_dirs.append(result.parent)
                    (result / "result.json").write_bytes(export.encoded(report["worker"]))
                    (result / "boot-stage").mkdir(mode=0o700)
                    for name, raw in raws.items():
                        (result / "boot-stage" / name).write_bytes(b"changed" if bad == "image" else raw)
                    return vt.subprocess.CompletedProcess(argv, 1 if bad == "process" else 0, b"", b"")
                publish = host.base.publish
                def evidence_publish(destination, files):
                    if destination == output:
                        self.assertFalse(any(p.exists() for p in payload_dirs))
                        if bad == "evidence": raise OSError("fixture evidence write failed")
                    publish(destination, files)
                with mock.patch.object(host, "prepare", return_value=({"receipt.json": b"{}", "runtime-hook-review.json": b"{}"}, {}, {}, {}, {})), \
                        mock.patch.object(host.prep, "assemble", return_value=({"receipt.json": b"{}"}, {})), \
                        mock.patch.object(host.probe.ownership, "host_inputs", return_value=({}, {})), \
                        mock.patch.object(host.probe, "materialize", side_effect=lambda files, root: root.mkdir()), \
                        mock.patch.object(host, "command", side_effect=lambda r, p, o, *a, **k: [str(o)]), \
                        mock.patch.object(host.base, "publish", side_effect=evidence_publish), \
                        mock.patch.object(host.base.processes, "execute_process_group", side_effect=execute):
                    if bad == "evidence":
                        with self.assertRaises(OSError):
                            host.run(Path("/unused"), Path("/unused"), output, report["git_sha"], sources,
                                     True, True, True, True, True, True)
                    else:
                        self.assertEqual(host.run(Path("/unused"), Path("/unused"), output, report["git_sha"], sources,
                                                  True, True, True, True, True, True), 0 if bad is None else 1)
                        result = export.parsed((output / "result.json").read_bytes())
                        self.assertEqual(result["result"], "pass" if bad is None else "fail")
                pending, final = export.paths(output)
                self.assertEqual(pending.exists(), bad is None)
                self.assertFalse(final.exists())  # The child cannot publish a bundle.

    def test_worker_preserves_boot_diagnostics_with_export_callback(self):
        for rejected in (False, True):
            with self.subTest(rejected=rejected), tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
                root = Path(tmp)
                def path(*parts):
                    return root / Path(*parts).relative_to("/")
                for name in ("nib/prepared", "out", "work"):
                    (root / name).mkdir(parents=True)
                (root / "nib/ownership-input.json").write_text("{}")
                (root / "nib/prepared/guest-inputs.json").write_text(
                    json.dumps({"scope": "guest-runtime-dependency-coverage"}))
                modules = {name: mock.Mock() for name in (
                    "namespace-check", "pid-check", "ownership", "snapshot", "package-inputs",
                    "dkms-worker", "confinement", "runtime-policy", "appraisal-probe", "verity-root",
                    "boot-uki", "boot-export")}
                modules["package-inputs"].regular.return_value = b"{}"
                modules["dkms-worker"].CONTROL_NAMES = ()
                for name, method in (("dkms-worker", "build"), ("runtime-policy", "apply"),
                                     ("appraisal-probe", "probe")):
                    getattr(modules[name], method).return_value = {"result": "pass"}
                modules["boot-export"].parsed.side_effect = json.loads
                modules["boot-export"].owned.return_value = nullcontext()
                modules["boot-export"].stage.return_value = {"published": False}
                def verity(*args, require_boot_ids, consume):
                    self.assertIs(require_boot_ids, True)
                    consume({"root_hash": "ab" * 32})
                    return {"result": "pass"}
                def boot(*args, diagnostics, consume):
                    self.assertEqual(diagnostics, {})
                    if rejected:
                        diagnostics.update(member="usr/lib/os-release", observed={"bytes": 15})
                        raise ValueError("private fixture detail must not appear")
                    consume({"root_hash": "ab" * 32})
                    return {"result": "pass"}
                modules["verity-root"].build.side_effect = verity
                modules["boot-uki"].build.side_effect = boot
                stack.enter_context(mock.patch.object(worker, "Path", side_effect=path))
                stack.enter_context(mock.patch.object(worker, "module", side_effect=modules.__getitem__))
                stack.enter_context(mock.patch.object(worker, "sys", mock.Mock(
                    platform="linux", version_info=(3, 12),
                    argv=["worker", "1", "2", "3", "4", "5", "--install-driver", "--verity-root",
                          "--boot-uki", "--stage-boot"])))
                for name in ("geteuid", "getegid"):
                    stack.enter_context(mock.patch.object(worker.os, name, return_value=0))
                for name in ("expected_packages", "prepare_configuration", "install", "installation_environment"):
                    stack.enter_context(mock.patch.object(worker, name, return_value={}))
                stack.enter_context(mock.patch.object(worker, "configured", return_value=212))
                self.assertEqual(worker.main(), 1 if rejected else 0)
                report = json.loads((root / "out/result.json").read_bytes())
                self.assertEqual(report["stage"], "boot-uki")
                self.assertEqual(report["boot_input_diagnostics"],
                                 {"member": "usr/lib/os-release", "observed": {"bytes": 15}} if rejected else {})
                self.assertEqual("artifact_stage" in report, not rejected)
                self.assertNotIn("private fixture", json.dumps(report))

    def test_nested_verified_lifetimes_preserve_original_bytes_and_cleanup_on_every_failure(self):
        # Real helper orchestration and file copies with synthetic subprocess
        # outputs only. The GPT/UKI parsers' native-format tests remain separate.
        verity, boot = vt.verity, bt.boot
        vf, bf = vt.VerityTests(), bt.BootTests()
        failures = [None, *verity.OPERATIONS, *boot.OPERATIONS, "member", "copy", "late-root", "late-uki", "cleanup"]
        for failure in failures:
            with self.subTest(failure=failure), vf.fixture() as vr, bf.fixture() as (br, _), ExitStack() as stack:
                stage = vr / "out/boot-stage"
                stack.enter_context(mock.patch.object(export, "STAGE", stage))
                stack.enter_context(mock.patch.object(verity, "verify_tools", return_value={}))
                stack.enter_context(mock.patch.object(boot, "verify_tools", return_value={}))
                stack.enter_context(mock.patch.object(boot, "assemble", side_effect=bf.module_fixture()))
                original_images = vf.images
                def images():
                    data, hashes, disk, rows = original_images()
                    for row in rows: row["uuid"] = str(bt.uuid.UUID(hex="ab" * 16))
                    return data, hashes, disk, rows
                stack.enter_context(mock.patch.object(vf, "images", side_effect=images))
                original_copy = export.copy
                def mapped_copy(source, *args):
                    mapped = {Path("/work/verity-root/root.raw"): verity.WORK / verity.DISK,
                              Path("/work/boot-uki/nib.efi"): boot.WORK / "nib.efi"}
                    self.assertIn(source, mapped)
                    if failure == "copy": raise OSError("fixture copy failure")
                    return original_copy(mapped[source], *args)
                stack.enter_context(mock.patch.object(export, "copy", side_effect=mapped_copy))
                vp, bp = vf.supervisor(failure), bf.supervisor(failure)
                reports, observed, diagnostics = {}, [], {}
                source = {"git_sha": "ab" * 20, "sources": {host.EXPORT: base.digest(b"fixture source")}}
                remove = boot.shutil.rmtree
                def cleanup(path, *args, **kwargs):
                    if failure == "cleanup" and path == boot.WORK:
                        raise OSError("fixture UKI cleanup failure")
                    return remove(path, *args, **kwargs)
                stack.enter_context(mock.patch.object(boot.shutil, "rmtree", side_effect=cleanup))
                def consume_root(root):
                    self.assertEqual(vp.execute_process_group.call_count, len(verity.OPERATIONS))
                    self.assertTrue(all((verity.WORK / name).exists() for name in (verity.DATA, verity.HASH, verity.DISK)))
                    if failure == "member": (boot.ROOTFS / "usr/lib/os-release").write_bytes(b"PRIVATE-CONTENT")
                    def consume_boot(uki):
                        self.assertEqual(bp.execute_process_group.call_count, len(boot.OPERATIONS))
                        observed.extend([(verity.WORK / verity.DISK).read_bytes(), (boot.WORK / "nib.efi").read_bytes()])
                        reports["staged"] = export.stage(root, uki, source, base.directory)
                        if failure == "late-uki": (boot.WORK / "nib.efi").write_bytes(b"changed")
                    reports["boot"] = boot.build(bp, bt.guard.PREFIX, root["root_hash"], br / "out",
                                                diagnostics=diagnostics, consume=consume_boot)
                    if failure == "late-root": (verity.WORK / verity.DISK).write_bytes(b"changed")
                def run():
                    with export.owned(stage, base.directory, base.inputs.remove_flat_directory):
                        reports["root"] = verity.build(vp, bt.guard.PREFIX, vt.dkms.regular, vr / "out",
                                                       require_boot_ids=True, consume=consume_root)
                if failure is None:
                    run()
                    self.assertEqual([(stage / name).read_bytes() for name in export.FILES], observed)
                    self.assertIs(reports["root"]["private_images_removed"], True)
                    self.assertIs(reports["boot"]["private_images_removed"], True)
                    self.assertEqual(diagnostics, {})
                else:
                    with self.assertRaises((OSError, ValueError, vt.subprocess.SubprocessError)): run()
                    self.assertFalse(stage.exists())
                    if failure == "member":
                        self.assertEqual(diagnostics["member"], "usr/lib/os-release")
                        self.assertEqual(diagnostics["observed"], {"regular": True, "links": 1, "bytes": 15})
                        self.assertNotIn("PRIVATE", json.dumps(diagnostics))
                        self.assertEqual(observed, [])
                self.assertFalse(verity.WORK.exists())
                if failure != "cleanup": self.assertFalse(boot.WORK.exists())

    def test_retention_callback_cannot_bypass_boot_id_gate(self):
        with self.assertRaises(ValueError):
            vt.verity.build(None, None, None, consume=lambda _: None)


if __name__ == "__main__":
    unittest.main()
