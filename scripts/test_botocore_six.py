# SPDX-License-Identifier: Apache-2.0
"""Synthetic virtual-module fixtures only; no upstream or image execution."""

import ast
import hashlib
import importlib.util
import marshal
from pathlib import Path
import struct
import subprocess
import unittest
from unittest import mock

import test_deepseek_lab as lab


# Original fixture for the pinned loader protocol, not copied upstream source.
SIX = '''
import importlib.util
import sys
import types

class MovedModule:
    def __init__(self, name, target):
        self.name, self.mod = name, target
    def resolve(self):
        return importlib.import_module(self.mod)

class _SixMetaPathImporter:
    def __init__(self, name):
        self.name, self.known_modules = name, {}
    def find_spec(self, fullname, path, target=None):
        if fullname in self.known_modules:
            return importlib.util.spec_from_loader(fullname, self)
    def is_package(self, fullname):
        value = self.known_modules[fullname]
        if isinstance(value, MovedModule):
            value = value.resolve()
        return "__path__" in vars(value)
    def create_module(self, spec):
        value = self.known_modules[spec.name]
        if isinstance(value, MovedModule):
            value = value.resolve()
        sys.modules[spec.name] = value
        return value
    def exec_module(self, module):
        pass

_importer = _SixMetaPathImporter(__name__)
moves = types.ModuleType(__name__ + ".moves")
moves.__path__ = []
urllib = types.ModuleType(__name__ + ".moves.urllib")
urllib.__path__ = []
parse = types.ModuleType(__name__ + ".moves.urllib.parse")
parse.VALUE = "synthetic"
urllib.parse = parse
moves.urllib = urllib
_importer.known_modules = {
    __name__ + ".moves": moves,
    __name__ + ".moves.http_client": MovedModule("http_client", "six_target"),
    __name__ + ".moves.urllib": urllib,
    __name__ + ".moves.urllib.parse": parse,
    __name__ + ".moves.urllib_parse": parse,
}
__path__ = []
__spec__.submodule_search_locations = []
sys.meta_path.append(_importer)
'''
NAMES = ("moves", "moves.http_client", "moves.urllib", "moves.urllib.parse", "moves.urllib_parse")
POSITIVE = '''    import importlib
    before = tuple(sys.meta_path)
    six = importlib.import_module("botocore.vendored.six")
    moves = importlib.import_module("botocore.vendored.six.moves")
    assert moves is six.moves
    nested = importlib.import_module("botocore.vendored.six.moves.urllib.parse")
    flat = importlib.import_module("botocore.vendored.six.moves.urllib_parse")
    assert nested is flat is moves.urllib.parse
    assert nested.VALUE == "synthetic"
    moved = importlib.import_module("botocore.vendored.six.moves.http_client")
    import six_target
    assert moved is six_target and moved.__name__ == "six_target"
    assert importlib.import_module("botocore.vendored.six.moves.http_client") is moved
    assert tuple(sys.meta_path) == before
    assert STATE["six_status"] == "active" and not STATE["six_pending"]
    assert "_nib_register_botocore_six" not in vars(six)
    assert flat.__spec__.loader.get_code(flat.__spec__.name) is None
    assert flat.__spec__.loader.get_source(flat.__spec__.name) is None
    import six as canonical
    assert canonical.VALUE == "unrelated-canonical"
    STATE["check"]()
'''
STICKY = '''    assert STATE["six_status"] == "failed"
    # Python returns cached modules without invoking finders. That must not
    # reset failure or permit the next guarded boundary/uncached virtual load.
    if "botocore.vendored.six" in sys.modules:
        importlib.import_module("botocore.vendored.six")
        assert STATE["six_status"] == "failed"
    try: STATE["check"]()
    except ValueError: pass
    else: raise AssertionError("failed adapter recovered")
    try: importlib.import_module("botocore.vendored.six.moves.unloaded")
    except (ValueError, ImportError): pass
    else: raise AssertionError("uncached import accepted after failure")
    print('{"rejected":true}')
    raise SystemExit(0)
'''


