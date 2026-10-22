# SPDX-License-Identifier: Apache-2.0
"""Offline graph, parser and filesystem tests; no package execution."""

import copy
import importlib.util
import json
import lzma
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location(
    "guest_package_inputs", Path(__file__).with_name("guest_package_inputs.py"))
G = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(G)


def row(name, **changes):
    result = dict.fromkeys(G.FIELDS, "")
    result.update(Package=name, Version="1.0-1", Architecture="amd64",
                  Filename=f"pool/{name}_1.0-1_amd64.deb", Size="100",
                  SHA256="a" * 64)
    result.update(changes)
    return result


def encoded(rows):
    return ("\n\n".join("\n".join(f"{k}: {v}" for k, v in r.items() if v)
                       for r in rows) + "\n\n").encode()


class InputsTest(unittest.TestCase):
    def test_versions(self):
        ordered = ["1.0~~", "1.0~rc1", "1.0", "1.0-0.1", "1.0-1",
                   "1.0-1+b1", "1.0a", "1.0+", "1.9", "1.10", "1:0"]
        for i, a in enumerate(ordered):
            for j, b in enumerate(ordered):
                self.assertEqual(G.compare(a, b), (i > j) - (i < j), (a, b))
        for a, b in [("1", "1-0"), ("1.01", "1.1"), ("0:1", "1"),
                     ("1:3.4.3-1ubuntu1", "1:3.04.3-01ubuntu1")]:
            self.assertEqual(G.compare(a, b), 0)
        for value in ("bad", "1_0", "1-", ":1", "1::2", "1\n", "1/2", "1:"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                G.version(value)

    def test_relationships_fail_closed(self):
        for text in ("foo [amd64]", "foo <!nocheck>", "foo (>=1)", "foo |",
                     "foo (=> 2)", "foo:arm64", "foo,"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                G.relationships(text)
        with self.assertRaises(ValueError):
            G.relationships("foo (>= 1)", provides=True)
        with self.assertRaises(ValueError):
            G.relationships("foo | bar", provides=True)
        with self.assertRaises(ValueError):
            G.relationships("foo | bar", alternatives=False)
        for oversized in (",".join(["foo"] * 257), "|".join(["foo"] * 9)):
            with self.assertRaises(ValueError):
                G.relationships(oversized)

    def test_graph_alternatives_epoch_and_versioned_provides(self):
        rows = [row("app", Depends="missing | virt (>= 2), dkms (>= 3.1.8)",
                    **{"Pre-Depends": "base"}),
                row("impl", Provides="virt (= 2)", Conflicts="virt"),
                row("base", Essential="yes"),
                row("dkms", Version="1:3.4.3-1ubuntu1")]
        self.assertEqual(G.check_graph(rows, ["app"], ["base"]), 400)
        for mutate in ("missing", "old", "unversioned", "conflict", "breaks", "extra"):
            bad = copy.deepcopy(rows)
            if mutate == "missing":
                bad.pop()
            elif mutate == "old":
                bad[1]["Provides"] = "virt (= 1)"
            elif mutate == "unversioned":
                bad[1]["Provides"] = "virt"
            elif mutate == "conflict":
                bad[3]["Conflicts"] = "virt"
            elif mutate == "breaks":
                bad[3]["Breaks"] = "impl (<< 2)"
            else:
                bad.append(row("unreachable"))
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                G.check_graph(bad, ["app"], ["base"])

    def test_architecture_and_duplicates(self):
        a = row("app", Depends="perl:any")
        b = row("perl", **{"Multi-Arch": "allowed"})
        self.assertEqual(G.check_graph([a, b], ["app"], []), 200)
        for change in ({"Multi-Arch": ""}, {"Multi-Arch": "foreign"},
                       {"Architecture": "arm64"}):
            bad = dict(b, **change)
            with self.assertRaises(ValueError):
                G.check_graph([a, bad], ["app"], [])
        with self.assertRaises(ValueError):
            G.check_graph([a, b, b], ["app"], [])

    def test_deb822_duplicate_and_malformed_fields(self):
        self.assertEqual(list(G.paragraphs(encoded([row("pkg")]))), [row("pkg")])
        for data in (b"Package: foo\npackage: bar\n",
                     b"Package: foo\nnot a header\n",
                     b"Package: foo\r\n", b"Package: foo\0\n",
                     b"Bad_Field: value\n"):
            with self.subTest(data=data), self.assertRaises(ValueError):
                list(G.paragraphs(data))

    def test_xz_concatenation_padding_and_bounds(self):
        first, second = lzma.compress(b"abc"), lzma.compress(b"def")
        self.assertEqual(G.index_bytes(first + second + b"\0" * 32, "xz"), b"abcdef")
        self.assertEqual(G.index_bytes(first + lzma.compress(b""), "xz"), b"abc")
        for data in (first[:-1], first + b"\0", first + b"private",
                     first * 5, first + b"\0" * 4100):
            with self.subTest(size=len(data)), self.assertRaises((ValueError, lzma.LZMAError)):
                G.index_bytes(data, "xz")
        with mock.patch.object(G, "LIMIT", 1024), self.assertRaises(lzma.LZMAError):
            G.index_bytes(first, "xz")

    def test_regular_input_rejects_special_links_and_size(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            source.write_bytes(b"abc")
            self.assertEqual(G.regular(source, 3), b"abc")
            with self.assertRaises(ValueError):
                G.regular(source, 2)
            link = root / "link"
            link.symlink_to(source)
            with self.assertRaises(OSError):
                G.regular(link, 10)
            os.link(source, root / "hardlink")
            with self.assertRaises(ValueError):
                G.regular(source, 10)
            fifo = root / "fifo"
            os.mkfifo(fifo)
            with self.assertRaises(ValueError):
                G.regular(fifo, 10)
            with self.assertRaises(ValueError):
                G.regular(root, 10)

    def test_regular_input_rejects_changed_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "source"
            path.write_bytes(b"abc")
            before = path.stat()
            names = ("st_dev", "st_ino", "st_mode", "st_nlink", "st_size",
                     "st_mtime_ns", "st_ctime_ns")
            for changed in names:
                values = {name: getattr(before, name) for name in names}
                values[changed] += 1
                after = SimpleNamespace(**values)
                with self.subTest(changed=changed), mock.patch.object(
                        G.os, "fstat", side_effect=[before, after]), self.assertRaises(ValueError):
                    G.regular(path, 10)

    def fixture(self, root):
        rows = [row("app", Depends="base"), row("base", Essential="yes")]
        raw = encoded(rows)
        (root / "test.packages").write_bytes(raw)
        return {"format": "nib-package-inputs/v1",
                "scope": "kernel-driver-build-dependency-coverage",
                "deployable": False, "installable_verified": False,
                "indexes": [{"id": "test", "file": "test.packages",
                             "encoding": "plain", "bytes": len(raw),
                             "sha256": G.digest(raw)}],
                "packages": [dict(r, index="test") for r in rows],
                "roots": ["app"], "essential": ["base"], "archive_bytes": 200}

    def test_full_offline_check_and_tamper(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lock = self.fixture(root)
            result = G.check(lock, root)
            self.assertEqual(result["packages"], 2)
            self.assertFalse(result["installable_verified"])
            self.assertFalse(result["deployable"])
            self.assertFalse(result["hardware_validated"])
            cases = []
            for key, value in (("deployable", True), ("archive_bytes", 201),
                               ("essential", []), ("roots", ["nonexistent"])):
                bad = copy.deepcopy(lock)
                bad[key] = value
                cases.append(bad)
            bad = copy.deepcopy(lock)
            bad["packages"][0]["SHA256"] = "b" * 64
            cases.append(bad)
            bad = copy.deepcopy(lock)
            bad["indexes"][0]["file"] = "../private"
            cases.append(bad)
            for bad in cases:
                with self.assertRaises(ValueError):
                    G.check(bad, root)
            (root / "test.packages").write_bytes(b"private")
            with self.assertRaises(ValueError):
                G.check(lock, root)

    def test_committed_lock_graph(self):
        lock = json.loads(G.LOCK.read_bytes())
        self.assertEqual(len(lock["packages"]), 145)
        self.assertEqual(G.check_graph(lock["packages"], lock["roots"], lock["essential"]),
                         lock["archive_bytes"])
        by_name = {r["Package"]: r for r in lock["packages"]}
        self.assertEqual(by_name["nvidia-headless-open"]["Version"], "595.91.07-1ubuntu1")
        self.assertEqual(by_name["gcc-13"]["Version"], "13.3.0-6ubuntu2~24.04.1")
        self.assertEqual(by_name["linux-image-6.8.0-147-generic"]["Version"],
                         "6.8.0-147.147")
        self.assertEqual(by_name["nvattest"]["Version"], "2.0.0.1790976895-1")

    def test_runtime_lock_is_additive_without_initrd_or_debug_services(self):
        base = json.loads(G.LOCK.read_bytes())
        lock = json.loads(G.LOCK.with_name("guest-runtime-ubuntu2404.json").read_bytes())
        self.assertEqual(lock["scope"], "guest-runtime-dependency-coverage")
        self.assertEqual(lock["indexes"], base["indexes"])
        self.assertFalse(lock["installable_verified"])
        self.assertFalse(lock["deployable"])
        rows = {r["Package"]: r for r in lock["packages"]}
        self.assertEqual(len(rows), len(lock["packages"]))
        self.assertEqual(G.check_graph(lock["packages"], lock["roots"], lock["essential"]),
                         lock["archive_bytes"])
        for row in base["packages"]:
            self.assertEqual(rows[row["Package"]], row)
        for name, version in {"systemd": "255.4-1ubuntu8.18", "udev": "255.4-1ubuntu8.18",
                              "podman": "4.9.3+ds1-1ubuntu0.2", "crun": "1.14.1-1",
                              "cryptsetup-bin": "2:2.7.0-1ubuntu4.2", "nftables": "1.0.9-1ubuntu0.1",
                              "nvidia-container-toolkit": "1.20.1-1"}.items():
            self.assertEqual(rows[name]["Version"], version)
        for name in ("initramfs-tools", "dracut", "openssh-server", "systemd-resolved",
                     "systemd-timesyncd"):
            self.assertNotIn(name, rows)

    def test_runtime_scope_does_not_relax_input_integrity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lock = self.fixture(root)
            lock["scope"] = "guest-runtime-dependency-coverage"
            with self.assertRaises(ValueError):
                G.check(lock, root)
            self.assertEqual(G.check(lock, root, scope=lock["scope"])["result"], "pass")
            lock["packages"][0]["SHA256"] = "b" * 64
            with self.assertRaises(ValueError):
                G.check(lock, root, scope=lock["scope"])


if __name__ == "__main__":
    unittest.main()
