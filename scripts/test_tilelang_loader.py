# SPDX-License-Identifier: Apache-2.0
"""Synthetic TileLang namespace contract; no GPU or upstream native execution."""

import ast
import hashlib
import subprocess
import unittest
import zipfile
from unittest import mock

import test_deepseek_lab as lab


SOURCE = '''import os, sys
TL_ROOT = os.path.dirname(__file__)
TL_LIBS = [TL_ROOT + "/lib"]
DEV = False
for lib in TL_LIBS:
    if lib not in sys.path:
        sys.path.insert(0, lib)
def prepend_pythonpath(path):
    if not os.environ.get("PYTHONPATH", None):
        os.environ["PYTHONPATH"] = path
    else:
        os.environ["PYTHONPATH"] = path + os.pathsep + os.environ["PYTHONPATH"]
    sys.path.insert(0, path)
prepend_pythonpath(TL_ROOT + "/3rdparty/tvm/python")
VALUE = "preserved"
'''
TVM = 'from .sibling import VALUE\n'
NATIVE = b"synthetic native file, not loadable"
SCRIPT = '''import sys
_DIALECT_REGISTRY = {}
def register_dialect(name, module_path):
    _DIALECT_REGISTRY[name] = module_path
class _DialectRedirectFinder:
    pass
if not any(isinstance(f, _DialectRedirectFinder) for f in sys.meta_path):
    sys.meta_path.append(_DialectRedirectFinder())
'''
POSITIVE = '''    import tilelang.env
    import tvm
    assert tvm.VALUE == "private-tvm"
    assert tilelang.env.VALUE == "preserved"
    assert sys.path == LAYOUT["paths"]
    assert "PYTHONPATH" not in os.environ
    assert sys.meta_path == STATE["finders"]
    assert not any(name in vars(tilelang.env) for name in TILELANG_CALLBACKS)
    spec = STATE["finders"][-1].find_spec("tilelang_cython_wrapper")
    assert type(spec.loader) is importlib.machinery.ExtensionFileLoader
    assert spec.origin.endswith("/tilelang/lib/tilelang_cython_wrapper.abi3.so")
    try: import nib_private_neighbor
    except ModuleNotFoundError: pass
    else: raise AssertionError("unrelated private namespace exposed")
    STATE["check"]()
'''


