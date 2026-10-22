# SPDX-License-Identifier: Apache-2.0
"""Synthetic assembly/inspection contracts, not a boot or native build proof."""
import ast
from contextlib import contextmanager
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock
import uuid

import mkosi_boot as boot
import mkosi_confinement as guard
import prepare_mkosi_boot as prep
from test_mkosi_build import host, launcher, worker, ROOT
from test_mkosi_verity import verity


def fixture_cpio(tree, members):
    """Write test data only; production always uses authenticated GNU cpio."""
    result = bytearray()
    for ino, name in enumerate([*sorted(members), "TRAILER!!!"], 1):
        row = members.get(name, {"kind": "file", "mode": 0})
        kind = row["kind"]
        data = (row["target"].encode() if kind == "link" else
                (tree / name).read_bytes() if kind == "file" and name != "TRAILER!!!" else b"")
        mode = {"file": stat.S_IFREG, "link": stat.S_IFLNK, "dir": stat.S_IFDIR}[kind] | row["mode"]
        fields = (ino, mode, 0, 0, 1, boot.EPOCH, len(data), 0, 0, 0, 0, len(name) + 1, 0)
        result += b"070701" + b"".join(f"{n:08x}".encode() for n in fields) + name.encode() + b"\0"
        result += bytes(-len(result) % 4) + data
        result += bytes(-len(result) % 4)
    result += bytes(-len(result) % 512)
    return bytes(result)


