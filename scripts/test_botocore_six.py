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
    assert STATE["six"]["botocore.vendored.six"]["status"] == "active"
    assert not STATE["six"]["botocore.vendored.six"]["pending"]
    assert "_nib_register_six" not in vars(six)
    assert flat.__spec__.loader.get_code(flat.__spec__.name) is None
    assert flat.__spec__.loader.get_source(flat.__spec__.name) is None
    STATE["check"]()
'''
STICKY = '''    assert STATE["six"]["botocore.vendored.six"]["status"] == "failed"
    # Python returns cached modules without invoking finders. That must not
    # reset failure or permit the next guarded boundary/uncached virtual load.
    if "botocore.vendored.six" in sys.modules:
        importlib.import_module("botocore.vendored.six")
        assert STATE["six"]["botocore.vendored.six"]["status"] == "failed"
    try: STATE["check"]()
    except ValueError: pass
    else: raise AssertionError("failed adapter recovered")
    try: importlib.import_module("botocore.vendored.six.moves.unloaded")
    except (ValueError, ImportError): pass
    else: raise AssertionError("uncached import accepted after failure")
    print('{"rejected":true}')
    raise SystemExit(0)
'''


class SixFixture:
    setUp = lab.BootstrapTests.setUp
    run_fixture = lab.BootstrapTests.run_fixture
    source_name = "botocore.vendored.six"

    @property
    def source_row(self):
        return lab.bootstrap.SIX_SOURCES[self.source_name]

    def fixture(self, body=POSITIVE, source=SIX, target='VALUE = "target"\n'):
        sources = {
            "botocore/__init__.py": "",
            "botocore/vendored/__init__.py": "",
            "six_target.py": target,
        }
        pins = "\nSIX_NAMES=" + repr(NAMES) + "\n"
        for name, row in lab.bootstrap.SIX_SOURCES.items():
            text = (source if name == self.source_name else SIX).ljust(row["bytes"])
            sources[row["path"]] = text
            sources[row["distribution"] + "-" + row["version"] + ".dist-info/METADATA"] = (
                "Name: " + row["distribution"] + "\nVersion: " + row["version"] + "\n")
            pins += "SIX_SOURCES[" + repr(name) + "][\"sha256\"]=" + repr(hashlib.sha256(text.encode()).hexdigest()) + "\n"
        for name, text in sources.items():
            path = self.package / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        self.six_source = self.source + pins.encode()
        body = body.replace("botocore.vendored.six", self.source_name)
        self.six_driver = lab.DRIVER.replace("    import fake_engine", "    import fake_engine\n" + body)
        (self.root / "launch.py").write_bytes(self.six_source + self.six_driver.encode())


class SixTests(SixFixture, unittest.TestCase):
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
        raw = SIX.ljust(self.source_row["bytes"]).encode()
        with mock.patch.dict(self.source_row, {"sha256": hashlib.sha256(raw).hexdigest()}):
            with mock.patch.object(lab.bootstrap, "compile", create=True) as compile_mock:
                lab.bootstrap.six_source_code(raw, "six.py", self.source_name, lab.helpers.require)
                actual = compile_mock.call_args.args[0]
            expected = ast.parse(raw, filename="six.py")
            call = expected.body[-1].value
            call.func = ast.copy_location(ast.Name(id=lab.bootstrap.SIX_REGISTER, ctx=ast.Load()), call.func)
            self.assertEqual(ast.dump(actual, include_attributes=True), ast.dump(expected, include_attributes=True))
            for invalid in (raw + b"\n", raw.replace(b"synthetic", b"DIFFERENT")):
                with self.subTest(invalid=len(invalid)), self.assertRaises(ValueError):
                    lab.bootstrap.six_source_code(invalid, "six.py", self.source_name, lab.helpers.require)

    def test_unadapted_registration_still_fails_the_unchanged_finder_guard(self):
        self.fixture('''    global SIX_SOURCES
    SIX_SOURCES = {}
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
            'six._importer.name = "unreviewed.six"',
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

    def test_optional_windows_import_preserves_healthy_linux_registry(self):
        self.fixture(POSITIVE + '''
    for _ in range(2):
        try: from botocore.vendored.six.moves import winreg
        except ImportError: pass
        else: raise AssertionError("Windows-only import accepted")
        try: importlib.import_module("botocore.vendored.six.moves.winreg")
        except ModuleNotFoundError as error:
            assert error.name == "botocore.vendored.six.moves.winreg"
        else: raise AssertionError("Windows-only module accepted")
        assert "botocore.vendored.six.moves.winreg" not in sys.modules
        assert STATE["six"]["botocore.vendored.six"]["status"] == "active"
        assert not STATE["six"]["botocore.vendored.six"]["pending"]
        STATE["check"]()
''' + POSITIVE)
        self.assertTrue(self.run_fixture("positive")["tracker"])

    def test_optional_windows_import_cannot_execute_a_shadow(self):
        shadow = self.package / "optional_shadow"
        shadow.mkdir()
        (shadow / "winreg.py").write_text('raise AssertionError("shadow executed")\n')
        self.fixture(POSITIVE + "    moves.__path__ = [" + repr(str(shadow)) + "]\n" + '''
    try: importlib.import_module("botocore.vendored.six.moves.winreg")
    except ModuleNotFoundError: pass
    else: raise AssertionError("shadow accepted")
    STATE["check"]()
''' + POSITIVE)
        self.assertTrue(self.run_fixture("positive")["tracker"])

    def test_optional_windows_import_does_not_bypass_invalid_state(self):
        for mutation in (
            'sys.platform = "win32"',
            'six._importer.known_modules = dict(six._importer.known_modules)',
            'six._importer.known_modules["botocore.vendored.six.moves.winreg"] = moves',
        ):
            with self.subTest(mutation=mutation):
                self.fixture(POSITIVE + "    " + mutation + '''
    try: importlib.import_module("botocore.vendored.six.moves.winreg")
    except ValueError: pass
    else: raise AssertionError("invalid state accepted as optional absence")
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
        (self.package / self.source_row["path"]).write_text('raise AssertionError("unverified source executed")\n')
        self.assertEqual(self.run_fixture("fresh"), {"rejected": True})

    def test_distribution_version_mismatch_is_sticky(self):
        self.fixture('''    import importlib
    try: importlib.import_module("botocore.vendored.six")
    except ValueError: pass
    else: raise AssertionError("wrong version accepted")
''' + STICKY)
        row = self.source_row
        (self.package / (row["distribution"] + "-" + row["version"] + ".dist-info/METADATA")).write_text(
            "Name: " + row["distribution"] + "\nVersion: 0.0.0\n")
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
        source = self.package / self.source_row["path"]
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
        source = self.package / self.source_row["path"]
        source.with_suffix(".pyc").write_bytes(
            importlib.util.MAGIC_NUMBER + struct.pack("<III", 0, 0, 0)
            + marshal.dumps(compile('raise AssertionError("sourceless code executed")', str(source), "exec")))
        source.unlink()
        self.assertEqual(self.run_fixture("fresh"), {"rejected": True})

    def test_preloaded_source_or_virtual_module_is_rejected_before_install(self):
        for name in ("botocore.vendored.six", "botocore.vendored.six.moves"):
            with self.subTest(name=name):
                self.fixture()
                name = name.replace("botocore.vendored.six", self.source_name)
                prefix = 'import types\nsys.modules[' + repr(name) + ']=types.ModuleType("injected")\n'
                (self.root / "launch.py").write_bytes(self.six_source + prefix.encode() + self.six_driver.encode())
                result = subprocess.run([self.layout["wrapper"], "fresh"], cwd="/", capture_output=True, timeout=10)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, b"")


class StandardSixTests(SixTests):
    source_name = "six"

    def test_shadow_source_outside_selected_path_is_sticky(self):
        self.fixture('''    import importlib
    try: importlib.import_module("botocore.vendored.six")
    except ValueError: pass
    else: raise AssertionError("shadow source accepted")
''' + STICKY)
        (self.package / "six.py").unlink()
        (self.package / "setuptools/_vendor/six.py").write_text('raise AssertionError("shadow executed")\n')
        self.assertEqual(self.run_fixture("fresh"), {"rejected": True})


class CombinedSixTests(SixFixture, unittest.TestCase):
    def test_only_two_pinned_sources_match_the_unchanged_finder_catalog(self):
        cpu = lab.module("scripts/engine_cpu_entry.py")
        self.assertEqual(set(lab.bootstrap.SIX_SOURCES), {"six", "botocore.vendored.six"})
        for row in lab.bootstrap.SIX_SOURCES.values():
            spec = next(item for item in cpu.FINDER_SPECS if item["installed"] == row["path"])
            self.assertEqual((spec["bytes"], spec["sha256"]), (row["bytes"], row["sha256"]))

    def test_both_modules_interleave_without_registering_or_sharing_virtual_packages(self):
        for first, second in (("six", "botocore.vendored.six"), ("botocore.vendored.six", "six")):
            with self.subTest(first=first):
                self.fixture('''    import importlib
    before = tuple(sys.meta_path)
    first, second = ''' + repr((first, second)) + '''
    one = importlib.import_module(first)
    left = importlib.import_module(first + ".moves.urllib.parse")
    two = importlib.import_module(second)
    right = importlib.import_module(second + ".moves.urllib_parse")
    assert one is not two and one.moves is not two.moves and left is not right
    assert left is importlib.import_module(first + ".moves.urllib_parse")
    assert right is importlib.import_module(second + ".moves.urllib.parse")
    target = importlib.import_module(first + ".moves.http_client")
    assert importlib.import_module(second + ".moves.http_client") is target
    for name in (first, second):
        assert STATE["six"][name]["status"] == "active"
        assert not STATE["six"][name]["pending"]
    assert STATE["six"][first]["registry_object"] is not STATE["six"][second]["registry_object"]
    assert tuple(sys.meta_path) == before
    STATE["check"]()
''')
                self.assertTrue(self.run_fixture("positive")["tracker"])

    def test_cross_namespace_module_registry_and_alias_replacement_is_sticky(self):
        for change in (
            'sys.modules["botocore.vendored.six"] = other',
            'six._importer = other._importer',
            'six._importer.known_modules = other._importer.known_modules',
            'sys.modules["botocore.vendored.six.moves"] = other.moves',
        ):
            with self.subTest(change=change):
                self.fixture(POSITIVE + '''    other = importlib.import_module("six")
    importlib.import_module("six.moves")
    ''' + change + '''
    try: STATE["check"]()
    except ValueError: pass
    else: raise AssertionError("cross-namespace replacement accepted")
''' + STICKY)
                self.assertEqual(self.run_fixture("fresh"), {"rejected": True})

    def test_reload_in_either_namespace_after_both_loads_stays_failed(self):
        for name in ("six", "six.moves", "botocore.vendored.six", "botocore.vendored.six.moves"):
            with self.subTest(name=name):
                self.fixture('''    import importlib
    for name in ("six.moves", "botocore.vendored.six.moves"):
        importlib.import_module(name)
    try: importlib.reload(sys.modules[''' + repr(name) + '''])
    except ValueError: pass
    else: raise AssertionError("reload accepted")
    try: STATE["check"]()
    except ValueError: pass
    else: raise AssertionError("shared guard recovered")
    assert any(row["status"] == "failed" for row in STATE["six"].values())
    assert tuple(sys.meta_path) == tuple(STATE["finders"])
    print('{"rejected":true}')
    raise SystemExit(0)
''')
                self.assertEqual(self.run_fixture("fresh"), {"rejected": True})


if __name__ == "__main__":
    unittest.main()
