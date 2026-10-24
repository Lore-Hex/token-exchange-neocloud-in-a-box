# SPDX-License-Identifier: Apache-2.0
"""Portable bundle ownership must match the capability-free container UID."""

import os
import hashlib
import json
from pathlib import Path
import stat
import tempfile
import types
import unittest
from unittest import mock

from test_deepseek_lab import host
from test_evidence_history import producer


class PayloadOwnershipTests(unittest.TestCase):
    def test_original_failed_receipts_and_claim_limits(self):
        directory = Path(__file__).resolve().parents[1] / "evidence/p2/2026-10-24-provider-transfer-ownership-failure"
        expected = {
            "run.json": (1059, "f938c54c3f111d7e5b69c6612ad102c1ac76267d6add00faa2b73f91d773627f"),
            "output.log": (2122, "adba22c4fbd4d178ab4c61f13b43d7654289dee8226e4aeee8a0c4fe10305a0a"),
        }
        self.assertEqual({p.name for p in directory.iterdir()}, set(expected))
        for name, binding in expected.items():
            raw = (directory / name).read_bytes()
            self.assertEqual((len(raw), hashlib.sha256(raw).hexdigest()), binding)
        run = json.loads((directory / "run.json").read_bytes())
        self.assertEqual(producer((directory / "run.json").read_bytes()),
                         "68ab473fda5b29bf85be07099494702b63aeba69")
        self.assertEqual((run["result"], run["execution"], run["host"]["gpus"]),
                         ("fail", "software", "unobserved"))
        row = json.loads((directory / "output.log").read_bytes())
        self.assertEqual((row["stage"], row["experiment_step"], row["cleanup"]),
                         ("experiment", "attach", "confirmed"))
        self.assertEqual(row["container_observation"], {"status": "exited", "exit_code": 2, "oom_killed": False})
        self.assertIsNone(row["provider_conformance"])
        for flag in ("deployable", "confidential", "functional_pass"):
            self.assertIs(row[flag], False)

    def test_private_root_owned_payload_and_ownership_mode_mutations(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp)
            root = bundle / "payload"
            root.mkdir(mode=0o700)
            for name in ("launch.py", "python", "nib"):
                (root / name).write_bytes(b"fixture")
                (root / name).chmod(0o500 if name in ("python", "nib") else 0o600)
            real_stat = Path.lstat

            def observed(path, *args, **kwargs):
                row = real_stat(path, *args, **kwargs)
                return types.SimpleNamespace(st_uid=0, st_mode=row.st_mode, st_nlink=row.st_nlink)

            with mock.patch.object(Path, "lstat", observed):
                host.validate_runtime_payload(bundle)
            for name, field, value in (
                    ("launch.py", "st_uid", 501), ("", "st_uid", 501),
                    ("launch.py", "st_mode", stat.S_IFREG | 0o200),
                    ("launch.py", "st_mode", stat.S_IFREG | 0o622),
                    ("python", "st_mode", stat.S_IFREG | 0o400),
                    ("nib", "st_mode", stat.S_IFREG | 0o400),
                    ("", "st_mode", stat.S_IFDIR | 0o400),
                    ("launch.py", "st_nlink", 2)):
                def changed(path, *args, **kwargs):
                    row = observed(path, *args, **kwargs)
                    if path == root / name:
                        setattr(row, field, value)
                    return row
                with self.subTest(name=name, field=field, value=value), \
                        mock.patch.object(Path, "lstat", changed), self.assertRaises(ValueError):
                    host.validate_runtime_payload(bundle)

    def test_symlinks_and_special_files_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp)
            root = bundle / "payload"
            root.mkdir(mode=0o700)
            target = bundle / "outside"
            target.mkdir()
            real_stat = Path.lstat

            def observed(path, *args, **kwargs):
                row = real_stat(path, *args, **kwargs)
                return types.SimpleNamespace(st_uid=0, st_mode=row.st_mode, st_nlink=row.st_nlink)

            for kind in ("symlink", "fifo"):
                entry = root / kind
                if kind == "symlink":
                    entry.symlink_to(target, target_is_directory=True)
                else:
                    os.mkfifo(entry, 0o600)
                with self.subTest(kind=kind), mock.patch.object(Path, "lstat", observed), self.assertRaises(ValueError):
                    host.validate_runtime_payload(bundle)
                entry.unlink()

    def test_runtime_validation_precedes_any_container_or_watchdog(self):
        import ast
        tree = ast.parse(Path(host.__file__).read_text())
        main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
        calls = {node.func.id: node.lineno for node in ast.walk(main)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                 and node.func.id in ("bundle_inputs", "validate_runtime_payload", "run_container")}
        self.assertLess(calls["bundle_inputs"], calls["validate_runtime_payload"])
        self.assertLess(calls["validate_runtime_payload"], calls["run_container"])


if __name__ == "__main__":
    unittest.main()
