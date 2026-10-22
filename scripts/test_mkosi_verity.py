# SPDX-License-Identifier: Apache-2.0
"""Fixture-only contracts; these tests do NOT generate or verify a real image."""
import ast
from contextlib import contextmanager, ExitStack
import copy
import hashlib
import hmac
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import types
import unittest
from unittest import mock
import uuid

import mkosi_confinement as guard
import mkosi_dkms as dkms
import mkosi_verity as verity
from test_mkosi_build import host, launcher, worker, ROOT


class VerityTests(unittest.TestCase):
    def test_original_native_failure_is_mkfs_progress_after_appraiser_help(self):
        root = ROOT / "evidence/p3/2026-10-22-verity-progress-failure"
        expected = {
            "run.json": "9b4cdfa341631830512f9d8886427b40d11f07bf57fef2a6c5368ba8e573f312",
            "result.json": "fb8cc56be24eb0a77613d6205d0cb2f4573e8412b2f4dddc7be3e6a97d82592d",
            "verity-mkfs.stdout": "cf60a75ff89b4a094ecf5b6e7d92594110f501af666e4ce677ea21f72d1a6e3c",
            "evidence-resources.json": "a0d8e39843be5801c6c1f4efa54829445673de949b10d079712d12b60929ae90",
        }
        for name, digest in expected.items():
            self.assertEqual(hashlib.sha256((root / name).read_bytes()).hexdigest(), digest)
        progress = (root / "verity-mkfs.stdout").read_bytes()
        self.assertEqual(len(progress), 65536)
        self.assertGreater(progress.count(b"Processing "), 100)
        result = json.loads((root / "result.json").read_bytes())
        self.assertEqual(result["git_sha"], "84152f74b436e12626f03450013a2690b96e03ac")
        self.assertEqual(result["result"], "fail")
        self.assertEqual(result["worker"]["stage"], "verity-root")
        appraisal = result["worker"]["appraisal_cli"]
        self.assertEqual(appraisal["result"], "pass")
        self.assertEqual(set(appraisal["help"]), {"help", "attest-help", "token-help"})
        self.assertFalse(appraisal["appraisal_performed"])
        self.assertTrue(result["process_cleanup_confirmed"])
        self.assertTrue(result["payload_removed"])
        self.assertFalse(result["bootable"])
        self.assertFalse(result["deployable"])
        resources = json.loads((root / "evidence-resources.json").read_bytes())
        self.assertEqual(resources["oom_kill"], 0)
        self.assertTrue(resources["empty_verified"])
        self.assertTrue(resources["group_removed"])

    @contextmanager
    def fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "guest").mkdir()
            (root / "out").mkdir()
            with mock.patch.object(verity, "WORK", root / "private"), \
                    mock.patch.object(verity, "ROOTFS", root / "guest"):
                yield root

    def images(self):
        data = b"fixture, not an EROFS image\n".ljust(8192, b"\0")
        sb = struct.pack("<8sII16s32sIIQH6s256s168s", b"verity\0\0", 1, 1,
            uuid.UUID(verity.VERITY_UUID).bytes, b"sha256".ljust(32, b"\0"), 4096, 4096, 2, 32,
            bytes(6), bytes.fromhex(verity.SALT) + bytes(224), bytes(168))
        hashes = sb.ljust(4096, b"\0")
        disk = bytes(4096) + data + hashes + bytes(4096)
        rows = [{"type": kind, "label": label, "partno": i, "raw_size": len(raw),
                 "offset": offset, "roothash": "ab" * 32}
                for i, (kind, label, raw, offset) in enumerate((
                    ("root-x86-64", "nib-root", data, 4096),
                    ("root-x86-64-verity", "nib-verity", hashes, 12288)))]
        return data, hashes, disk, rows

    def populate(self):
        verity.WORK.mkdir(mode=0o700)
        data, hashes, disk, rows = self.images()
        for name, raw in zip((verity.DATA, verity.HASH, verity.DISK), (data, hashes, disk)):
            (verity.WORK / name).write_bytes(raw)
        return rows

    def supervisor(self, fail=None):
        def execute(argv, **kwargs):
            name = argv[argv.index("/nib/verity-root.py") + 1]
            self.assertEqual(kwargs["timeout"], verity.OPERATIONS[name])
            self.assertEqual(kwargs["max_output_bytes"], 65536)
            self.assertEqual(kwargs["max_stderr_bytes"], 65536)
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
            self.assertEqual(kwargs["env"], verity.environment())
            if name == fail:
                raise subprocess.TimeoutExpired(argv, 120)
            data, hashes, disk, rows = self.images()
            stdout, stderr, code = b"", b"", 0
            if name == "mkfs":
                (verity.WORK / verity.DATA).write_bytes(data)
            elif name == "repart":
                (verity.WORK / verity.HASH).write_bytes(hashes)
                (verity.WORK / verity.DISK).write_bytes(disk)
                stdout = json.dumps(rows).encode()
            elif name == "reject-root":
                stderr, code = b"Verification of root hash failed.\n", 1
            return subprocess.CompletedProcess(argv, code, stdout, stderr)
        return types.SimpleNamespace(execute_process_group=mock.Mock(side_effect=execute),
            OutputLimitExceeded=host.base.processes.OutputLimitExceeded)

    def test_seed_parameter_pins_match_upstream_derivation(self):
        seed = uuid.UUID(verity.SEED).bytes
        self.assertNotEqual(seed, bytes(16))
        self.assertEqual(hmac.digest(seed, b"verity-salt", "sha256").hex(), verity.SALT)
        self.assertEqual(str(uuid.UUID(bytes=hmac.digest(seed, b"verity-uuid", "sha256")[:16], version=4)),
                         verity.VERITY_UUID)

    def test_fixed_tools_flags_and_no_environment_inheritance(self):
        with mock.patch.dict(os.environ, {"LD_PRELOAD": "/bad", "REPART_MKFS_OPTIONS_EROFS": "bad",
                                         "SOURCE_DATE_EPOCH": "0", "SECRET": "sentinel"}, clear=True):
            self.assertEqual(set(verity.environment()),
                {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "SYSTEMD_PAGER", "SYSTEMD_COLORS"})
        self.assertEqual(verity.operation("mkfs"), ["/usr/bin/mkfs.erofs", "--quiet", "-b", "4096",
            "-z", "lz4", "-T", "1792454400", "-U", verity.SEED, "-L", "nib-root",
            "/work/verity-root/root.erofs", "/work/output/rootfs"])
        self.assertEqual(verity.operation("fsck"), ["/usr/bin/fsck.erofs", "--extract", "/work/verity-root/root.erofs"])
        repart = verity.operation("repart")
        for flag in ("--offline=yes", "--empty=create", "--dry-run=no", "--size=auto", "--seed=" + verity.SEED,
                     "--split=yes", "--sector-size=4096", "--json=short", "--discard=no"):
            self.assertIn(flag, repart)
        for bad in (None, "", "format", "open", "../verify"):
            with self.assertRaises((ValueError, TypeError)):
                verity.operation(bad)
        for bad in (None, "x" * 64, "A" * 64, "a" * 63, "a" * 65, "a" * 64 + "\n"):
            with self.assertRaises(ValueError):
                verity.operation("verify", bad)
        good = verity.operation("verify", "ab" * 32)
        bad = verity.operation("reject-root", "ab" * 32)
        self.assertEqual(good[:-1], ["/usr/sbin/veritysetup", "verify", "/tmp/root.erofs", "/tmp/root.verity.raw"])
        self.assertEqual(good[:-1], bad[:-1])
        self.assertEqual(int(good[-1], 16) ^ int(bad[-1], 16), 1 << 252)

    def test_mounts_are_readonly_except_new_image_workspace(self):
        for name in verity.OPERATIONS:
            args = verity.command(name, guard.PREFIX, "ab" * 32 if name in verity.VERIFY else None)
            i = args.index(guard.PREFIX[0])
            self.assertEqual(args[i:i + len(guard.PREFIX)], list(guard.PREFIX))
            self.assertIn(["--ro-bind", "/", "/"], [args[i:i+3] for i in range(len(args)-2)])
            self.assertEqual(args.count("--bind"), 1 if name in ("mkfs", "repart") else 0)
            if "--bind" in args:
                i = args.index("--bind")
                self.assertEqual(args[i+1:i+3], [str(verity.WORK)] * 2)
            if name in verity.VERIFY:
                self.assertIn(["--ro-bind", str(verity.WORK), str(verity.ROOTFS / "tmp")],
                              [args[i:i+3] for i in range(len(args)-2)])
                self.assertIn(str(verity.ROOTFS / "dev"), args)
            self.assertNotIn("--become-root", args)
        self.assertNotIn("sys_admin", str(guard.PREFIX))

    def test_definitions_exact_sizing_match_and_no_extra_files(self):
        with self.fixture():
            verity.WORK.mkdir()
            verity.write_definitions(8192)
            for name, raw in verity.definitions(8192).items():
                self.assertEqual((verity.WORK / "definitions" / name).read_bytes(), raw)
                self.assertIn(b"VerityMatchKey=root\n", raw)
                self.assertIn(b"ReadOnly=yes\n", raw)
                self.assertNotIn(b"CopyFiles=", raw)
            data = verity.definitions(8192)["10-root.conf"]
            self.assertIn(b"SizeMinBytes=8192\nSizeMaxBytes=8192\n", data)
            self.assertIn(b"SplitName=-\n", data)
            with self.assertRaises(FileExistsError):
                verity.write_definitions(8192)
        for bad in (True, 0, 4095, 4097, verity.MAX_DATA + 4096, 4096.0):
            with self.assertRaises(ValueError):
                verity.definitions(bad)

    def test_file_inspection_rejects_links_special_files_bounds_and_partial_blocks(self):
        for kind in ("symlink", "hardlink", "fifo", "directory", "oversize", "partial", "empty"):
            with self.subTest(kind=kind), self.fixture() as root:
                verity.WORK.mkdir()
                target = verity.WORK / verity.DATA
                other = root / "sentinel"
                other.write_bytes(b"s" * 4096)
                if kind == "symlink": target.symlink_to(other)
                elif kind == "hardlink": os.link(other, target)
                elif kind == "fifo": os.mkfifo(target)
                elif kind == "directory": target.mkdir()
                else: target.write_bytes(b"x" * {"oversize": 8192, "partial": 4095, "empty": 0}[kind])
                with self.assertRaises((ValueError, OSError)):
                    verity.inspect_file(verity.DATA, 4096)
                self.assertEqual(other.read_bytes(), b"s" * 4096)
        with self.fixture() as root:
            verity.WORK.symlink_to(root / "guest")
            with self.assertRaises(OSError):
                with verity.directory(verity.WORK): self.fail("followed symlink")

    def test_parameter_header_requires_fixed_seed_and_complete_data_coverage(self):
        with self.fixture():
            self.populate()
            self.assertEqual(verity.superblock(8192)["data_blocks"], 2)
            original = (verity.WORK / verity.HASH).read_bytes()
            # Signature/version/type/UUID/hash/block sizes/coverage/salt/padding.
            for offset in (0, 8, 12, 16, 32, 64, 68, 72, 80, 82, 88, 120, 344):
                bad = bytearray(original)
                bad[offset] ^= 1
                (verity.WORK / verity.HASH).write_bytes(bad)
                with self.subTest(offset=offset), self.assertRaises(ValueError):
                    verity.superblock(8192)
            (verity.WORK / verity.HASH).write_bytes(original)
            for size in (4096, 12288, 8193):
                with self.assertRaises(ValueError): verity.superblock(size)

    def test_partition_json_and_disk_bytes_must_match(self):
        with self.fixture():
            rows = self.populate()
            data, hashes, disk = (verity.inspect_file(name, verity.MAX_DISK)
                                  for name in (verity.DATA, verity.HASH, verity.DISK))
            self.assertEqual(verity.partition_report(json.dumps(rows).encode(), data, hashes, disk), "ab" * 32)
            for field, bad in (("label", "wrong"), ("type", "linux-generic"), ("partno", True),
                               ("partno", 1), ("offset", 0), ("offset", 4097), ("offset", 8192),
                               ("raw_size", 4096), ("raw_size", 8192.0), ("roothash", "TBD")):
                changed = copy.deepcopy(rows)
                changed[0][field] = bad
                with self.subTest(field=field, bad=bad), self.assertRaises(ValueError):
                    verity.partition_report(json.dumps(changed).encode(), data, hashes, disk)
            for bad in (b"", b"[]", b"{}", b"x" * 65537, json.dumps(rows + rows).encode(),
                        json.dumps(rows[::-1]).encode(), json.dumps(rows).encode().replace(b'"partno": 0', b'"partno": 0, "partno": 0')):
                with self.assertRaises(ValueError): verity.partition_report(bad, data, hashes, disk)
            with (verity.WORK / verity.DISK).open("r+b") as file:
                file.seek(4096)
                file.write(b"!")
            with self.assertRaises(ValueError):
                verity.partition_report(json.dumps(rows).encode(), data, hashes, disk)

    def test_fixture_orchestration_orders_checks_and_removes_private_outputs(self):
        with self.fixture() as root, mock.patch.object(verity, "verify_tools", return_value={"fixture": {}}):
            processes = self.supervisor()
            report = verity.build(processes, guard.PREFIX, dkms.regular, root / "out")
            operations = [c.args[0][c.args[0].index("/nib/verity-root.py") + 1]
                          for c in processes.execute_process_group.call_args_list]
            self.assertEqual(operations, list(verity.OPERATIONS))
            self.assertFalse(verity.WORK.exists())
            self.assertEqual(report["result"], "pass")
            self.assertEqual(report["images"][verity.DATA]["bytes"], 8192)
            self.assertIs(report["private_images_removed"], True)
            for field in ("images_exported", "boot_verified", "kernel_verity_verified",
                          "reproducibility_verified", "confidentiality_verified"):
                self.assertIs(report[field], False)
            self.assertEqual({p.name for p in (root / "out").iterdir()},
                             {"verity-repart.stdout", "verity-reject-root.stderr"})

    def test_each_stage_failure_cleans_up_without_success_or_later_execution(self):
        for stage in verity.OPERATIONS:
            with self.subTest(stage=stage), self.fixture() as root, \
                    mock.patch.object(verity, "verify_tools", return_value={}):
                processes = self.supervisor(fail=stage)
                with self.assertRaises(subprocess.TimeoutExpired):
                    verity.build(processes, guard.PREFIX, dkms.regular, root / "out")
                self.assertFalse(verity.WORK.exists())
                self.assertEqual(processes.execute_process_group.call_count, list(verity.OPERATIONS).index(stage) + 1)

    def test_metadata_and_late_identity_failure_remove_images_without_success(self):
        for failure in ("partition_report", "superblock", "identity"):
            with self.subTest(failure=failure), self.fixture() as root, ExitStack() as stack:
                if failure == "identity":
                    stack.enter_context(mock.patch.object(verity, "verify_tools", side_effect=[{}, {"changed": {}}]))
                else:
                    stack.enter_context(mock.patch.object(verity, "verify_tools", return_value={}))
                    stack.enter_context(mock.patch.object(verity, failure, side_effect=ValueError("fixture mismatch")))
                with self.assertRaises(ValueError):
                    verity.build(self.supervisor(), guard.PREFIX, dkms.regular, root / "out")
                self.assertFalse(verity.WORK.exists())

    def test_collision_never_removes_existing_workspace_and_cleanup_error_fails(self):
        with self.fixture() as root, mock.patch.object(verity, "verify_tools", return_value={}):
            verity.WORK.mkdir()
            sentinel = verity.WORK / "sentinel"
            sentinel.write_bytes(b"keep")
            processes = self.supervisor()
            with self.assertRaises(FileExistsError):
                verity.build(processes, guard.PREFIX, dkms.regular, root / "out")
            self.assertEqual(sentinel.read_bytes(), b"keep")
            processes.execute_process_group.assert_not_called()
        with self.fixture() as root, mock.patch.object(verity, "verify_tools", return_value={}), \
                mock.patch.object(verity.shutil, "rmtree", side_effect=OSError("fixture cleanup failure")):
            with self.assertRaises(OSError):
                verity.build(self.supervisor(), guard.PREFIX, dkms.regular, root / "out")

    def test_wrong_root_control_rejects_generic_failures_and_false_success(self):
        for code, text in ((0, b""), (1, b""), (2, b"Verification of root hash failed.\n"),
                           (-9, b"Verification of root hash failed.\n"), (127, b"exec failure"),
                           (1, b"Verification of root hash failed.\n" * 2)):
            with self.subTest(code=code, text=text), self.fixture() as root:
                processes = self.supervisor()
                processes.execute_process_group.side_effect = None
                processes.execute_process_group.return_value = subprocess.CompletedProcess([], code, b"", text)
                with self.assertRaises(ValueError):
                    verity.execute(processes, guard.PREFIX, "reject-root", root / "out", "ab" * 32)

    def test_logs_are_bounded_exclusive_nofollow_and_not_an_image_export(self):
        with self.fixture() as root:
            output = root / "out"
            verity.write_log(output, "mkfs", "stdout", b"public")
            with self.assertRaises(FileExistsError): verity.write_log(output, "mkfs", "stdout", b"other")
            with self.assertRaises(ValueError): verity.write_log(output, "mkfs", "stderr", b"x" * 65537)
            target = output / "verity-mkfs.stderr"
            target.symlink_to(output / "verity-mkfs.stdout")
            with self.assertRaises(OSError): verity.write_log(output, "mkfs", "stderr", b"changed")
            target.unlink()
            (output / verity.DATA).write_bytes(b"not for export")
            self.assertEqual(host.collect_diagnostics(output), {})
            self.assertEqual(host.collect_diagnostics(output, True), {"verity-mkfs.stdout": b"public"})
            self.assertEqual(set(verity.LOGS), {"verity-" + n + "." + s for n in verity.OPERATIONS
                                              for s in ("stdout", "stderr")})

    def test_output_overflow_retains_only_prefix_and_fails(self):
        for stream in ("stdout", "stderr"):
            with self.subTest(stream=stream), self.fixture() as root, \
                    mock.patch.object(verity, "verify_tools", return_value={}):
                processes = self.supervisor()
                prefix = b"x" * 65536
                def overflow(argv, **kwargs):
                    self.assertEqual(kwargs["max_output_bytes"], 65536)
                    self.assertEqual(kwargs["max_stderr_bytes"], 65536)
                    self.assertEqual(kwargs["timeout"], 240)
                    (verity.WORK / verity.DATA).write_bytes(b"partial image")
                    raise processes.OutputLimitExceeded(stream,
                        prefix if stream == "stdout" else b"", prefix if stream == "stderr" else b"")
                processes.execute_process_group.side_effect = overflow
                with self.assertRaises(processes.OutputLimitExceeded):
                    verity.build(processes, guard.PREFIX, dkms.regular, root / "out")
                self.assertEqual((root / "out" / ("verity-mkfs." + stream)).read_bytes(), prefix)
                self.assertEqual(len(list((root / "out").iterdir())), 1)
                self.assertFalse(verity.WORK.exists())
                processes.execute_process_group.assert_called_once()

    def test_all_positive_tool_nonzero_exits_fail(self):
        for name in verity.OPERATIONS:
            if name == "reject-root": continue
            with self.subTest(name=name), self.fixture() as root:
                processes = self.supervisor()
                processes.execute_process_group.side_effect = None
                processes.execute_process_group.return_value = subprocess.CompletedProcess([], 1, b"", b"failure")
                with self.assertRaises(subprocess.CalledProcessError):
                    verity.execute(processes, guard.PREFIX, name, root / "out", "ab" * 32 if name in verity.VERIFY else None)

    def test_tool_pins_and_preload_are_fail_closed(self):
        with mock.patch.object(verity.os.path, "lexists", return_value=False):
            reader = mock.Mock(return_value=b"wrong")
            with self.assertRaises(ValueError): verity.verify_tools(reader)
            reader.assert_called_once()
        with mock.patch.object(verity.os.path, "lexists", return_value=True):
            reader = mock.Mock()
            with self.assertRaises(ValueError): verity.verify_tools(reader)
            reader.assert_not_called()
        with mock.patch.object(verity.os.path, "lexists", return_value=False), \
                mock.patch.object(verity, "TOOL_FILES", {"usr/bin/tool": (3, hashlib.sha256(b"pin").hexdigest())}), \
                mock.patch.object(verity, "GUEST_FILES", {"usr/sbin/tool": (3, hashlib.sha256(b"pin").hexdigest())}):
            reader = mock.Mock(return_value=b"pin")
            self.assertEqual(len(verity.verify_tools(reader)), 2)
            self.assertEqual(reader.call_args_list, [mock.call(Path("/"), "usr/bin/tool", 3),
                                                   mock.call(verity.ROOTFS, "usr/sbin/tool", 3)])

    def test_child_checks_guard_before_exec_and_preserves_limits(self):
        for name in verity.OPERATIONS:
            with self.subTest(name=name), ExitStack() as stack:
                stack.enter_context(mock.patch.object(verity.sys, "platform", "linux"))
                stack.enter_context(mock.patch.object(verity.sys, "version_info", (3, 12)))
                stack.enter_context(mock.patch.object(verity.sys, "flags",
                    types.SimpleNamespace(isolated=1, no_site=1, dont_write_bytecode=1)))
                stack.enter_context(mock.patch.object(verity.sys, "argv",
                    ["worker", name] + (["ab" * 32] if name in verity.VERIFY else [])))
                stack.enter_context(mock.patch.object(verity.os, "geteuid", return_value=0))
                stack.enter_context(mock.patch.object(verity.os, "getegid", return_value=0))
                stack.enter_context(mock.patch.object(verity.socket, "if_nameindex", return_value=[(1, "lo")]))
                events = []
                stack.enter_context(mock.patch.object(verity, "module", side_effect=[
                    types.SimpleNamespace(check=lambda: events.append("guard")), types.SimpleNamespace(regular=None)]))
                stack.enter_context(mock.patch.object(verity, "verify_tools", side_effect=lambda _: events.append("pins")))
                stack.enter_context(mock.patch.object(verity.os, "umask"))
                limits = stack.enter_context(mock.patch.object(verity.resource, "setrlimit"))
                chroot = stack.enter_context(mock.patch.object(verity.os, "chroot", side_effect=lambda _: events.append("chroot")))
                stack.enter_context(mock.patch.object(verity.os, "chdir"))
                execute = stack.enter_context(mock.patch.object(verity.os, "execve", side_effect=lambda *a: events.append("exec")))
                verity.main()
                self.assertEqual(events, ["guard", "pins"] + (["chroot"] if name in verity.VERIFY else []) + ["exec"])
                self.assertEqual(chroot.call_count, int(name in verity.VERIFY))
                self.assertEqual(execute.call_args.args[2], verity.environment())
                limit = verity.MAX_DATA if name == "mkfs" else verity.MAX_DISK if name == "repart" else 0
                limits.assert_has_calls([mock.call(verity.resource.RLIMIT_FSIZE, (limit, limit)),
                                         mock.call(verity.resource.RLIMIT_CORE, (0, 0))])

    def test_opt_in_gates_and_default_command_bytes(self):
        with mock.patch.object(host.os, "readlink", return_value="ns:[1]"), \
                mock.patch.object(host.probe.ownership, "prefix", return_value=[]):
            args = (Path("/root"), Path("/payload"), Path("/out"), {})
            original = host.command(*args, True, True)
            self.assertEqual(host.command(*args, True, True, True), original + ["--verity-root"])
            with self.assertRaises(ValueError): host.command(*args, True, False, True)
        for args, expected in (([], (False, False, False)), (["--compile-driver"], (True, False, False)),
                (["--install-driver"], (True, True, False)), (["--install-driver", "--verity-root"], (True, True, True))):
            self.assertEqual(worker.options(args), (*expected, False))
        for args in (["--verity-root"], ["--compile-driver", "--verity-root"],
                     ["--verity-root", "--install-driver"], ["--install-driver", "--verity-root", "bad"]):
            with self.assertRaises(ValueError): worker.options(args)
        argv = ["host", "--downloads", "/downloads", "--archive", "/archive", "--output", "/output", "--verity-root"]
        for entry in (host, launcher):
            for flags in ([], ["--guest-runtime"], ["--install-driver"], ["--compile-driver", "--install-driver"]):
                with mock.patch.object(entry.sys, "argv", argv + flags), self.assertRaises(ValueError):
                    entry.main()

    def test_opt_in_source_closure_is_thirty_extra_paths_without_package_changes(self):
        argv = ["host", "--downloads", "/downloads", "--archive", "/archive", "--output", "/output",
                "--compile-driver", "--install-driver", "--guest-runtime"]
        captures = []
        for opt in ([], ["--verity-root"]):
            with mock.patch.object(host, "preflight"), mock.patch.object(host.tools, "separate_output"), \
                    mock.patch.object(host.tools, "committed", return_value=("a" * 40, {})) as committed, \
                    mock.patch.object(host, "run", return_value=0) as run, mock.patch.object(host.sys, "argv", argv + opt):
                self.assertEqual(host.main(), 0)
                captures.append(committed.call_args.args[0])
                self.assertEqual(run.call_args.kwargs, {"verity_root": bool(opt), "boot_uki": False})
        self.assertEqual(captures[1], (*captures[0], host.VERITY))
        self.assertEqual(len(captures[1]), 30)
        self.assertEqual(len(set(captures[1]) | set(host.tools.FILES)), 38)
        source = (ROOT / host.WORKER).read_text()
        self.assertLess(source.index('module("appraisal-probe").probe('), source.index('stage = "verity-root"'))
        self.assertLess(source.index('module("verity-root").build('), source.index('report["result"] = "pass"'))
        self.assertIn('require(not verity_root or guest["scope"] == "guest-runtime-dependency-coverage")', source)
        ast.parse((ROOT / host.VERITY).read_bytes(), feature_version=(3, 10))

    def test_payload_helper_comes_only_from_bound_source_in_opt_in_mode(self):
        names = (host.WORKER, host.ENTRY, host.DKMS, host.RUNTIME_POLICY, host.APPRAISAL, host.VERITY,
                 host.tools.FILES[4], host.tools.FILES[3], host.probe.native.CHECKER,
                 host.probe.PID_CHECKER, host.probe.OWNERSHIP)
        sources = {name: (ROOT / name).read_bytes() for name in names}
        upstream = {"mkosi/sandbox.py": b"fixture sandbox", host.mkosi.PATCH_TARGET: b"fixture original"}
        sources[host.mkosi.EXTRA[1]] = json.dumps({"source_files": {name: {"mode": "100644"}
                                                  for name in upstream}}).encode()
        prepared = {"mkosi-overlay/" + host.mkosi.PATCH_TARGET: b"fixture patch",
                    "nib/package-worker.py": b"fixture worker", "nib/confinement.py": b"fixture guard"}
        with mock.patch.object(host, "runtime_preflight", return_value={}), \
                mock.patch.object(host.base, "read", return_value=b"fixture archive"), \
                mock.patch.object(host.mkosi, "prepare", side_effect=lambda *a, **k: (dict(prepared), {})), \
                mock.patch.object(host.mkosi, "verify_source", side_effect=lambda *a: ({}, dict(upstream))):
            for flag in (False, True):
                payload = host.prepare(Path("/inputs"), Path("/archive"), "a" * 40, sources, True, flag)[2]
                if flag: self.assertEqual(payload["verity-root.py"], sources[host.VERITY])
                else: self.assertNotIn("verity-root.py", payload)
            with self.assertRaises(ValueError):
                host.prepare(Path("/inputs"), Path("/archive"), "a" * 40, sources, False, True)

    def test_host_requires_all_verification_cleanup_and_no_claim_flags(self):
        with self.fixture() as root, mock.patch.object(verity, "verify_tools", return_value={}):
            valid = verity.build(self.supervisor(), guard.PREFIX, dkms.regular, root / "out")
        mutations = [(None, None), ("result", "fail"), ("schema", "unknown")]
        mutations += [(name, not value) for name, value in valid.items() if type(value) is bool]
        for key, value in mutations:
            with self.subTest(key=key), tempfile.TemporaryDirectory() as tmp:
                proof = {**valid, **({key: value} if key else {})}
                result = {"schema": "nib.mkosi-install-worker/v1", "result": "pass", "bootable": False,
                    "deployable": False, "configured_state_verified": True, "installed_packages": 212,
                    "dkms_built": True, "driver_build": {"result": "pass", "installed": True}, "verity_root": proof}
                def materialize(files, root): root.mkdir()
                def execute(argv, **kwargs):
                    (Path(argv[0]) / "result.json").write_text(json.dumps(result))
                    return subprocess.CompletedProcess(argv, 0, b"", b"")
                output = Path(tmp).resolve() / "evidence"
                with mock.patch.object(host, "prepare", return_value=({"receipt.json": b"{}", "runtime-hook-review.json": b"{}"}, {}, {}, {}, {})), \
                        mock.patch.object(host.prep, "assemble", return_value=({"receipt.json": b"{}"}, {})), \
                        mock.patch.object(host.probe.ownership, "host_inputs", return_value=({}, {})), \
                        mock.patch.object(host.probe, "materialize", side_effect=materialize), \
                        mock.patch.object(host, "command", side_effect=lambda r, p, o, *a: [str(o)]), \
                        mock.patch.object(host.base.processes, "execute_process_group", side_effect=execute):
                    self.assertEqual(host.run(Path("/unused"), Path("/unused"), output, "a" * 40, {}, True, True, True, True),
                                     0 if key is None else 1)
                report = json.loads((output / "result.json").read_bytes())
                self.assertIs(report["payload_removed"], True)
                self.assertIs(report["process_cleanup_confirmed"], True)


if __name__ == "__main__":
    unittest.main()
