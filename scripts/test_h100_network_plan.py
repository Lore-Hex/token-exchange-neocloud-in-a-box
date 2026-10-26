# SPDX-License-Identifier: Apache-2.0
"""Offline static-plan tests; no device, network, systemd or package execution."""
from contextlib import contextmanager, redirect_stdout
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import prepare_h100_network as plan

ROOT = plan.ROOT
FIXTURE = json.loads((ROOT / "guest/nftables/testdata/h100-static-plan.json").read_bytes())


def raw(value=None):
    return plan.base.encoded(FIXTURE["manifest"] if value is None else value)


def sources():
    return {p: (ROOT / p).read_bytes() for p in dict.fromkeys((*plan.tools.FILES, *plan.EXTRA))}


class ManifestTests(unittest.TestCase):
    def test_exact_shared_fixture_and_no_activation_artifacts(self):
        files = plan.render(plan.manifest(raw()))
        self.assertEqual(files, {
            "candidate/etc/nib/network.json": FIXTURE["network_config"].encode(),
            "candidate/etc/systemd/network/10-nib-nras.network": FIXTURE["networkd"].encode(),
            "candidate/etc/hosts": FIXTURE["hosts"].encode(),
        })
        self.assertNotIn("0.0.0.0/0", FIXTURE["networkd"])
        self.assertEqual((ROOT / "guest/network/network.json").read_bytes(),
                         b'{"schema":"nib.guest-network/v1","egress":[]}\n')

    def test_closed_manifest_rejects_injections_types_and_unsafe_addresses(self):
        mutations = {
            "schema": ["other", None],
            "interface": ["lo", "en*", "!eth0", "eth0 eth1", "a" * 16, "eth0\nDHCP=yes", None],
            "mac": ["ff:ff:ff:ff:ff:ff", "00:00:00:00:00:00", "01:00:00:00:00:01", "AA:00:00:00:00:02"],
            "address": ["192.0.2.10", "192.0.2.0/24", "192.0.2.255/24", "192.0.2.10/0", "::1/64"],
            "gateway": ["192.0.2.10", "192.0.2.255", "198.51.100.1"],
            "gateway_on_link": [True, 0, 1, None, "false"],
            "nras_ipv4": ["192.0.2.12", "192.0.2.1", "127.0.0.1", "0.1.2.3", "0.0.0.0",
                          "169.254.169.254", "224.0.0.1", "255.255.255.255", "240.0.0.1",
                          "::ffff:198.51.100.10", "198.51.100.10/32", "198.051.100.10",
                          "example.invalid", "198.51.100.10\nother.example", ["198.51.100.10"]],
        }
        for key, values in mutations.items():
            for value in values:
                bad = {**FIXTURE["manifest"], key: value}
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    plan.manifest(raw(bad))
        for key in FIXTURE["manifest"]:
            bad = dict(FIXTURE["manifest"])
            del bad[key]
            with self.subTest(missing=key), self.assertRaises(ValueError): plan.manifest(raw(bad))
        for key in ("url", "dns", "mtu", "device", "memory_max", "port", "hostname"):
            with self.subTest(extra=key), self.assertRaises(ValueError):
                plan.manifest(raw({**FIXTURE["manifest"], key: "private-canary"}))

    def test_explicit_off_subnet_gateway_is_not_a_reachability_claim(self):
        value = {**FIXTURE["manifest"], "address": "192.0.2.10/32", "gateway_on_link": True}
        files = plan.render(plan.manifest(raw(value)))
        self.assertIn(b"GatewayOnLink=yes\n", files["candidate/etc/systemd/network/10-nib-nras.network"])
        with self.assertRaises(ValueError): plan.manifest(raw({**value, "gateway_on_link": False}))

    def test_duplicate_nonfinite_oversized_and_nested_json_rejected(self):
        for value in (raw().replace(b'{', b'{"schema":"duplicate",', 1), b'{"schema":NaN}',
                      b" " * 4097, b"[]", b"[" * 1100):
            with self.subTest(value=value[:20]), self.assertRaises((ValueError, RecursionError)):
                plan.manifest(value)


