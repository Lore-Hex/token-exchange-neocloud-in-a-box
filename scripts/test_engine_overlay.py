# SPDX-License-Identifier: Apache-2.0
"""Real filesystem functions with synthetic wheel trees; no upstream imports."""

import base64
from contextlib import contextmanager
import csv
import importlib.machinery as machinery
import io
import json
import os
from pathlib import Path
import py_compile
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent


def source_module(name, relative):
    path = ROOT / relative
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
    return module


prep = source_module("test_overlay_preparer", "scripts/prepare-engine-overlay.py")
overlay = prep.inspector


class OverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = prep.checker.working_bundle(ROOT)
        cls.extra = {name: (ROOT / name).read_bytes() for name in (prep.SCRIPT, prep.INSPECTOR, prep.PINS, prep.TEST)}
        cls.files, cls.receipt = prep.prepare_files("1" * 40, cls.bundle, cls.extra)
        cls.contract = json.loads(cls.files["payload/contract.json"])
        cls.stages = prep.checker.verify_stages(cls.bundle)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="nib-wheel-test-", dir=Path(tempfile.gettempdir()).resolve())
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)
        self.root = self.work / "site"
        self.root.mkdir()
        self.dist = "vllm-0.32.0.dist-info"
        for name, data in self.bundle.items():
            prefix = prep.checker.DIRECTORY + "/base/"
            if name.startswith(prefix):
                self.write(name.removeprefix(prefix), data)
        self.write("vllm/__init__.py", b"raise RuntimeError('UPSTREAM MUST NOT EXECUTE')\n")
        self.write("vllm/_version.py", b"__version__ = version = '0.32.0'\n")
        elf = bytearray(64)
        elf[:6], elf[16:20] = b"\x7fELF\x02\x01", b"\x03\x00\x3e\x00"
        self.write("vllm/_C.abi3.so", bytes(elf))
        for name, version in self.contract["dependency_pins"].items():
            self.write(f"{name.replace('-', '_')}-{version}.dist-info/METADATA",
                       f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n\n".encode())
        requires = "".join(f"Requires-Dist: {n}=={v}\n" for n, v in self.contract["dependency_pins"].items()
                           if n != "nvidia-nccl-cu13")
        self.write(self.dist + "/METADATA", ("Metadata-Version: 2.1\nName: vllm\nVersion: 0.32.0\n"
                                            "Requires-Python: >=3.11,<3.15\n" + requires + "\n").encode())
        self.write(self.dist + "/WHEEL", b"Wheel-Version: 1.0\nRoot-Is-Purelib: false\nTag: cp38-abi3-linux_x86_64\n\n")
        self.refresh_record()

    def write(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def refresh_record(self, extra=()):
        output = io.StringIO()
        writer = csv.writer(output)
        for p in sorted(self.root.rglob("*")):
            name = p.relative_to(self.root).as_posix()
            if p.is_file() and (name.startswith("vllm/") or name.startswith(self.dist + "/")) and not name.endswith("/RECORD"):
                data = p.read_bytes()
                hashed = "sha256=" + base64.urlsafe_b64encode(bytes.fromhex(overlay.digest(data))).decode().rstrip("=")
                writer.writerow((name, "" if name.endswith(".pyc") else hashed, "" if name.endswith(".pyc") else len(data)))
        writer.writerows(extra)
        writer.writerow((self.dist + "/RECORD", "", ""))
        self.write(self.dist + "/RECORD", output.getvalue().encode())

    def inspect(self, **kwargs):
        with overlay.directory(self.root) as fd:
            return overlay.inspect(overlay.Tree(fd), self.contract, **kwargs)

    def test_inspection_does_not_import_and_full_overlay_preserves_native(self):
        before = self.inspect()
        with overlay.directory(self.root) as fd:
            report = overlay.apply_overlay(overlay.Tree(fd), self.contract, self.stages, before)
        self.assertEqual(report["stages"], 5)
        self.assertTrue(report["native_inventory_unchanged"])
        self.assertFalse(report["abi_validated"])
        self.assertFalse(report["image_origin_verified"])
        for item in self.contract["files"]:
            self.assertEqual(overlay.digest((self.root / item["path"]).read_bytes()), item["sha256"][-1])
        with self.assertRaises(ValueError):
            self.inspect()  # Not accepted as the original wheel after modification.

    def test_record_path_escape_and_malformed_rows(self):
        original = (self.root / self.dist / "RECORD").read_bytes()
        for row in ("../outside,,", "/absolute,,", "vllm/../escape,,", "vllm\\escape,,",
                    "vllm//escape,,", "vllm/envs.py,,", "vllm/envs.py,sha512=x,1", "one,two"):
            with self.subTest(row=row):
                self.write(self.dist + "/RECORD", original + row.encode() + b"\n")
                with self.assertRaises(ValueError):
                    self.inspect()

    def test_missing_and_duplicate_record_entries(self):
        original = (self.root / self.dist / "RECORD").read_bytes()
        for data in (original.split(b"\n", 1)[1], original + original.splitlines()[0] + b"\n"):
            self.write(self.dist + "/RECORD", data)
            with self.assertRaises(ValueError):
                self.inspect()

    def test_source_and_native_tampering(self):
        for name in ("vllm/envs.py", "vllm/_C.abi3.so"):
            p = self.root / name
            original = p.read_bytes()
            p.write_bytes(original + b"changed")
            with self.assertRaises(ValueError):
                self.inspect()
            p.write_bytes(original)

    def test_changed_native_even_with_rewritten_record_rejected_after_inspection(self):
        before = self.inspect()
        p = self.root / "vllm/_C.abi3.so"
        p.write_bytes(p.read_bytes() + b"changed")
        self.refresh_record()
        with overlay.directory(self.root) as fd, self.assertRaises(ValueError):
            overlay.apply_overlay(overlay.Tree(fd), self.contract, self.stages, before)

    def test_native_change_during_application_is_failure(self):
        before = self.inspect()
        original = overlay.Tree.write
        changed = False
        def write(tree, name, data, mode):
            nonlocal changed
            original(tree, name, data, mode)
            if not changed:
                changed = True
                p = self.root / "vllm/_C.abi3.so"
                p.write_bytes(p.read_bytes() + b"changed")
        with overlay.directory(self.root) as fd, mock.patch.object(overlay.Tree, "write", write), self.assertRaises(ValueError):
            overlay.apply_overlay(overlay.Tree(fd), self.contract, self.stages, before)

    def test_inconsistent_source_even_when_record_matches(self):
        self.write("vllm/envs.py", b"forged source\n")
        self.refresh_record()
        with self.assertRaises(ValueError):
            self.inspect()

    def test_symlink_directory_file_hardlink_and_fifo_rejected(self):
        target = self.root / "vllm/envs.py"
        outside = self.work / "outside"
        target.rename(outside)
        for kind in ("symlink", "hardlink", "fifo"):
            with self.subTest(kind=kind):
                if kind == "symlink":
                    target.symlink_to(outside)
                elif kind == "hardlink":
                    os.link(outside, target)
                else:
                    os.mkfifo(target)
                with self.assertRaises((ValueError, OSError)):
                    self.inspect()
                target.unlink()
        outside.rename(target)
        alias = self.work / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError), overlay.directory(alias):
            pass

    def test_new_helper_and_bytecode_must_be_absent(self):
        for name in ("vllm/v1/conf_compute_utils.py", "vllm/v1/conf_compute_utils.pyc",
                     "vllm/v1/__pycache__/conf_compute_utils.cpython-312.pyc"):
            self.write(name, b"forged")
            self.refresh_record()
            with self.assertRaises(ValueError):
                self.inspect()
            (self.root / name).unlink()

    def test_all_patch_targets_reject_import_collisions_before_writes(self):
        before = self.inspect()
        native = (self.root / "vllm/_C.abi3.so").read_bytes()
        for item in self.contract["files"]:
            stem = item["path"][:-3]
            for suffix in ("/", "/__init__.py", "/__init__.pyc", "/__init__.abi3.so",
                           ".so", ".abi3.so", ".cpython-312-x86_64-linux-gnu.so", ".pyd", ".pyw"):
                with self.subTest(module=item["path"], suffix=suffix):
                    name = stem + suffix
                    top = self.root / (stem if suffix.startswith("/") else name)
                    if suffix == "/":
                        top.mkdir()
                    else:
                        self.write(name, native if suffix.endswith(".so") else
                                   b"raise RuntimeError('SHADOW MUST NOT EXECUTE')\n")
                    try:
                        self.refresh_record()  # The collision has valid RECORD metadata.
                        with self.assertRaisesRegex(ValueError, "^offline overlay validation failed$"):
                            self.inspect()
                        with overlay.directory(self.root) as fd, \
                                mock.patch.object(overlay.Tree, "write") as write, \
                                mock.patch.object(overlay.Tree, "unlink") as unlink:
                            with self.assertRaisesRegex(ValueError, "^offline overlay validation failed$"):
                                overlay.apply_overlay(overlay.Tree(fd), self.contract, self.stages, before)
                            write.assert_not_called()
                            unlink.assert_not_called()
                    finally:
                        shutil.rmtree(top) if top.is_dir() else top.unlink()
                        self.refresh_record()
        self.assertEqual(self.inspect(), before)

    def test_python_resolver_would_select_package_native_and_new_helper_shadows(self):
        loaders = ((machinery.ExtensionFileLoader, machinery.EXTENSION_SUFFIXES),
                   (machinery.SourceFileLoader, machinery.SOURCE_SUFFIXES),
                   (machinery.SourcelessFileLoader, machinery.BYTECODE_SUFFIXES))
        for name in ("vllm/utils/torch_utils/__init__.py", "vllm/utils/torch_utils.abi3.so",
                     "vllm/v1/conf_compute_utils/__init__.py"):
            with self.subTest(shadow=name):
                package = name.endswith("/__init__.py")
                module = Path(name).parent if package else Path(name).with_suffix("").with_suffix("")
                self.write(name, (self.root / "vllm/_C.abi3.so").read_bytes() if not package else
                           b"raise RuntimeError('SHADOW MUST NOT EXECUTE')\n")
                try:
                    self.refresh_record()
                    finder = machinery.FileFinder(str(self.root / module.parent), *loaders)
                    spec = finder.find_spec(module.name)  # Resolution only; never load/execute.
                    self.assertEqual(spec.origin, str(self.root / name))
                    with self.assertRaises(ValueError):
                        self.inspect()
                finally:
                    if package:
                        shutil.rmtree(self.root / module)
                    else:
                        (self.root / name).unlink()
                    self.refresh_record()

    def test_changed_module_bytecode_removed_unrelated_bytecode_retained(self):
        names = ["vllm/envs.pyc", "vllm/__pycache__/envs.cpython-312.pyc",
                 "vllm/__pycache__/envs.cpython-312.opt-1.pyc", "vllm/__pycache__/unrelated.cpython-312.pyc"]
        for name in names:
            self.write(name, b"malicious bytecode must never run")
        self.refresh_record()
        before = self.inspect()
        with overlay.directory(self.root) as fd:
            report = overlay.apply_overlay(overlay.Tree(fd), self.contract, self.stages, before)
        self.assertEqual(report["removed_bytecode_files"], 3)
        self.assertFalse(any((self.root / name).exists() for name in names[:-1]))
        self.assertTrue((self.root / names[-1]).exists())

    def test_dependency_missing_wrong_version_and_wheel_constraint_mismatch(self):
        target = self.root / "torch-2.13.0.dist-info/METADATA"
        original = target.read_bytes()
        target.unlink()
        with self.assertRaises(ValueError):
            self.inspect()
        target.write_bytes(original.replace(b"2.13.0", b"2.12.0"))
        with self.assertRaises(ValueError):
            self.inspect()
        target.write_bytes(original)
        meta = self.root / self.dist / "METADATA"
        meta.write_bytes(meta.read_bytes().replace(b"torch==2.13.0", b"torch>=2.13.0"))
        self.refresh_record()
        with self.assertRaises(ValueError):
            self.inspect()

    def test_duplicate_distribution_editable_and_hooks_rejected(self):
        for name, data in (("torch-9.dist-info/METADATA", b"Name: torch\nVersion: 9\n"),
                           (self.dist + "/direct_url.json", b'{"dir_info":{"editable":true}}'),
                           (self.dist + "/direct_url.json", b'{"dir_info":{"editable":true},"dir_info":{}}'),
                           ("unknown.pth", b"import dangerous\n"), ("vllm.egg-link", b"elsewhere")):
            self.write(name, data)
            self.refresh_record()
            with self.assertRaises(ValueError):
                self.inspect()
            (self.root / name).unlink()

    @contextmanager
    def startup_hook(self, root, name):
        path = root / name
        marker = self.work / "hook-executed"
        source = (f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
                  "raise RuntimeError('SYNTHETIC_PRIVATE_VALUE')\n")
        path.parent.mkdir(parents=True, exist_ok=True)
        if name.endswith("/"):
            path.mkdir()
        elif path.suffix in (".pyc", ".pyo"):
            temporary = self.work / "compile-hook.py"
            temporary.write_text(source)
            try:
                py_compile.compile(str(temporary), cfile=str(path), doraise=True)
            finally:
                temporary.unlink()
        else:
            path.write_text(source if path.suffix != ".pth" else
                            f"import pathlib; pathlib.Path({str(marker)!r}).touch()\n")
        try:
            yield
            self.assertFalse(marker.exists(), "startup hook must never execute")
        finally:
            top = root / name.split("/", 1)[0]
            shutil.rmtree(top) if top.is_dir() else top.unlink()

    @staticmethod
    def startup_hook_forms():
        return ["SYNTHETIC_PRIVATE_VALUE.pth"] + [
            name + suffix for name in ("sitecustomize", "usercustomize") for suffix in (
                ".py", ".pyc", ".pyo", ".cpython-312-x86_64-linux-gnu.so", "/",
                "/__init__.py", "/__init__.pyc", "/__pycache__/__init__.cpython-312.pyc")
        ] + [f"__pycache__/{name}.cpython-312{optimization}.pyc"
             for name in ("sitecustomize", "usercustomize") for optimization in ("", ".opt-1")]

    def test_selected_root_startup_hooks_rejected_without_execution(self):
        before = self.inspect()
        for name in self.startup_hook_forms():
            with self.subTest(name=name), self.startup_hook(self.root, name):
                with self.assertRaisesRegex(ValueError, "^offline overlay validation failed$"):
                    self.inspect()
        self.assertEqual(self.inspect(), before)

    def test_all_discovered_roots_reject_startup_hooks_without_execution(self):
        other = self.work / "other-site"
        other.mkdir()
        site_only = self.work / "site-only"
        site_only.mkdir()
        paths = {"purelib": str(self.root), "platlib": str(other), "scripts": str(self.work / "bin")}
        with mock.patch.object(overlay.platform, "system", return_value="Linux"), \
                mock.patch.object(overlay.platform, "machine", return_value="x86_64"), \
                mock.patch.object(overlay.sys, "version_info", (3, 12)), \
                mock.patch.object(overlay.sysconfig, "get_paths", return_value=paths), \
                mock.patch.object(overlay.site, "getsitepackages", return_value=[str(self.root), str(site_only)]):
            self.assertEqual(overlay.installed_root(), (str(self.root), paths["scripts"]))
            for root in (self.root, other, site_only):
                for name in self.startup_hook_forms():
                    with self.subTest(root=root.name, name=name), self.startup_hook(root, name):
                        with self.assertRaisesRegex(ValueError, "^offline overlay validation failed$"):
                            overlay.installed_root()
            self.assertEqual(overlay.installed_root(), (str(self.root), paths["scripts"]))

    def test_startup_hook_scan_is_bounded_and_does_not_follow_cache_links(self):
        other = self.work / "other-site"
        other.mkdir()
        (other / "ordinary.py").write_text("raise RuntimeError('must not execute')\n")
        (other / "__pycache__").symlink_to(self.root, target_is_directory=True)
        with overlay.directory(other) as fd, self.assertRaises(OSError):
            overlay.Tree(fd).check_startup_hooks()
        (other / "__pycache__").unlink()
        with overlay.directory(other) as fd:
            tree = overlay.Tree(fd)
            tree.check_startup_hooks()
            with mock.patch.object(overlay, "MAX_FILES", 0), self.assertRaises(ValueError):
                tree.check_startup_hooks()
            tree.deadline = 0
            with self.assertRaises(ValueError):
                tree.check_startup_hooks()

    def test_hook_names_within_ordinary_packages_are_not_global_startup_hooks(self):
        self.write("ordinary/sitecustomize.py", b"raise RuntimeError('must not execute')\n")
        self.write("not_usercustomize.py", b"raise RuntimeError('must not execute')\n")
        self.write("__pycache__/ordinary.cpython-312.pyc", b"not loaded\n")
        result = self.inspect()
        self.assertIn("ordinary/sitecustomize.py", result["files"])
        self.assertIn("not_usercustomize.py", result["files"])

    def test_wrong_wheel_python_version_and_platform(self):
        for name, data in ((self.dist + "/WHEEL", b"Wheel-Version: 1.0\nRoot-Is-Purelib: false\nTag: cp311-cp311-linux_aarch64\n"),
                           ("vllm/_version.py", b"__version__ = '0.31.0'\n")):
            original = (self.root / name).read_bytes()
            self.write(name, data)
            self.refresh_record()
            with self.assertRaises(ValueError):
                self.inspect()
            self.write(name, original)

    def test_invalid_stage_rejected_before_writes(self):
        before = self.inspect()
        stages = [dict(stage) for stage in self.stages]
        stages[-1]["vllm/envs.py"] += b"bad"
        with overlay.directory(self.root) as fd, self.assertRaises(ValueError):
            overlay.apply_overlay(overlay.Tree(fd), self.contract, stages, before)
        self.assertEqual(self.inspect(), before)

    def test_private_exclusive_context_and_tamper(self):
        destination = self.work / "context"
        prep.publish(destination, self.files)
        self.assertEqual(destination.stat().st_mode & 0o777, 0o700)
        self.assertEqual((destination / "receipt.json").stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            prep.publish(destination, self.files)
        with mock.patch.object(overlay, "__file__", str(destination / "payload/inspector.py")):
            contract, stages = overlay.load_context(destination / "payload", self.receipt["payload_sha256"])
            self.assertEqual(len(stages), 5)
            self.assertEqual(contract["git_sha"], "1" * 40)
            (destination / "payload/LICENSE").write_bytes(b"changed")
            with self.assertRaises(ValueError):
                overlay.load_context(destination / "payload", self.receipt["payload_sha256"])

    def test_forbidden_cli_overrides_never_echo_secrets(self):
        for script, flags in ((prep.SCRIPT, ["--output", str(self.work / "out"), "--env", "SYNTHETIC_PRIVATE_VALUE"]),
                              (prep.INSPECTOR, ["--context", str(self.work), "--payload-sha256", "f" * 64,
                                                "--site-root", "SYNTHETIC_PRIVATE_VALUE"])):
            result = subprocess.run([sys.executable, "-I", "-S", str(ROOT / script), *flags], capture_output=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn(b"SYNTHETIC_PRIVATE_VALUE", result.stdout + result.stderr)
        self.assertFalse((self.work / "out").exists())

    def test_no_import_build_launch_or_profile_activation_in_generated_context(self):
        plan = json.loads(self.files["launch-plan.json"])
        self.assertFalse(plan["executable"])
        self.assertEqual(plan["fixed_engine_environment"]["VLLM_ALLREDUCE_USE_FLASHINFER"], "0")
        self.assertIn("kv_offloading_size", plan["must_remain_absent"])
        dockerfile = self.files["Dockerfile"]
        self.assertIn(b"RUN --network=none python3 -I -S", dockerfile)
        self.assertIn(b"BLOCKED: no runtime launcher", dockerfile)
        self.assertNotIn(b"pip ", dockerfile)
        self.assertFalse(self.receipt["installed_wheel_inspected"])
        self.assertNotIn(str(self.work), json.dumps(self.receipt))

    def test_resource_limits_and_unknown_base(self):
        for patch in ({"MAX_FILES": 2}, {"MAX_BYTES": 4}, {"MAX_SMALL": 2}):
            with mock.patch.multiple(overlay, **patch), self.assertRaises(ValueError):
                self.inspect()
        contract = {**self.contract, "base_image": "unknown"}
        with overlay.directory(self.root) as fd, self.assertRaises(ValueError):
            overlay.inspect(overlay.Tree(fd), contract)

    def test_selected_root_ambiguous_shadowed_and_wrong_platform(self):
        other = self.work / "other-site"
        other.mkdir()
        paths = {"purelib": str(self.root), "platlib": str(other), "scripts": str(self.work / "bin")}
        with mock.patch.object(overlay.platform, "system", return_value="Linux"), \
                mock.patch.object(overlay.platform, "machine", return_value="x86_64"), \
                mock.patch.object(overlay.sys, "version_info", (3, 12)), \
                mock.patch.object(overlay.sysconfig, "get_paths", return_value=paths), \
                mock.patch.object(overlay.site, "getsitepackages", return_value=[str(self.root)]):
            self.assertEqual(overlay.installed_root(), (str(self.root), paths["scripts"]))
            (other / "vllm.py").write_text("raise RuntimeError('must not execute')")
            with self.assertRaises(ValueError):
                overlay.installed_root()
            (other / "vllm.py").unlink()
            (other / "vllm-0.32.0.dist-info").mkdir()
            with self.assertRaises(ValueError):
                overlay.installed_root()
        with mock.patch.object(overlay.platform, "system", return_value="not-Linux"), self.assertRaises(ValueError):
            overlay.installed_root()

    def test_populated_alternate_roots_rejected_regardless_of_import_order(self):
        other = self.work / "other-site"
        other.mkdir()
        paths = {"purelib": str(self.root), "platlib": str(other), "scripts": str(self.work / "bin")}
        with mock.patch.object(overlay.platform, "system", return_value="Linux"), \
                mock.patch.object(overlay.platform, "machine", return_value="x86_64"), \
                mock.patch.object(overlay.sys, "version_info", (3, 12)), \
                mock.patch.object(overlay.sysconfig, "get_paths", return_value=paths):
            for roots in ([str(other), str(self.root)], [str(self.root), str(other)]):
                with mock.patch.object(overlay.site, "getsitepackages", return_value=roots):
                    self.assertEqual(overlay.installed_root(), (str(self.root), paths["scripts"]))
                    for name in ("torch-2.12.0.dist-info/METADATA", "torch/__init__.py", "torch.pyc",
                                 "ordinary.txt", "empty-directory/"):
                        with self.subTest(roots=roots, name=name):
                            path = other / name
                            path.parent.mkdir(parents=True, exist_ok=True)
                            if name.endswith("/"):
                                path.mkdir()
                            else:
                                path.write_bytes(b"Name: torch\nVersion: 2.12.0\n")
                            try:
                                with self.assertRaisesRegex(ValueError, "^offline overlay validation failed$"):
                                    overlay.installed_root()
                            finally:
                                top = other / name.split("/", 1)[0]
                                shutil.rmtree(top) if top.is_dir() else top.unlink()
                    self.assertEqual(overlay.installed_root(), (str(self.root), paths["scripts"]))

    def test_alternate_torch_resolver_positive_control_is_rejected(self):
        self.write("torch/__init__.py", b"raise RuntimeError('PINNED TORCH MUST NOT EXECUTE')\n")
        other = self.work / "other-site"
        (other / "torch").mkdir(parents=True)
        (other / "torch/__init__.py").write_bytes(b"raise RuntimeError('SHADOW MUST NOT EXECUTE')\n")
        (other / "torch-2.12.0.dist-info").mkdir()
        (other / "torch-2.12.0.dist-info/METADATA").write_bytes(b"Name: torch\nVersion: 2.12.0\n")
        finder = machinery.FileFinder(str(other), (machinery.SourceFileLoader, machinery.SOURCE_SUFFIXES))
        self.assertEqual(finder.find_spec("torch").origin, str(other / "torch/__init__.py"))
        paths = {"purelib": str(self.root), "platlib": str(other), "scripts": str(self.work / "bin")}
        with mock.patch.object(overlay.platform, "system", return_value="Linux"), \
                mock.patch.object(overlay.platform, "machine", return_value="x86_64"), \
                mock.patch.object(overlay.sys, "version_info", (3, 12)), \
                mock.patch.object(overlay.sysconfig, "get_paths", return_value=paths), \
                mock.patch.object(overlay.site, "getsitepackages", return_value=[str(other), str(self.root)]), \
                self.assertRaisesRegex(ValueError, "^offline overlay validation failed$"):
            overlay.installed_root()

    def test_real_git_source_binding_ignores_untracked_and_rejects_bootstrap_change(self):
        repo = self.work / "repo"
        repo.mkdir()
        env = {**prep.checker.tool_env(), "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
               "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}
        def git(*args):
            return subprocess.run(["git", "-C", str(repo), *args], env=env, capture_output=True, check=True, timeout=10)
        git("init", "-q")
        for name, data in {**self.bundle, **self.extra}.items():
            path = repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        git("add", ".")
        git("commit", "-qm", "synthetic source fixture")
        revision, bundle, extra = prep.inputs(repo)
        self.assertEqual(len(revision), 40)
        self.assertEqual(extra, self.extra)
        (repo / "scripts/ignored.py").write_text("raise RuntimeError('must not execute')")
        (repo / prep.PINS).write_text("uncommitted forged pins")
        self.assertEqual(prep.inputs(repo), (revision, bundle, extra))
        with mock.patch.object(prep, "BOOTSTRAP", b"forged"), self.assertRaises(ValueError):
            prep.inputs(repo)
        with mock.patch.object(prep, "INSPECTOR_SOURCE", b"forged"), self.assertRaises(ValueError):
            prep.inputs(repo)

    def test_failed_publish_has_no_success_receipt_and_no_overwrite(self):
        destination = self.work / "failed"
        files = {"payload/first": b"first", "payload/second": b"second", "receipt.json": b"success"}
        real_open = prep.os.open
        def fail(path, *args, **kwargs):
            if path == "second":
                raise OSError("synthetic write failure")
            return real_open(path, *args, **kwargs)
        with mock.patch.object(prep.os, "open", side_effect=fail), self.assertRaises(OSError):
            prep.publish(destination, files)
        self.assertFalse((destination / "receipt.json").exists())
        with self.assertRaises(FileExistsError):
            prep.publish(destination, files)

    def test_forged_helper_source_is_rejected_before_execution(self):
        path = self.work / "helper.py"
        path.write_text("raise RuntimeError('SYNTHETIC_PRIVATE_VALUE')\n")
        with self.assertRaisesRegex(ValueError, "offline helper source differs"):
            prep.source_module("overlay_inspector", path)

    def test_source_reader_bounded_regular_only_and_no_ancestor_links(self):
        path = self.work / "source.py"
        path.write_bytes(b"pass\n")
        self.assertEqual(prep.read_source(path), b"pass\n")
        with mock.patch.object(prep, "SOURCE_LIMIT", 5):
            self.assertEqual(prep.read_source(path), b"pass\n")
        with mock.patch.object(prep, "SOURCE_LIMIT", 4), \
                self.assertRaisesRegex(ValueError, "^bounded regular source required$"):
            prep.read_source(path)
        alias = self.work / "alias"
        alias.symlink_to(self.work, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "^bounded regular source required$"):
            prep.read_source(alias / path.name)
        with self.assertRaisesRegex(ValueError, "^bounded regular source required$"):
            prep.read_source(Path(os.devnull))
        linked = self.work / "linked.py"
        os.link(path, linked)
        with self.assertRaisesRegex(ValueError, "^bounded regular source required$"):
            prep.read_source(path)

    def test_helpers_reject_fifo_special_links_and_oversize_before_compilation(self):
        for helper in ("check-engine-candidate.py", "engine_overlay.py"):
            for kind in ("fifo", "directory", "symlink", "hardlink", "oversize"):
                with self.subTest(helper=helper, kind=kind):
                    scripts = self.work / (helper + "-" + kind) / "scripts"
                    scripts.mkdir(parents=True)
                    for name in ("prepare-engine-overlay.py", "check-engine-candidate.py", "engine_overlay.py"):
                        (scripts / name).write_bytes((ROOT / "scripts" / name).read_bytes())
                    target = scripts / helper
                    target.unlink()
                    if kind == "fifo":
                        os.mkfifo(target)
                    elif kind == "directory":
                        target.mkdir()
                    elif kind == "symlink":
                        target.symlink_to(ROOT / "scripts" / helper)
                    elif kind == "hardlink":
                        original = scripts / "SYNTHETIC_PRIVATE_VALUE.py"
                        original.write_bytes((ROOT / "scripts" / helper).read_bytes())
                        os.link(original, target)
                    else:
                        with target.open("wb") as stream:
                            stream.truncate(prep.SOURCE_LIMIT + 1)
                    result = subprocess.run([sys.executable, "-I", "-S", str(scripts / "prepare-engine-overlay.py"),
                                             "--help"], capture_output=True, timeout=3)
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(result.stdout, b"")
                    self.assertEqual(result.stderr, b"Offline helper validation failed.\n")

    def test_self_bootstrap_rereads_are_bounded_in_preparer_and_candidate(self):
        fifo = self.work / "SYNTHETIC_PRIVATE_VALUE.py"
        os.mkfifo(fifo)
        # The interpreter's initial trusted script load is out of scope. Execute
        # known source bytes with a FIFO __file__ to test each internal reread.
        program = ("import pathlib, sys\n"
                   "data = pathlib.Path(sys.argv[1]).read_bytes()\n"
                   "namespace = {'__name__': '__main__', '__file__': sys.argv[2]}\n"
                   "exec(compile(data, '<bootstrap-fixture>', 'exec'), namespace)\n")
        for script, diagnostic in ((prep.SCRIPT, b"Offline helper validation failed.\n"),
                                   ("scripts/check-engine-candidate.py", b"Bounded regular candidate source required.\n")):
            with self.subTest(script=script):
                result = subprocess.run([sys.executable, "-I", "-S", "-c", program, str(ROOT / script), str(fifo)],
                                        capture_output=True, timeout=3)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, b"")
                self.assertEqual(result.stderr, diagnostic)

    def test_explicit_interpreter_derived_console_record_only(self):
        scripts = self.work / "bin"
        scripts.mkdir()
        (scripts / "vllm").write_bytes(b"#!/usr/bin/python3\n# synthetic console script\n")
        self.write(self.dist + "/entry_points.txt", b"[console_scripts]\nvllm = vllm.entrypoints.cli.main:main\n")
        data = (scripts / "vllm").read_bytes()
        encoded = "sha256=" + base64.urlsafe_b64encode(bytes.fromhex(overlay.digest(data))).decode().rstrip("=")
        self.refresh_record([("../bin/vllm", encoded, len(data))])
        with self.assertRaises(ValueError):
            self.inspect()
        with overlay.directory(scripts) as fd:
            console = ("../bin/vllm", overlay.Tree(fd))
            before = self.inspect(console=console)
            with overlay.directory(self.root) as root_fd:
                report = overlay.apply_overlay(overlay.Tree(root_fd), self.contract, self.stages, before, console=console)
            self.assertTrue(report["native_inventory_unchanged"])


if __name__ == "__main__":
    unittest.main()
