# SPDX-License-Identifier: Apache-2.0
"""Synthetic archive/filesystem tests only; never execute packaged programs."""
import copy
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tarfile
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    module = types.ModuleType("tools_member_fixture")
    module.__file__ = str(ROOT / path)
    exec(compile(Path(module.__file__).read_bytes(), module.__file__, "exec",
                 dont_inherit=True), module.__dict__)
    return module


prep = load("scripts/prepare_mkosi_tools.py")
fixtures = load("scripts/test_guest_tools.py")
controls = load("scripts/test_prepare_guest_packages.py")
NAME = "usr/share/fixture/cert-\u00e9.pem"


def pin(raw, mode=0o644):
    return {"bytes": len(raw), "sha256": prep.base.digest(raw), "mode": mode}


def data_policy(raw, data=b"synthetic-public-certificate"):
    return {"archive_sha256": prep.base.digest(raw), "members": {NAME: pin(data)}}


def elf(bits=64, kind=3):
    raw = bytearray(64 if bits == 64 else 52)
    raw[:7] = b"\x7fELF" + bytes((2 if bits == 64 else 1, 1, 1))
    struct.pack_into("<HHI", raw, 16, kind, 62 if bits == 64 else 3, 1)
    struct.pack_into("<H", raw, 52 if bits == 64 else 40, len(raw))
    return bytes(raw)


def binary():
    target = b"/lib64/loader.so\0"
    raw = bytearray(128 + len(target))
    raw[:64] = elf()
    struct.pack_into("<Q", raw, 32, 64)
    struct.pack_into("<HH", raw, 54, 56, 1)
    struct.pack_into("<I", raw, 64, 3)
    struct.pack_into("<Q", raw, 72, 128)
    struct.pack_into("<Q", raw, 96, len(target))
    raw[128:] = target
    return bytes(raw)


def foreign_fixture():
    rows = {"usr/lib/grub/i386-pc/normal.mod": fixtures.file(elf(32, 1), 0o644),
            "usr/lib/grub/i386-pc/kernel.img": fixtures.file(elf(32, 2), 0o644)}
    pins = {name: {**pin(row["data"]), "elf_type": 1 if name.endswith(".mod") else 2}
            for name, row in rows.items()}
    return rows, {"packages": {"grub-pc-bin": {"members": pins}}}


