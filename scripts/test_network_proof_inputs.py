# SPDX-License-Identifier: Apache-2.0
"""Offline synthetic preparation only; no guest, network or package execution."""
from contextlib import redirect_stdout
import copy
import gzip
import io
import json
import os
from pathlib import Path
import stat
import tarfile
import tempfile
import unittest
from unittest import mock

import prepare_network_proof as proof


def sources():
    return {p: (proof.ROOT / p).read_bytes() for p in dict.fromkeys((*proof.tools.FILES, *proof.EXTRA))}


def bundle():
    return proof.prepare("ab" * 20, sources(), {"synthetic": True}, {"synthetic": True})


def archive(members):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT) as target:
        for name, data in members:
            member = tarfile.TarInfo("systemd-stable-255.4/" + name)
            member.size = len(data)
            target.addfile(member, io.BytesIO(data))
    return gzip.compress(stream.getvalue())


class InputTests(unittest.TestCase):
    def test_fixed_candidate_reuses_shared_go_fixture_without_startup_files(self):
        files, receipt = bundle()
        self.assertEqual(set(files), {"receipt.json", "proof-plan.json",
            "candidate/etc/nib/network.json", "candidate/etc/hosts",
            "candidate/etc/systemd/network/10-nib-nras.network"})
        fixture = json.loads(sources()[proof.FIXTURE])
        self.assertEqual(files["candidate/etc/systemd/network/10-nib-nras.network"], fixture["networkd"].encode())
        self.assertEqual(files["candidate/etc/nib/network.json"], fixture["network_config"].encode())
        plan = json.loads(files["proof-plan.json"])
        self.assertFalse(plan["guest"]["outer_nic"])
        self.assertEqual(plan["topology"]["kind"], "veth-only-inside-debug-guest")
        self.assertEqual(plan["topology"]["peer_namespace"], "separate-inside-debug-guest")
        self.assertTrue(all(row["status"] == "not-run" for row in plan["required_cases"]))
        self.assertEqual(len(set(proof.CASES)), 20)
        self.assertFalse(any(value for value in plan.values() if type(value) is bool))
        self.assertFalse(any(value for value in receipt.values() if type(value) is bool))
        self.assertEqual(receipt["scope"], "offline-synthetic-inputs-only")

    def test_exact_source_and_output_hashes_and_determinism(self):
        files, receipt = bundle()
        self.assertEqual(bundle(), (files, receipt))
        self.assertEqual(receipt["source_hashes"], {p: proof.base.digest(b) for p, b in sources().items()})
        self.assertEqual(receipt["files"], {p: proof.identity(b) for p, b in files.items() if p != "receipt.json"})
        changed = sources()
        changed[proof.FIXTURE] = changed[proof.FIXTURE].replace(b"DHCP=no", b"DHCP=yes")
        with self.assertRaises(ValueError): proof.prepare("ab" * 20, changed, {}, {})

    def test_default_gate_policy_and_workflow_have_no_new_entrypoint(self):
        root = proof.ROOT
        self.assertEqual((root / "guest/network/network.json").read_bytes(),
                         b'{"schema":"nib.guest-network/v1","egress":[]}\n')
        for path in (root / ".github/workflows").glob("*.yml"):
            self.assertNotIn(b"prepare_network_proof", path.read_bytes())
        self.assertIn(b"fs.Type == 0xe0f5e1e2 && fs.Flags&1 != 0",
                      (root / "guest/internal/service/network_linux.go").read_bytes())

    def test_primary_archive_and_members_are_authenticated_before_use(self):
        members = {name: b"synthetic " + name.encode() for name in proof.PRIMARY}
        raw = archive(list(members.items()))
        pins = {p: (len(b), proof.base.digest(b)) for p, b in members.items()}
        with mock.patch.object(proof, "SOURCE_ARCHIVE", (len(raw), proof.base.digest(raw))), \
                mock.patch.object(proof, "PRIMARY", pins), mock.patch.object(proof.base, "read", return_value=raw):
            result = proof.primary_sources(Path("/synthetic"))
        self.assertEqual(result["files"], {p: proof.identity(b) for p, b in members.items()})
        for bad in (raw + b"x", raw[:-1]):
            with mock.patch.object(proof.base, "read", return_value=bad), \
                    mock.patch.object(proof.base, "decompress") as decompress, self.assertRaises(ValueError):
                proof.primary_sources(Path("/synthetic"))
            decompress.assert_not_called()

    def test_primary_duplicate_missing_and_wrong_member_fail(self):
        members = {name: b"synthetic " + name.encode() for name in proof.PRIMARY}
        pins = {p: (len(b), proof.base.digest(b)) for p, b in members.items()}
        rows = list(members.items())
        for bad in (rows[:1], rows + rows[:1], [(rows[0][0], b"wrong"), rows[1]]):
            raw = archive(bad)
            with self.subTest(bad=bad), mock.patch.object(proof, "PRIMARY", pins), \
                    mock.patch.object(proof, "SOURCE_ARCHIVE", (len(raw), proof.base.digest(raw))), \
                    mock.patch.object(proof.base, "read", return_value=raw), self.assertRaises(ValueError):
                proof.primary_sources(Path("/synthetic"))

    def package_fixture(self):
        raw = b"synthetic authenticated package"
        elf = b"\x7fELF\x02\x01" + b"\0" * 12 + b"\x3e\x00"
        tree = {"usr/lib/systemd/systemd-networkd": {"kind": "file", "mode": 0o755, "data": elf},
                "usr/lib/systemd/network/80-existing.network": {"kind": "file", "mode": 0o644, "data": b"original"}}
        pins = {"systemd": ("255.4-1ubuntu8.18", len(raw), proof.base.digest(raw))}
        exe = {"systemd": ("usr/lib/systemd/systemd-networkd", len(elf), proof.base.digest(elf))}
        lock = {"packages": [{"Package": "systemd", "Architecture": "amd64", "Version": pins["systemd"][0],
                              "Size": str(len(raw)), "SHA256": proof.base.digest(raw)}]}
        return raw, tree, pins, exe, lock

    def test_package_executable_and_all_network_configuration_members_bound(self):
        raw, tree, pins, exe, lock = self.package_fixture()
        with mock.patch.object(proof, "PACKAGES", pins), mock.patch.object(proof, "EXECUTABLES", exe), \
                mock.patch.object(proof.base, "read", return_value=raw), \
                mock.patch.object(proof.tools, "payload", return_value=tree):
            report = proof.packages(Path("/synthetic"), {proof.LOCK: json.dumps(lock).encode()})
        self.assertEqual(report["systemd"]["archive"], proof.identity(raw))
        self.assertEqual(report["systemd"]["network_configuration_inventory"],
                         {"usr/lib/systemd/network/80-existing.network": proof.identity(b"original")})

    def test_package_pin_and_lock_mismatch_never_reach_archive_parser(self):
        raw, tree, pins, exe, lock = self.package_fixture()
        for key, value in (("Version", "newer"), ("Architecture", "arm64"), ("SHA256", "0" * 64), ("Size", "1")):
            changed = copy.deepcopy(lock)
            changed["packages"][0][key] = value
            with mock.patch.object(proof, "PACKAGES", pins), mock.patch.object(proof.tools, "payload") as parse, \
                    self.assertRaises(ValueError):
                proof.packages(Path("/synthetic"), {proof.LOCK: json.dumps(changed).encode()})
            parse.assert_not_called()
        with mock.patch.object(proof, "PACKAGES", pins), mock.patch.object(proof.base, "read", return_value=raw + b"x"), \
                mock.patch.object(proof.tools, "payload") as parse, self.assertRaises(ValueError):
            proof.packages(Path("/synthetic"), {proof.LOCK: json.dumps(lock).encode()})
        parse.assert_not_called()

    def test_executable_mutations_and_configuration_links_fail(self):
        raw, tree, pins, exe, lock = self.package_fixture()
        name = exe["systemd"][0]
        mutations = [dict(tree, **{name: {**tree[name], "data": tree[name]["data"] + b"x"}}),
                     dict(tree, **{name: {**tree[name], "mode": 0o777}}),
                     dict(tree, **{name: {"kind": "link", "target": "host-binary"}}),
                     dict(tree, **{"run/systemd/network/extra.network": {"kind": "link", "target": "elsewhere"}})]
        for bad in mutations:
            with mock.patch.object(proof, "PACKAGES", pins), mock.patch.object(proof, "EXECUTABLES", exe), \
                    mock.patch.object(proof.base, "read", return_value=raw), \
                    mock.patch.object(proof.tools, "payload", return_value=bad), self.assertRaises(ValueError):
                proof.packages(Path("/synthetic"), {proof.LOCK: json.dumps(lock).encode()})

    def test_publication_is_private_exclusive_and_verification_rejects_tampering(self):
        files, _ = bundle()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve() / "proof"
            proof.base.publish(root, files)
            proof.base.verify(root, files)
            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
            self.assertTrue(all(stat.S_IMODE((root / p).stat().st_mode) == 0o600 for p in files))
            with self.assertRaises(FileExistsError): proof.base.publish(root, files)
            (root / "extra").write_bytes(b"unexpected")
            with self.assertRaises(ValueError): proof.base.verify(root, files)
            (root / "extra").unlink()
            (root / "proof-plan.json").write_bytes(b"changed")
            with self.assertRaises(ValueError): proof.base.verify(root, files)

    def test_failure_before_receipt_preserves_incomplete_output(self):
        files, _ = bundle()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve() / "proof"
            with mock.patch.object(proof.base.os, "fsync", side_effect=OSError("synthetic")), self.assertRaises(OSError):
                proof.base.publish(root, files)
            self.assertTrue(root.exists())
            self.assertFalse((root / "receipt.json").exists())
            with self.assertRaises(FileExistsError): proof.base.publish(root, files)

    def test_cli_has_no_operator_manifest_or_execution_option(self):
        for option in ("--manifest", "--run", "--interface", "--nras", "--activate", "--arch"):
            with self.subTest(option=option), self.assertRaises(ValueError): proof.main([option, "synthetic"])

    def test_main_uses_verification_only_and_content_free_stdout(self):
        inputs = sources()
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp).resolve()
            args = ["--archives", str(parent / "archives"), "--systemd-source", str(parent / "source.tgz")]
            with mock.patch.object(proof.static, "separate_output"), \
                    mock.patch.object(proof, "committed", return_value=("ab" * 20, inputs)), \
                    mock.patch.object(proof, "packages", return_value={}), \
                    mock.patch.object(proof, "primary_sources", return_value={}):
                output = io.StringIO()
                with redirect_stdout(output): proof.main([*args, "--output", str(parent / "proof")])
                with mock.patch.object(proof.base, "publish") as publish, redirect_stdout(io.StringIO()):
                    proof.main([*args, "--verify", str(parent / "proof")])
                publish.assert_not_called()
                self.assertNotIn(str(parent), output.getvalue())
                self.assertNotIn("192.0.2", output.getvalue())
                self.assertFalse(json.loads(output.getvalue())["proof_completed"])

    def test_output_overlap_and_symlink_ancestors_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp).resolve()
            (parent / "archives").mkdir()
            (parent / "source.tgz").write_bytes(b"synthetic")
            for destination in (parent / "archives" / "output", parent, proof.ROOT / "output"):
                with self.assertRaises(ValueError):
                    proof.static.separate_output(destination, parent / "source.tgz", parent / "archives")
            (parent / "alias").symlink_to(parent / "archives", target_is_directory=True)
            with self.assertRaises(OSError):
                proof.static.separate_output(parent / "alias" / "output", parent / "source.tgz", parent / "archives")


if __name__ == "__main__":
    unittest.main()
