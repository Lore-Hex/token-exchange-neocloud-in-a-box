# SPDX-License-Identifier: Apache-2.0
"""Synthetic OpenCV loader protocol; native execution needs the exact image run."""

import ast
import hashlib
import unittest
from unittest import mock

import test_deepseek_lab as lab


SOURCE = '''
import sys
import os
import importlib
def bootstrap():
    from .load_config_py3 import exec_file_wrapper
    LOADER_DIR = os.path.dirname(__file__)
    PYTHON_EXTENSIONS_PATHS = []
    BINARIES_PATHS = []
    g_vars = globals()
    l_vars = locals().copy()
    exec_file_wrapper(LOADER_DIR + "/config.py", g_vars, l_vars)
    exec_file_wrapper(LOADER_DIR + "/config-3.py", g_vars, l_vars)
    applySysPathWorkaround = False
    for p in reversed(l_vars['PYTHON_EXTENSIONS_PATHS']):
        sys.path.insert(1 if not applySysPathWorkaround else 0, p)
    py_module = sys.modules.pop("cv2")
    native_module = importlib.import_module("cv2")
    sys.modules["cv2"] = py_module
    py_module._native = native_module
    py_module.VALUE = native_module.VALUE
    from .extra import EXTRA
    py_module.EXTRA = EXTRA
bootstrap()
'''
CONFIGS = {
    "config.py": 'BINARIES_PATHS = [LOADER_DIR + "/../../lib64"] + BINARIES_PATHS\n',
    "config-3.py": 'PYTHON_EXTENSIONS_PATHS = [LOADER_DIR] + PYTHON_EXTENSIONS_PATHS\n',
    "version.py": 'headless = True\n',
}
NATIVE = b"synthetic-native-do-not-execute"
FAKE_LOADER = '''    import importlib.util
    from importlib import metadata
    import types
    import unittest.mock
    class Native:
        def __init__(self, name, path): self.name, self.path = name, path
        def create_module(self, spec):
            assert sys.path == LAYOUT["paths"]
            assert "cv2" not in sys.modules
            return types.ModuleType(spec.name)
        def exec_module(self, value):
            STATE["check"]()
            value.VALUE = "synthetic-native"
        def is_package(self, name): return False
'''
POSITIVE = '''    if "cv2" not in sys.modules:
        with unittest.mock.patch.object(importlib.machinery, "ExtensionFileLoader", Native):
            import cv2
    import cv2
    assert cv2.VALUE == "synthetic-native" and cv2.EXTRA == "python-export"
    assert cv2._native.__name__ == "cv2" and cv2._native is not cv2
    assert sys.path == LAYOUT["paths"]
    assert STATE["opencv_status"] == "active"
    assert not set(OPENCV_CALLBACKS).intersection(vars(cv2))
    assert sys.meta_path == STATE["finders"]
    STATE["check"]()
'''
REJECT = '''    with unittest.mock.patch.object(importlib.machinery, "ExtensionFileLoader", Native):
        try: import cv2
        except (ValueError, ImportError, OSError): pass
        else: raise AssertionError("invalid OpenCV accepted")
    assert STATE["opencv_status"] == "failed"
    assert not set(OPENCV_CALLBACKS).intersection(vars(STATE["opencv_module"]))
    try: STATE["check"]()
    except ValueError: pass
    else: raise AssertionError("failure was not sticky")
    print('{"rejected":true}')
    raise SystemExit(0)
'''