class LiteralDataTests(unittest.TestCase):
    def test_literal_policy_is_bounded_and_not_a_general_path_mode(self):
        raw = fixtures.deb({NAME: fixtures.file(b"synthetic-public-certificate", 0o644)})
        invalid = [True, [NAME], {"archive_sha256": prep.base.digest(raw), "members": {}},
                   {**data_policy(raw), "allow_unicode": True}]
        for name in ("usr/share/ascii.pem", "../" + NAME, "/" + NAME, NAME + "\n"):
            invalid.append({"archive_sha256": prep.base.digest(raw),
                            "members": {name: pin(b"synthetic-public-certificate")}})
        invalid.append({"archive_sha256": prep.base.digest(raw),
                        "members": {NAME + str(i): pin(b"x") for i in range(9)}})
        for field, value in (("bytes", (1 << 20) + 1), ("mode", 0o755)):
            policy = data_policy(raw)
            policy["members"][NAME][field] = value
            invalid.append(policy)
        for policy in invalid:
            with self.subTest(policy=policy), self.assertRaises((ValueError, TypeError)):
                prep.tools.payload(raw, exact_data=policy)

    def test_default_rejects_but_exact_data_preserves_utf8_bytes(self):
        raw = fixtures.deb({NAME: fixtures.file(b"synthetic-public-certificate", 0o644)})
        with self.assertRaises(ValueError):
            prep.tools.payload(raw)
        tree = prep.tools.payload(raw, exact_data=data_policy(raw))
        self.assertEqual(tree[NAME]["data"], b"synthetic-public-certificate")
        with tarfile.open(fileobj=io.BytesIO(prep.tools.tar_bytes(tree))) as archive:
            self.assertEqual(archive.extractfile(NAME).read(), tree[NAME]["data"])

    def test_archive_content_size_mode_and_missing_member_reject(self):
        data = b"synthetic-public-certificate"
        raw = fixtures.deb({NAME: fixtures.file(data, 0o644)})
        for changed in (raw + b"x", fixtures.deb({NAME: fixtures.file(data + b"x", 0o644)}),
                        fixtures.deb({NAME: fixtures.file(data, 0o755)}), fixtures.deb({})):
            with self.subTest(archive=prep.base.digest(changed)), self.assertRaises(ValueError):
                prep.tools.payload(changed, exact_data=data_policy(raw))
        for field, value in (("bytes", 3), ("sha256", "0" * 64), ("mode", 0o600)):
            policy = data_policy(raw)
            policy["members"][NAME][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                prep.tools.payload(raw, exact_data=policy)
        absent = fixtures.deb({})
        with self.assertRaises(ValueError):
            prep.tools.payload(absent, exact_data=data_policy(absent))

    def test_unknown_unicode_normalization_traversal_and_controls_reject(self):
        data = b"synthetic-public-certificate"
        for name in (NAME.replace("\u00e9", "e\u0301"), NAME + "\u00e9", "../" + NAME,
                     "/" + NAME, "usr//" + NAME, "usr/../" + NAME, NAME + "\n"):
            with self.subTest(name=name):
                raw = fixtures.deb({name: fixtures.file(data, 0o644)})
                with self.assertRaises(ValueError):
                    prep.tools.payload(raw, exact_data=data_policy(raw))
        raw = fixtures.deb({NAME: fixtures.file(data, 0o644), NAME + "\u00e9": fixtures.file(data, 0o644)})
        with self.assertRaises(ValueError):
            prep.tools.payload(raw, exact_data=data_policy(raw))

    def test_literal_member_cannot_be_link_directory_or_special(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.DIRTYPE, tarfile.FIFOTYPE):
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT) as archive:
                entry = tarfile.TarInfo(NAME)
                entry.type, entry.mode, entry.linkname = kind, 0o644, "usr/share/other"
                archive.addfile(entry)
            raw = b"fixture archive"
            with mock.patch.object(prep.tools.base, "archive_members",
                                   return_value=(("control.tar", b""), ("data.tar", stream.getvalue()))), \
                    self.subTest(kind=kind), self.assertRaises(ValueError):
                prep.tools.payload(raw, exact_data=data_policy(raw))


class ClassificationTests(unittest.TestCase):
    def test_exact_foreign_data_not_admitted_as_host_programs(self):
        tree, pins = foreign_fixture()
        value = prep.classify(tree, pins)
        self.assertEqual(len(value["non_host_grub_inputs"]), 2)
        self.assertFalse(value["abi_validated"])
        for path in tree:
            with self.subTest(path=path), self.assertRaises(ValueError):
                prep.tools.executable(tree, "/" + path)

    def test_changed_missing_relocated_extra_or_alias_foreign_reject(self):
        tree, pins = foreign_fixture()
        name = next(iter(tree))
        cases = []
        changed = copy.deepcopy(tree)
        changed[name]["data"] += b"x"
        cases.append(changed)
        cases.append({n: r for n, r in tree.items() if n != name})
        for other in ("usr/bin/foreign", "usr/lib/grub/i386-pc/unknown.mod"):
            cases.append({**tree, other: tree[name]})
        cases.append({**tree, name: {"kind": "link", "target": "/usr/other", "mode": 0o777},
                      "usr/other": tree[name]})
        for bad in cases:
            with self.subTest(paths=list(bad)), self.assertRaises(ValueError):
                prep.classify(bad, pins)

    def test_headers_checked_even_with_consistent_content_pin(self):
        for offset, value in ((4, 2), (5, 2), (6, 0), (16, 3), (18, 62), (20, 0), (40, 64)):
            tree, pins = foreign_fixture()
            name = next(iter(tree))
            raw = bytearray(tree[name]["data"])
            raw[offset] = value
            tree[name]["data"] = bytes(raw)
            pins["packages"]["grub-pc-bin"]["members"][name].update(pin(bytes(raw)))
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                prep.classify(tree, pins)

    def test_unknown_wrong_architecture_and_bad_host_headers_reject(self):
        tree, pins = foreign_fixture()
        for offset, value in ((4, 1), (5, 2), (6, 0), (16, 4), (18, 183), (20, 0), (52, 0)):
            raw = bytearray(elf())
            raw[offset] = value
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                prep.classify({**tree, "usr/bin/host": fixtures.file(bytes(raw))}, pins)

    def test_awk_links_are_exact_new_only_and_target_bound(self):
        raw = binary()
        tree = {"usr/bin/gawk": fixtures.file(raw)}
        p = {"members": {"usr/bin/gawk": pin(raw, 0o755)}}
        links = prep.add_awk(tree, p)
        self.assertEqual(links, {"usr/bin/awk": "/etc/alternatives/awk",
                                 "etc/alternatives/awk": "/usr/bin/gawk"})
        self.assertEqual(prep.tools.resolve(tree, "usr/bin/awk"), "usr/bin/gawk")
        with self.assertRaises(ValueError):
            prep.add_awk(tree, p)
        for name, row in (("usr/bin/awk", fixtures.file()),
                          ("etc/alternatives/awk", {"kind": "link", "target": "/usr/bin/other", "mode": 0o777}),
                          ("etc", {"kind": "link", "target": "/private", "mode": 0o777}),
                          ("usr/bin/gawk", fixtures.file(b"changed"))):
            bad = {"usr/bin/gawk": fixtures.file(raw), name: row}
            with self.subTest(name=name), self.assertRaises(ValueError):
                prep.add_awk(bad, p)
            if name != "etc/alternatives/awk":
                self.assertNotIn("etc/alternatives/awk", bad)


