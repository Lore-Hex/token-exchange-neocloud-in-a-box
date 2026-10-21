# SPDX-License-Identifier: Apache-2.0
"""Synthetic hook archives only. No package script is executed."""
import copy
import io
import json
from pathlib import Path
import runpy
import tarfile
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
review = types.SimpleNamespace(**runpy.run_path(str(ROOT / "scripts/review_guest_hooks.py")))
fixtures = types.SimpleNamespace(**runpy.run_path(str(ROOT / "scripts/test_prepare_guest_packages.py")))
base = review.base


def deb(entries=None):
    entries = entries if entries is not None else [("etc/kernel/postinst.d/synthetic", tarfile.REGTYPE, 0o755)]
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, kind, mode in entries:
            member = tarfile.TarInfo(name)
            member.type, member.mode, member.linkname = kind, mode, "../PRIVATE" if kind == tarfile.SYMTYPE else ""
            raw = b"exit 99 # never execute\n"
            member.size = len(raw) if member.isfile() else 0
            archive.addfile(member, io.BytesIO(raw) if member.isfile() else None)
    name, raw = base.control_member(fixtures.deb())
    return (b"!<arch>\n" + fixtures.ar_member("debian-binary", b"2.0\n")
            + fixtures.ar_member(name, raw) + fixtures.ar_member("data.tar", buf.getvalue()))


def policy(lock_raw, rows):
    return {"schema": "nib.guest-hook-review-policy/v1", "scope": "fresh-directory-installation-source-review",
            "guest_lock_sha256": base.digest(lock_raw), "inventory_sha256": base.digest(base.encoded(rows)),
            "package_count": len(rows), "control_members": sum(len(r["control_hooks"]) for r in rows),
            "data_hooks": sum(len(r["data_hooks"]) for r in rows), "installation_authorized": False}


class HookReviewTests(unittest.TestCase):
    def inventory(self, raw=None, *, mutate=None):
        raw = deb() if raw is None else raw
        lock = {"packages": [fixtures.row(raw)], "archive_bytes": len(raw)}
        if mutate:
            mutate(lock)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / (base.digest(raw) + ".deb")).write_bytes(raw)
            with mock.patch.object(base.processes, "execute_process_group", side_effect=AssertionError("must not execute")):
                return base.encoded(lock), review.inventory(lock, root)

    def test_all_packages_and_control_hooks_are_bound_without_execution(self):
        lock, rows = self.inventory()
        self.assertEqual(rows[0]["control_hooks"], {"postinst": {"bytes": 8, "sha256": base.digest(b"exit 99\n")}})
        self.assertEqual(list(rows[0]["data_hooks"]), ["etc/kernel/postinst.d/synthetic"])
        result = review.validate(policy(lock, rows), lock, rows)
        self.assertTrue(result["source_review_matches"])
        for name in ("installation_authorized", "packages_executed", "image_built", "deployable"):
            self.assertFalse(result[name])
        self.assertNotIn("exit 99", json.dumps(result))

    def test_changed_lock_inventory_counts_or_authorization_reject(self):
        lock, rows = self.inventory()
        good = policy(lock, rows)
        for name, value in (("scope", "all-installs"), ("schema", "unknown"),
                            ("guest_lock_sha256", "0" * 64), ("inventory_sha256", "0" * 64),
                            ("package_count", 2), ("control_members", 0), ("data_hooks", True),
                            ("installation_authorized", True)):
            bad = {**good, name: value}
            with self.subTest(name=name), self.assertRaises(ValueError):
                review.validate(bad, lock, rows)
        changed = copy.deepcopy(rows)
        changed[0]["data_hooks"]["new-hook"] = {"bytes": 1, "sha256": "a" * 64, "mode": 0o755}
        with self.assertRaises(ValueError):
            review.validate(good, lock, changed)
        with self.assertRaises(ValueError):
            review.validate(good, lock + b" ", rows)

    def test_hook_links_duplicates_traversal_and_privileged_modes_reject(self):
        valid = ("etc/kernel/postinst.d/synthetic", tarfile.REGTYPE, 0o755)
        for entries in ((valid, valid), ((valid[0], tarfile.SYMTYPE, 0o777),),
                        ((valid[0], tarfile.FIFOTYPE, 0o755),), ((valid[0], tarfile.REGTYPE, 0o4755),),
                        (("/etc/kernel/postinst.d/absolute", tarfile.REGTYPE, 0o755),),
                        (("etc/kernel/../outside", tarfile.REGTYPE, 0o755),),
                        (("././etc/kernel/alias", tarfile.REGTYPE, 0o755),)):
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                review.data_hooks(deb(entries))

    def test_unrelated_payload_and_packages_without_hooks_remain_in_inventory(self):
        lock, rows = self.inventory(deb([("usr/share/doc/example", tarfile.REGTYPE, 0o644)]))
        self.assertEqual(rows[0]["data_hooks"], {})
        self.assertEqual(review.validate(policy(lock, rows), lock, rows)["package_count"], 1)

    def test_bad_archive_size_hash_total_and_duplicate_packages_reject(self):
        for mutate in (lambda p: p["packages"][0].update(Size="1"),
                       lambda p: p["packages"][0].update(SHA256="0" * 64),
                       lambda p: p.update(archive_bytes=1),
                       lambda p: p["packages"].append(p["packages"][0])):
            with self.assertRaises((ValueError, OSError)):
                self.inventory(mutate=mutate)

    def test_overall_scan_deadline_is_not_extended_per_archive(self):
        with mock.patch.object(review.time, "monotonic", side_effect=[0, 601]):
            with self.assertRaises(ValueError):
                self.inventory()

    def test_policy_is_bound_to_current_lock_and_never_authorizes_installation(self):
        value = json.loads((ROOT / review.POLICY).read_bytes())
        self.assertEqual(value["guest_lock_sha256"], base.digest((ROOT / review.LOCK).read_bytes()))
        self.assertEqual((value["package_count"], value["control_members"], value["data_hooks"]), (145, 180, 11))
        self.assertFalse(value["installation_authorized"])


if __name__ == "__main__":
    unittest.main()