class OpenCVTests(unittest.TestCase):
    setUp = lab.BootstrapTests.setUp
    run_fixture = lab.BootstrapTests.run_fixture

    def fixture(self, body=POSITIVE, source=SOURCE, configs=None):
        files = {"__init__.py": source, **(CONFIGS if configs is None else configs)}
        directory = self.package / "cv2"
        directory.mkdir(exist_ok=True)
        for name, text in files.items():
            (directory / name).write_text(text)
        (directory / "extra.py").write_text('EXTRA = "python-export"\n')
        (directory / "load_config_py3.py").write_text(
            'def exec_file_wrapper(path, g, l):\n    exec(compile(open(path).read(), path, "exec"), g, l)\n')
        (directory / "cv2.abi3.so").unlink(missing_ok=True)
        (directory / "cv2.abi3.so").write_bytes(NATIVE)
        dist = self.package / "opencv_python_headless-5.0.0.93.dist-info"
        dist.mkdir(exist_ok=True)
        (dist / "METADATA").write_text("Name: opencv-python-headless\nVersion: 5.0.0.93\n")
        pins = {name: hashlib.sha256(text.encode()).hexdigest() for name, text in files.items()}
        self.cv_source = self.source + ("\nOPENCV_SOURCES=" + repr(pins)
            + "\nOPENCV_NATIVE_SHA=" + repr(hashlib.sha256(NATIVE).hexdigest()) + "\n").encode()
        self.driver = lab.DRIVER.replace("    import fake_engine", "    import fake_engine\n" + FAKE_LOADER + body)
        (self.root / "launch.py").write_bytes(self.cv_source + self.driver.encode())

    def test_preserves_native_python_exports_and_spawn_without_search_path_mutation(self):
        self.fixture()
        self.assertTrue(self.run_fixture("positive")["tracker"])

    def test_only_three_exact_operations_change(self):
        raw = SOURCE.encode()
        with mock.patch.dict(lab.bootstrap.OPENCV_SOURCES, {"__init__.py": hashlib.sha256(raw).hexdigest()}):
            with mock.patch.object(lab.bootstrap, "compile", create=True) as compiler:
                lab.bootstrap.opencv_source_code(raw, "cv2/__init__.py", lab.helpers.require)
            actual = compiler.call_args.args[0]
            expected = ast.parse(SOURCE.replace("from .load_config_py3 import exec_file_wrapper",
                "exec_file_wrapper = _nib_cv2_config").replace(
                "for p in reversed(l_vars['PYTHON_EXTENSIONS_PATHS']):\n"
                "        sys.path.insert(1 if not applySysPathWorkaround else 0, p)",
                "_nib_cv2_paths(l_vars['PYTHON_EXTENSIONS_PATHS'], l_vars['BINARIES_PATHS'])").replace(
                'native_module = importlib.import_module("cv2")', 'native_module = _nib_cv2_native()'))
            self.assertEqual(ast.dump(actual), ast.dump(expected))
            with self.assertRaises(ValueError):
                lab.bootstrap.opencv_source_code(raw + b"\n", "cv2/__init__.py", lab.helpers.require)

    def test_original_loader_still_trips_the_unchanged_path_guard(self):
        self.fixture('''    global opencv_source_code
    opencv_source_code = lambda raw, path, require: compile(raw, path, "exec")
    try: import cv2
    except ValueError: pass
    else: raise AssertionError("unadapted loader accepted")
    assert sys.path != LAYOUT["paths"]
    assert STATE["opencv_status"] == "failed"
    sys.path[:] = LAYOUT["paths"]
    try: STATE["check"]()
    except ValueError: pass
    else: raise AssertionError("restoring path revived failed loader")
    print('{"rejected":true}')
    raise SystemExit(0)
''')
        self.assertEqual(self.run_fixture("fresh"), {"rejected": True})

    def test_source_config_native_and_version_tamper_are_sticky(self):
        for name in ("__init__.py", "config.py", "config-3.py", "version.py", "cv2.abi3.so"):
            with self.subTest(name=name):
                self.fixture(REJECT)
                path = self.package / "cv2" / name
                path.write_bytes(path.read_bytes() + b"\n")
                self.assertEqual(self.run_fixture("fresh"), {"rejected": True})

    def test_unknown_configuration_path_and_order_are_rejected(self):
        for old, new in (("/config-3.py", "/config-3.12.py"), ("/config.py", "/config-3.py")):
            with self.subTest(new=new):
                self.fixture(REJECT, SOURCE.replace(old, new))
                self.assertEqual(self.run_fixture("fresh"), {"rejected": True})

    def test_source_shape_cannot_silently_skip_adaptation(self):
        for source in (SOURCE + '\n_nib_cv2_paths = None\n', SOURCE.replace("sys.path.insert", "sys.path.append"),
                       SOURCE.replace('native_module = importlib.import_module("cv2")', 'native_module = None')):
            with self.subTest(source=len(source)):
                self.fixture(REJECT, source)
                self.assertEqual(self.run_fixture("fresh"), {"rejected": True})

    def test_wrong_configured_roots_are_rejected_even_when_fixture_source_is_pinned(self):
        for name, value in (("config.py", 'BINARIES_PATHS = ["/private"]\n'),
                            ("config-3.py", 'PYTHON_EXTENSIONS_PATHS = [LOADER_DIR, "/private"]\n')):
            with self.subTest(name=name):
                self.fixture(REJECT, configs={**CONFIGS, name: value})
                self.assertEqual(self.run_fixture("fresh"), {"rejected": True})

    def test_missing_symlinked_native_and_wrong_distribution_are_rejected(self):
        for variant in ("missing", "symlink", "distribution"):
            with self.subTest(variant=variant):
                self.fixture(REJECT)
                path = self.package / "cv2/cv2.abi3.so"
                if variant == "missing":
                    path.unlink()
                elif variant == "symlink":
                    target = self.root / "native"
                    target.write_bytes(NATIVE)
                    path.unlink()
                    path.symlink_to(target)
                else:
                    (self.package / "opencv_python_headless-5.0.0.93.dist-info/METADATA").write_text(
                        "Name: opencv-python-headless\nVersion: 9.9\n")
                self.assertEqual(self.run_fixture("fresh"), {"rejected": True})

    def test_reload_and_module_replacement_are_sticky(self):
        for action in ('importlib.reload(cv2)', 'sys.modules["cv2"] = object(); STATE["check"]()',
                       'cv2._native = object(); STATE["check"]()'):
            with self.subTest(action=action):
                self.fixture(POSITIVE + '''    try:
        ''' + action + '''
    except ValueError: pass
    else: raise AssertionError("invalid state accepted")
    assert STATE["opencv_status"] == "failed"
    print('{"rejected":true}')
    raise SystemExit(0)
''')
                self.assertEqual(self.run_fixture("fresh"), {"rejected": True})

    def test_module_injection_before_first_import_rejects(self):
        self.fixture('''    sys.modules["cv2"] = types.ModuleType("cv2")
    try: STATE["check"]()
    except ValueError: pass
    else: raise AssertionError("injected module accepted")
    assert STATE["opencv_status"] == "failed"
    print('{"rejected":true}')
    raise SystemExit(0)
''')
        self.assertEqual(self.run_fixture("fresh"), {"rejected": True})

    def test_native_failure_cleans_callbacks_and_remains_sticky(self):
        self.fixture('''    original = Native.exec_module
    def broken(self, value):
        raise ImportError("synthetic failure")
    Native.exec_module = broken
''' + REJECT)
        self.assertEqual(self.run_fixture("fresh"), {"rejected": True})


if __name__ == "__main__":
    unittest.main()
