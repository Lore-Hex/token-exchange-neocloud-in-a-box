# SPDX-License-Identifier: Apache-2.0
"""Synthetic CuTe import contract; no proprietary source or native execution."""

import ast
import hashlib
import subprocess
import unittest
from unittest import mock

import test_deepseek_lab as lab


SOURCE = '''import os, sys
_SCRIPT_PATH = os.path.dirname(os.path.abspath(__file__))
sys.path.append(_SCRIPT_PATH)
from .sibling import VALUE
def compile_options(value):
    return (VALUE, value)
'''
POSITIVE = '''    from cutlass.base_dsl import compiler
    assert compiler.compile_options("options") == ("relative-export", "options")
    assert sys.path == LAYOUT["paths"]
    assert sys.meta_path == STATE["finders"]
    STATE["check"]()
'''


class CutlassTests(unittest.TestCase):
    setUp = lab.BootstrapTests.setUp
    run_fixture = lab.BootstrapTests.run_fixture

    def fixture(self, body=POSITIVE, source=SOURCE):
        root = self.package / "nvidia_cutlass_dsl/dsl_packages"
        package = root / "cutlass/base_dsl"
        package.mkdir(parents=True, exist_ok=True)
        (root / "cutlass/__init__.py").write_text("")
        (package / "__init__.py").write_text("")
        (package / "sibling.py").write_text('VALUE = "relative-export"\n')
        self.compiler = package / "compiler.py"
        self.compiler.write_text(source)
        dist = self.package / "nvidia_cutlass_dsl_libs_core-4.8.0.dist-info"
        dist.mkdir(exist_ok=True)
        self.metadata = dist / "METADATA"
        self.metadata.write_text("Name: nvidia-cutlass-dsl-libs-core\nVersion: 4.8.0\n")
        if str(root) not in self.layout["paths"]:
            self.layout["paths"].insert(-1, str(root))
        (self.root / "layout.json").write_text(lab.json.dumps(self.layout))
        self.cutlass_source = self.source + ("\nCUTLASS_SOURCE_SHA="
            + repr(hashlib.sha256(source.encode()).hexdigest()) + "\n").encode()
        self.driver = lab.DRIVER.replace("    import fake_engine", "    import fake_engine\n" + body)
        (self.root / "launch.py").write_bytes(self.cutlass_source + self.driver.encode())

    def rejected(self):
        result = subprocess.run([self.layout["wrapper"], "fresh"], cwd="/", capture_output=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(b"private-body-ran", result.stdout + result.stderr)

    def test_preserves_relative_exports_parent_spawn_grandchild_and_fresh(self):
        self.fixture()
        self.assertTrue(self.run_fixture("positive")["tracker"])
        self.assertFalse(list(self.root.rglob("*.pyc")))

    def test_original_append_reproduces_path_guard_rejection(self):
        self.fixture('''    global cutlass_compiler_code
    cutlass_compiler_code = lambda raw, path, require: compile(raw, path, "exec")
    try: from cutlass.base_dsl import compiler
    except ValueError: pass
    else: raise AssertionError("unadapted path append accepted")
    assert sys.path != LAYOUT["paths"]
    print('{"rejected":true}')
    raise SystemExit(0)
''')
        self.assertTrue(self.run_fixture("fresh")["rejected"])

    def test_only_exact_top_level_append_is_changed(self):
        raw = SOURCE.encode()
        with mock.patch.object(lab.bootstrap, "CUTLASS_SOURCE_SHA", hashlib.sha256(raw).hexdigest()):
            with mock.patch.object(lab.bootstrap, "compile", create=True) as compiler:
                lab.bootstrap.cutlass_compiler_code(raw, "compiler.py", lab.helpers.require)
            self.assertEqual(ast.dump(compiler.call_args.args[0]),
                             ast.dump(ast.parse(SOURCE.replace("sys.path.append(_SCRIPT_PATH)", "pass"))))
            with self.assertRaises(ValueError):
                lab.bootstrap.cutlass_compiler_code(raw + b"\n", "compiler.py", lab.helpers.require)
        for source in (SOURCE.replace("sys.path.append(_SCRIPT_PATH)", "pass"),
                       SOURCE + "sys.path.append(_SCRIPT_PATH)\n",
                       SOURCE.replace("sys.path.append(_SCRIPT_PATH)", "def later():\n    sys.path.append(_SCRIPT_PATH)")):
            raw = source.encode()
            with self.subTest(source=source), mock.patch.object(lab.bootstrap, "CUTLASS_SOURCE_SHA", hashlib.sha256(raw).hexdigest()):
                with self.assertRaises(ValueError):
                    lab.bootstrap.cutlass_compiler_code(raw, "compiler.py", lab.helpers.require)

    def test_source_and_distribution_drift_rejected_before_execution(self):
        self.fixture()
        self.compiler.write_text("print('private-body-ran')\n")
        self.rejected()
        self.compiler.write_text(SOURCE)
        self.metadata.write_text("Name: nvidia-cutlass-dsl-libs-core\nVersion: 4.9.0\n")
        self.rejected()

    def test_wrong_origin_reload_and_unrelated_path_rejected(self):
        self.fixture('''    import cutlass.base_dsl
    cutlass.base_dsl.__path__[:] = [str(os.path.dirname(__file__))]
    from cutlass.base_dsl import compiler
''')
        (self.root / "compiler.py").write_text("print('private-body-ran')\n")
        self.rejected()
        self.fixture(POSITIVE + '''    import importlib
    try: importlib.reload(compiler)
    except ValueError: pass
    else: raise AssertionError("reload accepted")
    sys.path.append(compiler._SCRIPT_PATH)
    try: STATE["check"]()
    except ValueError: pass
    else: raise AssertionError("arbitrary append accepted")
    print('{"rejected":true}')
    raise SystemExit(0)
''')
        self.assertTrue(self.run_fixture("fresh")["rejected"])

    def test_cached_bytecode_does_not_replace_pinned_source(self):
        import importlib.util
        import marshal
        import struct
        self.fixture()
        cache = lab.Path(importlib.util.cache_from_source(str(self.compiler)))
        cache.parent.mkdir(exist_ok=True)
        cache.write_bytes(importlib.util.MAGIC_NUMBER
                          + struct.pack("<III", 0, int(self.compiler.stat().st_mtime), self.compiler.stat().st_size)
                          + marshal.dumps(compile("raise RuntimeError('private cached code')", str(self.compiler), "exec")))
        self.assertTrue(self.run_fixture("fresh")["paths"])

    def test_preloaded_compiler_rejected_before_install(self):
        self.fixture()
        preload = b'\nsys.modules[CUTLASS_COMPILER] = types.ModuleType(CUTLASS_COMPILER)\n'
        (self.root / "launch.py").write_bytes(self.cutlass_source + preload + self.driver.encode())
        self.rejected()


def public_source_audit(path):
    raw = lab.Path(path).read_bytes()
    lab.helpers.require(len(raw) <= 128 << 10)
    # Compile but never execute proprietary source or its imports.
    lab.bootstrap.cutlass_compiler_code(raw, "pinned-cutlass-compiler.py", lab.helpers.require)
    with mock.patch.object(lab.bootstrap, "compile", create=True) as compiler:
        lab.bootstrap.cutlass_compiler_code(raw, "pinned-cutlass-compiler.py", lab.helpers.require)
    expected = ast.parse(raw)
    matches = [n for n in expected.body if isinstance(n, ast.Expr)
               and ast.dump(n) == ast.dump(ast.parse("sys.path.append(_SCRIPT_PATH)").body[0])]
    lab.helpers.require(len(matches) == 1)
    expected.body[expected.body.index(matches[0])] = ast.Pass()
    lab.helpers.require(ast.dump(compiler.call_args.args[0]) == ast.dump(expected))
    print("Pinned CuTe DSL 4.8.0 compiler: PASS (one path append omitted; compile/parse only).")


if __name__ == "__main__":
    if len(lab.sys.argv) == 3 and lab.sys.argv[1] == "--public-source":
        public_source_audit(lab.sys.argv[2])
    else:
        unittest.main()
