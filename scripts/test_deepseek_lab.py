# SPDX-License-Identifier: Apache-2.0
"""Software-only lab boundary tests. No engine, GPU, Docker or model execution."""

import ast
import copy
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import sysconfig
import tempfile
import time
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent


def module(path):
    result = types.ModuleType(Path(path).stem.replace("-", "_"))
    result.__file__ = str(ROOT / path)
    if path == "scripts/engine_lab.py":
        exec(compile((ROOT / "scripts/engine_exception.py").read_bytes(), "exception.py", "exec"), result.__dict__)
    exec(compile((ROOT / path).read_bytes(), result.__file__, "exec"), result.__dict__)
    return result


helpers = module("scripts/engine_python_bootstrap.py")
bootstrap = module("scripts/engine_lab_bootstrap.py")
lab = module("scripts/engine_lab.py")
lab.require = helpers.require
lab.os = os
lab.sys = sys
probe = module("scripts/engine_lab_probe.py")
host = module("host/record-deepseek-lab.py")
snapshot = module("scripts/e2e/snapshot.py")
lab.PROCESSES = snapshot

TEMPLATE = """
import sys
if sys.platform != "linux":
    import torch  # noqa: F401
from ctypes import cdll
cdll.LoadLibrary("__lib_path__")
"""
FAKE_VEC = ("class VecISA:\n    _avx_py_load = " + repr(TEMPLATE) + "\n").encode()

# Original synthetic source contracts, not executed upstream setuptools code.
FAKE_SETUPTOOLS = {
    "setuptools/__init__.py": '''
import os, sys
vendor = os.path.join(os.path.dirname(__file__), "_vendor")
sys.path.extend([] if vendor in sys.path else [vendor])
import _distutils_hack.override
import distutils.core
import lab_vendor
VALUE = distutils.core.VALUE + lab_vendor.VALUE
''',
    "_distutils_hack/__init__.py": '''
import sys
class DistutilsMetaFinder:
    def find_spec(self, fullname, path, target=None):
        import importlib, importlib.util, importlib.abc
        mod = importlib.import_module("setuptools._distutils")
        class Loader(importlib.abc.Loader):
            def create_module(self, spec):
                mod.__name__ = "distutils"
                return mod
            def exec_module(self, module): pass
        return importlib.util.spec_from_loader("distutils", Loader(), origin=mod.__file__)
DISTUTILS_FINDER = DistutilsMetaFinder()
def insert_shim(): sys.meta_path.insert(0, DISTUTILS_FINDER)
def _remove_shim(): sys.meta_path.remove(DISTUTILS_FINDER)
def add_shim(): insert_shim()
class shim:
    def __enter__(self): insert_shim()
    def __exit__(self, *args): _remove_shim()
def do_override():
    import importlib
    with shim(): importlib.import_module("distutils")
    importlib.import_module("distutils.core")
''',
    "_distutils_hack/override.py": '__import__("_distutils_hack").do_override()\n',
    "setuptools/_distutils/__init__.py": 'VALUE = "source"\n',
}

# Exact function excerpts from Apache-2.0 vLLM env_override.py at
# 5df63a7203cdb9328d824a27b682e77c7b9928be, Copyright contributors to vLLM.
# Only these two functions run against synthetic modules, never the engine.
UPSTREAM_PATTERN_FUNCTIONS = '''
def _apply_inductor_pattern_matcher_patch() -> None:
    """Allow custom ops and functionalization wrappers with unsupported dtypes."""
    from torch._inductor import pattern_matcher
    from torch._inductor.lowering import fallback_node_due_to_unsupported_type

    def fallback_for_builtin(node, allow_cpu_inputs=True):
        return (
            isinstance(node.target, torch._ops.OpOverload)
            and torch._library.utils.is_builtin(node.target)
            and fallback_node_due_to_unsupported_type(node, allow_cpu_inputs)
        )

    pattern_matcher.fallback_node_due_to_unsupported_type = fallback_for_builtin


def _patch_inductor_pattern_matcher() -> None:
    """Apply the backport when Inductor imports its pattern matcher."""
    import sys

    target_name = "torch._inductor.pattern_matcher"
    if target_name in sys.modules:
        _apply_inductor_pattern_matcher_patch()
        return

    import importlib.abc

    class _PatternMatcherPatchFinder(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path, target=None):
            if fullname != target_name:
                return None
            sys.meta_path.remove(self)
            spec = importlib.util.find_spec(fullname)
            if spec is None or spec.loader is None:
                return None
            original_exec = spec.loader.exec_module

            def _exec_then_patch(module):
                original_exec(module)
                _apply_inductor_pattern_matcher_patch()

            spec.loader.exec_module = _exec_then_patch  # type: ignore[method-assign]
            return spec

    sys.meta_path.insert(0, _PatternMatcherPatchFinder())
'''
FAKE_PATTERN_ENV = ("import torch\n" + UPSTREAM_PATTERN_FUNCTIONS
                    + '\ndef is_torch_equal_or_newer(value):\n    assert value == "2.16.0.dev"\n    return False\n'
                    + 'if not is_torch_equal_or_newer("2.16.0.dev"):\n    _patch_inductor_pattern_matcher()\n')
FAKE_PATTERN_TARGET = 'from .lowering import fallback_node_due_to_unsupported_type\nLOADED = True\n'