class PreparationTests(unittest.TestCase):
    def test_receipt_binds_original_bytes_source_and_no_success_promotion(self):
        inputs = sources()
        files, receipt = plan.prepare(raw(), "ab" * 20, inputs, {"synthetic": True})
        self.assertEqual(files["operator-manifest.json"], raw())
        self.assertEqual(receipt["manifest_sha256"], plan.base.digest(raw()))
        self.assertEqual(receipt["source_hashes"], {p: plan.base.digest(b) for p, b in inputs.items()})
        self.assertEqual(receipt["files"], {p: {"bytes": len(b), "sha256": plan.base.digest(b)}
                                          for p, b in files.items() if p != "receipt.json"})
        self.assertFalse(any(v for v in receipt.values() if type(v) is bool))
        self.assertEqual(receipt["scope"], "offline-private-plan-only")
        self.assertNotIn(b"192.0.2.", files["receipt.json"])
        self.assertNotIn(b"198.51.100.", files["receipt.json"])
        changed = plan.prepare(raw() + b"\n", "ab" * 20, inputs, {})[1]
        self.assertNotEqual(receipt["manifest_sha256"], changed["manifest_sha256"])

    def test_primary_and_package_pins_fail_closed(self):
        inputs = sources()
        def read(path, limit):
            return b"synthetic pinned primary " + path.name.encode()
        fixture = {name: (len(read(Path(name), 0)), plan.base.digest(read(Path(name), 0))) for name in plan.PRIMARY}
        fixture_sources = dict(inputs)
        lock = json.loads(inputs[plan.EXTRA[2]])
        row = next(r for r in lock["packages"] if r["Package"] == "systemd")
        row["Size"], row["SHA256"] = str(fixture["systemd.deb"][0]), fixture["systemd.deb"][1]
        fixture_sources[plan.EXTRA[2]] = plan.base.encoded(lock)
        # Synthetic source bytes exercise every length/hash branch; the separate
        # real CLI audit uses the unmodified pins and downloaded public inputs.
        with mock.patch.object(plan, "PRIMARY", fixture):
            with mock.patch.object(plan.base, "read", side_effect=read):
                self.assertEqual(set(plan.primary_sources(Path("/primary"), fixture_sources)), set(fixture))
            for name in fixture:
                for kind in ("length", "hash"):
                    def corrupt(path, limit):
                        data = read(path, limit)
                        if path.name == name:
                            return data + b"x" if kind == "length" else b"x" * len(data)
                        return data
                    with self.subTest(name=name, kind=kind), \
                            mock.patch.object(plan.base, "read", side_effect=corrupt), self.assertRaises(ValueError):
                        plan.primary_sources(Path("/primary"), fixture_sources)
        for key, value in (("Version", "255.5"), ("SHA256", "ab" * 32), ("Size", "12")):
            changed = dict(inputs)
            lock = json.loads(changed[plan.EXTRA[2]])
            next(r for r in lock["packages"] if r["Package"] == "systemd")[key] = value
            changed[plan.EXTRA[2]] = plan.base.encoded(lock)
            with mock.patch.object(plan.base, "read") as read_mock, self.assertRaises(ValueError):
                plan.primary_sources(Path("/primary"), changed)
            read_mock.assert_not_called()

    def test_loaded_source_binding_rejects_each_bootstrap_mutation(self):
        for path in (plan.tools.FILES[0], plan.SELF, plan.EXTRA[1]):
            changed = sources()
            changed[path] += b"\n# changed\n"
            with self.subTest(path=path), mock.patch.object(plan.tools, "committed", return_value=("ab" * 20, changed)):
                with self.assertRaises(ValueError): plan.committed()


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.manifest = self.root / "operator.json"
        self.manifest.write_bytes(raw())
        self.manifest.chmod(0o600)
        self.primary = self.root / "sources"
        self.primary.mkdir()
        self.output = self.root / "out"

    def cli(self, mode="--output", output=None):
        with mock.patch.object(plan, "committed", return_value=("ab" * 20, sources())), \
                mock.patch.object(plan, "primary_sources", return_value={}), redirect_stdout(io.StringIO()) as out:
            plan.main(["--manifest", str(self.manifest), "--primary-sources", str(self.primary),
                       mode, str(output or self.output)])
        return out.getvalue()

    def test_full_cli_prepare_verify_private_modes_and_no_overwrite(self):
        before = self.manifest.read_bytes()
        text = self.cli()
        self.assertEqual(self.manifest.read_bytes(), before)
        self.assertNotIn(str(self.root), text)
        self.assertNotIn("198.51.100.10", text)
        self.assertFalse(json.loads(text)["network_activated"])
        self.assertEqual(self.cli("--verify"), text)
        for p in (self.output, *self.output.rglob("*")):
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o700 if p.is_dir() else 0o600)
        with self.assertRaises(FileExistsError): self.cli()
        self.assertEqual(self.cli("--verify"), text)

    def test_verify_rejects_mutation_extra_file_and_symlink(self):
        self.cli()
        target = self.output / "candidate/etc/hosts"
        original = target.read_bytes()
        target.write_bytes(original + b"127.0.0.1 malicious.invalid\n")
        with self.assertRaises(ValueError): self.cli("--verify")
        target.write_bytes(original)
        extra = self.output / "extra"
        extra.write_bytes(b"unexpected")
        with self.assertRaises(ValueError): self.cli("--verify")
        extra.unlink()
        target.unlink()
        target.symlink_to(self.manifest)
        with self.assertRaises((OSError, ValueError)): self.cli("--verify")

    def test_paths_privacy_overlap_and_hardlink_reject_before_publish(self):
        for output in (self.manifest, self.primary / "nested", self.root, ROOT / "build/plan", Path("relative")):
            with self.subTest(output=output), self.assertRaises(ValueError): self.cli(output=output)
        self.manifest.chmod(0o644)
        with self.assertRaises(ValueError): self.cli()
        self.manifest.chmod(0o600)
        os.link(self.manifest, self.root / "hardlink")
        with self.assertRaises(ValueError): self.cli()
        (self.root / "hardlink").unlink()
        link = self.root / "link"
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError): self.cli(output=link / "out")
        self.manifest.unlink()
        self.manifest.symlink_to(self.root / "missing")
        with self.assertRaises((OSError, ValueError)): self.cli()
        self.assertFalse(self.output.exists())

    def test_incomplete_publication_never_creates_success_receipt(self):
        original = plan.base.os.fsync
        calls = 0
        def fail(fd):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise OSError("private-canary")
            original(fd)
        with mock.patch.object(plan.base.os, "fsync", side_effect=fail), self.assertRaises(OSError): self.cli()
        self.assertTrue(self.output.exists())
        self.assertFalse((self.output / "receipt.json").exists())
        with self.assertRaises(FileExistsError): self.cli()

    def test_directory_aliases_reject_before_source_audit_or_publication(self):
        original = plan.base.directory
        alias = self.root / "alias"
        for target, output in ((ROOT, alias / "out"), (self.primary, alias / "out"),
                               (self.root, alias)):
            @contextmanager
            def directory(path):
                mapped = target / path.relative_to(alias) if path == alias or alias in path.parents else path
                with original(mapped) as fd:
                    yield fd
            with self.subTest(target=target, output=output), \
                    mock.patch.object(plan.base, "directory", side_effect=directory), \
                    mock.patch.object(plan, "committed") as audit, \
                    mock.patch.object(plan.base, "publish") as publish:
                with self.assertRaises(ValueError):
                    plan.main(["--manifest", str(self.manifest), "--primary-sources", str(self.primary),
                               "--output", str(output)])
                audit.assert_not_called()
                publish.assert_not_called()
        self.assertFalse(alias.exists())

    def test_real_case_alias_is_rejected_when_filesystem_resolves_it(self):
        alias = ROOT.with_name(ROOT.name.upper())
        if alias.exists() and os.path.samestat(ROOT.stat(), alias.stat()):
            with self.assertRaises(ValueError): self.cli(output=alias / "not-created")
            self.assertFalse((alias / "not-created").exists())
        else:
            with self.assertRaises(FileNotFoundError): self.cli(output=alias / "not-created")

    def test_real_cli_isolated_and_fixed_error_without_private_argument_echo(self):
        for flags in ([], ["-I"]):
            result = subprocess.run([sys.executable, *flags, str(ROOT / plan.SELF),
                                     "--private-canary=/private/hidden"], capture_output=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn(b"private-canary", result.stdout + result.stderr)
            self.assertNotIn(b"/private/hidden", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
