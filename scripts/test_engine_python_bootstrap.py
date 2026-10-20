# SPDX-License-Identifier: Apache-2.0
"""Local stdlib/fake-package processes only, never Docker or upstream imports."""

import copy
import json
import marshal
import os
from pathlib import Path
import shlex
import subprocess
import struct
import sys
import sysconfig
import tempfile
import threading
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SOURCE = (ROOT / "scripts/engine_python_bootstrap.py").read_bytes()
bootstrap = types.ModuleType("bootstrap_test")
exec(compile(SOURCE, "bootstrap_test", "exec"), bootstrap.__dict__)
smoke = types.ModuleType("smoke_contract_test")
exec(compile((ROOT / "scripts/engine_import_smoke.py").read_bytes(), "smoke_contract_test", "exec"), smoke.__dict__)

# This fixture is deliberately real subprocess code, not an AST assertion.
DRIVER = r'''
ARGS = sys.argv[1:]
LAYOUT = json.loads(read_source(os.path.dirname(__file__) + "/layout.json"))
from importlib import metadata
METADATA_BEFORE = [d.version for d in metadata.distributions(path=[LAYOUT["paths"][-1]], name="review-fake")]
STATE = install(LAYOUT)

def distribution_observation():
    points = metadata.entry_points(group="review.test")
    return {"before": METADATA_BEFORE, "version": metadata.version("review-fake"),
            "points": [[p.name, p.value] for p in points], "plugin_loaded": "never_load_plugin" in sys.modules,
            "pathfinder_restored": importlib.machinery.PathFinder in sys.meta_path,
            "first_denied_process": STATE["first_denied_process"]}

def before_unpickle():
    return observation(sys._nib_smoke_guard)

class Prepared:
    def __reduce__(self):
        return before_unpickle, ()

def target(writer, prepared, depth):
    import fake_engine
    result = {"before": prepared, "after": observation(STATE), "imported": fake_engine.VALUE,
              "metadata": distribution_observation()}
    if depth:
        result["grandchild"] = child(0)
    writer.send_bytes(json.dumps(result).encode())
    writer.close()

def child(depth):
    import multiprocessing as mp
    context = mp.get_context("spawn")
    reader, writer = context.Pipe(duplex=False)
    process = context.Process(target=target, args=(writer, Prepared(), depth))
    try:
        process.start()
        writer.close()
        require(reader.poll(10))
        data = reader.recv_bytes(16384)
        process.join(10)
        require(process.exitcode == 0)
        return json.loads(data)
    finally:
        reader.close()
        writer.close()
        if process.pid is not None:
            if process.is_alive():
                process.kill()
            process.join(5)
            process.close()

def main():
    import multiprocessing as mp
    import subprocess
    import site
    from multiprocessing import context, resource_tracker, spawn
    if ARGS == ["fresh"]:
        print(json.dumps({"state": observation(STATE), "metadata": distribution_observation()}))
        return
    if ARGS == ["positive"]:
        try:
            # A real semaphore causes the tracker to use the same sealed wrapper.
            lock = mp.Lock()
            result = child(1)
            fresh = subprocess.run([LAYOUT["wrapper"], "fresh"], check=True, capture_output=True, timeout=10)
            result["fresh"] = json.loads(fresh.stdout)
            result["parent"] = observation(STATE)
            result["metadata_parent"] = distribution_observation()
        finally:
            del lock
            result["tracker"] = stop_tracker(STATE)
        print(json.dumps(result))
        return
    if len(ARGS) == 2 and ARGS[0] == "reject":
        name = ARGS[1]
        def forbidden():
            if name == "fork": mp.get_context("fork")
            elif name == "forkserver": mp.get_context("forkserver")
            elif name == "direct-fork": context.ForkProcess(target=lambda: None).start()
            elif name == "direct-forkserver": context.ForkServerProcess(target=lambda: None).start()
            elif name == "method": mp.set_start_method("fork", force=True)
            elif name == "numa": mp.set_executable("/usr/bin/numactl")
            elif name == "ordinary-python": subprocess.run(["/usr/bin/python3", "-c", "raise Exception('private')"])
            elif name == "arbitrary-code": subprocess.run([LAYOUT["wrapper"], "-c", "raise Exception('private')"])
            elif name == "shell": subprocess.run("printf private", shell=True)
            elif name == "site": site.main()
            elif name == "addsitedir": site.addsitedir("/tmp")
            elif name == "sitecustomize": __import__("sitecustomize")
            elif name == "usercustomize": __import__("usercustomize")
            elif name == "path": sys.path.append("/private"); __import__("fake_engine")
            elif name == "env": os.environ["NUMACTL_PYTHON"] = "private"; __import__("fake_engine")
            elif name == "env-known": os.environ["TORCHINDUCTOR_COMPILE_THREADS"] = "2"; __import__("fake_engine")
            elif name == "metadata-outside": list(metadata.distributions(path=["/private"]))
            elif name == "metadata-mixed": list(metadata.distributions(path=LAYOUT["paths"] + ["/private"]))
            elif name == "metadata-relative": list(metadata.distributions(path=["."]))
            elif name == "metadata-string": list(metadata.distributions(path=LAYOUT["paths"][-1]))
            elif name == "metadata-extra": list(metadata.distributions(path=LAYOUT["paths"], private="private"))
            elif name == "metadata-alias": list(metadata.distributions(path=[LAYOUT["paths"][-1] + "/../packages"]))
            elif name == "preparation":
                data = spawn.get_preparation_data("test")
                data["sys_path"] = ["/private"]
                spawn.prepare(data)
            elif name == "main-module":
                data = spawn.get_preparation_data("test")
                data["init_main_from_name"] = "private"
                spawn.prepare(data)
            else: raise AssertionError("unknown test")
        try:
            forbidden()
        except ValueError as exc:
            print(json.dumps({"rejected": True, "message": str(exc), "failures": STATE["failures"],
                              "first_state_mismatch": STATE["first_state_mismatch"],
                              "first_denied_process": STATE["first_denied_process"]}))
            return
        raise AssertionError("accepted forbidden operation")
    dispatch(ARGS, STATE)

if __name__ == "__main__":
    main()
'''