class SixTests(unittest.TestCase):
    setUp = lab.BootstrapTests.setUp
    run_fixture = lab.BootstrapTests.run_fixture

    def fixture(self, body=POSITIVE, source=SIX, target='VALUE = "target"\n'):
        sources = {
            "botocore/__init__.py": "",
            "botocore/vendored/__init__.py": "",
            "botocore/vendored/six.py": source.ljust(34549),
            "botocore-1.43.114.dist-info/METADATA": "Name: botocore\nVersion: 1.43.114\n",
            "six_target.py": target,
            "six.py": 'VALUE = "unrelated-canonical"\n',
        }
        for name, text in sources.items():
            path = self.package / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        pins = ("\nSIX_SHA=" + repr(hashlib.sha256(sources[lab.bootstrap.SIX_PATH].encode()).hexdigest())
                + "\nSIX_NAMES=" + repr(NAMES) + "\n").encode()
        self.six_source = self.source + pins
        self.six_driver = lab.DRIVER.replace("    import fake_engine", "    import fake_engine\n" + body)
        (self.root / "launch.py").write_bytes(self.six_source + self.six_driver.encode())

    def test_virtual_aliases_in_fresh_spawn_grandchild_before_unpickle(self):
        self.fixture()
        row = self.run_fixture("positive")
        self.assertTrue(row["tracker"])
        self.assertEqual(row["child"]["grandchild"]["before"]["value"], "source-not-bytecode")

    def test_lazy_from_import_preserves_target_before_virtual_alias_load(self):
        source = SIX.replace(
            'moves = types.ModuleType(__name__ + ".moves")',
            '''class Moves(types.ModuleType):
    def __getattr__(self, name):
        if name != "http_client":
            raise AttributeError(name)
        return _importer.known_modules[__name__ + ".moves.http_client"].resolve()
moves = Moves(__name__ + ".moves")''')
        self.fixture('''    first = "botocore.vendored.six.moves.http_client" not in sys.modules
    from botocore.vendored.six.moves import http_client
    import six_target
    assert http_client is six_target
    if first:
        assert "botocore.vendored.six.moves.http_client" not in sys.modules
    STATE["check"]()
''' + POSITIVE, source=source)
        self.assertTrue(self.run_fixture("positive")["tracker"])

    def test_only_the_last_registration_call_is_changed(self):
        raw = SIX.ljust(34549).encode()
        with mock.patch.object(lab.bootstrap, "SIX_SHA", hashlib.sha256(raw).hexdigest()):
            with mock.patch.object(lab.bootstrap, "compile", create=True) as compile_mock:
                lab.bootstrap.six_source_code(raw, "six.py", lab.helpers.require)
                actual = compile_mock.call_args.args[0]
            expected = ast.parse(raw, filename="six.py")
            call = expected.body[-1].value
            call.func = ast.copy_location(ast.Name(id=lab.bootstrap.SIX_REGISTER, ctx=ast.Load()), call.func)
            self.assertEqual(ast.dump(actual, include_attributes=True), ast.dump(expected, include_attributes=True))
            for invalid in (raw + b"\n", raw.replace(b"synthetic", b"DIFFERENT")):
                with self.subTest(invalid=len(invalid)), self.assertRaises(ValueError):
                    lab.bootstrap.six_source_code(invalid, "six.py", lab.helpers.require)

    def test_unadapted_registration_still_fails_the_unchanged_finder_guard(self):
        self.fixture('''    global SIX_MODULE
    SIX_MODULE = "unadapted.fixture"
    import botocore.vendored.six
    assert len(sys.meta_path) == len(STATE["finders"]) + 1
    try: STATE["check"]()
    except ValueError: pass
    else: raise AssertionError("finder mutation accepted")
    print('{"rejected":true}')
    raise SystemExit(0)
''')
        self.assertEqual(self.run_fixture("fresh"), {"rejected": True})

    def test_registry_module_and_target_mutations_are_sticky(self):
        variants = (
            'six._importer.known_modules = dict(six._importer.known_modules)',
            'six._importer.known_modules["botocore.vendored.six.moves.extra"] = six.moves',
            'del six._importer.known_modules["botocore.vendored.six.moves"]',
            'six._importer.known_modules["botocore.vendored.six.moves"] = object()',
            'six._importer.known_modules["botocore.vendored.six.moves.http_client"].mod = "private_target"',
            'six._importer.find_spec = lambda *args: None',
            'type(six._importer).find_spec = lambda *args: None',
            'six._importer.name = "six"',
            'six._importer = object()',
            'sys.modules["botocore.vendored.six"] = object()',
            'del sys.modules["botocore.vendored.six"]',
            'sys.modules["botocore.vendored.six.moves"] = object()',
            'del sys.modules["botocore.vendored.six.moves"]',
            'sys.modules["botocore.vendored.six.moves.injected"] = object()',
        )
        for variant in variants:
            with self.subTest(variant=variant):
                self.fixture(POSITIVE + "    " + variant + '''
    try: STATE["check"]()
    except ValueError: pass
    else: raise AssertionError("mutation accepted")
''' + STICKY)
                self.assertEqual(self.run_fixture("fresh"), {"rejected": True})

    def test_reload_of_source_or_virtual_alias_is_sticky(self):
        for name in ("botocore.vendored.six", "botocore.vendored.six.moves.urllib_parse"):
            with self.subTest(name=name):
                self.fixture(POSITIVE + '''
    try: importlib.reload(sys.modules[''' + repr(name) + '''])
    except ValueError: pass
    else: raise AssertionError("reload accepted")
''' + STICKY)
                self.assertEqual(self.run_fixture("fresh"), {"rejected": True})

    def test_unknown_virtual_names_never_fall_through_to_pathfinder(self):
        self.fixture(POSITIVE + '''
    try: importlib.import_module("botocore.vendored.six.moves.unreviewed")
    except ValueError: pass
    else: raise AssertionError("unknown virtual name accepted")
''' + STICKY)
        self.assertEqual(self.run_fixture("fresh"), {"rejected": True})

    def test_failed_target_import_cannot_be_recovered_by_a_cached_alias(self):
        self.fixture('''    import importlib, types
    import botocore.vendored.six
    try: importlib.import_module("botocore.vendored.six.moves.http_client")
    except ImportError: pass
    else: raise AssertionError("target failure ignored")
    sys.modules["botocore.vendored.six.moves.http_client"] = types.ModuleType("injected")
''' + STICKY, target='raise ImportError("synthetic target failure")\n')
        self.assertEqual(self.run_fixture("fresh"), {"rejected": True})

    def test_failed_virtual_create_and_exec_remain_failed_after_caught_import(self):
        for source in (
            SIX.replace("        return value\n    def exec_module", '        raise ImportError("create failure")\n    def exec_module'),
            SIX.replace("    def exec_module(self, module):\n        pass",
                        '    def exec_module(self, module):\n        raise ImportError("exec failure")'),
        ):
            with self.subTest(source=hashlib.sha256(source.encode()).hexdigest()):
                self.fixture('''    import importlib
    try: importlib.import_module("botocore.vendored.six.moves")
    except ImportError: pass
    else: raise AssertionError("virtual load failure ignored")
''' + STICKY, source=source)
                self.assertEqual(self.run_fixture("fresh"), {"rejected": True})

    def test_source_failure_registration_shape_and_initial_registry_are_sticky(self):
        for source in (
            SIX.replace("sys.meta_path.append(_importer)", 'raise ImportError("source failure")\nsys.meta_path.append(_importer)'),
            SIX + "\npass\n",
            SIX.replace("sys.meta_path.append(_importer)",
                        '_importer.known_modules[__name__ + ".moves.extra"] = moves\nsys.meta_path.append(_importer)'),
        ):
            with self.subTest(source=hashlib.sha256(source.encode()).hexdigest()):
                self.fixture('''    import importlib
    try: importlib.import_module("botocore.vendored.six")
    except (ValueError, ImportError): pass
    else: raise AssertionError("bad source accepted")
''' + STICKY, source=source)
                self.assertEqual(self.run_fixture("fresh"), {"rejected": True})

    def test_pinned_source_change_fails_before_execution_and_cannot_retry(self):
        self.fixture('''    import importlib
    try: importlib.import_module("botocore.vendored.six")
    except ValueError: pass
    else: raise AssertionError("changed source accepted")
''' + STICKY)
        (self.package / lab.bootstrap.SIX_PATH).write_text('raise AssertionError("unverified source executed")\n')
        self.assertEqual(self.run_fixture("fresh"), {"rejected": True})

    def test_distribution_version_mismatch_is_sticky(self):
        self.fixture('''    import importlib
    try: importlib.import_module("botocore.vendored.six")
    except ValueError: pass
    else: raise AssertionError("wrong version accepted")
''' + STICKY)
        (self.package / "botocore-1.43.114.dist-info/METADATA").write_text(
            "Name: botocore\nVersion: 1.43.115\n")
        self.assertEqual(self.run_fixture("fresh"), {"rejected": True})

    def test_shadow_source_outside_selected_path_is_sticky(self):
        self.fixture('''    import importlib
    import botocore.vendored
    botocore.vendored.__path__[:] = [str(Path(__file__).with_name("shadow"))]
    try: importlib.import_module("botocore.vendored.six")
    except ValueError: pass
    else: raise AssertionError("shadow source accepted")
''' + STICKY)
        shadow = self.root / "shadow"
        shadow.mkdir()
        (shadow / "six.py").write_text('raise AssertionError("shadow executed")\n')
        self.assertEqual(self.run_fixture("fresh"), {"rejected": True})

    def test_source_backed_bytecode_is_never_executed(self):
        self.fixture()
        source = self.package / lab.bootstrap.SIX_PATH
        cache = Path(importlib.util.cache_from_source(str(source)))
        cache.parent.mkdir()
        cache.write_bytes(importlib.util.MAGIC_NUMBER
                          + struct.pack("<III", 0, int(source.stat().st_mtime), source.stat().st_size)
                          + marshal.dumps(compile('raise AssertionError("cached code executed")', str(source), "exec")))
        self.assertEqual(self.run_fixture("fresh")["value"], "source-not-bytecode")

    def test_sourceless_bytecode_cannot_supply_the_pinned_source(self):
        self.fixture('''    import importlib
    try: importlib.import_module("botocore.vendored.six")
    except ValueError: pass
    else: raise AssertionError("sourceless bytecode accepted")
''' + STICKY)
        source = self.package / lab.bootstrap.SIX_PATH
        source.with_suffix(".pyc").write_bytes(
            importlib.util.MAGIC_NUMBER + struct.pack("<III", 0, 0, 0)
            + marshal.dumps(compile('raise AssertionError("sourceless code executed")', str(source), "exec")))
        source.unlink()
        self.assertEqual(self.run_fixture("fresh"), {"rejected": True})

    def test_preloaded_source_or_virtual_module_is_rejected_before_install(self):
        for name in ("botocore.vendored.six", "botocore.vendored.six.moves"):
            with self.subTest(name=name):
                self.fixture()
                prefix = 'import types\nsys.modules[' + repr(name) + ']=types.ModuleType("injected")\n'
                (self.root / "launch.py").write_bytes(self.six_source + prefix.encode() + self.six_driver.encode())
                result = subprocess.run([self.layout["wrapper"], "fresh"], cwd="/", capture_output=True, timeout=10)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, b"")


if __name__ == "__main__":
    unittest.main()