class ContractTests(unittest.TestCase):
    def fixture(self, root):
        foreign, foreign_pins = foreign_fixture()
        host = {name.lstrip("/"): fixtures.file(binary()) for name in prep.HOST_PROGRAMS
                if name != "/usr/bin/awk"}
        host.update({"usr/bin/gawk": fixtures.file(binary()),
                     "usr/lib64/loader.so": fixtures.file(elf())})
        trees = {"ca-certificates": {NAME: fixtures.file(b"synthetic-public-certificate", 0o644)},
                 "gawk": host, "grub-pc-bin": foreign}
        packages, pins = [], {}
        pool = root / "repo/pool"
        pool.mkdir(parents=True)
        for name, tree in trees.items():
            control = (f"Package: {name}\nVersion: 1.0\nArchitecture: amd64\n"
                       "Description: synthetic public fixture\n").encode()
            control_name, packed_control = prep.base.control_member(controls.deb(control=control))
            raw = (b"!<arch>\n" + controls.ar_member("debian-binary", b"2.0\n")
                   + controls.ar_member(control_name, packed_control)
                   + controls.ar_member("data.tar", prep.tools.tar_bytes(copy.deepcopy(tree))))
            row = controls.row(raw)
            row.update(Package=name, Filename="pool/" + name + ".deb")
            packages.append(row)
            (pool / prep.base.package_name(row)).write_bytes(raw)
            selected = (foreign_pins["packages"]["grub-pc-bin"]["members"] if name == "grub-pc-bin"
                        else {key: pin(tree[key]["data"], tree[key]["mode"])
                              for key in ([NAME] if name == "ca-certificates" else ["usr/bin/gawk"])})
            pins[name] = {"members": selected, "archive_sha256": row["SHA256"],
                          "archive_bytes": len(raw), "version": "1.0",
                          "source_url": "https://example.invalid/" + row["Filename"]}
            if name == "gawk":
                pins[name]["postinst"] = {"bytes": 8, "sha256": prep.base.digest(b"exit 99\n")}
        lock = {"packages": packages, "archive_bytes": sum(int(r["Size"]) for r in packages),
                "indexes": [{"id": "fixture", "base_url": "https://example.invalid/"}]}
        lock_raw = prep.base.encoded(lock)
        policy = prep.base.encoded({"schema": "nib.mkosi-tools-members/v1",
                                   "tools_lock_sha256": prep.base.digest(lock_raw), "packages": pins})
        (root / "mkosi-tools-inputs.json").write_bytes(lock_raw)
        return {prep.LOCK: lock_raw, prep.POLICY: policy}, packages

    def test_real_archive_assembly_is_deterministic_and_never_executes_payloads(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            sources, rows = self.fixture(root)
            with mock.patch.object(prep, "POLICY_SHA", prep.base.digest(sources[prep.POLICY])), \
                    mock.patch.object(prep.mkosi, "validate_tools_lock"), \
                    mock.patch.object(subprocess, "Popen", side_effect=AssertionError("must not execute")):
                files, report = prep.assemble(root, "a" * 40, sources)
                self.assertEqual((files, report), prep.assemble(root, "a" * 40, sources))
                with tarfile.open(fileobj=io.BytesIO(files["tools-root.tar"])) as archive:
                    self.assertEqual(archive.getmember("usr/bin/awk").linkname, "/etc/alternatives/awk")
                    self.assertEqual(archive.extractfile(NAME).read(), b"synthetic-public-certificate")
                for key in ("abi_validated", "runtime_linkage_validated", "deployable", "bootable",
                            "installable_verified", "package_binaries_executed", "package_scripts_executed",
                            "upstream_executed", "package_configuration_simulated"):
                    self.assertIs(report[key], False)
                for name, item in report["files"].items():
                    self.assertEqual(prep.base.digest(files[name]), item["sha256"])
                target = root / "repo/pool" / prep.base.package_name(rows[0])
                target.write_bytes(target.read_bytes() + b"changed")
                with self.assertRaises(ValueError):
                    prep.assemble(root, "a" * 40, sources)

    def test_changed_control_pin_shared_deadline_and_unsafe_input_reject(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            sources, rows = self.fixture(root)
            bad = json.loads(sources[prep.POLICY])
            bad["packages"]["gawk"]["postinst"]["sha256"] = "0" * 64
            bad_sources = {**sources, prep.POLICY: prep.base.encoded(bad)}
            with mock.patch.object(prep.mkosi, "validate_tools_lock"):
                with mock.patch.object(prep, "POLICY_SHA", prep.base.digest(bad_sources[prep.POLICY])), \
                        self.assertRaises(ValueError):
                    prep.assemble(root, "a" * 40, bad_sources)
                with mock.patch.object(prep, "POLICY_SHA", prep.base.digest(sources[prep.POLICY])), \
                        mock.patch.object(prep.time, "monotonic", side_effect=[100, 700]), \
                        self.assertRaises(ValueError):
                    prep.assemble(root, "a" * 40, sources)
                target = root / "repo/pool" / prep.base.package_name(rows[0])
                saved = target.read_bytes()
                target.unlink()
                for kind in ("symlink", "fifo", "hardlink"):
                    if kind == "symlink":
                        target.symlink_to(root / "missing")
                    elif kind == "fifo":
                        os.mkfifo(target)
                    else:
                        (root / "regular").write_bytes(saved)
                        os.link(root / "regular", target)
                    with self.subTest(kind=kind), \
                            mock.patch.object(prep, "POLICY_SHA", prep.base.digest(sources[prep.POLICY])), \
                            self.assertRaises((OSError, ValueError)):
                        prep.assemble(root, "a" * 40, sources)
                    target.unlink()

    def test_committed_policy_binds_exact_lock_and_complete_foreign_inventory(self):
        raw = (ROOT / prep.POLICY).read_bytes()
        lock = (ROOT / prep.LOCK).read_bytes()
        p = prep.policy(raw, lock)
        members = p["packages"]["grub-pc-bin"]["members"]
        self.assertEqual(len(members), 278)
        self.assertEqual([name for name, row in members.items() if row["elf_type"] == 2],
                         ["usr/lib/grub/i386-pc/kernel.img"])
        for policy_raw, lock_raw in ((raw + b"\n", lock), (raw, lock + b"\n")):
            with self.assertRaises(ValueError):
                prep.policy(policy_raw, lock_raw)

    def test_no_ambient_bootstrap_or_argument_execution(self):
        result = subprocess.run([sys.executable, "-B", str(ROOT / prep.SCRIPT)],
                                capture_output=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"Use python3 -I", result.stderr)

    def test_publication_remains_private_nooverwrite_and_hash_verified(self):
        files = {"tools-root.tar": b"synthetic archive", "receipt.json": b"{}\n"}
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary).resolve() / "output"
            prep.base.publish(destination, files)
            prep.base.verify(destination, files)
            self.assertEqual(destination.stat().st_mode & 0o777, 0o700)
            self.assertEqual((destination / "tools-root.tar").stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                prep.base.publish(destination, files)
            (destination / "tools-root.tar").write_bytes(b"changed")
            with self.assertRaises(ValueError):
                prep.base.verify(destination, files)


if __name__ == "__main__":
    unittest.main()