class BootTests(unittest.TestCase):
    @contextmanager
    def fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "guest/usr/lib").mkdir(parents=True)
            (root / "guest/usr/lib/os-release").write_bytes(b"ID=ubuntu\n")
            (root / "out").mkdir()
            with mock.patch.object(boot, "WORK", root / "private"), \
                    mock.patch.object(boot, "ROOTFS", root / "guest"), \
                    mock.patch.object(boot, "INPUTS", root / "inputs.json"):
                members = {"usr": {"kind": "dir", "mode": 0o755},
                           "usr/lib": {"kind": "dir", "mode": 0o755},
                           "usr/lib/os-release": {"kind": "file", "mode": 0o644,
                                                  **boot.identity(b"ID=ubuntu\n")}}
                value = {"schema": "nib.initrd-inputs/v1", "kernel": boot.KERNEL,
                         "native_execution_verified": False, "members": members}
                boot.INPUTS.write_text(json.dumps(value))
                yield root, members

    def test_tools_and_inputs_are_exact_existing_package_pins(self):
        self.assertEqual(prep.KERNEL, boot.KERNEL)
        self.assertEqual(len(prep.PACKAGES), 39)
        runtime = json.loads((ROOT / prep.LOCK).read_bytes())
        self.assertEqual(len(runtime["packages"]), 212)
        self.assertLessEqual(prep.PACKAGES, {r["Package"] for r in runtime["packages"]})
        self.assertEqual(prep.INVENTORY_SHA256, "9e58a03558396b6cf3370b44405fad56acc18c7962e6b80661eb8942ce46d540")
        self.assertEqual(boot.GUEST["boot/vmlinuz-" + boot.KERNEL],
                         (15083912, "41ba36d860aa0b0cd995f4ca8856d80249b244577e704929b55be13fbd1ebd2f"))
        self.assertEqual(len(boot.TOOLS), 7)
        self.assertTrue(all(len(h) == 64 and n > 0 for n, h in boot.TOOLS.values()))
        self.assertFalse(any("nvidia" in p or "nvattest" in p for p in prep.PACKAGES))

    def test_fixed_selection_excludes_host_configuration_hooks_docs_and_extra_kernel(self):
        self.assertTrue(prep.selected("systemd", "usr/lib/systemd/systemd", "file"))
        self.assertTrue(prep.selected("libc6", "lib/x86_64-linux-gnu/libc.so.6", "file"))
        self.assertFalse(prep.selected("base-files", "etc/passwd", "file"))
        for name in ("etc/hosts", "etc/resolv.conf", "usr/share/initramfs-tools/hooks/kmod", "usr/share/doc/a"):
            self.assertFalse(prep.selected("kmod", name, "file"))
        self.assertFalse(prep.selected("linux-modules-" + boot.KERNEL, "lib/modules/other/kernel/a.ko", "file"))
        self.assertFalse(prep.selected("systemd", "usr/lib", "dir"))

    def test_read_write_are_anchored_exclusive_and_bounded(self):
        for kind in ("symlink", "hardlink", "fifo", "directory", "oversize"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                sentinel = root / "sentinel"
                sentinel.write_bytes(b"sentinel")
                path = root / "input"
                if kind == "symlink": path.symlink_to(sentinel)
                elif kind == "hardlink": os.link(sentinel, path)
                elif kind == "fifo": os.mkfifo(path)
                elif kind == "directory": path.mkdir()
                else: path.write_bytes(b"x" * 9)
                with self.assertRaises((OSError, ValueError)):
                    boot.read(path, 8)
                with self.assertRaises(OSError):
                    boot.write(path, b"bad")
                self.assertEqual(sentinel.read_bytes(), b"sentinel")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "link").symlink_to(root)
            with self.assertRaises(OSError): boot.write(root / "link/no", b"bad")
            boot.write(root / "empty", b"")
            self.assertEqual(boot.read(root / "empty", 0), b"")
            self.assertEqual(stat.S_IMODE((root / "empty").stat().st_mode), 0o600)

    def test_inventory_rejects_paths_counts_sizes_types_and_duplicates(self):
        with self.fixture() as (_, members):
            original = boot.INPUTS.read_bytes()
            self.assertEqual(boot.inventory()[0], members)
            for name in ("../escape", "/absolute", "usr//lib", "usr/./lib", "usr/../lib", "bad\n"):
                value = json.loads(original)
                value["members"][name] = {"kind": "dir", "mode": 0o755}
                boot.INPUTS.write_text(json.dumps(value))
                with self.subTest(name=name), self.assertRaises(ValueError): boot.inventory()
            for field, bad in (("bytes", True), ("bytes", -1), ("bytes", 65 << 20), ("mode", 0o4755),
                               ("sha256", "x" * 64), ("kind", "device")):
                value = json.loads(original)
                value["members"]["usr/lib/os-release"][field] = bad
                boot.INPUTS.write_text(json.dumps(value))
                with self.subTest(field=field, bad=bad), self.assertRaises(ValueError): boot.inventory()
            boot.INPUTS.write_bytes(original.replace(b'"kernel":', b'"kernel":"bad","kernel":'))
            with self.assertRaises(ValueError): boot.inventory()

    def test_assembly_preserves_pinned_bytes_and_adds_only_fixed_private_policy(self):
        with self.fixture() as (_, members):
            boot.WORK.mkdir()
            boot.assemble(members)
            tree = boot.WORK / "tree"
            self.assertEqual((tree / "usr/lib/os-release").read_bytes(), b"ID=ubuntu\n")
            self.assertEqual(os.readlink(tree / "init"), "/usr/lib/systemd/systemd")
            self.assertEqual(os.readlink(tree / "etc/initrd-release"), "os-release")
            self.assertEqual(os.readlink(tree / "etc/systemd/system/default.target"),
                             "/usr/lib/systemd/system/initrd.target")
            for name in boot.MASKS:
                self.assertEqual(os.readlink(tree / "etc/systemd/system" / name), "/dev/null")
            self.assertEqual(os.readlink(tree / "etc/systemd/journald.conf.d/syslog.conf"), "/dev/null")
            self.assertIn(b"Storage=none\nForwardToSyslog=no\n", (tree / "etc/systemd/journald.conf.d/99-nib.conf").read_bytes())
            self.assertFalse((tree / "etc/resolv.conf").exists())
            with self.assertRaises(FileExistsError): boot.assemble(members)

    def test_assembly_rejects_changed_installed_member_and_parent_link(self):
        for kind in ("changed", "link"):
            with self.subTest(kind=kind), self.fixture() as (_, members):
                boot.WORK.mkdir()
                source = boot.ROOTFS / "usr/lib/os-release"
                if kind == "changed": source.write_bytes(b"ID=other!\n")
                else:
                    source.unlink()
                    source.symlink_to("/etc/passwd")
                with self.assertRaises((ValueError, OSError)): boot.assemble(members)

    def test_rejected_member_diagnostics_are_bounded_metadata_not_contents(self):
        for kind in ("oversize", "changed", "hardlink", "directory", "symlink", "missing"):
            with self.subTest(kind=kind), self.fixture() as (root, members):
                source = boot.ROOTFS / "usr/lib/os-release"
                if kind == "oversize": source.write_bytes(b"PRIVATE-CONTENT")
                elif kind == "changed": source.write_bytes(b"PRIVATE!!\n")
                elif kind == "hardlink": os.link(source, root / "alias")
                else:
                    source.unlink()
                    if kind == "directory": source.mkdir()
                    elif kind == "symlink": source.symlink_to("/etc/passwd")
                diagnostics = {}
                with mock.patch.object(boot, "verify_tools", return_value={}):
                    with self.assertRaises((OSError, ValueError)):
                        boot.build(self.supervisor(), guard.PREFIX, "ab" * 32,
                                   root / "out", diagnostics=diagnostics)
                self.assertFalse(boot.WORK.exists())
                self.assertEqual(set(diagnostics), {"member", "expected_bytes", "expected_sha256", "observed"})
                self.assertEqual(diagnostics["member"], "usr/lib/os-release")
                self.assertEqual(diagnostics["expected_bytes"], members["usr/lib/os-release"]["bytes"])
                if kind in ("symlink", "missing", "directory"):
                    self.assertEqual(diagnostics["observed"], {})
                else:
                    self.assertEqual(diagnostics["observed"], {
                        "regular": True, "links": 2 if kind == "hardlink" else 1,
                        "bytes": source.stat().st_size})
                encoded = json.dumps(diagnostics)
                self.assertNotIn("PRIVATE", encoded)
                self.assertNotIn(str(root), encoded)
                self.assertLess(len(encoded), 2048)

    def test_successful_assembly_clears_last_member_diagnostic(self):
        with self.fixture() as (_, members):
            boot.WORK.mkdir()
            diagnostics = {"old": "must be cleared"}
            boot.assemble(members, diagnostics)
            self.assertEqual(diagnostics, {})

    def test_command_line_is_fixed_and_has_no_verity_bypass(self):
        self.assertEqual(boot.cmdline("ab" * 32),
                         b"root=/dev/mapper/root rootfstype=erofs ro roothash=" + b"ab" * 32)
        for bad in (None, "", "A" * 64, "a" * 63, "a" * 65, "a" * 64 + "\n", "a" * 64 + " quiet"):
            with self.assertRaises(ValueError): boot.cmdline(bad)
        for bad in ("ignore_corruption", "check_at_most_once", "rw", "init=/bin/sh"):
            self.assertNotIn(bad.encode(), boot.cmdline("ab" * 32).split())

    def test_fixed_vendor_commands_and_empty_config(self):
        self.assertEqual(boot.operation("depmod"), ["depmod", "--all", "--config",
            "/work/boot-uki/depmod.conf", "--basedir", "/work/boot-uki/tree", boot.KERNEL])
        self.assertEqual(boot.operation("cpio"), ["/usr/bin/cpio", "--create", "--reproducible",
            "--renumber-inodes", "--null", "--format=newc", "--quiet", "--directory",
            "/work/boot-uki/tree", "--owner=0:0"])
        args = boot.operation("ukify")
        for arg in ("--config=/work/boot-uki/ukify.conf", "--efi-arch=x64", "--no-sign-kernel", "--no-measure",
                    "--uname=" + boot.KERNEL, "--cmdline=@/work/boot-uki/cmdline", "build"):
            self.assertIn(arg, args)
        with self.assertRaises(ValueError): boot.operation("sign")
        with mock.patch.dict(os.environ, {"LD_PRELOAD": "/bad", "PYTHONPATH": "/bad", "SECRET": "bad"}, clear=True):
            self.assertEqual(set(boot.environment()), {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "SOURCE_DATE_EPOCH"})

    def test_mounts_and_privilege_boundary_stay_fixed(self):
        for name in boot.OPERATIONS:
            argv = boot.command(name, guard.PREFIX)
            triples = [argv[i:i+3] for i in range(len(argv)-2)]
            self.assertIn(["--ro-bind", "/", "/"], triples)
            self.assertIn(["--ro-bind", str(boot.ROOTFS), str(boot.ROOTFS)], triples)
            self.assertEqual(argv.count("--bind"), int(name != "inspect"))
            if name != "inspect": self.assertIn(["--bind", str(boot.WORK), str(boot.WORK)], triples)
            index = argv.index(guard.PREFIX[0])
            self.assertEqual(argv[index:index+len(guard.PREFIX)], list(guard.PREFIX))
            self.assertNotIn("--become-root", argv)
            self.assertNotIn("--share-net", argv)

    def test_cpio_contents_modes_ownership_and_trailer_are_verified(self):
        with self.fixture() as (_, members):
            boot.WORK.mkdir()
            boot.assemble(members)
            snapshot = boot.snapshot()
            raw = fixture_cpio(boot.WORK / "tree", snapshot)
            boot.check_cpio(raw, snapshot)
            for bad in (b"", raw[:-512], raw + b"extra", raw.replace(b"ID=ubuntu", b"ID=evil!!"),
                        raw.replace(b"TRAILER!!!", b"TRAILER!!?"), raw[:10] + b"zzzzzzzz" + raw[18:]):
                with self.subTest(size=len(bad)), self.assertRaises(ValueError): boot.check_cpio(bad, snapshot)
            for offset in (14, 22, 30, 46):
                bad = raw[:offset] + b"ffffffff" + raw[offset+8:]
                with self.subTest(offset=offset), self.assertRaises(ValueError): boot.check_cpio(bad, snapshot)
            changed = copy.deepcopy(snapshot)
            changed["usr/lib/os-release"]["mode"] = 0o777
            with self.assertRaises(ValueError): boot.check_cpio(raw, changed)

    def test_verified_source_loader_never_uses_bytecode(self):
        finder = boot.VendorSources()
        spec = finder.find_spec("pefile")
        value = importlib.util.module_from_spec(spec)
        source = b"sentinel = 'verified-source'\n"
        name = spec.origin.lstrip("/")
        with mock.patch.dict(boot.TOOLS, {name: (len(source), hashlib.sha256(source).hexdigest())}), \
                mock.patch.object(boot, "read", return_value=source) as reader:
            finder.exec_module(value)
            self.assertEqual(value.sentinel, "verified-source")
            reader.assert_called_once_with(Path(spec.origin), len(source))
        with mock.patch.object(boot, "read", return_value=source), self.assertRaises(ValueError):
            finder.exec_module(importlib.util.module_from_spec(spec))
        self.assertIsNone(finder.find_spec("unapproved_package"))
        self.assertIsNotNone(finder.find_spec("ordlookup").submodule_search_locations)

    def pe_fixture(self):
        def header(**values):
            return types.SimpleNamespace(__keys__=tuple((key,) for key in values), **values)
        def section(name, raw, index, characteristics):
            padded = raw + bytes(512 - len(raw))
            value = header(Name=name.encode(), Misc_VirtualSize=len(raw), SizeOfRawData=512,
                PointerToRawData=4096 + index * 512, VirtualAddress=(index + 1) * 4096,
                Characteristics=characteristics)
            value.get_data = lambda: padded
            value.get_file_offset = lambda: 256 + index * 40
            value.sizeof = lambda: 40
            return value
        payloads = {".linux": b"pinned-kernel", ".initrd": b"fixture-archive", ".cmdline": b"fixed cmdline",
                    ".osrel": b"ID=ubuntu\n", ".uname": boot.KERNEL.encode()}
        stub = types.SimpleNamespace(
            DOS_HEADER=header(e_magic=0x5a4d, e_lfanew=128), NT_HEADERS=header(Signature=0x4550),
            FILE_HEADER=header(Machine=0x8664, NumberOfSections=6, PointerToSymbolTable=0, NumberOfSymbols=0),
            OPTIONAL_HEADER=header(Magic=0x20b, Subsystem=10, AddressOfEntryPoint=4096,
                ImageBase=0x400000, FileAlignment=512, SectionAlignment=4096,
                SizeOfHeaders=4096, SizeOfImage=7 * 4096, SizeOfInitializedData=100, CheckSum=0),
            sections=[], get_warnings=lambda: [])
        stub.OPTIONAL_HEADER.DATA_DIRECTORY = [header(VirtualAddress=0, Size=0) for _ in range(16)]
        for index, name in enumerate((".text", ".rodata", ".data", ".sbat", ".sdmagic", ".reloc")):
            raw = b"sbat,1,fixture\nstub,1,fixture\n" if name == ".sbat" else b"pinned-stub-code"
            stub.sections.append(section(name, raw, index, 0x60000020 if name == ".text" else 0x40000040))
        kernel = types.SimpleNamespace(sections=[], get_warnings=lambda: [])
        pe = copy.deepcopy(stub)
        sbat = boot.merged_sbat(stub, kernel)
        pe.sections[3].Misc_VirtualSize = len(sbat)
        pe.sections[3].get_data = lambda: sbat + bytes(512 - len(sbat))
        for index, (name, raw) in enumerate(payloads.items(), 6):
            pe.sections.append(section(name, raw, index, 0x40000020 if name == ".linux" else 0x40000040))
        pe.FILE_HEADER.NumberOfSections = 11
        pe.OPTIONAL_HEADER.SizeOfImage = 12 * 4096
        pe.OPTIONAL_HEADER.SizeOfInitializedData += sum(map(len, payloads.values()))
        pe.stub, pe.kernel = stub, kernel
        files = {boot.WORK / "nib.efi": b"synthetic-image" + bytes(10000),
                 boot.ROOTFS / "usr/lib/systemd/boot/efi/linuxx64.efi.stub": b"synthetic-stub",
                 boot.ROOTFS / ("boot/vmlinuz-" + boot.KERNEL): payloads[".linux"],
                 boot.WORK / "initrd.cpio": payloads[".initrd"], boot.WORK / "cmdline": payloads[".cmdline"],
                 boot.WORK / "tree/usr/lib/os-release": payloads[".osrel"]}
        return pe, files

    def inspect_fixture(self, pe, files):
        name = "usr/lib/systemd/boot/efi/linuxx64.efi.stub"
        raw = files[boot.ROOTFS / name]
        def parse(data, fast_load):
            self.assertTrue(fast_load)
            if data == raw:
                return pe.stub
            if data == files[boot.ROOTFS / ("boot/vmlinuz-" + boot.KERNEL)]:
                return pe.kernel
            return pe
        with mock.patch.object(boot, "read", side_effect=lambda p,n: files[p]), \
                mock.patch.dict(boot.GUEST, {name: (len(raw), hashlib.sha256(raw).hexdigest())}):
            return boot.inspect_uki(types.SimpleNamespace(PE=parse))

    def test_pe_inspection_requires_exact_sections_machine_unsigned_and_no_warnings(self):
        pe, files = self.pe_fixture()
        value = self.inspect_fixture(pe, files)
        self.assertIs(value["signed"], False)
        self.assertEqual(set(value["sections"]), {".linux", ".initrd", ".cmdline", ".osrel", ".uname"})
        for bad in ("machine", "subsystem", "warning", "signed", "duplicate", "missing", "hash", "size", "offset", "extra"):
            with self.subTest(bad=bad):
                pe, files = self.pe_fixture()
                if bad == "machine": pe.FILE_HEADER.Machine = 0xaa64
                elif bad == "subsystem": pe.OPTIONAL_HEADER.Subsystem = 3
                elif bad == "warning": pe.get_warnings = lambda: ["bad header"]
                elif bad == "signed": pe.OPTIONAL_HEADER.DATA_DIRECTORY[4].Size = 1
                elif bad == "duplicate": pe.sections.append(pe.sections[0])
                elif bad == "missing": pe.sections.pop()
                elif bad == "hash": pe.sections[6].get_data = lambda: b"changed-kernel"
                elif bad == "size": pe.sections[0].SizeOfRawData = 0
                elif bad == "extra":
                    section = copy.copy(pe.sections[-1])
                    section.Name = b".profile"
                    pe.sections.append(section)
                else: pe.sections[0].PointerToRawData = 1 << 30
                with self.assertRaises(ValueError):
                    self.inspect_fixture(pe, files)

    def test_pe_rejects_modified_stub_entry_point_directories_and_sbat(self):
        for change in ("text", "entry", "base", "directory", "sbat", "stub-header", "permissions"):
            with self.subTest(change=change):
                pe, files = self.pe_fixture()
                if change == "text": pe.sections[0].get_data = lambda: b"altered" + bytes(505)
                elif change == "entry": pe.OPTIONAL_HEADER.AddressOfEntryPoint = pe.sections[7].VirtualAddress
                elif change == "base": pe.OPTIONAL_HEADER.ImageBase += 4096
                elif change == "directory": pe.OPTIONAL_HEADER.DATA_DIRECTORY[9].VirtualAddress = 4096
                elif change == "sbat": pe.sections[3].get_data = lambda: b"altered" + bytes(505)
                elif change == "stub-header": pe.sections[0].VirtualAddress += 4096
                else: pe.sections[7].Characteristics |= 0x20000000
                with self.assertRaises(ValueError): self.inspect_fixture(pe, files)

    def test_pe_rejects_overlapping_unaligned_header_and_out_of_image_sections(self):
        for change in ("raw-overlap", "virtual-overlap", "raw-align", "virtual-align", "raw-header",
                       "virtual-header", "image-bound", "section-header", "file-alignment", "image-alignment"):
            with self.subTest(change=change):
                pe, files = self.pe_fixture()
                section = pe.sections[7]
                if change == "raw-overlap": section.PointerToRawData = pe.sections[0].PointerToRawData
                elif change == "virtual-overlap": section.VirtualAddress = pe.sections[0].VirtualAddress
                elif change == "raw-align": section.PointerToRawData += 1
                elif change == "virtual-align": section.VirtualAddress += 1
                elif change == "raw-header": section.PointerToRawData = 512
                elif change == "virtual-header": section.VirtualAddress = 0
                elif change == "image-bound": section.VirtualAddress = pe.OPTIONAL_HEADER.SizeOfImage
                elif change == "section-header": section.get_file_offset = lambda: 4090
                elif change == "file-alignment": pe.OPTIONAL_HEADER.FileAlignment = 513
                else: pe.OPTIONAL_HEADER.SizeOfImage += 1
                with self.assertRaises(ValueError): self.inspect_fixture(pe, files)

    def supervisor(self, failure=None):
        def execute(argv, **kwargs):
            name = argv[-1]
            self.assertEqual(kwargs, {"env": boot.environment(), "timeout": boot.OPERATIONS[name],
                "max_output_bytes": 65536, "max_stderr_bytes": 65536, "stdin": subprocess.DEVNULL})
            if name == failure:
                raise subprocess.TimeoutExpired(argv, 1)
            stdout = b""
            if name == "depmod":
                path = boot.WORK / "tree/usr/lib/modules" / boot.KERNEL
                for leaf in boot.DEPMOD_FILES:
                    (path / leaf).write_bytes(b"fixture module metadata\n")
            elif name == "cpio":
                (boot.WORK / "initrd.cpio").write_bytes(fixture_cpio(boot.WORK / "tree", boot.snapshot()))
            elif name == "ukify":
                (boot.WORK / "nib.efi").write_bytes(b"fixture not a UKI\n")
            else:
                stdout = json.dumps({"schema": "nib.uki-sections/v1", "signed": False,
                    "image": boot.identity((boot.WORK / "nib.efi").read_bytes()),
                    "sections": {".initrd": boot.identity((boot.WORK / "initrd.cpio").read_bytes()),
                                 ".cmdline": boot.identity((boot.WORK / "cmdline").read_bytes())}}).encode()
            return subprocess.CompletedProcess(argv, 0, stdout, b"")
        return types.SimpleNamespace(execute_process_group=mock.Mock(side_effect=execute),
                                     OutputLimitExceeded=host.base.processes.OutputLimitExceeded)

    def module_fixture(self):
        original = boot.assemble
        def assemble(members, diagnostics=None):
            original(members, diagnostics)
            (boot.WORK / "tree/usr/lib/modules" / boot.KERNEL).mkdir(parents=True)
        return assemble

    def test_lifecycle_verifies_all_stages_then_removes_private_images(self):
        with self.fixture() as (root, _), mock.patch.object(boot, "verify_tools", return_value={}), \
                mock.patch.object(boot, "assemble", side_effect=self.module_fixture()):
            processes = self.supervisor()
            report = boot.build(processes, guard.PREFIX, "ab" * 32, root / "out")
            self.assertEqual([c.args[0][-1] for c in processes.execute_process_group.call_args_list], list(boot.OPERATIONS))
            self.assertEqual(report["result"], "pass")
            self.assertIs(report["private_images_removed"], True)
            for key in ("signed", "images_exported", "boot_verified", "reproducibility_verified", "confidentiality_verified"):
                self.assertIs(report[key], False)
            self.assertFalse(boot.WORK.exists())
            self.assertEqual({p.name for p in (root / "out").iterdir()}, {"boot-inspect.stdout"})

    def test_every_operation_timeout_removes_private_workspace_and_never_succeeds(self):
        for name in boot.OPERATIONS:
            with self.subTest(name=name), self.fixture() as (root, _), \
                    mock.patch.object(boot, "verify_tools", return_value={}), \
                    mock.patch.object(boot, "assemble", side_effect=self.module_fixture()):
                with self.assertRaises(subprocess.TimeoutExpired):
                    boot.build(self.supervisor(name), guard.PREFIX, "ab" * 32, root / "out")
                self.assertFalse(boot.WORK.exists())

    def test_collision_preserved_and_cleanup_failure_rejects(self):
        with self.fixture() as (root, _), mock.patch.object(boot, "verify_tools", return_value={}):
            boot.WORK.mkdir()
            (boot.WORK / "sentinel").write_bytes(b"preserve")
            with self.assertRaises(FileExistsError):
                boot.build(self.supervisor(), guard.PREFIX, "ab" * 32, root / "out")
            self.assertEqual((boot.WORK / "sentinel").read_bytes(), b"preserve")
        with self.fixture() as (root, _), mock.patch.object(boot, "verify_tools", return_value={}), \
                mock.patch.object(boot, "assemble", side_effect=self.module_fixture()), \
                mock.patch.object(boot.shutil, "rmtree", side_effect=OSError("fixture cleanup failure")):
            with self.assertRaises(OSError): boot.build(self.supervisor(), guard.PREFIX, "ab" * 32, root / "out")

    def test_bounded_failure_logs_are_preserved_and_exclusive(self):
        processes = host.base.processes
        for error in (processes.OutputLimitExceeded("stdout", b"prefix", b"error"), None):
            with tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp).resolve()
                process = (mock.Mock(side_effect=error) if error else
                           mock.Mock(return_value=subprocess.CompletedProcess([], 1, b"prefix", b"error")))
                with mock.patch.object(processes, "execute_process_group", process), \
                        self.assertRaises((subprocess.SubprocessError, ValueError)):
                    boot.execute(processes, guard.PREFIX, "ukify", output)
                self.assertEqual((output / "boot-ukify.stderr").read_bytes(), b"error")
                self.assertEqual(host.collect_diagnostics(output, boot_uki=True),
                                 {"boot-ukify.stdout": b"prefix", "boot-ukify.stderr": b"error"})
                self.assertEqual(host.collect_diagnostics(output), {})
                with self.assertRaises(FileExistsError): boot.write(output / "boot-ukify.stderr", b"changed")

    def test_opt_in_needs_all_previous_gates_and_default_command_is_unchanged(self):
        with mock.patch.object(host.os, "readlink", return_value="ns:[1]"), \
                mock.patch.object(host.probe.ownership, "prefix", return_value=[]):
            args = (Path("/root"), Path("/payload"), Path("/out"), {})
            original = host.command(*args, True, True, True)
            self.assertEqual(host.command(*args, True, True, True, True), original + ["--boot-uki"])
            with self.assertRaises(ValueError): host.command(*args, True, True, False, True)
        good = ["--install-driver", "--verity-root", "--boot-uki"]
        self.assertEqual(worker.options(good), (True, True, True, True))
        for bad in (["--boot-uki"], ["--install-driver", "--boot-uki"], good[::-1], good + ["--boot-uki"]):
            with self.assertRaises(ValueError): worker.options(bad)
        args = ["host", "--downloads", "/downloads", "--archive", "/archive", "--output", "/output", "--boot-uki"]
        for entry in (host, launcher):
            with mock.patch.object(entry.sys, "argv", args), self.assertRaises(ValueError): entry.main()

    def test_committed_source_closure_is_32_and_preserves_package_locks(self):
        args = ["host", "--downloads", "/downloads", "--archive", "/archive", "--output", "/output",
                "--compile-driver", "--install-driver", "--guest-runtime", "--verity-root", "--boot-uki"]
        with mock.patch.object(host, "preflight"), mock.patch.object(host.tools, "separate_output"), \
                mock.patch.object(host.tools, "committed", return_value=("a" * 40, {})) as committed, \
                mock.patch.object(host, "run", return_value=0) as run, mock.patch.object(host.sys, "argv", args):
            self.assertEqual(host.main(), 0)
            paths = committed.call_args.args[0]
            self.assertEqual(len(paths), 32)
            self.assertEqual(paths[-3:], (host.VERITY, host.BOOT, host.BOOT_PREP))
            self.assertEqual(run.call_args.kwargs, {"verity_root": True, "boot_uki": True})
        source = (ROOT / host.WORKER).read_text()
        self.assertLess(source.index('module("appraisal-probe").probe('), source.index('stage = "verity-root"'))
        self.assertLess(source.index('module("verity-root").build('), source.index('stage = "boot-uki"'))
        for path in (host.BOOT, host.BOOT_PREP, host.SCRIPT, host.LAUNCHER):
            ast.parse((ROOT / path).read_bytes(), feature_version=(3, 10))

    def test_verity_partition_ids_must_match_initrd_discovery_only_when_opted_in(self):
        # No images or crypto verification occurs in this metadata-only fixture.
        root_hash = "ab" * 32
        rows = [{"type": kind, "label": label, "partno": i, "raw_size": 4096,
                 "offset": 4096 * (i + 1), "roothash": root_hash, "uuid": str(uuid.UUID(hex=root_hash[i*32:(i+1)*32]))}
                for i, (kind, label) in enumerate((("root-x86-64", "nib-root"), ("root-x86-64-verity", "nib-verity")))]
        identity = {"bytes": 4096, "sha256": "cd" * 32}
        with mock.patch.object(verity, "inspect_file", return_value=identity):
            self.assertEqual(verity.partition_report(json.dumps(rows).encode(), identity, identity,
                                                    {"bytes": 16384}, require_boot_ids=True), root_hash)
            rows[1]["uuid"] = str(uuid.UUID(int=0))
            with self.assertRaises(ValueError):
                verity.partition_report(json.dumps(rows).encode(), identity, identity,
                                        {"bytes": 16384}, require_boot_ids=True)
            self.assertEqual(verity.partition_report(json.dumps(rows).encode(), identity, identity,
                                                    {"bytes": 16384}), root_hash)

    def test_native_entry_checks_confinement_pins_and_limits_before_depmod_exec(self):
        events = []
        flags = types.SimpleNamespace(isolated=True, no_site=True, dont_write_bytecode=True)
        with mock.patch.object(boot.sys, "platform", "linux"), \
                mock.patch.object(boot.sys, "version_info", (3, 12)), \
                mock.patch.object(boot.sys, "flags", flags), \
                mock.patch.object(boot.sys, "argv", ["worker", "depmod"]), \
                mock.patch.object(boot.os, "geteuid", return_value=0), \
                mock.patch.object(boot.os, "getegid", return_value=0), \
                mock.patch.object(boot.socket, "if_nameindex", return_value=[(1, "lo")]), \
                mock.patch.object(boot, "module", return_value=types.SimpleNamespace(check=lambda: events.append("guard"))), \
                mock.patch.object(boot, "verify_tools", side_effect=lambda: events.append("pins")), \
                mock.patch.object(boot.os, "umask", side_effect=lambda mode: events.append(mode)), \
                mock.patch.object(boot.resource, "setrlimit", side_effect=lambda *a: events.append(a)), \
                mock.patch.object(boot.os, "execve", side_effect=lambda *a: events.append(a)):
            boot.main()
        self.assertEqual(events, ["guard", "pins", 0o077, (boot.resource.RLIMIT_CORE, (0, 0)),
            (boot.resource.RLIMIT_FSIZE, (boot.MAX_IMAGE, boot.MAX_IMAGE)),
            ("/usr/bin/kmod", boot.operation("depmod"), boot.environment())])

    def test_payload_preparation_executes_only_bound_source_and_is_opt_in(self):
        names = (host.WORKER, host.ENTRY, host.DKMS, host.RUNTIME_POLICY, host.APPRAISAL, host.VERITY,
                 host.BOOT, host.tools.FILES[4], host.tools.FILES[3], host.probe.native.CHECKER,
                 host.probe.PID_CHECKER, host.probe.OWNERSHIP)
        sources = {name: (ROOT / name).read_bytes() for name in names}
        sources[host.BOOT_PREP] = b'def prepare(archives, lock, tools):\n    return b"bound inventory:" + lock\n'
        sources[host.hooks.RUNTIME_LOCK] = b"bound lock"
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
                payload = host.prepare(Path("/inputs"), Path("/archive"), "a" * 40, sources, True, True, flag)[2]
                if flag:
                    self.assertEqual(payload["boot-uki.py"], sources[host.BOOT])
                    self.assertEqual(payload["boot-inputs.json"], b"bound inventory:bound lock")
                else:
                    self.assertNotIn("boot-uki.py", payload)
                    self.assertNotIn("boot-inputs.json", payload)

    def test_host_rejects_changed_boot_proof_and_root_binding(self):
        valid = {"schema": "nib.private-boot-uki/v1", "result": "pass", "root_hash": "ab" * 32,
            "archive_checked": True, "sections_checked": True, "private_images_removed": True,
            "signed": False, "images_exported": False, "boot_verified": False,
            "reproducibility_verified": False, "confidentiality_verified": False}
        mutations = [(None, None), ("result", "fail"), ("schema", "unknown"), ("root_hash", "cd" * 32)]
        mutations += [(name, not value) for name, value in valid.items() if type(value) is bool]
        mutations += [("boot_partuuids_checked", False)]
        for key, value in mutations:
            with self.subTest(key=key), tempfile.TemporaryDirectory() as tmp:
                root_proof = {"schema": "nib.private-verity-root/v1", "result": "pass", "root_hash": "ab" * 32,
                    "erofs_checked": True, "userspace_verity_verified": True, "wrong_root_rejected": True,
                    "private_images_removed": True, "boot_partuuids_checked": True,
                    "images_exported": False, "boot_verified": False, "kernel_verity_verified": False,
                    "reproducibility_verified": False, "confidentiality_verified": False}
                proof = dict(valid)
                if key == "boot_partuuids_checked": root_proof[key] = value
                elif key: proof[key] = value
                result = {"schema": "nib.mkosi-install-worker/v1", "result": "pass", "bootable": False,
                    "deployable": False, "configured_state_verified": True, "installed_packages": 212,
                    "dkms_built": True, "driver_build": {"result": "pass", "installed": True},
                    "verity_root": root_proof, "boot_uki": proof}
                def execute(argv, **kwargs):
                    (Path(argv[0]) / "result.json").write_text(json.dumps(result))
                    return subprocess.CompletedProcess(argv, 0, b"", b"")
                output = Path(tmp).resolve() / "evidence"
                with mock.patch.object(host, "prepare", return_value=({"receipt.json": b"{}", "runtime-hook-review.json": b"{}"}, {}, {}, {}, {})), \
                        mock.patch.object(host.prep, "assemble", return_value=({"receipt.json": b"{}"}, {})), \
                        mock.patch.object(host.probe.ownership, "host_inputs", return_value=({}, {})), \
                        mock.patch.object(host.probe, "materialize", side_effect=lambda files, root: root.mkdir()), \
                        mock.patch.object(host, "command", side_effect=lambda r, p, o, *a: [str(o)]), \
                        mock.patch.object(host.base.processes, "execute_process_group", side_effect=execute):
                    self.assertEqual(host.run(Path("/unused"), Path("/unused"), output, "a" * 40, {},
                                             True, True, True, True, True), 0 if key is None else 1)
                report = json.loads((output / "result.json").read_bytes())
                self.assertIs(report["payload_removed"], True)
                self.assertIs(report["process_cleanup_confirmed"], True)