# Original synthetic model of the pinned backport's separate Context and
# registration decorator. It delegates filesystem parsing to stdlib metadata;
# no upstream package or engine is imported by these regression fixtures.
BACKPORT_COMPAT = '''import sys
def install(cls):
    sys.meta_path.append(cls())
    return cls
'''
BACKPORT = '''from importlib import metadata as std
import importlib, itertools, sys
from ._compat import install
class DistributionFinder:
    class Context:
        name = None
        def __init__(self, **kwargs): self.__dict__.update(kwargs)
        @property
        def path(self): return self.__dict__.get('path', sys.path)
@install
class MetadataPathFinder(DistributionFinder):
    @classmethod
    def find_distributions(cls, context=DistributionFinder.Context()):
        return (PathDistribution(d._path) for d in std.MetadataPathFinder.find_distributions(
            std.DistributionFinder.Context(path=context.path, name=context.name)))
class EntryPoint(std.EntryPoint):
    def load(self):
        module, attr = self.value.split(':')
        return getattr(importlib.import_module(module), attr)
class PathDistribution(std.PathDistribution):
    @property
    def entry_points(self):
        return std.EntryPoints(EntryPoint(p.name, p.value, p.group)._for(self) for p in super().entry_points)
def distributions(**kwargs):
    context = kwargs.pop('context', None) or DistributionFinder.Context(**kwargs)
    return itertools.chain.from_iterable(f.find_distributions(context) for f in sys.meta_path if hasattr(f, 'find_distributions'))
def distribution(name): return next(iter(distributions(name=name)))
def version(name): return distribution(name).version
def entry_points(**kwargs):
    return std.EntryPoints(p for d in distributions() for p in d.entry_points).select(**kwargs)
'''


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="nib-smoke-unit-", dir="/private/tmp" if sys.platform == "darwin" else None)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.launcher = self.root / "launch.py"
        self.wrapper = self.root / "python"
        self.launcher.write_bytes(SOURCE + DRIVER.encode())
        package = self.root / "packages"
        package.mkdir()
        (package / "fake_engine.py").write_text("VALUE = 'synthetic-only'\n")
        distribution = package / "review_fake-1.0.dist-info"
        distribution.mkdir()
        (distribution / "METADATA").write_text("Metadata-Version: 2.1\nName: review-fake\nVersion: 1.0\n")
        (distribution / "entry_points.txt").write_text("[review.test]\nfixture = never_load_plugin:entry\n")
        (package / "never_load_plugin.py").write_text("raise AssertionError('entrypoint must not be loaded')\n")
        for name in ("sitecustomize", "usercustomize"):
            (package / (name + ".py")).write_text("raise AssertionError('private hook must never run')\n")
        (package / "unknown.pth").write_text("import sitecustomize\n")
        stdlib = [sysconfig.get_path("stdlib"), sysconfig.get_config_var("DESTSHARED")]
        self.env = {**smoke.IMPORT_ENVIRONMENT, "PATH": "/usr/bin:/bin", "HOME": str(self.root), "LC_ALL": "C.UTF-8",
                    "VLLM_WORKER_MULTIPROC_METHOD": "spawn", "VLLM_PLUGINS": ""}
        # macOS may add its fixed per-account text encoding at interpreter start.
        # Observe this local test interpreter only; production has a fixed Linux env.
        env_probe = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", "import os,json;print(json.dumps(dict(os.environ)))"],
                                   cwd="/", env=self.env, capture_output=True, check=True)
        self.env = json.loads(env_probe.stdout)
        self.layout = {"paths": stdlib + [str(package)], "stdlib_roots": stdlib,
                       "launcher": str(self.launcher), "wrapper": str(self.wrapper), "environment": self.env}
        self.publish_layout()

    def publish_layout(self):
        self.layout["environment"] = self.env
        command = ["/usr/bin/env", "-i", *[key + "=" + value for key, value in self.env.items()],
                   sys.executable, "-I", "-S", "-B", str(self.launcher)]
        self.wrapper.write_text("#!/bin/sh\nexec " + shlex.join(command) + ' "$@"\n')
        self.wrapper.chmod(0o700)
        (self.root / "layout.json").write_text(json.dumps(self.layout))

    def run_fixture(self, *args):
        result = subprocess.run([str(self.wrapper), *args], cwd="/", env=self.env,
                                capture_output=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stderr, b"", result.stderr.decode())
        return json.loads(result.stdout)

    def metadata_fixture(self):
        package = self.root / "packages"
        backport = package / "importlib_metadata"
        backport.mkdir()
        (backport / "__init__.py").write_text(BACKPORT)
        (backport / "_compat.py").write_text(BACKPORT_COMPAT)
        distribution = package / "importlib_metadata-8.7.1.dist-info"
        distribution.mkdir()
        (distribution / "METADATA").write_text("Metadata-Version: 2.1\nName: importlib_metadata\nVersion: 8.7.1\n")
        context = package / "opentelemetry/context"
        context.mkdir(parents=True)
        (context.parent / "__init__.py").write_text("")
        (context / "__init__.py").write_text(
            "import os\nfrom importlib_metadata import entry_points\n"
            "RUNTIME = next(iter(entry_points(group='opentelemetry_context', name=os.environ['OTEL_PYTHON_CONTEXT']))).load()()\n"
            "assert os.environ['OTEL_PROPAGATORS'] == 'none'\n")
        (context / "contextvars_context.py").write_text("class ContextVarsRuntimeContext:\n    VALUE = 'synthetic-context'\n")
        dist = package / "opentelemetry_api-1.40.0.dist-info"
        dist.mkdir()
        (dist / "METADATA").write_text("Metadata-Version: 2.1\nName: opentelemetry-api\nVersion: 1.40.0\n")
        (dist / "entry_points.txt").write_text(
            "[opentelemetry_context]\ncontextvars_context = opentelemetry.context.contextvars_context:ContextVarsRuntimeContext\n"
            "[opentelemetry_propagator]\ntracecontext = never_load_plugin:entry\n"
            "[vllm.general_plugins]\nforbidden = never_load_plugin:entry\n")
        pins = {path: bootstrap.hashlib.sha256((package / path).read_bytes()).hexdigest() for path in bootstrap.METADATA_SOURCES}
        self.fixture_prefix = SOURCE + ("\nMETADATA_SOURCES = " + repr(pins) + "\n").encode()
        driver = DRIVER.replace("STATE = install(LAYOUT)",
            "STATE = install(LAYOUT)\nimport importlib_metadata as backport\nimport opentelemetry.context as otel_context\n"
            "assert not getattr(sys.modules[CONTEXT_MODULE], 'unexpected_pyc_executed', False)")
        driver = driver.replace('    points = metadata.entry_points(group="review.test")',
            '    points = metadata.entry_points(group="review.test")\n'
            '    assert backport.version("review-fake") == metadata.version("review-fake") == "1.0"\n'
            '    assert [(p.name, p.value) for p in backport.entry_points(group="review.test")] == [(p.name, p.value) for p in points]\n'
            '    assert otel_context.RUNTIME.VALUE == "synthetic-context"\n'
            '    assert STATE["entrypoint_loads"] >= 1\n'
            '    before_loads = STATE["entrypoint_loads"]\n'
            '    for api in (metadata, backport):\n'
            '        entry = next(iter(api.entry_points(group="opentelemetry_context", name="contextvars_context")))\n'
            '        assert entry.load()().VALUE == "synthetic-context"\n'
            '    assert STATE["entrypoint_loads"] == before_loads + 2\n'
            '    assert len(sys.meta_path) == 3\n'
            '    assert not any(isinstance(f, backport.MetadataPathFinder) for f in sys.meta_path)')
        driver = driver.replace('            if name == "fork":',
            '            if name == "backport-outside": list(backport.distributions(path=["/private"]))\n'
            '            elif name == "backport-direct-outside": list(backport.MetadataPathFinder.find_distributions(backport.DistributionFinder.Context(path=["/private"])))\n'
            '            elif name == "backport-extra": list(backport.distributions(private="private"))\n'
            '            elif name == "unknown-finder": sys.meta_path.append(object()); metadata.version("review-fake")\n'
            '            elif name == "unrestricted-finder": sys.meta_path.append(importlib.machinery.PathFinder); backport.version("review-fake")\n'
            '            elif name.startswith("entry-"):\n'
            '                api = metadata if name.startswith("entry-stdlib-") else backport\n'
            '                variant = name.rsplit("-", 1)[1]\n'
            '                group, label, target = CONTEXT_ENTRYPOINT\n'
            '                if variant == "target": target = "never_load_plugin:entry"\n'
            '                if variant == "name": label = "unexpected"\n'
            '                if variant == "group": group = "vllm.general_plugins"\n'
            '                entry = api.EntryPoint(label, target, group)\n'
            '                if variant != "unowned": entry._for(api.distribution("opentelemetry-api"))\n'
            '                entry.load()\n'
            '            elif name == "fork":')
        self.launcher.write_bytes(self.fixture_prefix + driver.encode())
        return driver

    def assert_state(self, row):
        self.assertTrue(all(row[key] for key in ("isolated", "no_site", "dont_write_bytecode", "before_unpickle")))
        self.assertFalse(any(row["rejections"].values()))
        self.assertEqual(row["path_sha256"], bootstrap.hashlib.sha256((json.dumps(self.layout["paths"], sort_keys=True, indent=2) + "\n").encode()).hexdigest())

    def test_real_spawn_grandchild_fresh_and_resource_tracker(self):
        row = self.run_fixture("positive")
        for stage in (row, row["grandchild"]):
            self.assertEqual(stage["imported"], "synthetic-only")
            self.assert_state(stage["before"])
            self.assert_state(stage["after"])
        self.assert_state(row["fresh"]["state"])
        self.assert_state(row["parent"])
        self.assertEqual(row["tracker"], {"launched": True, "exit_zero": True})
        self.assertFalse(list(self.root.rglob("*.pyc")))

    def test_metadata_discovery_parent_spawn_grandchild_and_fresh_without_plugin_load(self):
        row = self.run_fixture("positive")
        expected = {"before": ["1.0"], "version": "1.0", "points": [["fixture", "never_load_plugin:entry"]],
                    "plugin_loaded": False, "pathfinder_restored": False, "first_denied_process": None}
        for observation in (row["metadata_parent"], row["metadata"], row["grandchild"]["metadata"], row["fresh"]["metadata"]):
            self.assertEqual(observation, expected)
        for name in ("metadata-outside", "metadata-mixed", "metadata-relative", "metadata-string", "metadata-extra", "metadata-alias"):
            with self.subTest(name=name):
                rejected = self.run_fixture("reject", name)
                self.assertTrue(rejected["rejected"])
                self.assertEqual(rejected["message"], "sealed Python operation rejected")
                self.assertGreater(rejected["failures"]["import"], 0)
                self.assertNotIn("private", json.dumps(rejected))

    def test_backport_and_stdlib_metadata_child_propagation_with_exact_context_entrypoint(self):
        self.metadata_fixture()
        row = self.run_fixture("positive")
        for observation in (row["metadata_parent"], row["metadata"], row["grandchild"]["metadata"], row["fresh"]["metadata"]):
            self.assertEqual(observation["version"], "1.0")
            self.assertFalse(observation["plugin_loaded"])
            self.assertFalse(observation["pathfinder_restored"])
        for stage in (row, row["grandchild"]):
            self.assert_state(stage["before"])
            self.assert_state(stage["after"])

    def test_backport_rejects_unknown_finders_contexts_and_entrypoint_loads(self):
        self.metadata_fixture()
        names = ["backport-outside", "backport-direct-outside", "backport-extra", "unknown-finder", "unrestricted-finder"]
        names += ["entry-" + api + "-" + variant for api in ("stdlib", "backport") for variant in ("target", "name", "group", "unowned")]
        for name in names:
            with self.subTest(name=name):
                row = self.run_fixture("reject", name)
                self.assertTrue(row["rejected"])
                self.assertEqual(row["message"], "sealed Python operation rejected")
                self.assertGreater(sum(row["failures"].values()), 0)
                self.assertNotIn("private", json.dumps(row))

    def test_backport_and_context_source_or_version_changes_reject(self):
        self.metadata_fixture()
        paths = list(bootstrap.METADATA_SOURCES) + ["importlib_metadata-8.7.1.dist-info/METADATA", "opentelemetry_api-1.40.0.dist-info/METADATA"]
        for name in paths:
            with self.subTest(name=name):
                path = self.root / "packages" / name
                original = path.read_bytes()
                path.write_bytes(original.replace(b"Version: ", b"Version: changed-") if name.endswith("METADATA") else original + b"\n# changed\n")
                try:
                    result = subprocess.run([str(self.wrapper), "positive"], cwd="/", env=self.env, capture_output=True, timeout=15)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(result.stdout, b"")
                finally:
                    path.write_bytes(original)

    def test_context_loader_executes_verified_source_not_timestamp_valid_bytecode(self):
        self.metadata_fixture()
        source = self.root / "packages/opentelemetry/context/contextvars_context.py"
        original = source.read_bytes()
        cache = Path(bootstrap.importlib.util.cache_from_source(str(source)))
        cache.parent.mkdir()
        forged = compile(b"unexpected_pyc_executed = True\n" + original, str(source), "exec")
        cache.write_bytes(bootstrap.importlib.util.MAGIC_NUMBER + struct.pack("<III", 0, int(source.stat().st_mtime), len(original))
                          + marshal.dumps(forged))
        # Positive control: ordinary source loading accepts the forged cache
        # despite -B, which only disables writes. No upstream code is involved.
        control = subprocess.run([sys.executable, "-I", "-S", "-B", "-c",
            "import importlib.util,sys; s=importlib.util.spec_from_file_location('control',sys.argv[1]); "
            "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print(m.unexpected_pyc_executed)", str(source)],
            cwd="/", env=self.env, capture_output=True, check=True, timeout=10)
        self.assertEqual(control.stdout, b"True\n")
        self.assertEqual(source.read_bytes(), original)
        row = self.run_fixture("positive")
        for stage in (row, row["grandchild"]):
            self.assert_state(stage["after"])
        self.assertEqual(source.read_bytes(), original)

    def test_context_loader_rejects_alternate_sealed_root_shadow(self):
        self.metadata_fixture()
        alternate = self.root / "alternate"
        package = alternate / "opentelemetry/context"
        package.mkdir(parents=True)
        (package.parent / "__init__.py").write_text("")
        (package / "__init__.py").write_bytes((self.root / "packages/opentelemetry/context/__init__.py").read_bytes())
        (package / "contextvars_context.py").write_text("print('unexpected-shadow-executed')\n")
        self.layout["paths"].insert(-1, str(alternate))
        (self.root / "layout.json").write_text(json.dumps(self.layout))
        result = subprocess.run([str(self.wrapper), "positive"], cwd="/", env=self.env, capture_output=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"sealed Python operation rejected", result.stderr)

    def test_context_loader_rejects_preloaded_injected_or_replaced_module_cache(self):
        driver = self.metadata_fixture()
        injection = "import types\nsys.modules[CONTEXT_MODULE] = types.ModuleType(CONTEXT_MODULE)\n"
        for variant in ("before-install", "before-import", "after-import", "changed-target"):
            with self.subTest(variant=variant):
                if variant == "before-install":
                    changed = driver.replace("STATE = install(LAYOUT)", injection + "STATE = install(LAYOUT)")
                elif variant == "before-import":
                    changed = driver.replace("import opentelemetry.context as otel_context", injection + "import opentelemetry.context as otel_context")
                else:
                    alteration = (injection if variant == "after-import" else
                                  "sys.modules[CONTEXT_MODULE].ContextVarsRuntimeContext = lambda: None\n")
                    changed = driver.replace("def distribution_observation():", alteration + "\ndef distribution_observation():")
                self.launcher.write_bytes(self.fixture_prefix + changed.encode())
                result = subprocess.run([str(self.wrapper), "fresh"], cwd="/", env=self.env, capture_output=True, timeout=15)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, b"")
                self.assertIn(b"sealed Python", result.stderr)

    def test_real_smoke_child_functions_import_only_fake_package_after_bootstrap(self):
        runtime = (ROOT / "scripts/engine_import_smoke.py").read_bytes()
        (self.root / "packages/smoke_runtime.py").write_bytes(SOURCE + b"\n" + runtime)
        driver = DRIVER.replace("STATE = install(LAYOUT)",
            "STATE = install(LAYOUT)\nimport smoke_runtime\nsmoke_runtime.MODULES = ('fake_engine',)")
        driver = driver.replace('    if ARGS == ["fresh"]:',
            '    if ARGS == ["runtime"]:\n'
            '        try: result = smoke_runtime.child(1)\n'
            '        finally: stop_tracker(STATE)\n'
            '        print(json.dumps(result)); return\n'
            '    if ARGS == ["fresh"]:')
        self.launcher.write_bytes(SOURCE + driver.encode())
        row = self.run_fixture("runtime")
        for stage in (row, row["grandchild"]):
            self.assert_state(stage["before_unpickle"])
            self.assert_state(stage["state"])
            self.assertEqual(stage["imports"], [{"module": "fake_engine", "status": "pass", "error_category": None}])
            self.assertFalse(any(stage["guard_rejections"].values()))

    def test_pinned_import_environment_actions_do_not_mutate_sealed_state(self):
        # Synthetic import actions matching the four pinned vLLM assignments;
        # no upstream module, plugin, CUDA code or compiler is executed.
        (self.root / "packages/fake_engine.py").write_text(
            "import os\n"
            "before = dict(os.environ)\n"
            "os.environ['PYTORCH_NVML_BASED_CUDA_CHECK'] = '1'\n"
            "os.environ['TORCHINDUCTOR_COMPILE_THREADS'] = '1'\n"
            "os.environ.setdefault('TRITON_CACHE_AUTOTUNING', '1')\n"
            "os.environ.setdefault('TILELANG_CLEANUP_TEMP_FILES', '1')\n"
            "assert dict(os.environ) == before\n"
            "assert os.getenv('TORCH_DEVICE_BACKEND_AUTOLOAD', '1') != '1'\n"
            "assert int(os.environ['TORCHINDUCTOR_COMPILE_THREADS']) <= 1\n"
            "VALUE = 'synthetic-only'\n")
        row = self.run_fixture("positive")
        for stage in (row, row["grandchild"]):
            self.assertEqual(stage["imported"], "synthetic-only")
            self.assert_state(stage["after"])
        rejected = self.run_fixture("reject", "env-known")
        self.assertTrue(rejected["rejected"])
        self.assertEqual(rejected["message"], "sealed Python operation rejected")
        self.assertGreater(rejected["failures"]["state"], 0)

    def test_numpy_style_temporary_environment_requires_both_preconfigured_values_in_every_process(self):
        # Original synthetic reproduction of the pinned NumPy conditional
        # additions, nested import and finally restoration; no NumPy import.
        (self.root / "packages/fake_engine.py").write_text(
            "import os\nadded = []\n"
            "for key in ('OPENBLAS_MAIN_FREE', 'GOTOBLAS_MAIN_FREE'):\n"
            "    if key not in os.environ:\n        os.environ[key] = '1'\n        added.append(key)\n"
            "try:\n    import fake_multiarray\n"
            "finally:\n    for key in added:\n        del os.environ[key]\n"
            "VALUE = 'synthetic-only'\n")
        (self.root / "packages/fake_multiarray.py").write_text("VALUE = 'synthetic-only'\n")
        attempt = '''def attempt_import():
    before = dict(os.environ)
    try:
        import fake_engine
        passed = True
    except ValueError as error:
        assert str(error) == "sealed Python operation rejected"
        passed = False
    assert dict(os.environ) == before
    STATE["check"]()  # restored equality must not erase an earlier rejection
    return {"passed": passed, "first": STATE["first_state_mismatch"], "failures": dict(STATE["failures"])}

'''
        driver = DRIVER.replace("def target(writer, prepared, depth):", attempt + "def target(writer, prepared, depth):")
        driver = driver.replace("    import fake_engine\n    result =", "    imported = attempt_import()\n    result =")
        driver = driver.replace('"imported": fake_engine.VALUE', '"imported": imported')
        driver = driver.replace('"state": observation(STATE), "metadata": distribution_observation()',
                                '"state": observation(STATE), "metadata": distribution_observation(), "attempt": attempt_import()')
        driver = driver.replace("            result = child(1)", "            parent_attempt = attempt_import()\n            result = child(1)\n            result['parent_attempt'] = parent_attempt")
        self.launcher.write_bytes(SOURCE + driver.encode())
        baseline = dict(self.env)
        keys = ("OPENBLAS_MAIN_FREE", "GOTOBLAS_MAIN_FREE")
        for present in ((), keys[:1], keys[1:], keys):
            with self.subTest(present=present):
                self.env = {key: value for key, value in baseline.items() if key not in keys or key in present}
                self.publish_layout()
                row = self.run_fixture("positive")
                for observed in (row["parent_attempt"], row["imported"], row["grandchild"]["imported"], row["fresh"]["attempt"]):
                    passed = present == keys
                    self.assertIs(observed["passed"], passed)
                    self.assertEqual(observed["first"], None if passed else "env")
                    self.assertEqual(observed["failures"], {key: int(not passed and key == "state") for key in observed["failures"]})
                self.assertEqual(row["tracker"], {"launched": True, "exit_zero": True})

    def test_first_state_mismatch_is_fixed_sticky_and_does_not_relax_checks(self):
        mutations = {
            "paths": "sys.path.append('/synthetic-private')",
            "argv": "sys.argv.append('synthetic-private')",
            "executable": "sys.executable = '/synthetic-private'",
            "env": "os.environ['SYNTHETIC_PRIVATE'] = 'synthetic-private'",
            "cwd": "os.chdir(LAYOUT['paths'][-1])",
            "method": "mp.get_start_method = lambda: 'fork'",
            "pycache": "sys.pycache_prefix = '/synthetic-private'",
            "finders": "sys.meta_path.append(object())",
            "openblas": "os.environ['OPENBLAS_MAIN_FREE'] = '0'",
            "gotoblas": "os.environ['GOTOBLAS_MAIN_FREE'] = '0'",
        }
        for field, change in mutations.items():
            driver = DRIVER.replace('            if name == "fork":',
                '            if name == "diagnostic":\n                ' + change + '\n                STATE["check"]()\n'
                '            elif name == "fork":')
            self.launcher.write_bytes(SOURCE + driver.encode())
            with self.subTest(field=field):
                row = self.run_fixture("reject", "diagnostic")
                self.assertEqual(row["first_state_mismatch"], "env" if field in ("openblas", "gotoblas") else field)
                self.assertEqual(row["failures"]["state"], 1)
                self.assertEqual(row["message"], "sealed Python operation rejected")
                self.assertNotIn("synthetic-private", json.dumps(row))
        driver = DRIVER.replace('            if name == "fork":',
            '            if name == "sticky":\n'
            '                os.environ["SYNTHETIC_PRIVATE"] = "synthetic-private"\n'
            '                try: STATE["check"]()\n'
            '                except ValueError: pass\n'
            '                del os.environ["SYNTHETIC_PRIVATE"]\n'
            '                STATE["check"]()\n'
            '                sys.path.append("/synthetic-private")\n'
            '                STATE["check"]()\n'
            '            elif name == "fork":')
        self.launcher.write_bytes(SOURCE + driver.encode())
        row = self.run_fixture("reject", "sticky")
        self.assertEqual(row["first_state_mismatch"], "env")
        self.assertEqual(row["failures"]["state"], 2)
        self.assertNotIn("synthetic-private", json.dumps(row))

    def test_launch_state_and_import_rejections_are_fixed(self):
        for name in ("fork", "forkserver", "direct-fork", "direct-forkserver", "method", "numa", "ordinary-python",
                     "arbitrary-code", "shell", "site", "addsitedir", "sitecustomize", "usercustomize", "path", "env",
                     "preparation", "main-module"):
            with self.subTest(name=name):
                row = self.run_fixture("reject", name)
                self.assertTrue(row["rejected"])
                self.assertEqual(row["message"], "sealed Python operation rejected")
                self.assertGreater(sum(row["failures"].values()), 0)
                self.assertNotIn("private", json.dumps(row))

    def test_process_command_classes_require_exact_bounded_plain_values(self):
        class Hostile:
            def __str__(self): raise AssertionError("must not stringify")
            def __eq__(self, other): raise AssertionError("must not compare")
        class String(str):
            def __eq__(self, other): raise AssertionError("must not compare subclass")
        for shape, expected in bootstrap.PROCESS_SHAPES.items():
            for args in (list(shape), shape, [s.encode() for s in shape]):
                self.assertEqual(bootstrap.process_command(args[0], args), expected)
            executable, flag = shape
            for args in ([executable, flag, "synthetic-private"], [executable, flag + " "],
                         [executable, flag.upper()], ["/private/" + executable, flag],
                         [executable + "-private", flag], [executable, "private"],
                         [executable, flag, ""], [], shape[0] + " " + flag,
                         [Hostile(), flag], [String(executable), flag],
                         [executable, b"\xff"], [executable, "x" * 10000]):
                with self.subTest(args_type=type(args).__name__):
                    self.assertEqual(bootstrap.process_command(executable, args), "other")
            self.assertEqual(bootstrap.process_command("synthetic-private", list(shape)), "other")
            self.assertEqual(bootstrap.process_command(Hostile(), list(shape)), "other")
        for executable in ("/usr/bin/uname", "./uname", "UNAME", "/private/../uname"):
            self.assertEqual(bootstrap.process_command(executable, [executable, "-p"]), "other")
        for executable in ("ldconfig", "/usr/sbin/ldconfig", "/sbin/../sbin/ldconfig", "/private/ldconfig"):
            self.assertEqual(bootstrap.process_command(executable, [executable, "-p"]), "other")

    def process_fixture(self, operation):
        driver = DRIVER.replace('            if name == "fork":',
            '            if name == "diagnostic":\n' +
            ''.join('                ' + line + '\n' for line in operation.splitlines()) +
            '            elif name == "fork":')
        self.launcher.write_bytes(SOURCE + driver.encode())
        return self.run_fixture("reject", "diagnostic")

    def test_process_diagnostics_cover_audit_spawnv_and_do_not_execute_denied_commands(self):
        for shape, expected in bootstrap.PROCESS_SHAPES.items():
            row = self.process_fixture(f"subprocess.run({list(shape)!r}, check=True)")
            self.assertEqual(row["first_denied_process"],
                             {"event": "subprocess.Popen", "command": expected, "caller": "other", "callsites": row["first_denied_process"]["callsites"]})
            self.assertEqual(row["failures"], {key: int(key == "process") for key in row["failures"]})
            self.assertIsNone(row["first_state_mismatch"])
            self.assertEqual(row["message"], "sealed Python operation rejected")
        cases = {
            "os.exec": "sys.audit('os.exec', 'g++', ['g++', '--version'], {'PRIVATE': 'private'})",
            "os.posix_spawn": "sys.audit('os.posix_spawn', 'g++', ['g++', '--version'], {'PRIVATE': 'private'})",
            "os.fork": "sys.audit('os.fork')",
            "os.forkpty": "sys.audit('os.forkpty')",
            "os.system": "sys.audit('os.system', b'private --secret')",
            "multiprocessing.spawnv_passfds": "from multiprocessing import util\nutil.spawnv_passfds(LAYOUT['wrapper'], [LAYOUT['wrapper'], '-c', 'private'], [])",
        }
        for event, operation in cases.items():
            row = self.process_fixture(operation)
            self.assertEqual(row["first_denied_process"], {"event": event,
                "command": "gxx-version" if event in ("os.exec", "os.posix_spawn") else "other", "caller": "other",
                "callsites": row["first_denied_process"]["callsites"]})
            self.assertEqual(row["failures"]["process"], 1)
            self.assertNotIn("private", json.dumps(row))
        sentinel = self.root / "sentinel"
        executable = self.root / "private-executable"
        executable.write_text("#!/bin/sh\ntouch " + shlex.quote(str(sentinel)) + "\n")
        executable.chmod(0o700)
        row = self.process_fixture(f"subprocess.run([{str(executable)!r}, '--version'], env={{'PRIVATE': 'private'}})")
        self.assertEqual(row["first_denied_process"]["command"], "other")
        self.assertFalse(sentinel.exists())
        self.assertNotIn(str(self.root), json.dumps(row))
        self.assertNotIn("private", json.dumps(row))

    def test_process_diagnostics_keep_first_event_even_after_caught_rejection(self):
        row = self.process_fixture('''try:
    subprocess.run(['uname', '-p'])
except ValueError:
    pass
STATE['check']()
sys.audit('os.system', b'private')''')
        self.assertEqual(row["first_denied_process"],
                         {"event": "subprocess.Popen", "command": "uname-processor", "caller": "other", "callsites": row["first_denied_process"]["callsites"]})
        self.assertEqual(row["failures"]["process"], 2)
        self.assertEqual(row["failures"]["state"], 0)
        self.assertNotIn("private", json.dumps(row))
        row = self.process_fixture('''try:
    sys.audit('os.system', b'private')
except ValueError:
    pass
subprocess.run(['uname', '-p'])''')
        self.assertEqual(row["first_denied_process"],
                         {"event": "os.system", "command": "other", "caller": "other", "callsites": row["first_denied_process"]["callsites"]})
        self.assertEqual(row["failures"]["process"], 2)
        self.assertNotIn("private", json.dumps(row))

    def test_process_caller_is_bounded_exact_module_hint_never_authorization(self):
        for module in (*bootstrap.PROCESS_CALLERS, "private.module", "platform.private", "x" * 10000):
            operation = (f"namespace = {{'__name__': {module!r}, 'subprocess': subprocess}}\n"
                         "exec(compile(\"def call(): subprocess.run(['g++', '--version'])\", '/private/source.py', 'exec'), namespace)\n"
                         "namespace['call']()")
            row = self.process_fixture(operation)
            self.assertEqual(row["first_denied_process"]["caller"], bootstrap.PROCESS_CALLERS.get(module, "other"))
            self.assertEqual(row["first_denied_process"]["command"], "gxx-version")
            self.assertEqual(row["failures"]["process"], 1)
            self.assertNotIn("private", json.dumps(row))
        # A recognized outer frame cannot defeat the fixed scan bound; neither
        # frame filenames nor locals are observed. Unknown inner frames skipped.
        for depth, expected in ((2, "stdlib-platform"), (64, "other")):
            operation = ("unknown = {'__name__': 'private', 'subprocess': subprocess}\n"
                         "exec(\"def nested(n):\\n    if n: return nested(n-1)\\n    subprocess.run(['uname', '-p'])\", unknown)\n"
                         "known = {'__name__': 'platform', 'nested': unknown['nested']}\n"
                         f"exec('nested({depth})', known)")
            row = self.process_fixture(operation)
            self.assertEqual(row["first_denied_process"]["caller"], expected)

    def test_process_diagnostics_parent_spawn_grandchild_fresh_and_tracker(self):
        self.launcher.write_bytes(SOURCE + DRIVER.encode())
        row = self.run_fixture("positive")
        for observed in (row["metadata_parent"], row["metadata"], row["grandchild"]["metadata"], row["fresh"]["metadata"]):
            self.assertIsNone(observed["first_denied_process"])
        self.assertEqual(row["tracker"], {"launched": True, "exit_zero": True})
        # Child construction remains allowed; synthetic imports then exercise
        # the same denied operation independently in each process, not vLLM.
        (self.root / "packages/fake_engine.py").write_text(
            "import subprocess\ntry:\n    subprocess.run(['g++', '--version'])\nexcept ValueError:\n    pass\nVALUE = 'synthetic-only'\n")
        driver = DRIVER.replace('    points = metadata.entry_points(group="review.test")',
                                '    import fake_engine\n    points = metadata.entry_points(group="review.test")')
        self.launcher.write_bytes(SOURCE + driver.encode())
        row = self.run_fixture("positive")
        for observed in (row["metadata_parent"], row["metadata"], row["grandchild"]["metadata"], row["fresh"]["metadata"]):
            self.assertEqual(observed["first_denied_process"],
                             {"event": "subprocess.Popen", "command": "gxx-version", "caller": "other", "callsites": observed["first_denied_process"]["callsites"]})
        self.assertEqual(row["tracker"], {"launched": True, "exit_zero": True})

    def test_callsite_catalog_matches_known_ancestor_for_unknown_command_in_all_children(self):
        raw = ("import subprocess\ntry:\n    subprocess.run(['synthetic-private-command', '-c', 'synthetic-private-script'])\n"
               "except ValueError:\n    pass\nVALUE = 'synthetic-only'\n").encode()
        path = self.root / "packages/fake_engine.py"
        path.write_bytes(raw)
        spec = {"id": "synthetic-source", "root": "packages", "installed": "fake_engine.py", "bytes": len(raw),
                "sha256": bootstrap.hashlib.sha256(raw).hexdigest(),
                "sites": [{"id": "synthetic-import", "qualname": "<module>"}]}
        driver = DRIVER.replace("STATE = install(LAYOUT)",
            f"CATALOG = compiled_catalog([{spec!r}], {{'packages': LAYOUT['paths'][-1]}})\nSTATE = install(LAYOUT, CATALOG)")
        driver = driver.replace('    points = metadata.entry_points(group="review.test")',
                                '    import fake_engine\n    points = metadata.entry_points(group="review.test")')
        self.launcher.write_bytes(SOURCE + driver.encode())
        row = self.run_fixture("positive")
        for observed in (row["metadata_parent"], row["metadata"], row["grandchild"]["metadata"], row["fresh"]["metadata"]):
            denied = observed["first_denied_process"]
            self.assertEqual(denied["command"], "other")
            self.assertEqual(denied["callsites"]["ids"], ["synthetic-import"])
            self.assertEqual(denied["callsites"]["sources"], [{"id": "synthetic-source", "status": "verified"}])
            self.assertFalse(denied["callsites"]["comparison_failed"])
            self.assertNotIn("synthetic-private", json.dumps(denied))
        self.assertEqual(row["tracker"], {"launched": True, "exit_zero": True})

    def test_diagnostic_failure_cannot_mask_original_sticky_deny(self):
        row = self.process_fixture('''def broken(*args): raise RuntimeError('synthetic-private')
globals()['callsite_observation'] = broken
try:
    subprocess.run(['uname', '-p'])
except ValueError:
    pass
STATE['check']()
sys.audit('os.system', b'private')''')
        self.assertEqual(row["message"], "sealed Python operation rejected")
        self.assertEqual(row["failures"]["process"], 2)
        self.assertEqual(row["first_denied_process"]["command"], "uname-processor")
        self.assertTrue(row["first_denied_process"]["callsites"]["comparison_failed"])
        self.assertNotIn("private", json.dumps(row))

    def test_source_pin_does_not_false_match_valid_stale_bytecode(self):
        import importlib.util
        raw = ("import subprocess\ntry:\n    subprocess.run(['synthetic-private', '-c', 'synthetic-private'])\n"
               "except ValueError:\n    pass\nVALUE = 'synthetic-only'\n").encode()
        path = self.root / "packages/fake_engine.py"
        path.write_bytes(raw)
        forged = compile(raw.replace(b"synthetic-only", b"forged-bytecode"), str(path), "exec")
        cached = Path(importlib.util.cache_from_source(str(path)))
        cached.parent.mkdir(exist_ok=True)
        cached.write_bytes(importlib.util.MAGIC_NUMBER + struct.pack("<III", 0, int(path.stat().st_mtime), len(raw)) + marshal.dumps(forged))
        spec = {"id": "synthetic-source", "root": "packages", "installed": "fake_engine.py", "bytes": len(raw),
                "sha256": bootstrap.hashlib.sha256(raw).hexdigest(),
                "sites": [{"id": "synthetic-import", "qualname": "<module>"}]}
        driver = DRIVER.replace("STATE = install(LAYOUT)",
            f"CATALOG = compiled_catalog([{spec!r}], {{'packages': LAYOUT['paths'][-1]}})\nSTATE = install(LAYOUT, CATALOG)")
        driver = driver.replace('    points = metadata.entry_points(group="review.test")',
                                '    import fake_engine\n    points = metadata.entry_points(group="review.test")')
        self.launcher.write_bytes(SOURCE + driver.encode())
        row = self.run_fixture("positive")
        self.assertEqual(row["imported"], "forged-bytecode")
        self.assertEqual(row["grandchild"]["imported"], "forged-bytecode")
        for observed in (row["metadata_parent"], row["metadata"], row["grandchild"]["metadata"], row["fresh"]["metadata"]):
            denied = observed["first_denied_process"]
            self.assertEqual(denied["callsites"]["sources"], [{"id": "synthetic-source", "status": "verified"}])
            self.assertEqual(denied["callsites"]["ids"], [])
            self.assertGreater(denied["callsites"]["unmatched_frames"], 0)
        self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(row["tracker"], {"launched": True, "exit_zero": True})

    def test_only_exact_spawn_and_tracker_forms_parse(self):
        good = ["-B", "-S", "-I", "-c", "from multiprocessing.spawn import spawn_main; spawn_main(tracker_fd=3, pipe_handle=4)", "--multiprocessing-fork"]
        self.assertEqual(bootstrap.route(good, check_fd=False), ("spawn", {"tracker_fd": 3, "pipe_handle": 4}))
        tracker = good[:4] + ["from multiprocessing.resource_tracker import main;main(3)"]
        self.assertEqual(bootstrap.route(tracker, check_fd=False), ("tracker", [3]))
        for args in (good + ["extra"], good[:4] + ["import os; os.system('private')"],
                     good[:4] + ["from multiprocessing.forkserver import main; main(3,4,[])"],
                     ["-I", "-S", "-B"] + good[3:],
                     good[:4] + [good[4].replace("tracker_fd=3", "tracker_fd=True")] + good[5:],
                     good[:4] + [good[4] + "; print('private')"] + good[5:]):
            with self.subTest(args=args), self.assertRaises(ValueError):
                bootstrap.route(args, check_fd=False)

    def test_preparation_rejects_every_mutable_bootstrap_field(self):
        valid = {"log_to_stderr": False, "authkey": b"test", "name": "unit", "sys_path": self.layout["paths"],
                 "sys_argv": [self.layout["launcher"]], "orig_dir": "/", "dir": "/", "start_method": "spawn",
                 "init_main_from_path": self.layout["launcher"]}
        bootstrap.check_preparation(valid, self.layout)
        for key, value in (("sys_path", ["/private"]), ("sys_argv", ["-c"]), ("orig_dir", "/tmp"),
                           ("dir", "/tmp"), ("start_method", "fork"), ("init_main_from_path", "/private.py"),
                           ("init_main_from_name", "private"), ("log_to_stderr", True), ("extra", 1)):
            data = copy.deepcopy(valid)
            data[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                bootstrap.check_preparation(data, self.layout)

    def test_safe_source_reader_rejects_links_fifo_and_size(self):
        source = self.root / "regular"
        source.write_bytes(b"ok")
        self.assertEqual(bootstrap.read_source(str(source)), b"ok")
        with self.assertRaises(ValueError):
            bootstrap.read_source(str(source), 1)
        link = self.root / "link"
        link.symlink_to(source)
        fifo = self.root / "fifo"
        os.mkfifo(fifo)
        hard = self.root / "hard"
        os.link(source, hard)
        for path in (source, link, fifo, hard, self.root):
            with self.subTest(path=path), self.assertRaises((ValueError, OSError)):
                bootstrap.read_source(str(path))

    def test_tracker_nonzero_restart_timeout_and_unknown_ownership_reject(self):
        from multiprocessing import resource_tracker
        for outcome in ("nonzero", "timeout", "restart", "unknown"):
            with self.subTest(outcome=outcome):
                read, write = os.pipe()
                tracker = types.SimpleNamespace(_lock=threading.RLock(), _pid=300000, _fd=read)
                state = {"check": lambda: None, "tracker_pids": [300000]}
                if outcome == "restart": state["tracker_pids"] = [299999, 300000]
                if outcome == "unknown": state["tracker_pids"] = []
                try:
                    with mock.patch.object(resource_tracker, "_resource_tracker", tracker), \
                         mock.patch.object(bootstrap.os, "waitpid", return_value=(0, 0) if outcome == "timeout" else (300000, 256)), \
                         mock.patch.object(bootstrap.os, "kill") as kill, \
                         mock.patch("time.monotonic", side_effect=[0, 11]):
                        with self.assertRaises(ValueError) as caught:
                            bootstrap.stop_tracker(state)
                        self.assertNotIn("300000", str(caught.exception))
                        self.assertEqual(kill.call_count, int(outcome == "timeout"))
                finally:
                    if tracker._fd is not None: os.close(tracker._fd)
                    os.close(write)


if __name__ == "__main__":
    unittest.main()