class VecTests(unittest.TestCase):
    def test_exact_template_only_not_generic_python(self):
        source = types.SimpleNamespace(require=helpers.require, read_source=mock.Mock(return_value=FAKE_VEC))
        layout = {"selected_root": "/selected", "paths": ["/selected", "/selected/setuptools/_vendor"]}
        with mock.patch.object(bootstrap, "VECISA_SOURCE_SHA", hashlib.sha256(FAKE_VEC).hexdigest()):
            path = "/cache/inductor/ab/candidate.so"
            self.assertEqual(bootstrap.vecisa_library(["-c", TEMPLATE.replace("__lib_path__", path)],
                                                      layout, source), path)
            source.read_source.assert_called_once_with("/selected/torch/_inductor/cpu_vec_isa.py", 32768)
            variants = [
                TEMPLATE.replace("__lib_path__", "/private/sentinel.so"),
                TEMPLATE.replace("__lib_path__", "/cache/inductor/../sentinel.so"),
                TEMPLATE.replace("__lib_path__", "/cache/inductor//sentinel.so"),
                TEMPLATE.replace("__lib_path__", path) + "\nprint('private')",
                TEMPLATE.replace("__lib_path__", path).replace("cdll.LoadLibrary", "exec"),
                TEMPLATE.replace("__lib_path__", "/cache/inductor/a';print('private').so"),
                "print('private')", "x" * 4097,
            ]
            for text in variants:
                with self.subTest(text=text[:20]), self.assertRaises((ValueError, SyntaxError)):
                    bootstrap.vecisa_library(["-c", text], layout, source)
        with self.assertRaises(ValueError):
            bootstrap.vecisa_library(["-c", TEMPLATE], layout, source)

    def test_selected_root_missing_or_inconsistent_never_reads(self):
        source = types.SimpleNamespace(require=helpers.require, read_source=mock.Mock(return_value=FAKE_VEC))
        base = {"selected_root": "/selected", "paths": ["/selected", "/selected/setuptools/_vendor"]}
        for changes in ({"selected_root": None}, {"selected_root": "/private/sentinel"},
                        {"selected_root": "/selected/setuptools/_vendor"}, {"selected_root": "relative"},
                        {"selected_root": "/selected/.."}, {"paths": ["/selected"]},
                        {"paths": ["/selected", "/unreviewed"]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                bootstrap.vecisa_library(["-c", TEMPLATE], {**base, **changes}, source)
        with self.assertRaises(ValueError):
            bootstrap.vecisa_library(["-c", TEMPLATE], {"paths": base["paths"]}, source)
        source.read_source.assert_not_called()

    def test_library_nofollow_regular_and_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            library = root / "generated.so"
            library.write_bytes(b"synthetic-not-a-real-library")
            with mock.patch.object(bootstrap.ctypes, "CDLL") as dlopen:
                bootstrap.vecisa_load(str(library), helpers)
                self.assertRegex(dlopen.call_args.args[0], r"^/proc/self/fd/[0-9]+$")
            for kind in ("symlink", "hardlink", "fifo", "empty", "large"):
                path = root / (kind + ".so")
                if kind == "symlink":
                    path.symlink_to(library)
                elif kind == "hardlink":
                    os.link(library, path)
                elif kind == "fifo":
                    os.mkfifo(path)
                elif kind == "empty":
                    path.touch()
                else:
                    with path.open("wb") as stream:
                        stream.truncate((64 << 20) + 1)
                with self.subTest(kind=kind), mock.patch.object(bootstrap.ctypes, "CDLL") as dlopen:
                    with self.assertRaises((ValueError, OSError)):
                        bootstrap.vecisa_load(str(path), helpers)
                    dlopen.assert_not_called()
                path.unlink()
            with mock.patch.object(bootstrap.ctypes, "CDLL", side_effect=lambda _: library.write_bytes(b"changed")):
                with self.assertRaises(ValueError):
                    bootstrap.vecisa_load(str(library), helpers)

    def test_wrapper_restores_fixed_loader_environment(self):
        plan = {"fixed_engine_environment": {"VLLM_PLUGINS": ""}}
        env = lab.lab_environment(plan)
        expected = "/usr/local/lib/python3.12/dist-packages/torch/lib:" + lab.IMAGE_LD_PATH
        self.assertEqual(env["LD_LIBRARY_PATH"], expected)
        tokens = shlex.split(lab.wrapper_bytes("/usr/bin/python3.12", env, "a" * 64).decode().splitlines()[1][5:])
        self.assertEqual(tokens[:2], ["/usr/bin/env", "-i"])
        self.assertIn("LD_LIBRARY_PATH=" + expected, tokens)
        self.assertEqual(tokens[tokens.index("/usr/bin/python3.12") + 1:-1],
                         ["-I", "-S", "-B", "/opt/nib/launch.py", "a" * 64])
        self.assertIn("-S", tokens)
        self.assertIn("-B", tokens)
        self.assertNotIn("TORCHINDUCTOR_VEC_ISA_OK", env)


DRIVER = r'''
import json
from pathlib import Path
LAYOUT = json.loads(Path(__file__).with_name("layout.json").read_text())
ARGS = sys.argv[1:]
STATE = install_lab(LAYOUT, sys.modules[__name__])
LAYOUT = STATE["layout"]
def observe():
    STATE["check"]()
    import fake_engine
    from importlib import metadata
    return {"isolated": bool(sys.flags.isolated), "no_site": bool(sys.flags.no_site),
            "no_bytecode": sys.dont_write_bytecode, "paths": sys.path == LAYOUT["paths"],
            "value": fake_engine.VALUE, "version": metadata.version("lab-fake"),
            "site": "sitecustomize" in sys.modules, "before_unpickle": True}
class Before:
    def __reduce__(self): return observe, ()
def child(connection, observation, depth):
    result = {"before": observation, "after": observe()}
    if depth:
        result["grandchild"] = spawn_one(depth - 1)
    connection.send(result)
    connection.close()
def spawn_one(depth):
    import multiprocessing as mp
    left, right = mp.Pipe()
    process = mp.Process(target=child, args=(right, Before(), depth))
    process.start()
    right.close()
    assert left.poll(15)
    result = left.recv()
    process.join(15)
    assert process.exitcode == 0
    return result
if __name__ == "__main__":
    if ARGS == ["positive"]:
        result = {"parent": observe(), "child": spawn_one(1)}
        import subprocess
        fresh = subprocess.run([LAYOUT["wrapper"], "fresh"], capture_output=True, check=True, timeout=20)
        result["fresh"] = json.loads(fresh.stdout)
        from multiprocessing import resource_tracker
        resource_tracker.ensure_running()
        resource_tracker._resource_tracker._stop()
        result["tracker"] = True
        print(json.dumps(result))
    elif ARGS == ["fresh"]:
        print(json.dumps(observe()))
    elif ARGS and ARGS[0] == "reject":
        import multiprocessing as mp
        from importlib import metadata
        try:
            if ARGS[1] == "fork": mp.get_context("fork")
            elif ARGS[1] == "forkserver": mp.get_context("forkserver")
            elif ARGS[1] == "executable": mp.set_executable("/private/sentinel")
            elif ARGS[1] == "metadata": list(metadata.distributions(path=["/private/sentinel"]))
            elif ARGS[1] == "finder": sys.meta_path.append(importlib.machinery.PathFinder); STATE["check"]()
            elif ARGS[1] == "path": sys.path.append("/private/sentinel"); STATE["check"]()
            elif ARGS[1] == "site": import site; site.main()
            elif ARGS[1] == "numa": os.environ["NUMACTL_PYTHON"] = "/private/sentinel"; STATE["check"]()
            elif ARGS[1] == "pth": import sitecustomize
            else: dispatch_lab(["-c", "print('private')"], STATE, sys.modules[__name__])
        except ValueError:
            print('{"rejected":true}')
        else: raise AssertionError("boundary accepted")
    else: dispatch_lab(ARGS, STATE, sys.modules[__name__])
'''


class BootstrapLayoutTests(unittest.TestCase):
    def test_symlinked_sysconfig_roots_are_canonical_before_sealed_launch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            actual = [str(Path(path).resolve(strict=True)) for path in
                      (sysconfig.get_path("stdlib"), sysconfig.get_config_var("DESTSHARED"))]
            aliases = [root / "stdlib-alias", root / "extensions-alias"]
            for alias, target in zip(aliases, actual):
                alias.symlink_to(target, target_is_directory=True)
            fixture = BootstrapTests("test_real_spawn_grandchild_fresh_tracker_before_unpickle")
            self.addCleanup(fixture.doCleanups)
            with mock.patch.object(sysconfig, "get_path", return_value=str(aliases[0])), \
                    mock.patch.object(sysconfig, "get_config_var", return_value=str(aliases[1])):
                fixture.setUp()
            self.assertEqual(fixture.layout["stdlib_roots"], actual)
            self.assertEqual(fixture.layout["paths"][:2], actual)
            fixture.test_real_spawn_grandchild_fresh_tracker_before_unpickle()

            # The fixture correction must not relax the runtime's canonical-root check.
            fixture.layout["stdlib_roots"] = [str(path) for path in aliases]
            fixture.layout["paths"][:2] = fixture.layout["stdlib_roots"]
            (fixture.root / "layout.json").write_text(json.dumps(fixture.layout))
            result = subprocess.run([fixture.layout["wrapper"], "fresh"], cwd="/",
                                    capture_output=True, timeout=45)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b"lab Python boundary rejected", result.stderr)


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.package = self.root / "packages"
        self.package.mkdir()
        (self.package / "fake_engine.py").write_text("VALUE='source-not-bytecode'\n")
        (self.package / "sitecustomize.py").write_text("raise AssertionError('must not run')\n")
        (self.package / "bad.pth").write_text("import sitecustomize\n")
        dist = self.package / "lab_fake-1.0.dist-info"
        dist.mkdir()
        (dist / "METADATA").write_text("Name: lab-fake\nVersion: 1.0\n")
        for name, text in FAKE_SETUPTOOLS.items():
            path = self.package / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        vendor = self.package / "setuptools/_vendor"
        vendor.mkdir()
        (vendor / "lab_vendor.py").write_text('VALUE="-vendor"\n')
        (self.package / "setuptools/_distutils/core.py").write_text('VALUE="distutils"\n')
        dist = self.package / "setuptools-80.10.2.dist-info"
        dist.mkdir()
        (dist / "METADATA").write_text("Name: setuptools\nVersion: 80.10.2\n")
        stdlib = [str(Path(path).resolve(strict=True)) for path in
                  (sysconfig.get_path("stdlib"), sysconfig.get_config_var("DESTSHARED"))]
        self.layout = {"paths": stdlib + [str(self.package)], "stdlib_roots": stdlib,
                       "launcher": str(self.root / "launch.py"), "wrapper": str(self.root / "python")}
        (self.root / "layout.json").write_text(json.dumps(self.layout))
        self.source = (ROOT / "scripts/engine_python_bootstrap.py").read_bytes() + b"\n" + (ROOT / "scripts/engine_lab_bootstrap.py").read_bytes()
        pins = {name: hashlib.sha256(text.encode()).hexdigest() for name, text in FAKE_SETUPTOOLS.items()}
        self.source += ("\nSETUPTOOLS_SOURCES=" + repr(pins) + "\n").encode()
        (self.root / "launch.py").write_bytes(self.source + DRIVER.encode())
        env = ["VLLM_PLUGINS=", "VLLM_WORKER_MULTIPROC_METHOD=spawn", "PATH=/usr/bin:/bin", "LC_ALL=C.UTF-8"]
        (self.root / "python").write_text("#!/bin/sh\nexec " + shlex.join(
            ["/usr/bin/env", "-i", *env, sys.executable, "-I", "-S", "-B", self.layout["launcher"]]) + ' "$@"\n')
        (self.root / "python").chmod(0o700)

    def run_fixture(self, *args):
        result = subprocess.run([self.layout["wrapper"], *args], cwd="/", capture_output=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stderr, b"")
        return json.loads(result.stdout)

    def test_real_spawn_grandchild_fresh_tracker_before_unpickle(self):
        row = self.run_fixture("positive")
        observations = [row["parent"], row["fresh"], row["child"]["before"], row["child"]["after"],
                        row["child"]["grandchild"]["before"], row["child"]["grandchild"]["after"]]
        for item in observations:
            self.assertEqual(item, {"isolated": True, "no_site": True, "no_bytecode": True, "paths": True,
                                    "value": "source-not-bytecode", "version": "1.0", "site": False, "before_unpickle": True})
        self.assertTrue(row["tracker"])
        self.assertFalse(list(self.root.rglob("*.pyc")))

    def test_fork_forkserver_paths_numa_and_generic_python_rejected(self):
        for variant in ("fork", "forkserver", "executable", "metadata", "finder", "path", "site", "numa", "pth", "python"):
            with self.subTest(variant=variant):
                self.assertEqual(self.run_fixture("reject", variant), {"rejected": True})

    def registry_fixture(self):
        path = self.package / "vllm/model_executor/models/registry.py"
        path.parent.mkdir(parents=True)
        for directory in (path.parent, path.parent.parent, path.parent.parent.parent):
            (directory / "__init__.py").touch()
        source = b'''import sys, json
def _run():
    state = sys._nib_lab_bootstrap
    state["check"]()
    # Represents upstream unpickling, after the sealed bootstrap is installed.
    print(json.dumps({"isolated": bool(sys.flags.isolated),
                      "no_site": bool(sys.flags.no_site),
                      "before_unpickle": sys.path == state["layout"]["paths"]}))
'''
        path.write_bytes(source)
        self.source += ("\nREGISTRY_SHA256=" + repr(hashlib.sha256(source).hexdigest()) + "\n").encode()
        (self.root / "launch.py").write_bytes(self.source + DRIVER.encode())
        return path

    def test_exact_registry_subprocess_reenters_before_unpickle(self):
        self.registry_fixture()
        self.assertEqual(self.run_fixture("-m", bootstrap.REGISTRY_MODULE),
                         {"isolated": True, "no_site": True, "before_unpickle": True})

    def test_registry_source_and_nonexact_module_routes_reject(self):
        path = self.registry_fixture()
        for args in (("-m", "os"), ("-m", bootstrap.REGISTRY_MODULE, "extra"),
                     ("-m", bootstrap.REGISTRY_MODULE + ".other"), ("-c", "import os")):
            result = subprocess.run([self.layout["wrapper"], *args], cwd="/", capture_output=True, timeout=30)
            self.assertNotEqual(result.returncode, 0)
        path.write_bytes(path.read_bytes() + b"\nraise AssertionError('unreviewed code ran')\n")
        result = subprocess.run([self.layout["wrapper"], "-m", bootstrap.REGISTRY_MODULE],
                                cwd="/", capture_output=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(b"unreviewed code ran", result.stderr)

    def test_timestamp_valid_stale_bytecode_ignored(self):
        import importlib.util
        import marshal
        import struct
        source = self.package / "fake_engine.py"
        cache = Path(importlib.util.cache_from_source(str(source)))
        cache.parent.mkdir()
        cache.write_bytes(importlib.util.MAGIC_NUMBER
                          + struct.pack("<III", 0, int(source.stat().st_mtime), source.stat().st_size)
                          + marshal.dumps(compile("raise RuntimeError('private bytecode ran')", str(source), "exec")))
        self.assertEqual(self.run_fixture("fresh")["value"], "source-not-bytecode")

    def test_preparation_tamper_rejected_before_object_unpickle(self):
        driver = DRIVER.replace('    if ARGS == ["positive"]:', '''    if ARGS == ["tamper"]:
        import multiprocessing.spawn as spawn
        data = spawn.get_preparation_data("safe")
        data["sys_path"] = ["/private/sentinel"]
        try: spawn.prepare(data)
        except ValueError: print('{"rejected":true}')
        else: raise AssertionError("preparation accepted")
    elif ARGS == ["positive"]:''')
        (self.root / "launch.py").write_bytes(self.source + driver.encode())
        self.assertEqual(self.run_fixture("tamper"), {"rejected": True})

    def test_backport_metadata_preserves_roots_parent_and_children(self):
        fixture = module("scripts/test_engine_python_bootstrap.py")
        backport = self.package / "importlib_metadata"
        backport.mkdir()
        (backport / "__init__.py").write_text(fixture.BACKPORT)
        (backport / "_compat.py").write_text(fixture.BACKPORT_COMPAT)
        dist = self.package / "importlib_metadata-8.7.1.dist-info"
        dist.mkdir()
        (dist / "METADATA").write_text("Name: importlib_metadata\nVersion: 8.7.1\n")
        pins = {name: hashlib.sha256((self.package / name).read_bytes()).hexdigest()
                for name in ("importlib_metadata/__init__.py", "importlib_metadata/_compat.py")}
        driver = DRIVER.replace("    import fake_engine", """    import fake_engine
    import importlib_metadata
    assert importlib_metadata.version("lab-fake") == "1.0"
    try: list(importlib_metadata.distributions(path=["/private/sentinel"]))
    except ValueError: pass
    else: raise AssertionError("outside metadata accepted")""")
        (self.root / "launch.py").write_bytes(self.source + ("\nMETADATA_SOURCES=" + repr(pins) + "\n").encode() + driver.encode())
        row = self.run_fixture("positive")
        self.assertEqual(row["child"]["grandchild"]["after"]["version"], "1.0")

    def test_setuptools_source_adapter_parent_spawn_grandchild_and_fresh(self):
        import importlib.util
        import marshal
        import struct
        for relative in FAKE_SETUPTOOLS:
            source = self.package / relative
            cache = Path(importlib.util.cache_from_source(str(source)))
            cache.parent.mkdir(exist_ok=True)
            cache.write_bytes(importlib.util.MAGIC_NUMBER
                              + struct.pack("<III", 0, int(source.stat().st_mtime), source.stat().st_size)
                              + marshal.dumps(compile("raise RuntimeError('private cached setuptools')", str(source), "exec")))
        driver = DRIVER.replace("    import fake_engine", """    import fake_engine
    before = list(sys.meta_path)
    import setuptools, _distutils_hack, distutils
    assert setuptools.VALUE == "distutils-vendor"
    assert sys.meta_path == before
    assert not STATE["distutils_active"]
    assert sys.path.count(LAYOUT["paths"][-1]) == 1
    with _distutils_hack.shim():
        STATE["check"]()
        assert sys.meta_path == before
    STATE["check"]()""")
        (self.root / "launch.py").write_bytes(self.source + driver.encode())
        result = self.run_fixture("positive")
        self.assertTrue(result["child"]["grandchild"]["before"]["paths"])
        self.assertTrue(result["fresh"]["paths"])

    def test_installed_layout_vecisa_handoff_parent_spawn_grandchild_and_fresh(self):
        source = self.package / "torch/_inductor/cpu_vec_isa.py"
        source.parent.mkdir(parents=True)
        source.write_bytes(FAKE_VEC)
        shadow = self.package / "setuptools/_vendor/torch/_inductor/cpu_vec_isa.py"
        shadow.parent.mkdir(parents=True)
        shadow.write_bytes(b"not the selected package source\n")
        path = "/cache/inductor/ab/synthetic.so"
        call = '    assert vecisa_library(["-c", ' + repr(TEMPLATE.replace("__lib_path__", path)) + \
               '], STATE["layout"], sys.modules[__name__]) == ' + repr(path)
        driver = DRIVER.replace("    import fake_engine", "    import fake_engine\n" + call +
                                '\n    assert STATE["layout"]["selected_root"] == LAYOUT["paths"][-2]')
        pin = "\nVECISA_SOURCE_SHA=" + repr(hashlib.sha256(FAKE_VEC).hexdigest()) + "\n"
        (self.root / "launch.py").write_bytes(self.source + pin.encode() + driver.encode())
        result = self.run_fixture("positive")
        self.assertTrue(result["parent"]["paths"])
        self.assertTrue(result["child"]["grandchild"]["before"]["paths"])
        self.assertTrue(result["fresh"]["paths"])

    def test_setuptools_unadapted_path_and_finder_are_rejected(self):
        # Positive controls reproduce both original strict-boundary failures.
        driver = DRIVER.replace("    import fake_engine", """    import fake_engine
    for field in ("path", "finder"):
        before = list(sys.path), list(sys.meta_path)
        if field == "path": sys.path.append(LAYOUT["paths"][-1])
        else:
            import _distutils_hack
            sys.meta_path.insert(0, _distutils_hack.DISTUTILS_FINDER)
        try: STATE["check"]()
        except ValueError: pass
        else: raise AssertionError("unadapted mutation accepted")
        sys.path[:], sys.meta_path[:] = before
    STATE["check"]()""")
        (self.root / "launch.py").write_bytes(self.source + driver.encode())
        self.assertTrue(self.run_fixture("fresh")["paths"])

    def test_setuptools_source_version_root_and_cached_module_rejections(self):
        def rejected():
            result = subprocess.run([self.layout["wrapper"], "fresh"], cwd="/", capture_output=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn(b"private-payload-executed", result.stdout + result.stderr)
        for name, original in FAKE_SETUPTOOLS.items():
            path = self.package / name
            path.write_text("raise RuntimeError('private-payload-executed')\n")
            with self.subTest(source=name):
                rejected()
            path.write_text(original)
        driver = DRIVER.replace("    import fake_engine", "    import fake_engine\n    import setuptools")
        (self.root / "launch.py").write_bytes(self.source + driver.encode())
        metadata = self.package / "setuptools-80.10.2.dist-info/METADATA"
        original = metadata.read_bytes()
        metadata.write_text("Name: setuptools\nVersion: 80.10.1\n")
        rejected()
        metadata.write_bytes(original)
        vendor = self.package / "setuptools/_vendor"
        moved = self.root / "outside"
        vendor.rename(moved)
        vendor.symlink_to(moved, target_is_directory=True)
        rejected()
        vendor.unlink()
        moved.rename(vendor)
        for cached in ("setuptools", "_distutils_hack", "distutils"):
            prefix = "\nsys.modules[" + repr(cached) + "] = type(sys)(" + repr(cached) + ")\n"
            (self.root / "launch.py").write_bytes(self.source + prefix.encode() + driver.encode())
            with self.subTest(cached=cached):
                rejected()

    def test_setuptools_rechecks_bytes_and_exact_origin_at_import(self):
        source = self.package / "_distutils_hack/override.py"
        original = source.read_bytes()
        driver = DRIVER.replace("    import fake_engine", """    import fake_engine
    Path(LAYOUT["paths"][-2] + "/_distutils_hack/override.py").write_text(
        "raise RuntimeError('private-code-ran')\\n")
    try: import setuptools
    except ValueError: pass
    else: raise AssertionError("changed source accepted")
    STATE["check"]()""")
        (self.root / "launch.py").write_bytes(self.source + driver.encode())
        self.assertTrue(self.run_fixture("fresh")["paths"])
        source.write_bytes(original)
        driver = DRIVER.replace("    import fake_engine", """    import fake_engine
    import _distutils_hack
    _distutils_hack.__path__[:] = [LAYOUT["paths"][-2] + "/shadow"]
    try: import setuptools
    except ValueError: pass
    else: raise AssertionError("alternate origin accepted")
    STATE["check"]()""")
        shadow = self.package / "shadow"
        shadow.mkdir()
        (shadow / "override.py").write_bytes(original)
        (self.root / "launch.py").write_bytes(self.source + driver.encode())
        self.assertTrue(self.run_fixture("fresh")["paths"])


class PatternMatcherTests(unittest.TestCase):
    setUp = BootstrapTests.setUp
    run_fixture = BootstrapTests.run_fixture

    def fixture(self, body, env_source=FAKE_PATTERN_ENV, target_source=FAKE_PATTERN_TARGET):
        sources = {
            "vllm/__init__.py": "",
            "vllm/env_override.py": env_source,
            "torch/__init__.py": '''from types import SimpleNamespace
class OpOverload:
    def __init__(self, builtin): self.builtin = builtin
_ops = SimpleNamespace(OpOverload=OpOverload)
_library = SimpleNamespace(utils=SimpleNamespace(is_builtin=lambda op: op.builtin))
''',
            "torch/_inductor/__init__.py": "",
            "torch/_inductor/lowering.py": '''def fallback_node_due_to_unsupported_type(node, allow_cpu_inputs=True):
    return (node.unsupported, allow_cpu_inputs)
''',
            "torch/_inductor/pattern_matcher.py": target_source,
            "torch-2.13.0.dist-info/METADATA": "Name: torch\nVersion: 2.13.0\n",
        }
        for name, raw in sources.items():
            path = self.package / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(raw)
        pins = {name: (relative, hashlib.sha256(sources[relative].encode()).hexdigest())
                for name, (relative, _) in bootstrap.PATTERN_SOURCES.items()}
        self.pattern_source = self.source + ("\nPATTERN_SOURCES=" + repr(pins) + "\n").encode()
        self.pattern_driver = DRIVER.replace("    import fake_engine", "    import fake_engine\n" + body)
        (self.root / "launch.py").write_bytes(self.pattern_source + self.pattern_driver.encode())

    def test_source_excerpts_are_exact_pinned_upstream_functions(self):
        tree = ast.parse(UPSTREAM_PATTERN_FUNCTIONS)
        expected = {
            "_apply_inductor_pattern_matcher_patch": "a40c9f901a694c92bca1be32fb9504e00a3df456f31d32f1bf54eaef62830e81",
            "_patch_inductor_pattern_matcher": "1f81d20a6113ace5b65a09b633c13866a29ab2fcaa8d20af74ef5187d5fb453f",
        }
        self.assertEqual({node.name: hashlib.sha256(ast.get_source_segment(
            UPSTREAM_PATTERN_FUNCTIONS, node).encode()).hexdigest() for node in tree.body}, expected)

    def test_original_source_registration_reproduces_strict_finder_rejection(self):
        self.fixture('''    before = list(sys.meta_path)
    # Deliberately omit only this adapter in the synthetic positive control.
    PATTERN_SOURCES.pop(PATTERN_ENV)
    import vllm.env_override
    assert len(sys.meta_path) == len(before) + 1
    assert type(sys.meta_path[0]).__name__ == "_PatternMatcherPatchFinder"
    try: STATE["check"]()
    except ValueError: pass
    else: raise AssertionError("original finder was accepted")
    sys.meta_path[:] = before
    STATE["check"]()''')
        self.assertTrue(self.run_fixture("fresh")["paths"])

    def test_deferred_callback_preserved_parent_spawn_grandchild_and_fresh(self):
        self.fixture('''    before = list(sys.meta_path)
    import vllm.env_override
    import torch
    assert STATE["pattern_status"] in ("pending", "applied")
    assert "_nib_register_pattern_matcher" not in vars(vllm.env_override)
    from torch._inductor import pattern_matcher
    assert STATE["pattern_status"] == "applied"
    assert pattern_matcher.LOADED
    from types import SimpleNamespace
    fallback = pattern_matcher.fallback_node_due_to_unsupported_type
    assert fallback(SimpleNamespace(target=torch._ops.OpOverload(True), unsupported=True), False) == (True, False)
    assert fallback(SimpleNamespace(target=torch._ops.OpOverload(False), unsupported=True)) is False
    assert fallback(SimpleNamespace(target=object(), unsupported=True)) is False
    assert sys.meta_path == before
    STATE["check"]()''')
        row = self.run_fixture("positive")
        self.assertTrue(row["fresh"]["paths"])
        self.assertTrue(row["child"]["grandchild"]["before"]["paths"])

    def test_already_loaded_target_and_later_unknown_finder(self):
        self.fixture('''    from torch._inductor import pattern_matcher
    before_function = pattern_matcher.fallback_node_due_to_unsupported_type
    assert STATE["pattern_status"] == "not-registered"
    import vllm.env_override
    assert STATE["pattern_status"] == "applied"
    assert pattern_matcher.fallback_node_due_to_unsupported_type is not before_function
    before = list(sys.meta_path)
    sys.meta_path.append(importlib.machinery.PathFinder)
    try: STATE["check"]()
    except ValueError: pass
    else: raise AssertionError("unrelated finder allowed")
    sys.meta_path[:] = before
    STATE["check"]()''')
        (self.package / "torch-2.13.0.dist-info/METADATA").write_text("Name: torch\nVersion: 2.13.0+cu130\n")
        self.assertTrue(self.run_fixture("fresh")["paths"])

    def test_changed_sources_wrong_version_and_origin_reject_before_execution(self):
        self.fixture('''    import vllm.env_override
    from torch._inductor import pattern_matcher''')
        def rejected():
            result = subprocess.run([self.layout["wrapper"], "fresh"], cwd="/", capture_output=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn(b"private-body-ran", result.stdout + result.stderr)
        for relative, _ in bootstrap.PATTERN_SOURCES.values():
            path = self.package / relative
            original = path.read_bytes()
            path.write_text("print('private-body-ran')\n")
            with self.subTest(relative=relative):
                rejected()
            path.write_bytes(original)
        metadata = self.package / "torch-2.13.0.dist-info/METADATA"
        metadata.write_text("Name: torch\nVersion: 2.16.0\n")
        rejected()
        metadata.write_text("Name: torch\nVersion: 2.13.0\n")
        for name, (relative, _) in bootstrap.PATTERN_SOURCES.items():
            shadow = self.package / "shadow"
            shadow.mkdir(exist_ok=True)
            (shadow / Path(relative).name).write_bytes((self.package / relative).read_bytes())
            parent = name.rsplit(".", 1)[0]
            code = ("    import " + parent + "\n    " + parent + ".__path__[:] = ["
                    + repr(str(shadow)) + "]\n    import " + name)
            if name == bootstrap.PATTERN_TARGET:
                code = "    import vllm.env_override\n" + code
            driver = DRIVER.replace("    import fake_engine", "    import fake_engine\n" + code)
            (self.root / "launch.py").write_bytes(self.pattern_source + driver.encode())
            with self.subTest(shadow=name):
                rejected()

    def test_cached_bytecode_is_never_the_verified_source(self):
        import importlib.util
        import marshal
        import struct
        self.fixture('''    import vllm.env_override
    from torch._inductor import pattern_matcher
    assert STATE["pattern_status"] == "applied"''')
        for relative, _ in bootstrap.PATTERN_SOURCES.values():
            source = self.package / relative
            cache = Path(importlib.util.cache_from_source(str(source)))
            cache.parent.mkdir(exist_ok=True)
            cache.write_bytes(importlib.util.MAGIC_NUMBER
                              + struct.pack("<III", 0, int(source.stat().st_mtime), source.stat().st_size)
                              + marshal.dumps(compile("raise RuntimeError('private cached pattern')", str(source), "exec")))
        self.assertTrue(self.run_fixture("fresh")["paths"])

    def test_preloaded_target_and_registration_replay_rejected(self):
        for replay in ("vllm.env_override", "torch._inductor.pattern_matcher"):
            self.fixture('''    import vllm.env_override
    from torch._inductor import pattern_matcher
    try: importlib.reload(sys.modules[''' + repr(replay) + '''])
    except ValueError: pass
    else: raise AssertionError("registration replay")
    assert STATE["pattern_status"] == "failed"
    try: STATE["check"]()
    except ValueError: pass
    else: raise AssertionError("replay failure not sticky")
    print('{"rejected":true}')
    raise SystemExit(0)''')
            with self.subTest(replay=replay):
                self.assertEqual(self.run_fixture("fresh"), {"rejected": True})
        prefix = '\nsys.modules[PATTERN_TARGET] = type(sys)(PATTERN_TARGET)\n'
        (self.root / "launch.py").write_bytes(self.pattern_source + prefix.encode() + self.pattern_driver.encode())
        result = subprocess.run([self.layout["wrapper"], "fresh"], cwd="/", capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)

    def test_target_load_verify_and_postcheck_failures_latch_before_retry(self):
        body = '''    import vllm.env_override
    assert STATE["pattern_status"] == "pending"
    try:
        from torch._inductor import pattern_matcher
    except (ImportError, ValueError, RuntimeError): pass
    else: raise AssertionError("failed target accepted")
    assert STATE["pattern_status"] == "failed"
    assert STATE["pattern_module"] is None
    assert STATE["pattern_loading"] is None
    assert PATTERN_TARGET not in sys.modules
    # Restore an attempted list mutation only to prove the failure stays latched.
    sys.meta_path[:] = STATE["finders"]
    for operation in (STATE["check"], lambda: importlib.import_module(PATTERN_TARGET)):
        try: operation()
        except ValueError: pass
        else: raise AssertionError("target retry accepted after partial load")
    print('{"rejected":true}')
    raise SystemExit(0)'''
        cases = {
            "execution": "PARTIAL = True\nraise ImportError('synthetic missing dependency')\n",
            "runtime": "PARTIAL = True\nraise RuntimeError('synthetic target failure')\n",
            "postcheck": "import sys\nsys.meta_path.append(object())\n",
            "verify": FAKE_PATTERN_TARGET,
        }
        for name, source in cases.items():
            self.fixture(body, target_source=source)
            if name == "verify":
                (self.package / "torch/_inductor/pattern_matcher.py").write_text(
                    "print('private-body-ran')\n")
            with self.subTest(failure=name):
                self.assertEqual(self.run_fixture("fresh"), {"rejected": True})

    def test_cached_partial_or_replaced_target_is_sticky_rejection(self):
        for phase in ("pending", "applied"):
            for change in ("foreign", "none", "deleted"):
                if phase == "pending" and change == "deleted":
                    continue
                code = '    import vllm.env_override\n'
                if phase == "applied":
                    code += '    from torch._inductor import pattern_matcher\n'
                code += {
                    "foreign": '    sys.modules[PATTERN_TARGET] = type(sys)(PATTERN_TARGET)\n',
                    "none": '    sys.modules[PATTERN_TARGET] = None\n',
                    "deleted": '    sys.modules.pop(PATTERN_TARGET)\n',
                }[change]
                code += '''    try: STATE["check"]()
    except ValueError: pass
    else: raise AssertionError("cached target identity accepted")
    assert STATE["pattern_status"] == "failed"
    print('{"rejected":true}')
    raise SystemExit(0)'''
                self.fixture(code)
                with self.subTest(phase=phase, change=change):
                    self.assertEqual(self.run_fixture("fresh"), {"rejected": True})

    def test_missing_or_sourceless_target_latches_before_fallback(self):
        import importlib.util
        import marshal
        import struct
        for kind in ("missing", "sourceless"):
            self.fixture('''    import vllm.env_override
    try: from torch._inductor import pattern_matcher
    except ValueError: pass
    else: raise AssertionError("target source not required")
    assert STATE["pattern_status"] == "failed"
    try: STATE["check"]()
    except ValueError: pass
    else: raise AssertionError("missing source failure not sticky")
    print('{"rejected":true}')
    raise SystemExit(0)''')
            source = self.package / "torch/_inductor/pattern_matcher.py"
            source.unlink()
            if kind == "sourceless":
                source.with_suffix(".pyc").write_bytes(importlib.util.MAGIC_NUMBER + struct.pack("<III", 0, 0, 0)
                    + marshal.dumps(compile("print('private-bytecode-ran')", str(source), "exec")))
            with self.subTest(kind=kind):
                self.assertEqual(self.run_fixture("fresh"), {"rejected": True})

    def test_callback_failure_is_sticky_and_does_not_publish_applied(self):
        changed = FAKE_PATTERN_ENV.replace(
            "    from torch._inductor import pattern_matcher\n",
            "    raise ImportError('synthetic callback failure')\n    from torch._inductor import pattern_matcher\n", 1)
        self.fixture('''    import vllm.env_override
    try: from torch._inductor import pattern_matcher
    except ImportError: pass
    else: raise AssertionError("callback failure accepted")
    assert STATE["pattern_status"] == "failed"
    assert STATE["pattern_loading"] is None
    try: STATE["check"]()
    except ValueError: pass
    else: raise AssertionError("callback failure not sticky")
    print('{"rejected":true}')
    raise SystemExit(0)''', env_source=changed)
        self.assertEqual(self.run_fixture("fresh"), {"rejected": True})

    def test_foreign_cached_target_cannot_receive_callback(self):
        self.fixture('''    sys.modules[PATTERN_TARGET] = type(sys)(PATTERN_TARGET)
    try: import vllm.env_override
    except ValueError: pass
    else: raise AssertionError("cached foreign target accepted")
    assert STATE["pattern_status"] == "failed"
    # No subsequent apparently successful operation may hide this failure.
    try: STATE["check"]()
    except ValueError: pass
    else: raise AssertionError("failure not sticky")
    raise SystemExit('{"rejected":true}')''')
        result = subprocess.run([self.layout["wrapper"], "fresh"], cwd="/", capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stderr.strip(), b'{"rejected":true}')

    def test_adapter_changes_only_registration_call_in_verified_ast(self):
        pins = copy.deepcopy(bootstrap.PATTERN_SOURCES)
        pins[bootstrap.PATTERN_ENV] = ("vllm/env_override.py", hashlib.sha256(FAKE_PATTERN_ENV.encode()).hexdigest())
        with mock.patch.object(bootstrap, "PATTERN_SOURCES", pins):
            with mock.patch.object(bootstrap, "compile", create=True, return_value="compiled") as compile_mock:
                self.assertEqual(bootstrap.pattern_env_code(FAKE_PATTERN_ENV.encode(), "pinned.py", helpers.require),
                                 "compiled")
                actual = compile_mock.call_args.args[0]
            expected = ast.parse(FAKE_PATTERN_ENV)
            for node in ast.walk(expected):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                        and node.func.id == "_patch_inductor_pattern_matcher":
                    node.func.id = bootstrap.PATTERN_REGISTER
            self.assertEqual(ast.dump(actual, include_attributes=True), ast.dump(expected, include_attributes=True))
            with self.assertRaises(ValueError):
                bootstrap.pattern_env_code(FAKE_PATTERN_ENV.encode() + b"\n", "pinned.py", helpers.require)


class BoundedProcessTests(unittest.TestCase):
    def test_quiet_command_bounded_output_both_streams(self):
        for stream in (1, 2):
            with self.subTest(stream=stream), self.assertRaises((ValueError, OSError, RuntimeError, subprocess.SubprocessError)):
                lab.quiet_command([sys.executable, "-I", "-S", "-B", "-c",
                                   f"import os;os.write({stream},b'x'*1000000)"], 5, output=True)

    def test_timeout_kills_descendant_and_no_delayed_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = str(Path(tmp) / "orphan")
            code = ("import subprocess,sys,time;subprocess.Popen([sys.executable,'-I','-S','-c',"
                    + repr("import time;time.sleep(1);open(" + repr(marker) + ",'w').write('bad')") + "]);time.sleep(30)")
            with self.assertRaises(subprocess.TimeoutExpired):
                lab.quiet_command([sys.executable, "-I", "-S", "-B", "-c", code], .15)
            time.sleep(1.1)
            self.assertFalse(Path(marker).exists())

    def test_quiet_command_flags_for_hardware(self):
        lab.GPU_PROBE = probe
        with mock.patch.object(lab, "quiet_command", return_value=b"{}") as command:
            with self.assertRaises((KeyError, ValueError)):
                lab.hardware()
            self.assertEqual(command.call_args.args[0], ["/opt/nib/python", "probe"])
            self.assertNotIn("python3", command.call_args.args[0])

    def test_read_fifo_symlink_hardlink_rejected_without_wait(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            target = root / "file"
            target.write_bytes(b"safe")
            for kind in ("fifo", "symlink", "hardlink"):
                path = root / kind
                if kind == "fifo": os.mkfifo(path)
                elif kind == "symlink": path.symlink_to(target)
                else: os.link(target, path)
                before = time.monotonic()
                with self.assertRaises((ValueError, OSError)):
                    host.read(path)
                self.assertLess(time.monotonic() - before, 1)

    def test_cancel_once_cleanup_not_interrupted(self):
        with mock.patch.object(host, "CANCELLED", False), mock.patch.object(host, "CLEANING", False):
            with self.assertRaises(InterruptedError): host.cancel()
            host.cancel()
        with mock.patch.object(host, "CANCELLED", False), mock.patch.object(host, "CLEANING", True):
            host.cancel()

    def test_watchdog_real_parent_death_cleans_owned_fake_resource(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            marker, ready = root / "cleaned", root / "ready"
            # Only the host lifecycle runs. The fake cleanup writes a marker;
            # no Docker client, image or hardware is involved.
            watchdog = root / "watch.py"
            watchdog.write_text(
                "import sys,types,pathlib,os,time\nm=types.ModuleType('fixture');m.__file__=" + repr(host.__file__) + "\n"
                "exec(compile(pathlib.Path(m.__file__).read_bytes(),m.__file__,'exec'),m.__dict__)\n"
                "m.WATCHDOG_SETTLE_SECONDS=0\nm.bundle_inputs=lambda *a:(None,None,None,None)\n"
                "m.cleanup=lambda *a:pathlib.Path(" + repr(str(marker)) + ").write_text('confirmed')\n"
                "pathlib.Path(" + repr(str(ready)) + ").write_text('ready')\n"
                "m.watchdog(None,None,'nib-deepseek-'+'a'*32,'b'*32,time.time()+30,int(sys.argv[1]),'unix:///var/run/docker.sock')\n")
            parent = root / "parent.py"
            parent.write_text("import os,subprocess,sys,time\nr,w=os.pipe()\n"
                              "p=subprocess.Popen([sys.executable,'-I','-S'," + repr(str(watchdog)) +
                              ",str(r)],pass_fds=(r,),start_new_session=True)\n"
                              "os.close(r)\nprint(p.pid,flush=True)\ntime.sleep(30)\n")
            process = subprocess.Popen([sys.executable, "-I", "-S", str(parent)],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                self.assertTrue(process.stdout.readline().strip().isdigit())
                end = time.monotonic() + 5
                while not ready.exists() and time.monotonic() < end:
                    time.sleep(.01)
                self.assertTrue(ready.exists())
                process.kill()
                process.wait(timeout=5)
                observed = None
                while time.monotonic() < end:
                    if marker.exists():
                        observed = marker.read_text()
                        if observed:
                            break
                    time.sleep(.01)
                self.assertEqual(observed, "confirmed")
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                process.stdout.close()
                process.stderr.close()


class HardwareTests(unittest.TestCase):
    def raw(self):
        return {"driver": "595.45.01", "cuda_driver": 13000, "feature": 1, "environment": 2,
                "devtools": 0, "multigpu": 2, "ready": 1,
                "devices": [{"b200": True, "protected_kib": 180 << 20} for _ in range(4)]}

    def test_positive_named_values_and_fixed_report(self):
        raw = self.raw()
        row = probe.validate(raw)
        self.assertEqual(probe.validate_report(row), row)
        self.assertEqual(row["multigpu"], "nvle")
        self.assertFalse(row["attestation_verified"])
        raw["multigpu"] = 1
        self.assertEqual(probe.validate(raw)["multigpu"], "protected-pcie")
        self.assertNotIn("UUID", json.dumps(row))

    def test_unknown_mixed_plain_and_malformed_rejected(self):
        for key, value in (("feature", 0), ("feature", True), ("environment", 1), ("devtools", 1),
                           ("multigpu", 0), ("multigpu", 99), ("ready", 0), ("cuda_driver", 12080),
                           ("driver", "private-sentinel"), ("private", "sentinel")):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                probe.validate({**self.raw(), key: value})
        for field, value in (("b200", False), ("protected_kib", 0), ("protected_kib", True)):
            raw = self.raw()
            raw["devices"][3][field] = value
            with self.assertRaises(ValueError):
                probe.validate(raw)
        row = probe.validate(self.raw())
        with self.assertRaises(ValueError):
            probe.validate_report({**row, "private": "sentinel"})

    def test_never_nvml_setter(self):
        instance = object.__new__(probe.NVML)
        instance.library = mock.Mock()
        with self.assertRaises(ValueError):
            instance.call("nvmlSystemSetConfComputeGpusReadyState", 1)
        instance.library.assert_not_called()

    def test_b300_is_explicit_and_never_accepted_as_b200(self):
        raw = self.raw()
        raw["devices"] = [{"b300": True, "protected_kib": 250 << 20} for _ in range(4)]
        row = probe.validate(raw, family="B300")
        self.assertEqual(probe.validate_report(row, family="B300"), row)
        self.assertEqual(row["family"], "B300")
        self.assertFalse(row["attestation_verified"])
        with self.assertRaises(ValueError):
            probe.validate(raw)
        with self.assertRaises(ValueError):
            probe.validate_report(row)
        with self.assertRaises(ValueError):
            probe.validate(self.raw(), family="B300")
        for family in ("H200", "b300", "auto", None, True):
            with self.subTest(family=family), self.assertRaises(ValueError):
                probe.validate(raw, family=family)
        for bad in ({"b300": False, "protected_kib": 250 << 20},
                    {"b200": True, "protected_kib": 250 << 20},
                    {"b300": True, "protected_kib": 180 << 20}):
            mixed = copy.deepcopy(raw)
            mixed["devices"][2] = bad
            with self.assertRaises(ValueError):
                probe.validate(mixed, family="B300")
        for field, value in (("feature", 0), ("devtools", 1), ("environment", 1), ("ready", 0), ("multigpu", 0)):
            with self.subTest(field=field), self.assertRaises(ValueError):
                probe.validate({**raw, field: value}, family="B300")

    def test_family_names_are_bounded_exact_tokens(self):
        self.assertTrue(probe.matches_family(b"NVIDIA B300", "B300"))
        for name in (b"NVIDIA B200", b"NVIDIA B3000", b"NVIDIA B300 B200", b"private"):
            self.assertFalse(probe.matches_family(name, "B300"))
        self.assertFalse(probe.matches_family(b"NVIDIA B300 B200", "B200"))

    def test_b300_nvml_keeps_actual_family_in_report(self):
        with mock.patch.object(probe.c, "CDLL", return_value=DiagnosticTests().library(
                name=b"NVIDIA B300", memory=250 << 20)):
            row = probe.validate(probe.NVML().observe("B300"), family="B300")
        self.assertEqual(row["family"], "B300")
        self.assertEqual(row["protected_kib"], [250 << 20] * 4)

    def test_nonconfidential_is_explicit_and_never_accepted_by_cc_validator(self):
        library = DiagnosticTests().library(feature=0, environment=0, multigpu=0)
        with mock.patch.object(probe.c, "CDLL", return_value=library):
            raw = probe.NVML().observe(nonconfidential=True)
        row = probe.validate_nonconfidential(raw)
        self.assertEqual(row["cc_feature"], "disabled")
        self.assertIsNone(row["system_ready"])
        self.assertFalse(row["attestation_verified"])
        self.assertEqual(row["total_kib"], [180 << 20] * 4)
        self.assertNotIn("protected_kib", row)
        for validate, value in ((probe.validate, raw), (probe.validate_report, row),
                                (probe.validate_nonconfidential, self.raw()),
                                (probe.validate_nonconfidential_report, probe.validate(self.raw()))):
            with self.subTest(validator=validate.__name__), self.assertRaises(ValueError):
                validate(value)
        for key, value in (("feature", 1), ("feature", False), ("environment", 1), ("environment", 2),
                           ("multigpu", 2), ("devtools", 1), ("ready", 1), ("cuda_driver", 12080),
                           ("driver", "private-sentinel"), ("private", "sentinel")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                probe.validate_nonconfidential({**raw, key: value})
        for value in (0, True, 119 << 20, 2 << 30):
            changed = copy.deepcopy(raw)
            changed["devices"][0]["total_kib"] = value
            with self.assertRaises(ValueError):
                probe.validate_nonconfidential(changed)
        for value in ({**row, "attestation_verified": True}, {**row, "protected_kib": row["total_kib"]},
                      {**row, "family": "B300"}, {**row, "cuda_driver": "12.8"}):
            with self.assertRaises(ValueError):
                probe.validate_nonconfidential_report(value)

    def test_nonconfidential_observer_never_queries_readiness_or_changes_mode(self):
        library = DiagnosticTests().library(feature=0, environment=0, multigpu=0)
        library.nvmlSystemGetConfComputeGpusReadyState = mock.Mock(side_effect=AssertionError("readiness"))
        library.nvmlDeviceGetConfComputeMemSizeInfo = mock.Mock(side_effect=AssertionError("protected"))
        library.nvmlShutdown = mock.Mock(return_value=0)
        with mock.patch.object(probe.c, "CDLL", return_value=library):
            probe.validate_nonconfidential(probe.NVML().observe(nonconfidential=True))
            library.nvmlShutdown.assert_called_once_with()
            library.nvmlSystemGetConfComputeGpusReadyState.assert_not_called()
            library.nvmlDeviceGetConfComputeMemSizeInfo.assert_not_called()
            for value in (1, None, "true"):
                with self.assertRaises(ValueError):
                    probe.NVML().observe(nonconfidential=value)
            with self.assertRaises(ValueError):
                probe.NVML().observe("H200", nonconfidential=True)
        for symbol in ("nvmlSystemGetConfComputeSettings", "nvmlDeviceGetMemoryInfo", "nvmlShutdown"):
            with self.subTest(symbol=symbol), mock.patch.object(probe.c, "CDLL", return_value=
                    DiagnosticTests().library(feature=0, environment=0, multigpu=0, codes={symbol: 3})):
                with self.assertRaises(ValueError):
                    probe.NVML().observe(nonconfidential=True)

    def test_profile_has_no_auto_downgrade(self):
        self.assertEqual(probe.security_profile({}), "confidential")
        contract = {"operation": "local-correctness", "gpu_security": "nonconfidential"}
        self.assertEqual(probe.security_profile(contract), "nonconfidential")
        self.assertEqual(probe.security_profile({**contract, "gpu_family": "B300"}), "nonconfidential")
        for change in ({"operation": "functional"}, {"operation": "hardware-diagnostic"},
                       {"gpu_family": "H200"}, {"gpu_family": []},
                       {"gpu_security": None}, {"gpu_security": "auto"}):
            with self.assertRaises(ValueError):
                probe.security_profile({**contract, **change})

    def test_b300_nonconfidential_family_and_memory_are_bound(self):
        library = DiagnosticTests().library(feature=0, environment=0, multigpu=0,
                                            name=b"NVIDIA B300", memory=250 << 20)
        library.nvmlSystemGetConfComputeGpusReadyState = mock.Mock(side_effect=AssertionError("readiness"))
        library.nvmlDeviceGetConfComputeMemSizeInfo = mock.Mock(side_effect=AssertionError("protected"))
        with mock.patch.object(probe.c, "CDLL", return_value=library):
            raw = probe.NVML().observe("B300", nonconfidential=True)
        row = probe.validate_nonconfidential(raw, family="B300")
        self.assertEqual(row["family"], "B300")
        self.assertEqual(row["total_kib"], [250 << 20] * 4)
        self.assertFalse(row["attestation_verified"])
        self.assertNotIn("protected_kib", row)
        library.nvmlSystemGetConfComputeGpusReadyState.assert_not_called()
        library.nvmlDeviceGetConfComputeMemSizeInfo.assert_not_called()
        for validator, value, kwargs in (
                (probe.validate_nonconfidential, raw, {}),
                (probe.validate_nonconfidential_report, row, {}),
                (probe.validate, raw, {"family": "B300"}),
                (probe.validate_report, row, {"family": "B300"})):
            with self.subTest(validator=validator.__name__), self.assertRaises(ValueError):
                validator(value, **kwargs)
        for device in ({"b300": False, "total_kib": 250 << 20},
                       {"b200": True, "total_kib": 250 << 20},
                       {"b300": True, "total_kib": 199 << 20},
                       {"b300": True, "total_kib": True}):
            changed = copy.deepcopy(raw)
            changed["devices"][0] = device
            with self.subTest(device=device), self.assertRaises(ValueError):
                probe.validate_nonconfidential(changed, family="B300")
        with mock.patch.object(lab, "GPU_PROBE", probe, create=True), \
                mock.patch.object(lab, "quiet_command", return_value=json.dumps(row).encode()):
            self.assertEqual(lab.hardware("B300", "nonconfidential"), row)
            with self.assertRaises(ValueError):
                lab.hardware("B200", "nonconfidential")
            with self.assertRaises(ValueError):
                lab.hardware("B300", "confidential")


class DiagnosticTests(unittest.TestCase):
    def library(self, *, codes=None, count=4, feature=1, environment=2, devtools=0, multigpu=2,
                ready=1, memory=180 << 20, name=b"NVIDIA B200 private-name-sentinel",
                driver=b"595.91.07"):
        codes = codes or {}
        library = types.SimpleNamespace()
        for symbol in set(probe.CALL_STAGES) | probe.NONCONFIDENTIAL_CALLS:
            def call(*args, symbol=symbol):
                code = codes.get(symbol, 0)
                if isinstance(code, Exception):
                    raise code
                if code != 0:
                    return code
                if symbol == "nvmlDeviceGetCount_v2":
                    args[0]._obj.value = count
                elif symbol == "nvmlSystemGetConfComputeSettings":
                    settings = args[0]._obj
                    settings.ccFeature, settings.environment = feature, environment
                    settings.devToolsMode, settings.multiGpuMode = devtools, multigpu
                elif symbol == "nvmlSystemGetConfComputeGpusReadyState":
                    args[0]._obj.value = ready
                elif symbol == "nvmlSystemGetCudaDriverVersion_v2":
                    args[0]._obj.value = 13000
                elif symbol == "nvmlSystemGetDriverVersion":
                    args[0].value = driver
                elif symbol == "nvmlDeviceGetHandleByIndex_v2":
                    args[1]._obj.value = 123456  # Never emitted.
                elif symbol == "nvmlDeviceGetName":
                    args[1].value = name
                elif symbol == "nvmlDeviceGetConfComputeMemSizeInfo":
                    args[1]._obj.protectedMemSizeKib = memory
                    args[1]._obj.unprotectedMemSizeKib = 0
                elif symbol == "nvmlDeviceGetMemoryInfo":
                    args[1]._obj.total = memory * 1024
                    args[1]._obj.free = memory * 1024
                    args[1]._obj.used = 0
                return 0
            setattr(library, symbol, call)
        return library

    def diagnose(self, **kwargs):
        with mock.patch.object(probe.c, "CDLL", return_value=self.library(**kwargs)):
            row = probe.diagnose()
        probe.validate_trace(row)
        self.assertNotIn("sentinel", json.dumps(row))
        return row

    def child(self, trace=None):
        return {"stage": "probe", "invocation": {key: True for key in
                ("linux", "isolated", "no_site", "no_bytecode", "argument_shape")},
                "bootstrap": {"stage": "complete", "source_index": None},
                "probe": trace if trace is not None else self.diagnose()}

    def test_numeric_success_is_never_functional_or_qualification(self):
        row = self.diagnose()
        self.assertTrue(row["policy_checks_passed"])
        self.assertIsNone(row["first_failure"])
        self.assertEqual(len(row["calls"]), 19)
        self.assertEqual(row["observed"]["count"], 4)
        self.assertEqual((row["settings_input_version"], row["settings_struct_bytes"]), (0x01000014, 20))
        self.assertEqual(row["calls"][-1], {"stage": "shutdown", "device": None, "return_code": 0})
        with mock.patch.object(lab, "GPU_PROBE", probe), \
                mock.patch.object(lab, "quiet_command", return_value=json.dumps(self.child(row)).encode()) as command:
            outer = lab.diagnostic_request()
        command.assert_called_once_with(["/opt/nib/python", "probe-diagnostic"], 15, output=True)
        probe.validate_diagnostic(outer)
        self.assertFalse(outer["functional_pass"])
        self.assertFalse(outer["confidential"])
        self.assertFalse(outer["deployable"])

    def test_load_each_call_failure_and_shutdown_are_separate(self):
        with mock.patch.object(probe.c, "CDLL", side_effect=OSError("/private/loader-sentinel")):
            row = probe.diagnose()
        self.assertEqual(row["first_failure"], "ctypes-loading")
        self.assertEqual(row["calls"], [])
        for name, stage in probe.CALL_STAGES.items():
            with self.subTest(stage=stage):
                row = self.diagnose(codes={name: 26})
                self.assertEqual(row["first_failure"], stage)
                self.assertFalse(row["policy_checks_passed"])
                self.assertIn(26, [c["return_code"] for c in row["calls"]])
        row = self.diagnose(codes={"nvmlSystemGetConfComputeSettings": 26, "nvmlShutdown": 999})
        self.assertEqual(row["first_failure"], "settings-abi")
        self.assertEqual(row["calls"][-1], {"stage": "shutdown", "device": None, "return_code": 999})
        row = self.diagnose(codes={"nvmlDeviceGetCount_v2": AttributeError("private-symbol-sentinel")})
        self.assertEqual(row["first_failure"], "count")
        self.assertIsNone(row["calls"][1]["return_code"])

    def test_original_count_cc_readiness_memory_policies_still_reject(self):
        for values, stage in (({"count": 8}, "required-count"), ({"feature": 0}, "cc-policy"),
                              ({"environment": 1}, "cc-policy"), ({"devtools": 1}, "cc-policy"),
                              ({"multigpu": 0}, "cc-policy"), ({"ready": 0}, "readiness-policy"),
                              ({"memory": 0}, "memory-policy"), ({"name": b"private-name-sentinel"}, "memory-policy"),
                              ({"driver": b"private-driver-sentinel"}, "driver-policy")):
            with self.subTest(values=values):
                row = self.diagnose(**values)
                self.assertEqual(row["first_failure"], stage)
                self.assertFalse(row["policy_checks_passed"])
        row = self.diagnose(count=8)
        self.assertEqual(row["observed"]["count"], 8)
        self.assertEqual([c["stage"] for c in row["calls"]], ["init", "count", "shutdown"])
        self.assertIsNone(row["observed"]["feature"])

    def test_child_source_binding_bootstrap_and_flags_fail_closed(self):
        fake_sys = types.SimpleNamespace(platform="linux", flags=types.SimpleNamespace(isolated=1, no_site=1),
                                         dont_write_bytecode=True, argv=["fixed", "a" * 64, "probe-diagnostic"],
                                         modules={lab.__name__: lab})
        contract = {"operation": "hardware-diagnostic", "paths": ["/stdlib", "/stdlib/lib-dynload", "/selected"]}
        for failing in ("invocation", "source-binding", "bootstrap", None):
            with self.subTest(failing=failing), mock.patch.object(lab, "sys", fake_sys), \
                    mock.patch.object(lab, "lab_context", return_value=(contract, {}, [], {}, {})) as context, \
                    mock.patch.object(lab, "install_lab", create=True) as install, \
                    mock.patch.object(lab, "GPU_PROBE", probe), \
                    mock.patch.object(probe, "diagnose", return_value=self.diagnose()) as inspect:
                fake_sys.flags.isolated = failing != "invocation"
                if failing == "source-binding":
                    context.side_effect = ValueError("private-source-sentinel")
                if failing == "bootstrap":
                    def fail(*args, checkpoint):
                        checkpoint("setuptools-source", 2)
                        raise ValueError("private-bootstrap-sentinel")
                    install.side_effect = fail
                else:
                    install.side_effect = lambda *args, checkpoint: checkpoint("complete", None)
                row = lab.diagnostic_child("a" * 64)
                self.assertEqual(row["stage"], failing or "probe")
                if failing:
                    inspect.assert_not_called()
                if failing == "invocation":
                    context.assert_not_called()
                if failing == "bootstrap":
                    self.assertEqual(row["bootstrap"], {"stage": "setuptools-source", "source_index": 2})
                probe.validate_diagnostic({"schema": "nib.deepseek-hardware-diagnostic/v1", "status": "BLOCKED",
                                          "deployable": False, "confidential": False, "functional_pass": False,
                                          "stage": "diagnostic", "probe_execution": "completed", "child": row})
                self.assertNotIn("sentinel", json.dumps(row))

    def test_timeout_failed_malformed_children_never_invent_observations(self):
        for effect, expected in ((subprocess.TimeoutExpired("private-sentinel", 15), "timeout"),
                                 (subprocess.CalledProcessError(1, "private-sentinel"), "failed"),
                                 (b'{"private":"sentinel"}', "malformed"), (b"not JSON", "malformed")):
            with self.subTest(expected=expected), mock.patch.object(lab, "GPU_PROBE", probe), \
                    mock.patch.object(lab, "quiet_command") as command:
                if isinstance(effect, Exception):
                    command.side_effect = effect
                else:
                    command.return_value = effect
                row = lab.diagnostic_request()
                probe.validate_diagnostic(row)
                self.assertEqual(row["probe_execution"], expected)
                self.assertIsNone(row["child"])
                self.assertFalse(row["functional_pass"])
                self.assertNotIn("sentinel", json.dumps(row))

    def test_diagnostic_dispatch_never_enters_application_or_hardware_acceptance(self):
        fake_sys = types.SimpleNamespace(flags=types.SimpleNamespace(isolated=1, no_site=1),
                                         dont_write_bytecode=True, argv=["fixed", "a" * 64, "diagnose-hardware"])
        contract = {"operation": "hardware-diagnostic"}
        result = {"fixed": "diagnostic"}
        with mock.patch.object(lab, "sys", fake_sys), \
                mock.patch.object(lab, "lab_context", return_value=(contract, {}, [], {}, {})), \
                mock.patch.object(lab, "diagnostic_request", return_value=result) as diagnostic, \
                mock.patch.object(lab, "application_and_request") as application, \
                mock.patch.object(lab, "hardware") as acceptance, \
                mock.patch.object(lab.signal, "signal"), mock.patch.object(lab.signal, "alarm") as alarm, \
                mock.patch("builtins.print") as output:
            lab.lab_entry()
            diagnostic.assert_called_once_with()
            application.assert_not_called()
            acceptance.assert_not_called()
            alarm.assert_called_once_with(60)
            output.assert_called_once_with(json.dumps(result, sort_keys=True))
            for mode in ("apply", "probe", "engine"):
                fake_sys.argv[-1] = mode
                with self.subTest(mode=mode), self.assertRaises(ValueError):
                    lab.lab_entry()
            self.assertEqual(diagnostic.call_count, 1)

    def test_numeric_and_shape_tamper_rejected(self):
        good = self.diagnose()
        for key, value in (("private", "sentinel"), ("required_count", 8),
                           ("settings_struct_bytes", 24), ("first_failure", "private-stage-sentinel")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                probe.validate_trace({**good, key: value})
        for key, value in (("count", True), ("count", 1 << 32), ("ready", "sentinel")):
            bad = copy.deepcopy(good)
            bad["observed"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                probe.validate_trace(bad)
        bad = copy.deepcopy(good)
        bad["calls"][2]["return_code"] = 26
        with self.assertRaises(ValueError):
            probe.validate_trace(bad)
        bad = copy.deepcopy(good)
        bad["devices"][0]["private"] = "sentinel"
        with self.assertRaises(ValueError):
            probe.validate_trace(bad)


class PayloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = module("scripts/test_engine_application.py")
        cls.fixtures.ApplicationTests.setUpClass()
        cls.builder = host.load("lab_test_builder", ROOT / host.BUILDER, host.BUILDER_SHA)
        cls.extra = dict(cls.fixtures.ApplicationTests.extra)
        for name in (host.SCRIPT, host.HELPER, host.RUNTIME, host.LAB_BOOTSTRAP, host.EXCEPTIONS, host.PROBE, host.SMOKE, host.CORRECTNESS,
                     host.TEST, host.SIX_TEST, host.PACK + "/pack.yaml", host.PACK + "/files.sha256"):
            cls.extra[name] = (ROOT / name).read_bytes()
        cls.bundle = cls.builder.checker.working_bundle(ROOT)
        cls.files, cls.contract = host.make_payload(cls.builder, "1"*40, cls.bundle, cls.extra, b"fake-linux-binary")

    def test_deterministic_payload_complete_hash_binding_and_unchanged_p0(self):
        again, contract = host.make_payload(self.builder, "1"*40, self.bundle, self.extra, b"fake-linux-binary")
        self.assertEqual((again, contract), (self.files, self.contract))
        changed = {**self.extra, host.SIX_TEST: self.extra[host.SIX_TEST] + b"\n"}
        _, changed_contract = host.make_payload(self.builder, "1"*40, self.bundle, changed, b"fake-linux-binary")
        self.assertNotEqual(changed_contract["source_bundle_sha256"], contract["source_bundle_sha256"])
        self.assertEqual(set(self.files) - set(contract["sources"]), {"lab.json", "python"})
        for name, sha in contract["sources"].items():
            self.assertEqual(hashlib.sha256(self.files[name]).hexdigest(), sha)
        self.assertIn(self.extra[host.HELPER], self.files["launch.py"])
        self.assertEqual(contract["pack_bytes"], 510311045295)
        args, env = contract["arguments"], contract["environment"]
        for flag, value in (("--tensor-parallel-size", "4"), ("--data-parallel-size", "1"),
                            ("--pipeline-parallel-size", "1"), ("--max-model-len", "4096"),
                            ("--max-num-seqs", "1"), ("--distributed-executor-backend", "mp")):
            self.assertEqual(args[args.index(flag)+1], value)
        self.assertIn("--no-numa-bind", args)
        self.assertIn("--disable-custom-all-reduce", args)
        self.assertEqual(env["VLLM_CONFIDENTIAL_COMPUTE"], "1")
        for name in ("VLLM_ALLREDUCE_USE_SYMM_MEM", "VLLM_ALLREDUCE_USE_FLASHINFER",
                     "VLLM_ALLREDUCE_USE_FLASHINFER_PCIE_IPC", "VLLM_USE_NCCL_SYMM_MEM", "VLLM_ENABLE_GEMM_RS"):
            self.assertEqual(env[name], "0")
        self.assertEqual(env["VLLM_PLUGINS"], "")
        self.assertNotIn("TORCHINDUCTOR_VEC_ISA_OK", env)
        self.assertFalse(contract["deployable"])

    def test_diagnostic_payload_is_explicit_source_bound_and_denial_only(self):
        files, contract = host.make_payload(self.builder, "1"*40, self.bundle, self.extra,
                                            b"fake-linux-binary", "hardware-diagnostic")
        self.assertEqual(contract["operation"], "hardware-diagnostic")
        self.assertEqual(contract["sources"]["probe.py"], hashlib.sha256(self.extra[host.PROBE]).hexdigest())
        self.assertNotEqual(files["lab.json"], self.files["lab.json"])
        with self.assertRaises(ValueError):
            host.make_payload(self.builder, "1"*40, self.bundle, self.extra, b"fake", "unreviewed")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "payload").mkdir()
            (root / "payload/lab.json").write_bytes(files["lab.json"])
            (root / "payload/probe.py").write_bytes(files["probe.py"])
            args = host.create_command("owned", "owner", root, "/empty-weights", contract, {"config": {"Env": []}})
            self.assertEqual(args[-1], "diagnose-hardware")
            row = {"schema": "nib.deepseek-hardware-diagnostic/v1", "status": "BLOCKED",
                   "deployable": False, "confidential": False, "functional_pass": False,
                   "stage": "diagnostic", "probe_execution": "timeout", "child": None}
            host.validate_result(row, root, contract, None)
            with self.assertRaises(ValueError):
                host.validate_result({**row, "functional_pass": True}, root, contract, None)

    def test_correctness_payload_is_opt_in_and_source_bound(self):
        files, contract = host.make_payload(self.builder, "1"*40, self.bundle, self.extra,
                                            b"fake-linux-binary", "local-correctness")
        self.assertEqual(contract["operation"], "local-correctness")
        self.assertEqual(files["correctness.py"], self.extra[host.CORRECTNESS])
        self.assertEqual(contract["sources"]["correctness.py"], hashlib.sha256(files["correctness.py"]).hexdigest())
        self.assertNotIn("correctness.py", self.files)
        self.assertEqual(contract["arguments"], self.contract["arguments"])
        self.assertEqual(contract["environment"], self.contract["environment"])
        self.assertFalse(contract["confidential"])
        self.assertFalse(contract["deployable"])

    def test_pre_serve_has_no_weights_mount_and_smaller_resource_bounds(self):
        files, contract = host.make_payload(self.builder, "1" * 40, self.bundle, self.extra,
                                            b"fake", "pre-serve", "B300")
        self.assertEqual(contract["operation"], "pre-serve")
        self.assertEqual(contract["gpu_family"], "B300")
        self.assertEqual(contract["arguments"], self.contract["arguments"])
        self.assertNotIn("correctness.py", files)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "payload").mkdir()
            (root / "payload/lab.json").write_bytes(files["lab.json"])
            args = host.create_command("owned", "owner", root, None, contract, {"config": {"Env": []}})
            self.assertEqual(args.count("--mount"), 1)
            self.assertEqual(args[args.index("--memory") + 1], "8g")
            self.assertEqual(args[args.index("--memory-swap") + 1], "8g")
            self.assertEqual(args[args.index("--cpus") + 1], "2")
            self.assertEqual(args[args.index("--network") + 1], "none")
            with self.assertRaises(ValueError):
                host.create_command("owned", "owner", root, "/weights", contract, {"config": {"Env": []}})

    def test_b300_payload_binds_family_without_changing_engine_or_sandbox(self):
        args = (self.builder, "1"*40, self.bundle, self.extra, b"fake-linux-binary")
        old_files, old = host.make_payload(*args, "local-correctness")
        files, contract = host.make_payload(*args, "local-correctness", "B300")
        self.assertNotIn("gpu_family", old)
        self.assertEqual(contract, {**old, "gpu_family": "B300"})
        self.assertEqual({k for k in files if files[k] != old_files[k]}, {"lab.json", "python"})
        self.assertIn(hashlib.sha256(files["lab.json"]).hexdigest().encode(), files["python"])
        for operation, family in (("functional", "B300"), ("hardware-diagnostic", "B300"),
                                  ("local-correctness", "H200"), ("local-correctness", None)):
            with self.subTest(operation=operation, family=family), self.assertRaises(ValueError):
                host.make_payload(*args, operation, family)

    def test_nonconfidential_payload_binds_opt_in_and_preserves_all_other_controls(self):
        args = (self.builder, "1" * 40, self.bundle, self.extra, b"fake-linux-binary")
        old_files, old = host.make_payload(*args, "local-correctness")
        files, contract = host.make_payload(*args, "local-correctness", "B200", "nonconfidential")
        self.assertNotIn("gpu_security", old)
        self.assertEqual(contract, {**old, "gpu_security": "nonconfidential"})
        self.assertEqual({k for k in files if files[k] != old_files[k]}, {"lab.json", "python"})
        self.assertIn(hashlib.sha256(files["lab.json"]).hexdigest().encode(), files["python"])
        self.assertFalse(contract["confidential"])
        self.assertFalse(contract["deployable"])
        for operation, family, security in (("functional", "B200", "nonconfidential"),
                ("hardware-diagnostic", "B200", "nonconfidential"),
                ("local-correctness", "H200", "nonconfidential"),
                ("local-correctness", "B200", None), ("local-correctness", "B200", "auto")):
            with self.subTest(operation=operation, family=family, security=security), self.assertRaises(ValueError):
                host.make_payload(*args, operation, family, security)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "payload").mkdir()
            for key in ("lab.json", "probe.py"):
                (root / "payload" / key).write_bytes(files[key])
            raw = HardwareTests().raw()
            raw.update(feature=0, environment=0, multigpu=0, ready=None,
                       devices=[{"b200": True, "total_kib": 180 << 20} for _ in range(4)])
            row = {"schema": "nib.deepseek-lab-correctness-result/v2", "status": "BLOCKED",
                   "deployable": False, "confidential": False, "functional_pass": False, "stage": "weights",
                   "hardware": probe.validate_nonconfidential(raw), "application": None,
                   "completion": None, "correctness": None, "startup": None, "engine_stopped": False}
            host.validate_result(row, root, contract, None)
            with self.assertRaises(ValueError):
                host.validate_result(row, root, old, None)
            command = host.create_command("owned", "owner", root, "/weights", contract, {"config": {"Env": []}})
            self.assertEqual(command[command.index("--network") + 1], "none")
            self.assertNotIn("--privileged", command)

    def test_b300_baseline_payload_and_result_keep_security_and_family_separate(self):
        args = (self.builder, "1" * 40, self.bundle, self.extra, b"fake-linux-binary")
        cc_files, cc_contract = host.make_payload(*args, "local-correctness", "B300")
        files, contract = host.make_payload(*args, "local-correctness", "B300", "nonconfidential")
        self.assertEqual(contract, {**cc_contract, "gpu_security": "nonconfidential"})
        self.assertEqual({k for k in files if files[k] != cc_files[k]}, {"lab.json", "python"})
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "payload").mkdir()
            (root / "payload/probe.py").write_bytes(files["probe.py"])
            raw = HardwareTests().raw()
            raw.update(feature=0, environment=0, multigpu=0, ready=None,
                       devices=[{"b300": True, "total_kib": 250 << 20} for _ in range(4)])
            row = {"schema": "nib.deepseek-lab-correctness-result/v2", "status": "BLOCKED",
                   "deployable": False, "confidential": False, "functional_pass": False, "stage": "weights",
                   "hardware": probe.validate_nonconfidential(raw, family="B300"), "application": None,
                   "completion": None, "correctness": None, "startup": None, "engine_stopped": False}
            host.validate_result(row, root, contract, None)
            for changed in (cc_contract, {**contract, "gpu_family": "B200"}):
                with self.assertRaises(ValueError):
                    host.validate_result(row, root, changed, None)

    def test_prepare_cli_profiles_remain_explicit_and_mutually_exclusive(self):
        cases = {
            "--prepare": ("functional", "B200", "confidential"),
            "--prepare-hardware-diagnostic": ("hardware-diagnostic", "B200", "confidential"),
            "--prepare-local-correctness": ("local-correctness", "B200", "confidential"),
            "--prepare-local-correctness-b300": ("local-correctness", "B300", "confidential"),
            "--prepare-pre-serve": ("pre-serve", "B200", "confidential"),
            "--prepare-pre-serve-b300": ("pre-serve", "B300", "confidential"),
            "--prepare-nonconfidential-baseline": ("local-correctness", "B200", "nonconfidential"),
            "--prepare-nonconfidential-baseline-b300": ("local-correctness", "B300", "nonconfidential"),
        }
        for option, profile in cases.items():
            with self.subTest(option=option), mock.patch.object(host, "prepare") as prepare, \
                    mock.patch.object(sys, "argv", [host.SCRIPT, option, "/private/bundle"]):
                self.assertEqual(host.main(), 0)
                prepare.assert_called_once_with("/private/bundle", *profile)
        for args in (["--prepare-nonconfidential-baseline-b300", "/private/bundle", "--prepare", "/other"],
                     ["--prepare-nonconfidential-baseline-b300", "/private/bundle", "--weights", "/weights"]):
            with mock.patch.object(host, "prepare") as prepare, \
                    mock.patch.object(sys, "argv", [host.SCRIPT, *args]), self.assertRaises(ValueError):
                host.main()
            prepare.assert_not_called()

    def test_diagnostic_main_publishes_failed_nvml_observations_with_authoritative_schema(self):
        recorder = self.builder.recorder
        revision = recorder.checker.git(ROOT, "rev-parse", "HEAD").decode().strip()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            env = recorder.offline_environment(root / "control")
            build = root / "build"
            build.mkdir()
            binary, _ = recorder.build_validator(ROOT, revision, build, env)
            files, contract = host.make_payload(self.builder, revision, self.bundle, self.extra,
                                                b"fake-container-binary", "hardware-diagnostic")
            bundle, weights = root / "bundle", root / "empty-weights"
            (bundle / "payload").mkdir(parents=True)
            weights.mkdir()
            (bundle / "payload/probe.py").write_bytes(files["probe.py"])
            receipt = {"git_sha": revision, "source_bundle_sha256": contract["source_bundle_sha256"],
                       "payload_sha256": self.builder.overlay.bundle_digest(files)}
            fixture = DiagnosticTests()
            children = [fixture.child(fixture.diagnose(count=8)),
                        fixture.child(fixture.diagnose(codes={"nvmlDeviceGetCount_v2": 26})),
                        fixture.child(fixture.diagnose()), None]
            children.append({"stage": "bootstrap", "invocation": children[0]["invocation"],
                             "bootstrap": {"stage": "setuptools-source", "source_index": 2}, "probe": None})
            original_publish = recorder.publish_evidence

            def publish(record, report, _container_binary, work, output, _container_env):
                # Only the container boundary is synthetic; run the real native
                # committed-source validator and no-overwrite evidence publisher.
                original_publish(record, report, binary, work, output, env)

            for index, child in enumerate(children):
                output = root / ("published-" + str(index))
                experiment = {"schema": "nib.deepseek-hardware-diagnostic/v1", "status": "BLOCKED",
                              "deployable": False, "confidential": False, "functional_pass": False,
                              "stage": "diagnostic", "probe_execution": "timeout" if child is None else "completed",
                              "child": child}
                host.validate_result(experiment, bundle, contract, recorder)
                report = {"result": "fail", "status": "BLOCKED", "deployable": False, "confidential": False,
                          "functional_pass": False, "stage": "completed", "cleanup": "confirmed", "experiment": experiment}
                deadline = (host.dt.datetime.now(host.dt.timezone.utc) + host.dt.timedelta(seconds=240)).strftime("%Y-%m-%dT%H:%M:%SZ")
                argv = [host.__file__, "--run", str(bundle), "--receipt-sha256", "a" * 64, "--weights", str(weights),
                        "--output", str(output), "--deadline", deadline]
                with self.subTest(index=index), mock.patch.object(host.sys, "argv", argv), \
                        mock.patch.object(host.sys, "platform", "linux"), \
                        mock.patch.object(host, "bundle_inputs", return_value=(receipt, contract, {}, recorder)), \
                        mock.patch.object(host, "run_container", return_value=report), \
                        mock.patch.object(host.signal, "signal"), mock.patch("builtins.print"), \
                        mock.patch.object(recorder, "publish_evidence", side_effect=publish):
                    self.assertEqual(host.main(), 1)
                record = json.loads((output / "run.json").read_bytes())
                self.assertEqual(record["execution"], "software")
                self.assertEqual(record["result"], "fail")
                self.assertFalse(record["hardware_validated"])
                self.assertIsNone(record["host"]["driver"])
                self.assertIsNone(record["host"]["cuda"])
                self.assertEqual(json.loads((output / "output.log").read_bytes())["experiment"], experiment)
            for index, change in enumerate(({"execution": "hardware"}, {"hardware_validated": True})):
                work = root / ("rejected-" + str(index))
                work.mkdir()
                with self.assertRaises(subprocess.CalledProcessError):
                    original_publish({**record, **change}, report, binary, work, work / "published", env)
                self.assertFalse((work / "published").exists())

    def test_authoritative_application_receipt_reused_not_relaxed(self):
        fixtures = self.fixtures
        fixture = fixtures.ApplicationTests()
        with fixture.wheel() as (_, tree, console, application, measured):
            before = fixtures.app.inspect(fixtures.overlay, tree, application, measured, console)
            receipt = fixtures.app.apply(fixtures.overlay, tree, application,
                                         self.builder.checker.verify_stages(self.bundle), before, measured, console)
        writer_hash = fixtures.overlay.digest(fixtures.overlay.json_bytes(before))
        self.assertEqual(receipt["before_inventory_sha256"], writer_hash)
        self.assertNotEqual(writer_hash, measured["packaging"]["inventory_sha256"])
        actual = json.loads(self.files["application/contract.json"])
        measured = self.builder.application.measurement(self.builder.overlay, self.files["application/measurement.json"])
        receipt.update(git_sha=actual["git_sha"], source_bundle_sha256=actual["source_bundle_sha256"],
                       removed_bytecode_files=2879)
        receipt["packaging"].update(removal_plan_sha256=measured["packaging"]["removal_plan_sha256"],
                                    removed_unlisted_bytecode_files=2879)
        row = {"schema": "nib.deepseek-lab-result/v2", "status": "BLOCKED", "deployable": False,
               "confidential": False, "functional_pass": True, "stage": "complete",
               "application": receipt, "hardware": probe.validate(HardwareTests().raw()),
               "completion": {"generated": True, "finish": "stop", "prompt_tokens": 10, "completion_tokens": 1,
                              "final_content_present": True, "synthetic_answer_match": True},
               "startup": {"outcome": "ready", "wait_ms": 1, "exit_code": None, "health_probes": 1,
                           "exception_diagnostic": None,
                           "checkpoints": {"status": "eof", "last_id": 17}},
               "engine_stopped": True}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            for name, data in self.files.items():
                path = root / "payload" / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            path = root / "support" / host.SMOKE
            path.parent.mkdir(parents=True)
            path.write_bytes(self.extra[host.SMOKE])
            host.validate_result(row, root, self.contract, self.builder.recorder)
            pre_contract = {**self.contract, "operation": "pre-serve"}
            pre = {**row, "schema": "nib.deepseek-lab-pre-serve/v1", "entry_pass": True,
                   "functional_pass": False, "completion": None,
                   "startup": {**row["startup"], "outcome": "prepared", "exit_code": 0, "health_probes": 0,
                               "checkpoints": {"status": "eof", "last_id": 16}}}
            host.validate_result(pre, root, pre_contract, self.builder.recorder)
            for key, value in (("functional_pass", True), ("completion", row["completion"]),
                               ("entry_pass", False), ("engine_stopped", False), ("stage", "weights")):
                with self.subTest(pre_field=key), self.assertRaises(ValueError):
                    host.validate_result({**pre, key: value}, root, pre_contract, self.builder.recorder)
            with self.assertRaises(ValueError):
                host.validate_result([], root, pre_contract, self.builder.recorder)
            for section in ("application", "hardware", "completion", "startup"):
                changed = copy.deepcopy(row)
                changed[section]["private"] = "sentinel"
                with self.subTest(section=section), self.assertRaises(ValueError):
                    host.validate_result(changed, root, self.contract, self.builder.recorder)
            for mutation in ({"outcome": "PRIVATE"}, {"exit_code": 0}, {"wait_ms": True},
                             {"wait_ms": -1}, {"wait_ms": host.MAX_SECONDS * 1000 + 1},
                             {"health_probes": True}, {"health_probes": 0},
                             {"outcome": "child-exited", "exit_code": 1}):
                changed = copy.deepcopy(row)
                changed["startup"].update(mutation)
                with self.subTest(startup=mutation), self.assertRaises(ValueError):
                    host.validate_result(changed, root, self.contract, self.builder.recorder)
            for checkpoints in ({"status": "PRIVATE", "last_id": None},
                                {"status": "open", "last_id": 1},
                                {"status": "eof", "last_id": True},
                                {"status": "eof", "last_id": 18},
                                {"status": "eof", "last_id": 0},
                                {"status": "not-started", "last_id": 1},
                                {"status": "eof", "last_id": 17, "private": "sentinel"}):
                changed = copy.deepcopy(row)
                changed["startup"]["checkpoints"] = checkpoints
                with self.subTest(checkpoints=checkpoints), self.assertRaises(ValueError):
                    host.validate_result(changed, root, self.contract, self.builder.recorder)
            for field in ("final_content_present", "synthetic_answer_match"):
                changed = copy.deepcopy(row)
                changed["completion"][field] = False
                with self.subTest(field=field), self.assertRaises(ValueError):
                    host.validate_result(changed, root, self.contract, self.builder.recorder)
            changed = copy.deepcopy(row)
            changed["completion"].update(finish="length", completion_tokens=128)
            with self.assertRaises(ValueError):
                host.validate_result(changed, root, self.contract, self.builder.recorder)
            # Retain a valid token-generation observation, never functional pass.
            changed["functional_pass"] = False
            host.validate_result(changed, root, self.contract, self.builder.recorder)
            correctness = module("scripts/engine_local_correctness.py")
            cases = module("scripts/test_engine_local_correctness.py")
            contract = copy.deepcopy(self.contract)
            contract["operation"] = "local-correctness"
            contract["sources"]["correctness.py"] = hashlib.sha256(self.extra[host.CORRECTNESS]).hexdigest()
            (root / "payload/correctness.py").write_bytes(self.extra[host.CORRECTNESS])
            good = {**row, "schema": "nib.deepseek-lab-correctness-result/v2",
                    "completion": None, "correctness": correctness.run(cases.success, lab.MODEL)}
            host.validate_result(good, root, contract, self.builder.recorder)
            for mutation in ({"correctness": None}, {"completion": row["completion"]},
                             {"engine_stopped": False}, {"schema": row["schema"]}):
                with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                    host.validate_result({**good, **mutation}, root, contract, self.builder.recorder)
            failed = correctness.run(lambda *_: "{}", lab.MODEL)
            with self.assertRaises(ValueError):
                host.validate_result({**good, "correctness": failed}, root, contract, self.builder.recorder)
            host.validate_result({**good, "correctness": failed, "functional_pass": False},
                                 root, contract, self.builder.recorder)
            # An engine startup failure must retain the real application receipt,
            # not disappear behind the outer recorder's validation failure.
            for operation, candidate, input_contract in (
                    ("functional", row, self.contract), ("local-correctness", good, contract)):
                startup_failed = {**candidate, "functional_pass": False, "stage": "startup",
                                  "completion": None, "engine_stopped": True,
                                  "startup": {"outcome": "child-exited", "wait_ms": 1,
                                              "exception_diagnostic": None,
                                              "exit_code": 1, "health_probes": 0,
                                              "checkpoints": {"status": "eof", "last_id": 11}}}
                if operation == "local-correctness":
                    startup_failed["correctness"] = None
                with self.subTest(operation=operation):
                    host.validate_result(startup_failed, root, input_contract, self.builder.recorder)
                    self.assertEqual(startup_failed["application"]["before_inventory_sha256"], writer_hash)
                    for malformed in ("", "0" * 63, "private"):
                        bad = copy.deepcopy(startup_failed)
                        bad["application"]["before_inventory_sha256"] = malformed
                        with self.assertRaises(ValueError):
                            host.validate_result(bad, root, input_contract, self.builder.recorder)


class CheckpointTests(unittest.TestCase):
    def reader(self):
        return lab.EntryCheckpointReader({"status": "not-started", "last_id": None})

    def test_fixed_order_nonblocking_bound_and_no_raw_values(self):
        for data, status, last in ((bytes(range(1, 18)), "eof", 17),
                                   (b"\x01\x02", "eof", 2),
                                   (b"", "eof", None),
                                   (b"\x01PRIVATE", "invalid", 1),
                                   (b"\x02", "invalid", None),
                                   (bytes(range(1, 18)) + b"\x01" * 1000, "overflow", 17)):
            channel = self.reader()
            try:
                channel.observation["status"] = "open"
                os.write(channel.writer, data)
                channel.close_writer()
                with mock.patch.object(lab.os, "read", wraps=os.read) as read:
                    channel.finish()
                self.assertLessEqual(sum(call.args[1] for call in read.call_args_list), 36)
                self.assertLessEqual(read.call_count, 2)
                self.assertEqual(channel.observation, {"status": status, "last_id": last})
                self.assertNotIn("PRIVATE", json.dumps(channel.observation))
            finally:
                channel.finish()

    def test_live_pipe_never_blocks_and_close_is_idempotent(self):
        channel = self.reader()
        child = os.dup(channel.writer)
        try:
            channel.observation["status"] = "open"
            channel.close_writer()
            channel.poll()
            self.assertEqual(channel.observation, {"status": "open", "last_id": None})
            channel.finish()
            self.assertEqual(channel.observation, {"status": "incomplete", "last_id": None})
            channel.finish()
        finally:
            os.close(child)
            channel.finish()

    def test_writer_requires_explicit_write_only_nonblocking_fifo(self):
        channel = self.reader()
        try:
            for argument in ("3x", "03", "-1", "0", "1", "2", "1048576", str(channel.reader), True):
                with self.subTest(argument=argument), self.assertRaises((ValueError, OSError)):
                    lab.EntryCheckpointWriter(argument)
            with tempfile.TemporaryFile() as regular:
                with self.assertRaises(ValueError):
                    lab.EntryCheckpointWriter(str(regular.fileno()))
            os.set_blocking(channel.writer, True)
            with self.assertRaises(ValueError):
                lab.EntryCheckpointWriter(str(channel.writer))
            os.set_blocking(channel.writer, False)
            writer = lab.EntryCheckpointWriter(str(os.dup(channel.writer)))
            try:
                self.assertFalse(os.get_inheritable(writer.fd))
                for bad in (True, 0, 2, 18, "PRIVATE"):
                    with self.subTest(code=bad), self.assertRaises(ValueError):
                        writer.emit(bad)
                writer.emit(1)
                with self.assertRaises(ValueError):
                    writer.emit(1)
            finally:
                writer.close()
        finally:
            channel.finish()

    def test_real_child_inherits_nondefault_fd_but_exec_descendant_does_not(self):
        channel = self.reader()
        self.assertGreater(channel.writer, 3)
        code = """
import os, runpy, subprocess, sys
module = runpy.run_path(sys.argv[1])
def require(value):
    if not value: raise ValueError("fixed rejection")
module["EntryCheckpointWriter"].__init__.__globals__.update(require=require, os=os)
writer = module["EntryCheckpointWriter"](sys.argv[2])
assert not os.get_inheritable(writer.fd)
child = "import os,sys\\ntry: os.fstat(int(sys.argv[1]))\\nexcept OSError: sys.exit(0)\\nsys.exit(1)"
subprocess.run([sys.executable, "-I", "-S", "-B", "-c", child, str(writer.fd)],
               close_fds=False, check=True, timeout=5)
for code in range(1, 18): writer.emit(code)
writer.close()
"""
        try:
            process = subprocess.Popen([sys.executable, "-I", "-S", "-B", "-c", code,
                                        str(ROOT / "scripts/engine_lab.py"), str(channel.writer)],
                                       pass_fds=(channel.writer,), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            channel.observation["status"] = "open"
            channel.close_writer()
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual((process.returncode, stdout, stderr), (0, b"", b""))
            channel.finish()
            self.assertEqual(channel.observation, {"status": "eof", "last_id": 17})
        finally:
            channel.finish()

    def test_engine_boundaries_close_before_serve_and_preserve_first_failure(self):
        for failure in (None, "parse", "validate", "check"):
            channel = self.reader()
            writer = lab.EntryCheckpointWriter(str(os.dup(channel.writer)))
            channel.observation["status"] = "open"
            channel.close_writer()
            writer.emit(1)
            writer.emit(2)
            parser = mock.Mock()
            parser.parse_args.return_value = object()
            serve = mock.Mock()
            def cmd(_):
                self.assertIsNone(writer.fd)
                channel.poll()
                self.assertEqual(channel.observation, {"status": "eof", "last_id": 17})
            serve.cmd.side_effect = cmd
            check = mock.Mock()
            if failure == "parse":
                parser.parse_args.side_effect = ValueError("PRIVATE")
            elif failure == "validate":
                serve.validate.side_effect = ValueError("PRIVATE")
            elif failure == "check":
                check.side_effect = ValueError("PRIVATE")
            def install(*args, checkpoint):
                checkpoint("preconditions", None)
                for index in range(4):
                    checkpoint("setuptools-source", index)
                for stage in ("vendor-root", "install", "complete"):
                    checkpoint(stage, None)
                return {"check": check}
            fake_serve = types.ModuleType("vllm.entrypoints.cli.serve")
            fake_serve.ServeSubcommand = lambda: serve
            fake_parser = types.ModuleType("vllm.utils.argparse_utils")
            fake_parser.FlexibleArgumentParser = lambda: parser
            try:
                with self.subTest(failure=failure), mock.patch.dict(sys.modules, {
                        "engine_lab": lab, "vllm.entrypoints.cli.serve": fake_serve,
                        "vllm.utils.argparse_utils": fake_parser}), \
                        mock.patch.object(lab, "install_lab", install, create=True):
                    if failure is None:
                        lab.run_engine({"paths": ["/stdlib", "/dyn", "/selected"], "arguments": []}, writer)
                        serve.cmd.assert_called_once()
                    else:
                        with self.assertRaises(ValueError):
                            lab.run_engine({"paths": ["/stdlib", "/dyn", "/selected"], "arguments": []}, writer)
                        serve.cmd.assert_not_called()
            finally:
                writer.close()
                channel.finish()
            self.assertEqual(channel.observation["last_id"],
                             {None: 17, "parse": 14, "validate": 15, "check": 16}[failure])

    @mock.patch.object(sys, "flags", types.SimpleNamespace(isolated=True, no_site=True))
    @mock.patch.object(sys, "dont_write_bytecode", True)
    def test_engine_entry_requires_exact_route_and_closes_on_early_failure(self):
        for args in (["engine"], ["engine", "--checkpoint-fd"], ["engine", "3"],
                     ["engine", "--checkpoint-fd", "3", "extra"]):
            with self.subTest(args=args), mock.patch.object(sys, "argv", ["launch.py", "a" * 64, *args]), \
                    self.assertRaises(ValueError):
                lab.lab_entry()
        for args in (["--checkpoint-fd", "4"], ["-c", "--checkpoint-fd", "4"]):
            with self.assertRaises(ValueError):
                helpers.route(args)
        channel = self.reader()
        errors = self.reader()
        child = os.dup(channel.writer)
        error_child = os.dup(errors.writer)
        channel.close_writer()
        errors.close_writer()
        channel.observation["status"] = "open"
        try:
            with mock.patch.object(sys, "argv", ["launch.py", "a" * 64, "engine", "--checkpoint-fd", str(child),
                                                "--error-fd", str(error_child)]), \
                    mock.patch.object(lab, "lab_context", side_effect=ValueError("PRIVATE")):
                with self.assertRaises(ValueError):
                    lab.lab_entry()
            with self.assertRaises(OSError):
                os.fstat(child)
            child = None
            self.assertEqual(lab.read_entry_exception(errors)["type"], "value")
            with self.assertRaises(OSError):
                os.fstat(error_child)
            channel.finish()
            errors.finish()
            self.assertEqual(channel.observation, {"status": "eof", "last_id": 1})
        finally:
            if child is not None:
                try:
                    os.close(child)
                except OSError:
                    pass
            channel.finish()
            errors.finish()

    def test_engine_entry_rejects_each_wrong_flag_before_claiming_fd(self):
        for isolated, no_site, no_bytecode in ((False, True, True), (True, False, True), (True, True, False)):
            with self.subTest(flags=(isolated, no_site, no_bytecode)), \
                    mock.patch.object(sys, "flags", types.SimpleNamespace(isolated=isolated, no_site=no_site)), \
                    mock.patch.object(sys, "dont_write_bytecode", no_bytecode), \
                    mock.patch.object(sys, "argv", ["launch.py", "a" * 64, "engine", "--checkpoint-fd", "17", "--error-fd", "18"]), \
                    mock.patch.object(lab, "EntryCheckpointWriter") as writer, \
                    mock.patch.object(lab, "lab_context") as context:
                with self.assertRaises(ValueError):
                    lab.lab_entry()
                writer.assert_not_called()
                context.assert_not_called()

    @mock.patch.object(sys, "flags", types.SimpleNamespace(isolated=True, no_site=True))
    @mock.patch.object(sys, "dont_write_bytecode", True)
    def test_pre_serve_entry_stops_before_serve_and_closes_both_pipes(self):
        channel, errors = self.reader(), self.reader()
        child, error_child = os.dup(channel.writer), os.dup(errors.writer)
        channel.close_writer()
        errors.close_writer()
        channel.observation["status"] = "open"
        contract = {"operation": "pre-serve", "paths": ["/stdlib", "/dyn", "/selected"]}
        def prepared(actual, writer):
            self.assertIs(actual, contract)
            for code in range(3, 17):
                writer.emit(code)
            return mock.Mock(), object()
        try:
            with mock.patch.object(sys, "argv", ["launch.py", "a" * 64, "engine", "--checkpoint-fd", str(child),
                                                "--error-fd", str(error_child)]), \
                    mock.patch.object(lab, "lab_context", return_value=(contract, {}, [None], {}, {})), \
                    mock.patch.object(lab, "recheck_sources"), \
                    mock.patch.object(lab, "prepare_engine", side_effect=prepared) as prepare, \
                    mock.patch.object(lab, "run_engine") as serve:
                lab.lab_entry()
                prepare.assert_called_once()
                serve.assert_not_called()
            channel.finish()
            self.assertEqual(channel.observation, {"status": "eof", "last_id": 16})
            self.assertIsNone(lab.read_entry_exception(errors))
            for fd in (child, error_child):
                with self.assertRaises(OSError):
                    os.fstat(fd)
        finally:
            channel.finish()
            errors.finish()

    def test_error_pipe_is_bounded_and_never_formats_exceptions(self):
        packet = lab.exception_packet(ValueError("PRIVATE prompt key"), ())
        for raw in (b"", packet, packet[:-1], packet + b"extra", b"x" * len(packet)):
            channel = self.reader()
            os.write(channel.writer, raw)
            channel.close_writer()
            try:
                if raw in (b"", packet):
                    row = lab.read_entry_exception(channel)
                    self.assertNotIn("PRIVATE", json.dumps(row))
                    self.assertEqual(None if row is None else row["type"], None if not raw else "value")
                else:
                    with self.assertRaises(ValueError):
                        lab.read_entry_exception(channel)
            finally:
                channel.finish()

    def test_serve_retains_noninheritable_error_channel(self):
        checkpoints = mock.Mock()
        errors = self.reader()
        writer = lab.EntryCheckpointWriter(str(os.dup(errors.writer)))
        errors.close_writer()
        serve = mock.Mock()
        def call(_):
            self.assertIsNotNone(writer.fd)
            self.assertFalse(os.get_inheritable(writer.fd))
            with self.assertRaises(BlockingIOError):
                lab.read_entry_exception(errors)
        serve.cmd.side_effect = call
        try:
            with mock.patch.object(lab, "prepare_engine", return_value=(serve, object())):
                lab.run_engine({}, checkpoints, writer)
            serve.cmd.assert_called_once()
            checkpoints.close.assert_called_once()
        finally:
            writer.close()
            errors.finish()

    @mock.patch.object(sys, "flags", types.SimpleNamespace(isolated=True, no_site=True))
    @mock.patch.object(sys, "dont_write_bytecode", True)
    def test_exception_from_actual_serve_boundary_is_recorded_without_content(self):
        channel, errors = self.reader(), self.reader()
        child, error_child = os.dup(channel.writer), os.dup(errors.writer)
        channel.close_writer()
        errors.close_writer()
        channel.observation["status"] = "open"
        contract = {"operation": "local-correctness", "paths": ["/stdlib", "/dyn", "/selected"]}
        serve = mock.Mock()
        serve.cmd.side_effect = RuntimeError("PRIVATE model prompt credential")
        def prepared(actual, writer):
            self.assertIs(actual, contract)
            for code in range(3, 17):
                writer.emit(code)
            return serve, object()
        try:
            with mock.patch.object(sys, "argv", ["launch.py", "a" * 64, "engine", "--checkpoint-fd", str(child),
                                                "--error-fd", str(error_child)]), \
                    mock.patch.object(lab, "lab_context", return_value=(contract, {}, [None], {}, {})), \
                    mock.patch.object(lab, "recheck_sources"), \
                    mock.patch.object(lab, "prepare_engine", side_effect=prepared):
                with self.assertRaises(RuntimeError):
                    lab.lab_entry()
            row = lab.read_entry_exception(errors)
            self.assertEqual(row["type"], "runtime")
            self.assertNotIn("PRIVATE", json.dumps(row))
            channel.finish()
            self.assertEqual(channel.observation, {"status": "eof", "last_id": 17})
            for fd in (child, error_child):
                with self.assertRaises(OSError):
                    os.fstat(fd)
        finally:
            channel.finish()
            errors.finish()


class LifecycleTests(unittest.TestCase):
    def test_pre_serve_waits_without_http_and_requires_complete_checkpoints(self):
        for code, last in ((0, 16), (1, 12), (0, 15), (0, 17)):
            engine = mock.Mock()
            engine.wait.return_value = code
            observation = {"outcome": "launch-failed", "wait_ms": 0, "exit_code": None, "health_probes": 0}
            checkpoints = mock.Mock(observation={"status": "eof", "last_id": last})
            with mock.patch.object(lab, "http_request") as http:
                if (code, last) == (0, 16):
                    lab.observe_pre_serve(engine, observation, checkpoints)
                    self.assertEqual(observation["outcome"], "prepared")
                else:
                    with self.assertRaises(ValueError):
                        lab.observe_pre_serve(engine, observation, checkpoints)
                    self.assertEqual(observation["outcome"], "child-exited")
                http.assert_not_called()
            engine.wait.assert_called_once_with(timeout=300)

    def test_pre_serve_lifecycle_cannot_check_weights_or_send_requests(self):
        @contextmanager
        def selected(*_):
            yield types.SimpleNamespace(deadline=0), (None, types.SimpleNamespace(deadline=0))
        app = types.SimpleNamespace(verify_layout=lambda *_: None, selected_tree=selected,
                                    inspect=lambda *_: {}, apply=lambda *_: {"stages": 5})
        for code in (0, 1):
            engine = mock.Mock(pid=99999999)
            engine.wait.return_value = engine.poll.return_value = code
            def start(argv, **kwargs):
                os.write(int(argv[3]), bytes(range(1, 17 if code == 0 else 13)))
                if code:
                    os.write(int(argv[5]), lab.exception_packet(ValueError("PRIVATE"), ()))
                return engine
            with mock.patch.object(lab, "hardware", return_value={"fixed": True}), \
                    mock.patch.object(lab, "quiet_command") as weights, \
                    mock.patch.object(lab, "http_request") as http, \
                    mock.patch.object(lab.subprocess, "Popen", side_effect=start), \
                    mock.patch.object(lab.os, "killpg"):
                row = lab.application_and_request({"operation": "pre-serve"}, {},
                                                  [None, None, None, app], {"files": []}, {})
                weights.assert_not_called()
                http.assert_not_called()
            self.assertEqual(row["entry_pass"], code == 0)
            self.assertFalse(row["functional_pass"])
            self.assertIsNone(row["completion"])
            self.assertTrue(row["engine_stopped"])
            self.assertNotIn("PRIVATE", json.dumps(row))

    def test_startup_observations_are_bounded_and_content_free(self):
        for scenario in ("ready", "exit", "signal", "timeout", "models", "supervisor"):
            observation = {"outcome": "launch-failed", "wait_ms": 0,
                           "exit_code": None, "health_probes": 0}
            engine = mock.Mock()
            engine.poll.return_value = {"exit": 1, "signal": -9}.get(scenario)
            if scenario == "supervisor":
                engine.poll.side_effect = OSError("PRIVATE")
            clock = iter([0, lab.STARTUP_SECONDS, lab.STARTUP_SECONDS]) if scenario == "timeout" else iter([0, 0, 0.25])
            body = b"PRIVATE" if scenario == "models" else json.dumps({"data": [{"id": lab.MODEL}]}).encode()
            with self.subTest(scenario=scenario), mock.patch.object(lab.time, "monotonic", side_effect=lambda: next(clock)), \
                    mock.patch.object(lab, "http_request", side_effect=[b"ok", body]):
                if scenario == "ready":
                    lab.observe_startup(engine, observation)
                else:
                    with self.assertRaises((ValueError, OSError)):
                        lab.observe_startup(engine, observation)
            self.assertEqual(observation["outcome"], {
                "ready": "ready", "exit": "child-exited", "signal": "child-exited",
                "timeout": "startup-timeout", "models": "models-rejected",
                "supervisor": "supervisor-failure"}[scenario])
            self.assertEqual(observation["exit_code"], {"exit": 1, "signal": -9}.get(scenario))
            self.assertNotIn("PRIVATE", json.dumps(observation))

    def test_request_and_shutdown_terminal_states(self):
        @contextmanager
        def selected(*_):
            yield types.SimpleNamespace(deadline=0), (None, types.SimpleNamespace(deadline=0))
        application = {"files": []}
        app = types.SimpleNamespace(verify_layout=lambda *_: None, selected_tree=selected,
                                    inspect=lambda *_: {}, apply=lambda *_: {"stages": 5})
        response = {"object": "chat.completion", "model": lab.MODEL,
                    "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "2"}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11}}
        for terminal in (0, -signal.SIGTERM, 1, "timeout", "request-error", "dead-worker", "reasoning-only", "wrong-answer", "truncated-answer"):
            engine = mock.Mock(pid=99999999)
            engine.poll.return_value = 1 if terminal == "dead-worker" else None
            engine.wait.side_effect = ([subprocess.TimeoutExpired("fixed", 60), -9] if terminal == "timeout"
                                       else [terminal if type(terminal) is int else 0])
            def http(path, **kwargs):
                if path == "/health": return b"ok"
                if path == "/v1/models": return json.dumps({"data": [{"id": lab.MODEL}]}).encode()
                if terminal == "request-error": raise OSError("private upstream failure")
                body = copy.deepcopy(response)
                if terminal == "reasoning-only":
                    body["choices"][0].update(finish_reason="length", message={"role": "assistant", "content": None,
                                                                            "reasoning": "synthetic-private"})
                elif terminal == "wrong-answer":
                    body["choices"][0]["message"]["content"] = "3"
                elif terminal == "truncated-answer":
                    body["choices"][0]["finish_reason"] = "length"
                    body["usage"].update(completion_tokens=128, total_tokens=138)
                return json.dumps(body).encode()
            with self.subTest(terminal=terminal), mock.patch.object(lab, "hardware", return_value={"fixed": True}), \
                    mock.patch.object(lab, "quiet_command"), mock.patch.object(lab, "http_request", side_effect=http), \
                    mock.patch.object(lab.subprocess, "Popen", return_value=engine) as start, mock.patch.object(lab.os, "killpg"), \
                    mock.patch.object(lab, "read_entry_exception", wraps=lab.read_entry_exception) as diagnostic:
                row = lab.application_and_request({"operation": "functional"}, {},
                                                  [None, None, None, app], application, {})
                if terminal != "dead-worker":
                    diagnostic.assert_not_called()
            argv = start.call_args.args[0]
            self.assertEqual(argv[:3], [lab.ROOT + "/python", "engine", "--checkpoint-fd"])
            self.assertEqual(argv[4], "--error-fd")
            self.assertEqual(start.call_args.kwargs["pass_fds"], (int(argv[3]), int(argv[5])))
            with self.assertRaises(OSError):
                os.fstat(int(argv[5]))
            with self.assertRaises(OSError):
                os.fstat(int(argv[3]))
            self.assertEqual(row["functional_pass"], terminal in (0, -signal.SIGTERM))
            self.assertNotIn("private upstream failure", json.dumps(row))
            self.assertNotIn("synthetic-private", json.dumps(row))
            if terminal in ("reasoning-only", "wrong-answer"):
                self.assertTrue(row["completion"]["generated"])
                self.assertFalse(row["completion"]["synthetic_answer_match"])
            if terminal == "truncated-answer":
                self.assertTrue(row["completion"]["synthetic_answer_match"])
                self.assertTrue(row["completion"]["generated"])
                self.assertEqual(row["completion"]["finish"], "length")
            self.assertEqual(row["startup"]["outcome"], "child-exited" if terminal == "dead-worker" else "ready")
            self.assertEqual(row["startup"]["exit_code"], 1 if terminal == "dead-worker" else None)
            engine.wait.assert_called()

    def test_correctness_runs_inside_existing_lifecycle_and_source_failure_stops_startup(self):
        @contextmanager
        def selected(*_):
            yield types.SimpleNamespace(deadline=0), (None, types.SimpleNamespace(deadline=0))
        app = types.SimpleNamespace(verify_layout=lambda *_: None, selected_tree=selected,
                                    inspect=lambda *_: {}, apply=lambda *_: {"stages": 5})
        correctness = module("scripts/engine_local_correctness.py")
        cases = module("scripts/test_engine_local_correctness.py")
        contract = {"operation": "local-correctness", "paths": ["/selected"]}
        for source_ok in (True, False):
            engine = mock.Mock(pid=99999999)
            engine.poll.return_value = None
            engine.wait.return_value = 0
            def http(path, **kwargs):
                if path == "/health":
                    return b"ok"
                if path == "/v1/models":
                    return json.dumps({"data": [{"id": lab.MODEL}]}).encode()
                raise AssertionError("unexpected endpoint")
            with self.subTest(source_ok=source_ok), \
                    mock.patch.object(lab, "CORRECTNESS", correctness), \
                    mock.patch.object(correctness, "verify_sources", side_effect=None if source_ok else ValueError()), \
                    mock.patch.object(lab, "read_source", create=True), \
                    mock.patch.object(lab, "hardware", return_value={"fixed": True}), \
                    mock.patch.object(lab, "quiet_command"), \
                    mock.patch.object(lab, "http_request", side_effect=http), \
                    mock.patch.object(lab, "correctness_request", side_effect=cases.success) as request, \
                    mock.patch.object(lab.subprocess, "Popen", return_value=engine) as start, \
                    mock.patch.object(lab.os, "killpg"):
                row = lab.application_and_request(contract, {}, [None, None, None, app], {"files": []}, {})
            self.assertEqual(row["functional_pass"], source_ok)
            self.assertEqual(row["engine_stopped"], source_ok)
            self.assertIsNone(row["completion"])
            self.assertEqual(start.call_count, int(source_ok))
            self.assertEqual(request.call_count, 10 if source_ok else 0)
            if source_ok:
                self.assertTrue(row["correctness"]["all_cases_pass"])
                self.assertFalse(row["correctness"]["tau_acceptance"])
            else:
                self.assertIsNone(row["correctness"])

    def test_correctness_request_restores_outer_watchdog_on_success_and_failure(self):
        for failure in (None, TimeoutError("request deadline")):
            with self.subTest(failure=failure), \
                    mock.patch.object(lab.time, "monotonic", side_effect=[100.0, 101.0]), \
                    mock.patch.object(lab.signal, "getsignal", return_value="outer"), \
                    mock.patch.object(lab.signal, "getitimer", return_value=(30.0, 0.0)), \
                    mock.patch.object(lab.signal, "signal") as handler, \
                    mock.patch.object(lab.signal, "setitimer") as timer, \
                    mock.patch.object(lab, "http_request", return_value=b"ok", side_effect=failure):
                if failure is None:
                    self.assertEqual(lab.correctness_request({}, 105.0), b"ok")
                else:
                    with self.assertRaises(TimeoutError):
                        lab.correctness_request({}, 105.0)
            self.assertEqual(timer.call_args_list, [
                mock.call(signal.ITIMER_REAL, 5.0), mock.call(signal.ITIMER_REAL, 0),
                mock.call(signal.ITIMER_REAL, 29.0)])
            self.assertEqual(handler.call_args_list[-1], mock.call(signal.SIGALRM, "outer"))

    def test_owned_cleanup_absence_and_foreign_owner(self):
        recorder = types.SimpleNamespace(call=mock.Mock(side_effect=[b"exists", b"removed", b""]))
        with mock.patch.object(host, "inspect", return_value={"Config": {"Labels": {host.LABEL: "owner"}}}):
            host.cleanup(recorder, "name", "owner", {})
        self.assertIn("rm", recorder.call.call_args_list[1].args[0])
        recorder.call = mock.Mock(return_value=b"exists")
        with mock.patch.object(host, "inspect", return_value={"Config": {"Labels": {host.LABEL: "foreign"}}}):
            with self.assertRaises(ValueError):
                host.cleanup(recorder, "name", "owner", {})
        self.assertEqual(recorder.call.call_count, 1)

    def test_cancel_and_uncertain_create_never_cleanup_success(self):
        recorder = types.SimpleNamespace(image_binding=lambda *_: {"fixed": True},
                                         call=mock.Mock(side_effect=[b"", subprocess.TimeoutExpired("docker", 30)]))
        monitor = mock.Mock(returncode=0)
        monitor.poll.return_value = None
        contract = {"base_image": "fixed"}
        with mock.patch.object(host, "CANCELLED", False), mock.patch.object(host, "CLEANING", False), \
                mock.patch.object(host, "inspect", return_value={"Id": "fixed"}), \
                mock.patch.object(host.subprocess, "Popen", return_value=monitor), \
                mock.patch.object(host, "create_command", return_value=["docker", "create"]), \
                mock.patch.object(host, "cleanup") as cleanup:
            row = host.run_container("/unused", "a"*64, "/unused", time.time()+60,
                                     recorder, contract, {}, host.controlled_env("unix:///var/run/docker.sock"))
        self.assertEqual(row["cleanup"], "create-completion-unknown")
        self.assertFalse(row["functional_pass"])
        cleanup.assert_called_once()
        monitor.wait.assert_called_once()
        with mock.patch.object(host, "CANCELLED", True), mock.patch.object(host, "CLEANING", False), \
                mock.patch.object(host, "inspect", return_value={"Id": "fixed"}), \
                mock.patch.object(host.subprocess, "Popen") as start:
            row = host.run_container("/unused", "a"*64, "/unused", time.time()+60,
                                     recorder, contract, {}, host.controlled_env("unix:///var/run/docker.sock"))
            start.assert_not_called()
        self.assertEqual(row["cleanup"], "not-created")

    def test_outer_experiment_failures_preserve_only_fixed_observations_before_cleanup(self):
        identifier, owner = "a" * 64, "b" * 32
        for failure in ("attach", "decode", "terminal", "validation", "none", "unavailable"):
            final = {"Id": identifier, "Config": {"Labels": {host.LABEL: owner}},
                     "State": {"Status": "exited", "ExitCode": 137 if failure == "terminal" else 0,
                               "OOMKilled": failure == "terminal", "Error": "private sentinel"}}
            initial = {**final, "State": {"Status": "created"}}
            raw = b'{"functional_pass":false}' if failure != "decode" else b"private sentinel"
            attach = subprocess.CalledProcessError(1, "private sentinel") if failure == "attach" else raw
            recorder = types.SimpleNamespace(image_binding=lambda *_: {"fixed": True},
                call=mock.Mock(side_effect=[b"", identifier.encode(), attach]))
            monitor = mock.Mock(returncode=0)
            monitor.poll.return_value = None
            inspections = [{"Id": "image"}, initial, final]
            if failure == "unavailable":
                inspections = [{"Id": "image"}, initial, ValueError("private sentinel"),
                               ValueError("private sentinel")]
            events = []
            def inspect(*_):
                events.append("inspect")
                value = inspections.pop(0)
                if isinstance(value, Exception):
                    raise value
                return value
            with self.subTest(failure=failure), mock.patch.object(host, "CANCELLED", False), \
                    mock.patch.object(host, "CLEANING", False), \
                    mock.patch.object(host.uuid, "uuid4", return_value=types.SimpleNamespace(hex=owner)), \
                    mock.patch.object(host, "inspect", side_effect=inspect), \
                    mock.patch.object(host.subprocess, "Popen", return_value=monitor), \
                    mock.patch.object(host, "create_command", return_value=["docker", "create"]), \
                    mock.patch.object(host, "check_container"), \
                    mock.patch.object(host, "validate_result",
                        side_effect=ValueError("private sentinel") if failure == "validation" else None), \
                    mock.patch.object(host, "cleanup", side_effect=lambda *_: events.append("cleanup")):
                row = host.run_container("/unused", "a" * 64, "/unused", time.time() + 60,
                    recorder, {"base_image": "fixed"}, {}, host.controlled_env("unix:///var/run/docker.sock"))
            expected = "completed" if failure == "none" else ("terminal" if failure == "unavailable" else failure)
            self.assertEqual(row["experiment_step"], expected)
            self.assertFalse(row["functional_pass"])
            self.assertEqual(row["cleanup"], "confirmed")
            self.assertEqual(events[-1], "cleanup")
            self.assertEqual(row["output_bytes"], None if failure == "attach" else len(raw))
            if failure == "unavailable":
                self.assertIsNone(row["container_observation"])
            else:
                self.assertEqual(row["container_observation"],
                    {"status": "exited", "exit_code": 137 if failure == "terminal" else 0,
                     "oom_killed": failure == "terminal"})
            self.assertEqual("experiment" in row, failure == "none")
            self.assertNotIn("private sentinel", json.dumps(row))

    def test_container_observations_reject_foreign_or_malformed_state(self):
        base = {"Id": "fixed", "Config": {"Labels": {host.LABEL: "owner"}},
                "State": {"Status": "exited", "ExitCode": 137, "OOMKilled": False}}
        self.assertFalse(host.container_observation(base, "fixed", "owner")["oom_killed"])
        for state in ({"Status": "private"}, {"ExitCode": True}, {"ExitCode": 256},
                      {"OOMKilled": "false"}, {"ExitCode": -129}):
            with self.subTest(state=state), self.assertRaises(ValueError):
                host.container_observation({**base, "State": {**base["State"], **state}}, "fixed", "owner")
        with self.assertRaises(ValueError):
            host.container_observation(base, "different", "owner")
        with self.assertRaises(ValueError):
            host.container_observation(base, "fixed", "foreign")


class ReportTests(unittest.TestCase):
    def test_final_answer_is_distinct_from_reasoning_token_generation(self):
        row = {"object": "chat.completion", "model": lab.MODEL,
               "choices": [{"index": 0, "finish_reason": "length", "message": {"role": "assistant"}}],
               "usage": {"prompt_tokens": 10, "completion_tokens": 128, "total_tokens": 138}}
        for content, present, matches in ((None, False, False), ("", False, False), (" \n", False, False),
                                          ("3", True, False), ("The answer is 2.", True, False),
                                          ("2", True, True), ("\n2\n", True, True)):
            row["choices"][0]["message"] = {"role": "assistant", "content": content,
                                          "reasoning": "private reasoning sentinel"}
            with self.subTest(content=content):
                result = lab.completion_result(json.dumps(row))
                self.assertTrue(result["generated"])
                self.assertEqual((result["final_content_present"], result["synthetic_answer_match"]), (present, matches))
                self.assertNotIn("private reasoning sentinel", json.dumps(result))

    def test_completion_no_content_retained_and_strict_bounds(self):
        row = {"object": "chat.completion", "model": lab.MODEL,
               "choices": [{"index": 0, "finish_reason": "length", "message": {"role": "assistant", "content": "synthetic-private"}}],
               "usage": {"prompt_tokens": 10, "completion_tokens": 128, "total_tokens": 138}}
        safe = lab.completion_result(json.dumps(row))
        self.assertNotIn("synthetic-private", json.dumps(safe))
        for change in ({"error": "private"}, {"usage": {"prompt_tokens": 10, "completion_tokens": 129, "total_tokens": 139}},
                       {"choices": []}, {"model": "private"}):
            with self.assertRaises(ValueError):
                lab.completion_result(json.dumps({**row, **change}))

    def test_failure_report_cannot_smuggle_nested_private_data(self):
        row = {"schema": "nib.deepseek-lab-result/v2", "status": "BLOCKED", "deployable": False,
               "confidential": False, "functional_pass": False, "stage": "hardware",
               "application": None, "hardware": None, "completion": None,
               "startup": None, "engine_stopped": False}
        host.validate_result(row, Path("/unused"), {}, None)
        for update in ({"completion": {"private": "sentinel"}}, {"private": "sentinel"},
                       {"stage": "sentinel"}, {"functional_pass": True}, {"engine_stopped": 1}):
            with self.subTest(update=update), self.assertRaises((ValueError, KeyError, TypeError)):
                host.validate_result({**row, **update}, Path("/unused"), {}, None)

    def test_unexpected_args_do_not_echo_private_input(self):
        result = subprocess.run([sys.executable, "-I", "-S", str(ROOT / host.SCRIPT), "--private-sentinel"],
                                capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(b"private-sentinel", result.stdout + result.stderr)

    def test_finite_isolated_container_plan_no_host_services(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "payload").mkdir()
            (root / "payload/lab.json").write_text("{}")
            args = host.create_command("nib-deepseek-" + "a"*32, "b"*32, root, "/weights",
                                       {"base_image": "pinned", "environment": {"VLLM_PLUGINS": ""}, "operation": "functional"},
                                       {"config": {"Env": ["SECRET_IMAGE_DEFAULT=unused"]}})
        for flag, value in (("--network", "none"), ("--pull", "never"), ("--ipc", "private"),
                            ("--shm-size", "2g"), ("--memory", "768g"), ("--pids-limit", "1024")):
            self.assertEqual(args[args.index(flag) + 1], value)
        self.assertNotIn("--publish", args)
        self.assertNotIn("--privileged", args)
        self.assertIn("SECRET_IMAGE_DEFAULT=", args)
        self.assertIn("-S", args)
        self.assertIn("-B", args)
        self.assertEqual(args[-1], "apply")

    def test_verified_nvidia_constraints_survive_creation_and_inspection(self):
        config = {"config": {"Env": ["NVIDIA_REQUIRE_CUDA=cuda>=13.0 driver>=580",
                                    "NVIDIA_REQUIRE_BRAND=brand=tesla", "NVIDIA_DISABLE_REQUIRE=true",
                                    "UNRELATED=private-sentinel"]}}
        contract = {"base_image": "pinned", "environment": {"VLLM_PLUGINS": ""}, "operation": "functional"}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "payload").mkdir()
            (root / "payload/lab.json").write_text("{}")
            args = host.create_command("owned", "owner", root, "/weights", contract, config)
            env = [args[i + 1] for i, arg in enumerate(args) if arg == "--env"]
            self.assertIn("NVIDIA_REQUIRE_CUDA=cuda>=13.0 driver>=580", env)
            self.assertIn("NVIDIA_REQUIRE_BRAND=brand=tesla", env)
            self.assertIn("NVIDIA_DISABLE_REQUIRE=", env)
            self.assertIn("UNRELATED=", env)
            application_args = args[args.index("pinned") + 1:]
            self.assertEqual(application_args[0], "-i")
            self.assertNotIn("NVIDIA_REQUIRE_CUDA=cuda>=13.0 driver>=580", application_args)
            info = {
                "Image": "image-id",
                "Config": {"Image": "pinned", "Labels": {host.LABEL: "owner"}, "Entrypoint": ["/usr/bin/env"],
                           "Cmd": application_args, "User": "0:0", "WorkingDir": "/", "Env": env,
                           "Healthcheck": {"Test": ["NONE"]}},
                "HostConfig": {"NetworkMode": "none", "Privileged": False, "Runtime": "nvidia",
                               "ReadonlyRootfs": False, "CapDrop": ["ALL"], "IpcMode": "private",
                               "ShmSize": 2 << 30, "CgroupnsMode": "private", "Memory": 768 << 30,
                               "MemorySwap": 768 << 30, "NanoCpus": 64000000000, "PidsLimit": 1024,
                               "RestartPolicy": {"Name": "no"}, "LogConfig": {"Type": "none"},
                               "SecurityOpt": ["no-new-privileges"],
                               "Tmpfs": {"/cache": "rw,nosuid,nodev,size=16g,mode=0700",
                                         "/tmp": "rw,nosuid,nodev,noexec,size=256m,mode=1777"},
                               "DeviceRequests": [{"DeviceIDs": ["0", "1", "2", "3"], "Capabilities": [["gpu"]],
                                                   "Count": 0, "Driver": "nvidia"}]},
                "Mounts": [{"Type": "bind", "Source": str(root / "payload"), "Destination": "/opt/nib", "RW": False},
                           {"Type": "bind", "Source": "/weights", "Destination": "/model", "RW": False}],
            }
            host.check_container(info, "owned", "owner", root, "/weights", contract, config, "image-id")
            for key in ("NVIDIA_REQUIRE_CUDA", "NVIDIA_REQUIRE_BRAND"):
                original = next(value for value in env if value.startswith(key + "="))
                for replacement in (None, key + "=", key + "=changed"):
                    changed = copy.deepcopy(info)
                    changed["Config"]["Env"].remove(original)
                    if replacement is not None:
                        changed["Config"]["Env"].append(replacement)
                    with self.subTest(key=key, replacement=replacement), self.assertRaises(ValueError):
                        host.check_container(changed, "owned", "owner", root, "/weights", contract, config, "image-id")


def public_source_audit(path):
    raw = Path(path).read_bytes()
    if len(raw) != 22427 or hashlib.sha256(raw).hexdigest() != bootstrap.VECISA_SOURCE_SHA:
        raise ValueError("public VecISA source rejected")
    source = types.SimpleNamespace(require=helpers.require, read_source=lambda *_: raw)
    parsed = ast.parse(raw)
    klass = next(n for n in parsed.body if isinstance(n, ast.ClassDef) and n.name == "VecISA")
    template = next(n.value.value for n in klass.body if isinstance(n, ast.Assign)
                    and isinstance(n.targets[0], ast.Name) and n.targets[0].id == "_avx_py_load")
    path = "/cache/inductor/ab/source-bound.so"
    layout = {"selected_root": "/selected", "paths": ["/selected", "/selected/setuptools/_vendor"]}
    assert bootstrap.vecisa_library(["-c", template.replace("__lib_path__", path)], layout, source) == path
    print("Pinned Torch VecISA source/template: PASS (compile/parse only; no upstream execution).")


def public_setuptools_audit(path):
    for name, sha in bootstrap.SETUPTOOLS_SOURCES.items():
        raw = (Path(path) / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != sha or len(raw) > 32768:
            raise ValueError("public setuptools source rejected")
        compile(raw, name, "exec", flags=0, dont_inherit=True, optimize=0)
        tree = ast.parse(raw)
        if name == "_distutils_hack/__init__.py":
            functions = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
            assert {"DistutilsMetaFinder", "insert_shim", "_remove_shim", "add_shim", "shim"} <= functions
            shim = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "shim")
            calls = {n.func.id for n in ast.walk(shim) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
            assert calls == {"insert_shim", "_remove_shim"}
    print("Pinned setuptools 80.10.2: four sources PASS (compile/parse only; no upstream execution).")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--public-vecisa":
        public_source_audit(sys.argv[2])
    elif len(sys.argv) == 3 and sys.argv[1] == "--public-setuptools":
        public_setuptools_audit(sys.argv[2])
    else:
        unittest.main()