class TileLangTests(unittest.TestCase):
    setUp = lab.BootstrapTests.setUp
    run_fixture = lab.BootstrapTests.run_fixture

    def fixture(self, body=POSITIVE, source=SOURCE):
        root = self.package / "tilelang"
        tvm = root / "3rdparty/tvm/python/tvm"
        native = root / "lib"
        tvm.mkdir(parents=True, exist_ok=True)
        native.mkdir(exist_ok=True)
        (root / "__init__.py").write_text("")
        self.env = root / "env.py"
        self.env.write_text(source)
        self.tvm = tvm / "__init__.py"
        self.tvm.write_text(TVM)
        (tvm / "sibling.py").write_text('VALUE = "private-tvm"\n')
        self.native = native / "tilelang_cython_wrapper.abi3.so"
        self.native.write_bytes(NATIVE)
        for directory in (native, tvm.parent):
            (directory / "nib_private_neighbor.py").write_text("print('private-body-ran')\n")
        # A same-name package in the broad selected root must not shadow bundled TVM.
        (self.package / "tvm.py").write_text("print('private-body-ran')\n")
        dist = self.package / "tilelang-0.1.12.dist-info"
        dist.mkdir(exist_ok=True)
        self.metadata = dist / "METADATA"
        self.metadata.write_text("Name: tilelang\nVersion: 0.1.12\n")
        pins = {"TILELANG_ENV_SHA": source.encode(), "TILELANG_TVM_SHA": TVM.encode(), "TILELANG_NATIVE_SHA": NATIVE}
        self.tilelang_source = self.source + "".join(
            "\n" + name + "=" + repr(hashlib.sha256(raw).hexdigest()) + "\n"
            for name, raw in pins.items()).encode()
        self.driver = lab.DRIVER.replace("    import fake_engine", "    import fake_engine\n" + body)
        (self.root / "launch.py").write_bytes(self.tilelang_source + self.driver.encode())

    def rejected(self):
        result = subprocess.run([self.layout["wrapper"], "fresh"], cwd="/", capture_output=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(b"private-body-ran", result.stdout + result.stderr)

    def dialect_fixture(self, body):
        self.fixture(body)
        root = self.tvm.parent
        for name, content in {
            "script/__init__.py": SCRIPT,
            "script/parser/__init__.py": "",
            "script/ir_builder/__init__.py": "",
            "tirx/__init__.py": "",
            "tirx/script/__init__.py": 'VALUE = "dialect"\n',
            "tirx/script/parser/__init__.py": "",
            "tirx/script/parser/entry.py": 'VALUE = "entry"\n',
            "tirx/script/builder/__init__.py": "",
            "tirx/script/builder/entry.py": 'VALUE = "builder"\n',
            "script/tirx/real.py": 'VALUE = "physical"\n',
            "script/relax/__init__.py": 'VALUE = "physical-package"\n',
        }.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        override = "\nTVM_SCRIPT_SHA=" + repr(hashlib.sha256(SCRIPT.encode()).hexdigest()) + "\n"
        (self.root / "launch.py").write_bytes(self.tilelang_source + override.encode() + self.driver.encode())

    def test_dialect_aliases_keep_sealed_finder_across_spawn(self):
        self.dialect_fixture('''    import tilelang.env, tvm.script
    tvm.script.register_dialect("tirx", "tvm.tirx.script")
    from tvm.script.parser.tirx.entry import VALUE
    assert VALUE == "entry"
    from tvm.script.ir_builder.tirx.entry import VALUE
    assert VALUE == "builder"
    import tvm.script.tirx
    assert tvm.script.tirx is sys.modules["tvm.tirx.script"]
    tvm.script.register_dialect("relax", "tvm.relax.script")
    import tvm.script.relax
    assert tvm.script.relax.VALUE == "physical-package"
    assert sys.meta_path == STATE["finders"]
    assert sys.path == LAYOUT["paths"]
    STATE["check"]()
''')
        self.assertTrue(self.run_fixture("positive")["tracker"])

    def test_dialect_source_drift_and_registry_tamper_reject(self):
        for action in (
            'tvm.script.register_dialect("evil", "os")',
            'tvm.script.register_dialect("tirx", "os")',
            'tvm.script._DIALECT_REGISTRY = {}',
            'sys.modules["tvm.script"] = types.ModuleType("tvm.script")',
            'importlib.reload(tvm.script)',
        ):
            with self.subTest(action=action):
                self.dialect_fixture('    import tilelang.env, tvm.script\n    ' + action + '\n    STATE["check"]()\n')
                self.rejected()
        self.dialect_fixture('    import tilelang.env, tvm.script\n')
        (self.tvm.parent / "script/__init__.py").write_text(SCRIPT + "print('private-body-ran')\n")
        self.rejected()

    def test_dialect_adapter_rejects_missing_duplicate_or_nested_registration(self):
        site = 'if not any(isinstance(f, _DialectRedirectFinder) for f in sys.meta_path):\n    sys.meta_path.append(_DialectRedirectFinder())'
        for source in (SCRIPT.replace(site, "pass"), SCRIPT + site + "\n",
                       SCRIPT.replace(site, "def later():\n" + "\n".join("    " + line for line in site.splitlines()))):
            with self.subTest(source=source), mock.patch.object(lab.bootstrap, "TVM_SCRIPT_SHA", hashlib.sha256(source.encode()).hexdigest()):
                with self.assertRaises(ValueError):
                    lab.bootstrap.tvm_script_code(source.encode(), "script.py", lab.helpers.require)

    def test_explicit_namespace_preserves_exports_across_spawn_and_fresh(self):
        self.fixture()
        self.assertTrue(self.run_fixture("positive")["tracker"])
        self.assertFalse(list(self.root.rglob("*.pyc")))

    def test_optional_probes_before_registration_are_absent_not_boundary_faults(self):
        self.fixture('''    if "tilelang.env" not in sys.modules:
        for name in ("tvm", "tilelang_cython_wrapper"):
            assert importlib.util.find_spec(name) is None
            try: importlib.import_module(name)
            except ModuleNotFoundError as error: assert error.name == name
            else: raise AssertionError("private namespace loaded before registration")
            assert name not in sys.modules
        assert STATE["tilelang_status"] == "not-loaded"
        assert not STATE["tilelang_tvm"]
        STATE["check"]()
''' + POSITIVE)
        self.assertTrue(self.run_fixture("positive")["tracker"])

    def test_unregistered_probe_does_not_accept_reload_or_supplied_package_paths(self):
        for arguments in ('"tvm", ["/untrusted"]', '"tvm.child", ["/untrusted"]',
                          '"tvm", None, types.ModuleType("tvm")'):
            with self.subTest(arguments=arguments):
                self.fixture('    STATE["finders"][-1].find_spec(' + arguments + ')\n')
                self.rejected()

    def test_original_source_reproduces_path_guard_rejection(self):
        self.fixture('''    global tilelang_env_code
    tilelang_env_code = lambda raw, path, require: compile(raw, path, "exec")
    try: import tilelang.env
    except ValueError: pass
    else: raise AssertionError("unadapted paths accepted")
    assert sys.path != LAYOUT["paths"]
    print('{"rejected":true}')
    raise SystemExit(0)
''')
        self.assertTrue(self.run_fixture("fresh")["rejected"])

    def test_exact_replacements_and_source_pin(self):
        raw = SOURCE.encode()
        with mock.patch.object(lab.bootstrap, "TILELANG_ENV_SHA", hashlib.sha256(raw).hexdigest()):
            with mock.patch.object(lab.bootstrap, "compile", create=True) as compiler:
                lab.bootstrap.tilelang_env_code(raw, "env.py", lab.helpers.require)
            tree = compiler.call_args.args[0]
            self.assertEqual(sum(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                                 and isinstance(n.func.value, ast.Attribute) and n.func.value.attr == "path"
                                 for n in ast.walk(tree)), 1)  # os.path.dirname, no sys.path writes
            self.assertEqual(ast.dump(tree.body[4]), ast.dump(ast.parse("_nib_tilelang_libs(TL_LIBS, DEV)").body[0]))
            self.assertEqual(ast.dump(tree.body[5]), ast.dump(ast.parse(
                "def prepend_pythonpath(path):\n    _nib_tilelang_tvm(path)").body[0]))
            with self.assertRaises(ValueError):
                lab.bootstrap.tilelang_env_code(raw + b"\n", "env.py", lab.helpers.require)

    def test_missing_duplicate_nested_or_injected_adapter_sites_rejected(self):
        variants = (SOURCE.replace("sys.path.insert(0, lib)", "pass"),
                    SOURCE + "for lib in TL_LIBS:\n    if lib not in sys.path:\n        sys.path.insert(0, lib)\n",
                    SOURCE.replace("def prepend_pythonpath(path):", "def other(path):"),
                    SOURCE + "_nib_tilelang_tvm = None\n")
        for source in variants:
            raw = source.encode()
            with self.subTest(source=source), mock.patch.object(lab.bootstrap, "TILELANG_ENV_SHA", hashlib.sha256(raw).hexdigest()):
                with self.assertRaises(ValueError):
                    lab.bootstrap.tilelang_env_code(raw, "env.py", lab.helpers.require)

    def test_source_distribution_native_and_tvm_drift_reject_before_execution(self):
        for field in ("env", "tvm", "native", "metadata"):
            with self.subTest(field=field):
                self.fixture()
                getattr(self, field).write_text("print('private-body-ran')\n")
                self.rejected()

    def test_development_and_alternate_paths_rejected(self):
        for source in (SOURCE.replace("DEV = False", "DEV = True"),
                       SOURCE.replace('TL_LIBS = [TL_ROOT + "/lib"]', 'TL_LIBS = [TL_ROOT]'),
                       SOURCE.replace('prepend_pythonpath(TL_ROOT + "/3rdparty/tvm/python")',
                                      'prepend_pythonpath(TL_ROOT + "/elsewhere")')):
            with self.subTest(source=source):
                self.fixture(source=source)
                self.rejected()

    def test_external_path_environment_rejected(self):
        for name in ("PYTHONPATH", "TVM_IMPORT_PYTHON_PATH", "TVM_LIBRARY_PATH"):
            with self.subTest(name=name):
                self.fixture('    os.environ[' + repr(name) + '] = "/untrusted"\n    import tilelang.env\n')
                self.rejected()

    def test_symlinked_private_packages_rejected(self):
        for field in ("env", "tvm", "native"):
            with self.subTest(field=field):
                self.fixture()
                path = getattr(self, field)
                target = path.with_suffix(".original")
                path.rename(target)
                path.symlink_to(target)
                self.rejected()
                path.unlink()
                target.rename(path)

    def test_preloaded_namespaces_rejected_before_install(self):
        for name in ("tilelang.env", "tvm", "tvm.child", "tilelang_cython_wrapper"):
            with self.subTest(name=name):
                self.fixture()
                preload = ('\nsys.modules[' + repr(name) + '] = types.ModuleType(' + repr(name) + ')\n').encode()
                (self.root / "launch.py").write_bytes(self.tilelang_source + preload + self.driver.encode())
                self.rejected()

    def test_early_import_reload_and_package_path_tamper_rejected(self):
        for body in ('    import tvm\n', '    import tilelang_cython_wrapper\n',
                     '    import tilelang.env\n    importlib.reload(tilelang.env)\n',
                     '    import tilelang.env, tvm\n    importlib.reload(tvm)\n',
                     '    import tilelang.env, tvm\n    tvm.__path__[:] = [str(os.path.dirname(__file__))]\n    import tvm.untrusted\n'):
            with self.subTest(body=body):
                self.fixture(body)
                self.rejected()

    def test_failure_cannot_reenable_namespace_after_catch(self):
        self.fixture('''    try: import tilelang.env
    except ValueError: pass
    else: raise AssertionError("failure missing")
    try: STATE["check"]()
    except ValueError: print('{"rejected":true}')
    else: raise AssertionError("failed state accepted")
    raise SystemExit(0)
''', SOURCE.replace("DEV = False", "DEV = True"))
        self.assertTrue(self.run_fixture("fresh")["rejected"])


def public_source_audit(path):
    with zipfile.ZipFile(path) as archive:
        raw = archive.read("tilelang/env.py")
        lab.bootstrap.tilelang_env_code(raw, "pinned-tilelang-env.py", lab.helpers.require)
        lab.bootstrap.tvm_script_code(archive.read("tilelang/3rdparty/tvm/python/tvm/script/__init__.py"),
                                      "pinned-tvm-script.py", lab.helpers.require)
        for name, digest in (("tilelang/3rdparty/tvm/python/tvm/__init__.py", lab.bootstrap.TILELANG_TVM_SHA),
                             ("tilelang/lib/tilelang_cython_wrapper.abi3.so", lab.bootstrap.TILELANG_NATIVE_SHA)):
            lab.helpers.require(hashlib.sha256(archive.read(name)).hexdigest() == digest)
    print("Pinned TileLang 0.1.12: PASS (parse/compile/hash only, no upstream execution).")


if __name__ == "__main__":
    if len(lab.sys.argv) == 3 and lab.sys.argv[1] == "--public-source":
        public_source_audit(lab.sys.argv[2])
    else:
        unittest.main()
