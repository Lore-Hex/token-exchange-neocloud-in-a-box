# SPDX-License-Identifier: Apache-2.0
"""Isolated spawn-only lab bootstrap, NOT the P0 process-denying guard."""

import importlib.machinery
import importlib.util
import ast
from contextlib import contextmanager
import ctypes
import hashlib
import os
import re
import stat
import sys
import types

VECISA_SOURCE_SHA = "6cd49584e3cfdab9f0883c5a24d088f992909775f7d400ff0a4cefccbe6998a0"
# setuptools v80.10.2, immutable commit 5cf2d085186f2c8053940076db99045b826ec22a.
# Only this lab adapter changes startup; the P0 bootstrap remains unchanged.
SETUPTOOLS_SOURCES = {
    "setuptools/__init__.py": "bf5ad90eff95604f2db0f6b07f11bc73b8c4c88f1cdfbc805b160efc51664c86",
    "_distutils_hack/__init__.py": "df81e6bcba34ee3e3952f776551fb669143b9490fdd6c4caeb32609f97e985b4",
    "_distutils_hack/override.py": "12efecf8d17a5486780aa774b5b6c0e70b56932d8864f35df1eb7a18bb759b3a",
    "setuptools/_distutils/__init__.py": "c4662e856c0b1b4ec9d10e3d0559c48cfcbac320dc77abde24c0c95fb9639723",
}
SETUPTOOLS_MODULES = {
    "setuptools": "setuptools/__init__.py",
    "_distutils_hack": "_distutils_hack/__init__.py",
    "_distutils_hack.override": "_distutils_hack/override.py",
    "setuptools._distutils": "setuptools/_distutils/__init__.py",
}
# vLLM 5df63a7203cdb9328d824a27b682e77c7b9928be and Torch
# cf30153c4c131c8164ee7798e5022d810682e2cb. These are loader identities,
# not permissions to install an upstream meta-path finder.
PATTERN_ENV = "vllm.env_override"
PATTERN_TARGET = "torch._inductor.pattern_matcher"
PATTERN_SOURCES = {
    PATTERN_ENV: ("vllm/env_override.py", "8a22650f862565a9ce2ba4441d39a99afbcecc2b60664be2272dd18e2df1cd1d"),
    PATTERN_TARGET: ("torch/_inductor/pattern_matcher.py", "56e229fddc235768b78ca50118d1ec935c16ecf131578efebeebb391c71ed5d4"),
}
PATTERN_REGISTER = "_nib_register_pattern_matcher"
# Exactly two pinned implementations of the same virtual-module protocol.
# six 1.17.0: ebd9b3af90247b8858d415a05e96e9ee61e48d07;
# botocore 1.43.114: fca45a8afc1294fb6c1fc3d2d4a9187e415f37e1.
SIX_SOURCES = {
    "six": {"path": "six.py", "bytes": 34703, "distribution": "six", "version": "1.17.0",
            "sha256": "c51c91f703d3d4b3696c923cb5fec213e05e75d9215393befac7f2fa6a3904df"},
    "botocore.vendored.six": {"path": "botocore/vendored/six.py", "bytes": 34549,
                              "distribution": "botocore", "version": "1.43.114",
                              "sha256": "4ce39f422ee71467ccac8bed76beb05f8c321c7f0ceda9279ae2dfa3670106b3"},
}
SIX_REGISTER = "_nib_register_six"
# opencv-python-headless 5.0.0.93, OpenCV 40738fb16ceddb5fb3fea747585f7ce6abb0605b.
# The remaining files are generated wheel contents from the pinned engine image.
OPENCV_SOURCES = {
    "__init__.py": "69192904dead3ada37c8b305ee850ac7474924730881a9d5e0b2390944eb5aae",
    "config.py": "974e2d4096ee1a9a9a341df1bf33e16973683bf1ac733006de27ff2f23bc584d",
    "config-3.py": "9a7aadf724b822001f5e963b01fd4e375d45e2cbbe328d2ca7b42a440c083a1c",
    "version.py": "e718587212508acdcb296839c287c7fa346edb621005ffb199c26397592544ed",
}
OPENCV_NATIVE_SHA = "9e29605abcc31c9942d0e7dffc53b4ccaa601676c794327b0ea71a570a0c26c2"
OPENCV_CALLBACKS = ("_nib_cv2_config", "_nib_cv2_paths", "_nib_cv2_native")
# nvidia-cutlass-dsl-libs-core 4.8.0, public wheel SHA-256
# b32716d2ed10c185cdbbb6b5c6fca26a3113a60e6e2fe4e2f0cb15966422b621.
# Its compiler imports siblings relatively; do not expose them as top-level modules.
CUTLASS_COMPILER = "cutlass.base_dsl.compiler"
CUTLASS_SOURCE = "nvidia_cutlass_dsl/dsl_packages/cutlass/base_dsl/compiler.py"
CUTLASS_SOURCE_SHA = "26a940ac92440a63561411b0818c3f88da363b1247ab54fcb5572d242d382c3b"
# TileLang 0.1.12 Linux abi3 wheel bbeb5573cbe2544a51a5c9a6cc737c5e3b5c2d95411caa3687fd9b08eb9d5f97.
# Its bundled TVM and Cython wrapper are private namespaces, not general search roots.
TILELANG_ENV_SHA = "0c1aeb8521890e4822d84052171c2573ba9d72849d45d40f21014166d0add639"
TILELANG_TVM_SHA = "0121abb909736236a4300622968c5fdced080f3b124860344a986a1cf5002943"
TILELANG_NATIVE_SHA = "ffa5c118c75e3dabdbeff8d6ae1d91d07a08f66c8d14c449d13ba0b5f89c4604"
TILELANG_CALLBACKS = ("_nib_tilelang_libs", "_nib_tilelang_tvm")
SIX_NAMES = (
    "moves", "moves.BaseHTTPServer", "moves.CGIHTTPServer", "moves.SimpleHTTPServer",
    "moves._dummy_thread", "moves._thread", "moves.builtins", "moves.cPickle",
    "moves.collections_abc", "moves.configparser", "moves.copyreg", "moves.dbm_gnu",
    "moves.dbm_ndbm", "moves.email_mime_base", "moves.email_mime_image",
    "moves.email_mime_multipart", "moves.email_mime_nonmultipart", "moves.email_mime_text",
    "moves.html_entities", "moves.html_parser", "moves.http_client", "moves.http_cookiejar",
    "moves.http_cookies", "moves.queue", "moves.reprlib", "moves.socketserver", "moves.tkinter",
    "moves.tkinter_colorchooser", "moves.tkinter_commondialog", "moves.tkinter_constants",
    "moves.tkinter_dialog", "moves.tkinter_dnd", "moves.tkinter_filedialog", "moves.tkinter_font",
    "moves.tkinter_messagebox", "moves.tkinter_scrolledtext", "moves.tkinter_simpledialog",
    "moves.tkinter_tix", "moves.tkinter_tkfiledialog", "moves.tkinter_tksimpledialog",
    "moves.tkinter_ttk", "moves.urllib", "moves.urllib.error", "moves.urllib.parse",
    "moves.urllib.request", "moves.urllib.response", "moves.urllib.robotparser",
    "moves.urllib_error", "moves.urllib_parse", "moves.urllib_request", "moves.urllib_response",
    "moves.urllib_robotparser", "moves.xmlrpc_client", "moves.xmlrpc_server",
)


def opencv_source_code(raw, path, require):
    """Replace three exact loader operations, preserving upstream Python exports."""
    require(hashlib.sha256(raw).hexdigest() == OPENCV_SOURCES["__init__.py"])
    tree = ast.parse(raw, filename=path)
    require(not any(isinstance(node, ast.Name) and node.id in OPENCV_CALLBACKS for node in ast.walk(tree)))
    replacements = (
        ("from .load_config_py3 import exec_file_wrapper", "exec_file_wrapper = _nib_cv2_config"),
        ("for p in reversed(l_vars['PYTHON_EXTENSIONS_PATHS']):\n"
         "    sys.path.insert(1 if not applySysPathWorkaround else 0, p)",
         "_nib_cv2_paths(l_vars['PYTHON_EXTENSIONS_PATHS'], l_vars['BINARIES_PATHS'])"),
        ('native_module = importlib.import_module("cv2")', 'native_module = _nib_cv2_native()'),
    )
    for original, replacement in replacements:
        expected = ast.dump(ast.parse(original).body[0])
        matches = [node for node in ast.walk(tree) if ast.dump(node) == expected]
        require(len(matches) == 1)
        match = matches[0]
        class Replace(ast.NodeTransformer):
            def visit(self, node):
                if node is match:
                    return ast.copy_location(ast.parse(replacement).body[0], node)
                return super().visit(node)
        tree = Replace().visit(tree)
    return compile(ast.fix_missing_locations(tree), path, "exec", flags=0, dont_inherit=True, optimize=0)


def six_source_code(raw, path, name, require):
    """Replace only the final pinned registration, not six's alias machinery."""
    require(name in SIX_SOURCES)
    expected_source = SIX_SOURCES[name]
    require(len(raw) == expected_source["bytes"] and hashlib.sha256(raw).hexdigest() == expected_source["sha256"])
    tree = ast.parse(raw, filename=path)
    require(not any(isinstance(node, ast.Name) and node.id == SIX_REGISTER for node in ast.walk(tree)))
    expected = ast.parse("sys.meta_path.append(_importer)").body[0]
    require(ast.dump(tree.body[-1]) == ast.dump(expected))
    call = tree.body[-1].value
    call.func = ast.copy_location(ast.Name(id=SIX_REGISTER, ctx=ast.Load()), call.func)
    return compile(tree, path, "exec", flags=0, dont_inherit=True, optimize=0)


def pattern_env_code(raw, path, require):
    """Adapt one pinned registration call; leave its version gate/callback intact."""
    require(hashlib.sha256(raw).hexdigest() == PATTERN_SOURCES[PATTERN_ENV][1])
    tree = ast.parse(raw, filename=path)
    require(not any(isinstance(node, ast.Name) and node.id == PATTERN_REGISTER for node in ast.walk(tree)))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id == "_patch_inductor_pattern_matcher"]
    require(len(calls) == 1 and not calls[0].args and not calls[0].keywords)
    calls[0].func.id = PATTERN_REGISTER
    return compile(tree, path, "exec", flags=0, dont_inherit=True, optimize=0)


def cutlass_compiler_code(raw, path, require):
    """Omit one pinned global search-path append; preserve relative imports."""
    require(hashlib.sha256(raw).hexdigest() == CUTLASS_SOURCE_SHA)
    tree = ast.parse(raw, filename=path)
    expected = ast.dump(ast.parse("sys.path.append(_SCRIPT_PATH)").body[0])
    matches = [node for node in ast.walk(tree) if ast.dump(node) == expected]
    require(len(matches) == 1 and matches[0] in tree.body)
    index = tree.body.index(matches[0])
    tree.body[index] = ast.copy_location(ast.Pass(), matches[0])
    return compile(ast.fix_missing_locations(tree), path, "exec", flags=0, dont_inherit=True, optimize=0)


def tilelang_env_code(raw, path, require):
    """Replace exactly two path mutations, not the upstream compiler behavior."""
    require(hashlib.sha256(raw).hexdigest() == TILELANG_ENV_SHA)
    tree = ast.parse(raw, filename=path)
    require(not any(isinstance(node, ast.Name) and node.id in TILELANG_CALLBACKS for node in ast.walk(tree)))
    replacements = (
        ("for lib in TL_LIBS:\n    if lib not in sys.path:\n        sys.path.insert(0, lib)",
         "_nib_tilelang_libs(TL_LIBS, DEV)"),
        ('def prepend_pythonpath(path):\n'
         '    if not os.environ.get("PYTHONPATH", None):\n        os.environ["PYTHONPATH"] = path\n'
         '    else:\n        os.environ["PYTHONPATH"] = path + os.pathsep + os.environ["PYTHONPATH"]\n'
         '    sys.path.insert(0, path)',
         'def prepend_pythonpath(path):\n    _nib_tilelang_tvm(path)'),
    )
    for original, replacement in replacements:
        expected = ast.dump(ast.parse(original).body[0])
        matches = [node for node in ast.walk(tree) if ast.dump(node) == expected]
        require(len(matches) == 1 and matches[0] in tree.body)
        index = tree.body.index(matches[0])
        tree.body[index] = ast.copy_location(ast.parse(replacement).body[0], matches[0])
    return compile(ast.fix_missing_locations(tree), path, "exec", flags=0, dont_inherit=True, optimize=0)


def vecisa_library(args, layout, helpers):
    """Recognize pinned VecISA's one Linux dlopen template; never eval input."""
    require = helpers.require
    require(len(args) == 2 and args[0] == "-c" and type(args[1]) is str and len(args[1]) <= 4096)
    selected = layout.get("selected_root")
    require(type(selected) is str and os.path.isabs(selected) and os.path.normpath(selected) == selected
            and layout["paths"][-2:] == [selected, selected + "/setuptools/_vendor"])
    raw = helpers.read_source(selected + "/torch/_inductor/cpu_vec_isa.py", 32768)
    require(hashlib.sha256(raw).hexdigest() == VECISA_SOURCE_SHA)
    source = ast.parse(raw)
    classes = [n for n in source.body if isinstance(n, ast.ClassDef) and n.name == "VecISA"]
    require(len(classes) == 1)
    templates = [n.value.value for n in classes[0].body if isinstance(n, ast.Assign)
                 and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name) and n.targets[0].id == "_avx_py_load"
                 and isinstance(n.value, ast.Constant) and type(n.value.value) is str]
    require(len(templates) == 1 and templates[0].count("__lib_path__") == 1)
    tree = ast.parse(args[1])
    last = tree.body[-1] if tree.body else None
    require(isinstance(last, ast.Expr) and isinstance(last.value, ast.Call) and len(last.value.args) == 1
            and isinstance(last.value.args[0], ast.Constant) and type(last.value.args[0].value) is str)
    path = last.value.args[0].value
    require(len(path) < 1024 and re.fullmatch(r"/cache/inductor/[A-Za-z0-9_./-]+\.so", path)
            and all(p not in ("", ".", "..") for p in path.split("/")[1:])
            and templates[0].replace("__lib_path__", path) == args[1])
    return path


def vecisa_load(path, helpers):
    # Generated code is not pinned native code. Only this private cache location
    # can be loaded; keep an anchored regular-file descriptor across dlopen.
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        parts = path.split("/")[1:]
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        library = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            info = os.fstat(library)
            helpers.require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and 0 < info.st_size <= 64 << 20)
            ctypes.CDLL("/proc/self/fd/" + str(library))
            def identity(row):
                return (row.st_dev, row.st_ino, row.st_mode, row.st_nlink, row.st_size, row.st_mtime_ns, row.st_ctime_ns)
            helpers.require(identity(info) == identity(os.fstat(library))
                            and identity(info) == identity(os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)))
        finally:
            os.close(library)
    finally:
        os.close(fd)


def install_lab(layout, helpers, checkpoint=None):
    """Install before engine imports or spawn unpickling; no subprocess sandbox."""
    if checkpoint is not None:
        checkpoint("preconditions", None)
    require = helpers.require
    require(sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode
            and sys.pycache_prefix is None and os.getcwd() == "/")
    require("selected_root" not in layout)
    selected = layout["paths"][-1]
    vendor = selected + "/setuptools/_vendor"
    # Keep package identity separate from the additional active vendor path.
    # Callers handing this state to VecISA must not reinterpret its last path.
    layout = {**layout, "selected_root": selected, "paths": [*layout["paths"], vendor]}
    old = getattr(sys, "_nib_lab_bootstrap", None)
    if old is not None:
        require(old["layout"] == layout)
        old["check"]()
        return old
    require(not any(name == "vllm" or name.startswith("vllm.") or name == "torch" for name in sys.modules)
            and not any(name == "cv2" or name.startswith("cv2.") for name in sys.modules)
            and PATTERN_TARGET not in sys.modules
            and CUTLASS_COMPILER not in sys.modules
            and not any(name.split(".", 1)[0] in ("tilelang", "tvm", "tilelang_cython_wrapper")
                        for name in sys.modules)
            and not any(name == source or name.startswith(source + ".")
                        for name in sys.modules for source in SIX_SOURCES)
            and not {"sitecustomize", "usercustomize"} & set(sys.modules)
            and not any(name.split(".", 1)[0] in ("setuptools", "_distutils_hack", "distutils")
                        for name in sys.modules))
    # Verify before extending the path. Never execute .pth or load cached code.
    for index, (relative, expected) in enumerate(SETUPTOOLS_SOURCES.items()):
        if checkpoint is not None:
            checkpoint("setuptools-source", index)
        require(hashlib.sha256(helpers.read_source(selected + "/" + relative, 32768)).hexdigest() == expected)
    if checkpoint is not None:
        checkpoint("vendor-root", None)
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in vendor.split("/")[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
    finally:
        os.close(fd)
    if checkpoint is not None:
        checkpoint("install", None)
    import multiprocessing as mp
    import multiprocessing.context as contexts
    import multiprocessing.process as process
    import multiprocessing.spawn as spawn
    import multiprocessing.util as util
    import site
    require(layout["paths"][:2] == layout["stdlib_roots"]
            and len(layout["paths"]) == len(set(layout["paths"]))
            and all(type(p) is str and os.path.isabs(p) and os.path.normpath(p) == p for p in layout["paths"]))
    sys.path[:] = layout["paths"]
    sys.argv[:] = [layout["launcher"]]
    sys.executable = layout["wrapper"]
    mp.set_start_method("spawn", force=True)
    mp.set_executable(layout["wrapper"])
    state = {"layout": layout, "spawn_entry": False, "before_unpickle": True, "rejections": 0,
             "finders": None, "backport_loading": False, "backport_context": None,
             "backport_class": None, "backport_delegate": None,
             "distutils_active": False, "distutils_delegate": None,
             "pattern_status": "not-registered", "pattern_module": None, "pattern_loading": None,
             "pattern_callback": None,
             "opencv_status": "not-loaded", "opencv_module": None, "opencv_native": None,
             "tilelang_status": "not-loaded", "tilelang_env": None, "tilelang_tvm": False,
             "six": {name: {"status": "not-loaded", "module": None, "importer": None,
                            "class": None, "class_fields": None, "registry": None,
                            "registry_object": None, "moved": (), "aliases": {}, "pending": set()}
                     for name in SIX_SOURCES}}

    def reject():
        state["rejections"] += 1
        raise ValueError("lab Python boundary rejected")

    def check():
        if (state["tilelang_status"] == "failed"
                or (state["tilelang_env"] is not None
                    and sys.modules.get("tilelang.env") is not state["tilelang_env"])):
            state["tilelang_status"] = "failed"
            reject()
        if ((state["opencv_status"] == "not-loaded" and "cv2" in sys.modules)
                or (state["opencv_status"] == "active" and
                    (sys.modules.get("cv2") is not state["opencv_module"]
                     or vars(state["opencv_module"]).get("_native") is not state["opencv_native"]))):
            state["opencv_status"] = "failed"
        if state["opencv_status"] == "failed":
            reject()
        for name, six in state["six"].items():
            check_six(name, six)
        if ((PATTERN_TARGET in sys.modules and (sys.modules[PATTERN_TARGET] is None
                or all(sys.modules[PATTERN_TARGET] is not item
                       for item in (state["pattern_module"], state["pattern_loading"]))))
                or (state["pattern_module"] is not None
                    and sys.modules.get(PATTERN_TARGET) is not state["pattern_module"])):
            state["pattern_status"] = "failed"
        # Distinct source sites identify failures without reporting live values.
        if sys.path != layout["paths"]:
            reject()
        if sys.executable != layout["wrapper"]:
            reject()
        if spawn.get_executable() != os.fsencode(layout["wrapper"]):
            reject()
        if mp.get_start_method() != "spawn":
            reject()
        if os.getcwd() != "/":
            reject()
        if sys.pycache_prefix is not None:
            reject()
        if os.environ.get("VLLM_PLUGINS") != "":
            reject()
        if os.environ.get("VLLM_WORKER_MULTIPROC_METHOD") != "spawn":
            reject()
        if "NUMACTL_PYTHON" in os.environ:
            reject()
        if state["pattern_status"] == "failed":
            reject()
        if state["finders"] is not None and sys.meta_path != state["finders"]:
            reject()

    def check_six(source, six):
        if six["status"] == "failed":
            reject()
        if six["status"] not in ("active", "loading"):
            if any(name == source or name.startswith(source + ".") for name in sys.modules):
                six["status"] = "failed"
                reject()
            return
        try:
            require(sys.modules.get(source) is six["module"])
            registry = six["registry"]
            if registry is None:
                require(six["status"] == "loading")
                return
            importer = six["importer"]
            require(type(importer) is six["class"]
                    and set(vars(importer)) == {"name", "known_modules"}
                    and type(vars(importer)["name"]) is str and vars(importer)["name"] == source)
            fields = vars(six["class"])
            require(len(fields) == len(six["class_fields"])
                    and all(name in fields and fields[name] is value
                            for name, value in six["class_fields"].items()))
            actual = vars(importer).get("known_modules")
            require(six["module"].__dict__.get("_importer") is importer
                    and actual is six["registry_object"] and type(actual) is dict
                    and len(actual) == len(registry)
                    and all(type(key) is str and key in registry and actual[key] is registry[key] for key in actual))
            require(all(type(item) is cls and type(vars(item).get("name")) is str and vars(item)["name"] == name
                        and type(vars(item).get("mod")) is str and vars(item)["mod"] == target
                        for item, cls, name, target in six["moved"]))
            aliases, pending = six["aliases"], six["pending"]
            for name, value in list(sys.modules.items()):
                if name.startswith(source + "."):
                    require(name in aliases and value is aliases[name])
            require(all(name in pending or sys.modules.get(name) is value for name, value in aliases.items()))
        except BaseException:
            six["status"] = "failed"
            raise

    original_context = contexts.BaseContext.get_context
    def context(self, method=None):
        if method not in (None, "spawn"):
            reject()
        check()
        return original_context(self, "spawn")
    contexts.BaseContext.get_context = contexts.DefaultContext.get_context = context
    mp.get_context = contexts._default_context.get_context
    original_method = contexts.DefaultContext.set_start_method
    def method(self, value, force=False):
        if value != "spawn":
            reject()
        return original_method(self, value, force)
    contexts.DefaultContext.set_start_method = method
    mp.set_start_method = contexts._default_context.set_start_method
    def executable(value):
        if os.fsencode(value) != os.fsencode(layout["wrapper"]):
            reject()
    spawn.set_executable = mp.set_executable = executable
    def no_site(*args, **kwargs):
        reject()
    site.main = site.addsitedir = no_site
    original_start = process.BaseProcess.start
    def start(self):
        check()
        if getattr(self, "_start_method", None) not in (None, "spawn"):
            reject()
        return original_start(self)
    process.BaseProcess.start = start

    original_data, original_prepare = spawn.get_preparation_data, spawn.prepare
    def validate(data):
        try:
            helpers.check_preparation(data, layout)
        except (ValueError, TypeError, KeyError):
            reject()
    def data(name):
        check()
        value = original_data(name)
        validate(value)
        return value
    def prepare(value):
        check()
        validate(value)
        state["spawn_entry"] = False
        sys.argv[:] = [layout["launcher"]]
        original_prepare(value)
        check()
    spawn.get_preparation_data, spawn.prepare = data, prepare
    original_spawn = util.spawnv_passfds
    def spawnv(path, args, passfds):
        check()
        if os.fsencode(path) != os.fsencode(layout["wrapper"]) or os.fsdecode(args[0]) != layout["wrapper"]:
            reject()
        helpers.route([os.fsdecode(arg) for arg in args[1:]])
        return original_spawn(path, args, passfds)
    util.spawnv_passfds = spawnv

    class SourceOnly(importlib.machinery.SourceFileLoader):
        def get_code(self, fullname):
            # -B prevents writes, not reads. Ignore caches for all ordinary source
            # modules; the application separately rejects stale candidate caches.
            raw = helpers.read_source(self.path, 16 << 20)
            return compile(raw, self.path, "exec", flags=0, dont_inherit=True, optimize=0)

    class CutlassSource(SourceOnly):
        def get_code(self, fullname):
            check()
            require(fullname == CUTLASS_COMPILER and self.path == selected + "/" + CUTLASS_SOURCE)
            return cutlass_compiler_code(helpers.read_source(self.path, 128 << 10), self.path, require)

        def exec_module(self, module):
            check()
            require(module.__name__ == CUTLASS_COMPILER and sys.modules.get(CUTLASS_COMPILER) is module
                    and module.__spec__.loader is self)
            from importlib import metadata
            require(metadata.version("nvidia-cutlass-dsl-libs-core") == "4.8.0")
            exec(self.get_code(module.__name__), module.__dict__)
            check()

    class TileLangSource(SourceOnly):
        def exec_module(self, module):
            root = selected + "/tilelang"
            try:
                require(state["tilelang_status"] == "not-loaded" and self.path == root + "/env.py"
                        and module.__name__ == "tilelang.env" and sys.modules.get(module.__name__) is module)
                state["tilelang_status"] = "loading"
                state["tilelang_env"] = module
                check()
                from importlib import metadata
                require(metadata.version("tilelang") == "0.1.12"
                        and "PYTHONPATH" not in os.environ
                        and "TVM_IMPORT_PYTHON_PATH" not in os.environ
                        and "TVM_LIBRARY_PATH" not in os.environ)
                steps = {"libs": False, "tvm": False}

                def libs(paths, dev):
                    check()
                    require(steps == {"libs": False, "tvm": False} and dev is False
                            and type(paths) is list and paths == [root + "/lib"])
                    raw = helpers.read_source(root + "/lib/tilelang_cython_wrapper.abi3.so", 1 << 20)
                    require(hashlib.sha256(raw).hexdigest() == TILELANG_NATIVE_SHA)
                    steps["libs"] = True

                def tvm(path):
                    check()
                    require(steps == {"libs": True, "tvm": False}
                            and type(path) is str and path == root + "/3rdparty/tvm/python")
                    raw = helpers.read_source(path + "/tvm/__init__.py", 32768)
                    require(hashlib.sha256(raw).hexdigest() == TILELANG_TVM_SHA)
                    steps["tvm"] = True

                module.__dict__.update(zip(TILELANG_CALLBACKS, (libs, tvm)))
                raw = helpers.read_source(self.path, 32768)
                exec(tilelang_env_code(raw, self.path, require), module.__dict__)
                require(steps == {"libs": True, "tvm": True} and "PYTHONPATH" not in os.environ)
                state["tilelang_tvm"] = True
                state["tilelang_status"] = "active"
                check()
            except BaseException:
                state["tilelang_status"] = "failed"
                raise
            finally:
                for name in TILELANG_CALLBACKS:
                    module.__dict__.pop(name, None)

    def tilelang_spec(fullname, path, target):
        # Torch Dynamo probes optional TVM before TileLang registers its private
        # namespace. Absence is not a boundary fault; never search other roots.
        if state["tilelang_status"] == "not-loaded":
            require(fullname in ("tvm", "tilelang_cython_wrapper") and path is None and target is None)
            return None
        require(state["tilelang_status"] == "active" and state["tilelang_tvm"] and target is None)
        root = selected + "/tilelang"
        if fullname == "tilelang_cython_wrapper":
            require(path is None)
            origin = root + "/lib/tilelang_cython_wrapper.abi3.so"
            raw = helpers.read_source(origin, 1 << 20)
            require(hashlib.sha256(raw).hexdigest() == TILELANG_NATIVE_SHA)
            loader = importlib.machinery.ExtensionFileLoader(fullname, origin)
            return importlib.util.spec_from_file_location(fullname, origin, loader=loader)
        tvm_root = root + "/3rdparty/tvm/python"
        if fullname == "tvm":
            require(path is None)
            path = [tvm_root]
        else:
            require(type(path) is list and path and all(type(p) is str
                    and os.path.normpath(p) == p and p.startswith(tvm_root + "/tvm")
                    and (p == tvm_root + "/tvm" or p.startswith(tvm_root + "/tvm/")) for p in path))
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        require(spec is not None and type(spec.loader) is importlib.machinery.SourceFileLoader
                and spec.origin.startswith(tvm_root + "/tvm/"))
        if fullname == "tvm":
            require(spec.origin == tvm_root + "/tvm/__init__.py"
                    and hashlib.sha256(helpers.read_source(spec.origin, 32768)).hexdigest() == TILELANG_TVM_SHA)
        spec.loader = SourceOnly(fullname, spec.origin)
        return spec

    def register_metadata(cls):
        # The pinned backport's install decorator otherwise adds an unrestricted
        # metadata finder. Keep both APIs behind the same reviewed roots.
        check()
        module = sys.modules.get("importlib_metadata")
        require(state["backport_loading"] and state["backport_class"] is None and module is not None
                and cls.__module__ == "importlib_metadata" and cls.__name__ == "MetadataPathFinder"
                and issubclass(cls, module.DistributionFinder))
        state["backport_class"] = cls
        state["backport_context"] = module.DistributionFinder.Context
        state["backport_delegate"] = cls.find_distributions
        cls.find_distributions = classmethod(lambda cls, context=None: Finder.find_distributions(
            module.DistributionFinder.Context() if context is None else context))
        return cls

    class MetadataSource(SourceOnly):
        def exec_module(self, module):
            relative = "importlib_metadata/" + ("__init__.py" if module.__name__ == "importlib_metadata" else "_compat.py")
            raw = helpers.read_source(self.path)
            require(self.path == selected + "/" + relative
                    and hashlib.sha256(raw).hexdigest() == helpers.METADATA_SOURCES[relative])
            main = relative.endswith("__init__.py")
            if main:
                from importlib import metadata
                require(metadata.version("importlib_metadata") == "8.7.1")
                state["backport_loading"] = True
            else:
                require(state["backport_loading"])
            try:
                exec(compile(raw, self.path, "exec", dont_inherit=True, optimize=0), module.__dict__)
                if main:
                    require(module.MetadataPathFinder is state["backport_class"])
                else:
                    module.install = register_metadata
            finally:
                if main:
                    state["backport_loading"] = False
            check()

    class SetuptoolsSource(SourceOnly):
        def exec_module(self, module):
            name = module.__name__
            relative = SETUPTOOLS_MODULES[name]
            raw = helpers.read_source(self.path, 32768)
            require(self.path == selected + "/" + relative
                    and hashlib.sha256(raw).hexdigest() == SETUPTOOLS_SOURCES[relative])
            if name == "setuptools":
                from importlib import metadata
                require(metadata.version("setuptools") == "80.10.2")
            exec(compile(raw, self.path, "exec", flags=0, dont_inherit=True, optimize=0), module.__dict__)
            if name == "_distutils_hack":
                require(state["distutils_delegate"] is None
                        and type(module.DISTUTILS_FINDER) is module.DistutilsMetaFinder)
                state["distutils_delegate"] = module.DISTUTILS_FINDER.find_spec

                def insert_shim():
                    check()
                    require(not state["distutils_active"])
                    state["distutils_active"] = True

                def remove_shim():
                    check()
                    state["distutils_active"] = False

                # The pinned shim calls these globals. Do not add its finder
                # ahead of the sealed finder or permit arbitrary finder changes.
                module.insert_shim = insert_shim
                module._remove_shim = remove_shim
                module.add_shim = lambda: None if state["distutils_active"] else insert_shim()
            check()

    class OpenCVSource(SourceOnly):
        def exec_module(self, module):
            root = selected + "/cv2"
            try:
                require(state["opencv_status"] == "not-loaded" and self.path == root + "/__init__.py"
                        and module.__name__ == "cv2" and sys.modules.get("cv2") is module)
                state["opencv_status"] = "loading"
                state["opencv_module"] = module
                check()
                from importlib import metadata
                require(metadata.version("opencv-python-headless") == "5.0.0.93")
                sources = {}
                for name, digest in OPENCV_SOURCES.items():
                    raw = helpers.read_source(root + "/" + name, 16384)
                    require(hashlib.sha256(raw).hexdigest() == digest)
                    sources[name] = raw
                steps = {"configs": 0, "paths": False, "native": False}

                def config(path, g_vars, l_vars):
                    check()
                    require(steps["configs"] < 2 and not steps["paths"] and not steps["native"]
                            and g_vars is module.__dict__ and type(l_vars) is dict
                            and l_vars.get("LOADER_DIR") == root)
                    name = ("config.py", "config-3.py")[steps["configs"]]
                    require(path == root + "/" + name)
                    # Execute the already-verified bytes, not a second open of
                    # the config path selected by OpenCV's discovery loop.
                    exec(compile(sources[name], path, "exec", dont_inherit=True, optimize=0), g_vars, l_vars)
                    steps["configs"] += 1
                    check()

                def paths(extensions, binaries):
                    check()
                    require(steps == {"configs": 2, "paths": False, "native": False}
                            and type(extensions) is list and extensions == [root]
                            and type(binaries) is list and binaries == [root + "/../../lib64"])
                    steps["paths"] = True

                def native():
                    check()
                    require(steps == {"configs": 2, "paths": True, "native": False}
                            and "cv2" not in sys.modules)
                    steps["native"] = True
                    path = root + "/cv2.abi3.so"
                    raw = helpers.read_source(path, 80 << 20)
                    require(hashlib.sha256(raw).hexdigest() == OPENCV_NATIVE_SHA)
                    del raw
                    loader = importlib.machinery.ExtensionFileLoader("cv2", path)
                    spec = importlib.util.spec_from_file_location("cv2", path, loader=loader)
                    value = importlib.util.module_from_spec(spec)
                    loader.exec_module(value)
                    check()
                    require(value.__name__ == "cv2" and value.__file__ == path)
                    state["opencv_native"] = value
                    return value

                module.__dict__.update(zip(OPENCV_CALLBACKS, (config, paths, native)))
                exec(opencv_source_code(sources["__init__.py"], self.path, require), module.__dict__)
                require(steps == {"configs": 2, "paths": True, "native": True}
                        and sys.modules.get("cv2") is module)
                state["opencv_status"] = "active"
                check()
            except BaseException:
                state["opencv_status"] = "failed"
                raise
            finally:
                for name in OPENCV_CALLBACKS:
                    module.__dict__.pop(name, None)

    class SixSource(SourceOnly):
        def exec_module(self, module):
            name = self.name
            six, source = state["six"][name], SIX_SOURCES[name]
            try:
                require(six["status"] == "not-loaded" and self.path == selected + "/" + source["path"]
                        and module.__name__ == name and sys.modules.get(name) is module
                        and not any(key.startswith(name + ".") for key in sys.modules))
                six["status"] = "loading"
                six["module"] = module
                check()
                from importlib import metadata
                require(metadata.version(source["distribution"]) == source["version"])
                raw = helpers.read_source(self.path, 64 << 10)

                def register(importer):
                    check()
                    require(six["registry"] is None
                            and type(importer) is module.__dict__["_SixMetaPathImporter"]
                            and module.__dict__.get("_importer") is importer
                            and vars(importer).get("name") == name)
                    registry = vars(importer).get("known_modules")
                    expected = {name + "." + suffix for suffix in SIX_NAMES}
                    require(type(registry) is dict and len(registry) == len(expected)
                            and all(type(name) is str and name in expected for name in registry))
                    moved = [(value, type(value), vars(value).get("name"), vars(value).get("mod"))
                             for value in registry.values() if type(value) is module.__dict__["MovedModule"]]
                    require(all(type(name) is str and type(target) is str for _, _, name, target in moved))
                    six["importer"] = importer
                    six["class"] = type(importer)
                    six["class_fields"] = dict(vars(type(importer)))
                    six["registry"] = dict(registry)
                    six["registry_object"] = registry
                    six["moved"] = tuple(moved)

                require(SIX_REGISTER not in module.__dict__)
                module.__dict__[SIX_REGISTER] = register
                try:
                    exec(six_source_code(raw, self.path, name, require), module.__dict__)
                finally:
                    module.__dict__.pop(SIX_REGISTER, None)
                require(six["registry"] is not None)
                six["status"] = "active"
                check()
            except BaseException:
                six["status"] = "failed"
                raise

    class SixAliasLoader:
        def __init__(self, fullname, delegate, six):
            self.fullname, self.delegate, self.six = fullname, delegate, six

        def create_module(self, spec):
            name = self.fullname
            six = self.six
            try:
                check()
                require(six["status"] == "active" and name not in sys.modules
                        and name not in six["aliases"] and name not in six["pending"]
                        and spec.name == name and spec.loader is self)
                six["pending"].add(name)
                module = self.delegate.create_module(spec)
                require(sys.modules.get(name) is module)
                six["aliases"][name] = module
                check()
                return module
            except BaseException:
                six["status"] = "failed"
                raise

        def exec_module(self, module):
            six = self.six
            try:
                check()
                name = self.fullname
                require(name in six["pending"] and six["aliases"].get(name) is module
                        and sys.modules.get(name) is module)
                self.delegate.exec_module(module)
                six["pending"].remove(name)
                check()
            except BaseException:
                six["status"] = "failed"
                raise

        def get_code(self, fullname):
            try:
                check()
                require(fullname == self.fullname and fullname in self.six["registry"])
                return None
            except BaseException:
                self.six["status"] = "failed"
                raise

        get_source = get_code

    def apply_pattern_patch():
        check()
        require(state["pattern_status"] == "pending" and state["pattern_module"] is not None
                and sys.modules.get(PATTERN_TARGET) is state["pattern_module"])
        state["pattern_status"] = "applying"
        try:
            state["pattern_callback"]()
            check()
        except BaseException:
            state["pattern_status"] = "failed"
            raise
        state["pattern_status"] = "applied"

    class PatternSource(SourceOnly):
        def exec_module(self, module):
            try:
                if module.__name__ == PATTERN_TARGET:
                    require(state["pattern_module"] is None and state["pattern_loading"] is None
                            and sys.modules.get(PATTERN_TARGET) is module and module.__spec__.loader is self)
                    state["pattern_loading"] = module
                self.exec_verified(module)
            except BaseException:
                state["pattern_status"] = "failed"
                raise
            finally:
                if state["pattern_loading"] is module:
                    state["pattern_loading"] = None

        def exec_verified(self, module):
            check()
            name = module.__name__
            relative, expected = PATTERN_SOURCES[name]
            raw = helpers.read_source(self.path, 128 << 10)
            require(self.path == selected + "/" + relative and hashlib.sha256(raw).hexdigest() == expected
                    and sys.modules.get(name) is module)
            if name == PATTERN_TARGET:
                require(state["pattern_module"] is None)
                exec(compile(raw, self.path, "exec", flags=0, dont_inherit=True, optimize=0), module.__dict__)
                check()
                state["pattern_module"] = module
                if state["pattern_status"] == "pending":
                    apply_pattern_patch()
                return

            from importlib import metadata
            require(metadata.version("torch") in ("2.13.0", "2.13.0+cu130")
                    and state["pattern_status"] == "not-registered")

            def register():
                check()
                require(state["pattern_status"] == "not-registered")
                callback = module.__dict__["_apply_inductor_pattern_matcher_patch"]
                require(type(callback) is type(register) and callback.__globals__ is module.__dict__)
                state["pattern_callback"] = callback
                state["pattern_status"] = "pending"
                if PATTERN_TARGET in sys.modules:
                    # Only a completed load through this exact source loader is
                    # eligible, never a cached/injected module or partial import.
                    require(sys.modules[PATTERN_TARGET] is state["pattern_module"])
                    apply_pattern_patch()

            require(PATTERN_REGISTER not in module.__dict__)
            module.__dict__[PATTERN_REGISTER] = register
            try:
                exec(pattern_env_code(raw, self.path, require), module.__dict__)
                check()
                require(state["pattern_status"] in ("pending", "applied"))
            except BaseException:
                state["pattern_status"] = "failed"
                raise
            finally:
                module.__dict__.pop(PATTERN_REGISTER, None)

    class Finder:
        @staticmethod
        def find_spec(fullname, path=None, target=None):
            check()
            if fullname == "tvm" or fullname.startswith("tvm.") or fullname == "tilelang_cython_wrapper":
                return tilelang_spec(fullname, path, target)
            if fullname.split(".", 1)[0] in ("sitecustomize", "usercustomize"):
                reject()
            source_name = next((name for name in SIX_SOURCES if fullname.startswith(name + ".")), None)
            if source_name is not None:
                six = state["six"][source_name]
                try:
                    require(six["status"] == "active" and target is None
                            and fullname not in six["aliases"])
                    if fullname in six["registry"]:
                        delegate = six["importer"]
                        spec = delegate.find_spec(fullname, path, target)
                        check()
                        require(type(spec) is importlib.machinery.ModuleSpec and spec.name == fullname and spec.loader is delegate
                                and spec.origin is None and not spec.has_location)
                        spec.loader = SixAliasLoader(fullname, delegate, six)
                        return spec
                    # Both pinned Six registries omit winreg outside Windows.
                    # dateutil catches ImportError for this optional probe.
                    require(sys.platform != "win32" and fullname == source_name + ".moves.winreg")
                except BaseException:
                    six["status"] = "failed"
                    raise
                raise ModuleNotFoundError("Windows-only module unavailable", name=fullname)
            if fullname == "distutils":
                require(state["distutils_active"] and state["distutils_delegate"] is not None and path is None)
                spec = state["distutils_delegate"](fullname, path, target)
                require(spec is not None and spec.origin == selected + "/setuptools/_distutils/__init__.py")
                return spec
            try:
                spec = importlib.machinery.PathFinder.find_spec(fullname, path, target)
                if fullname == "tilelang.env":
                    require(state["tilelang_status"] == "not-loaded" and target is None
                            and spec is not None and type(spec.loader) is importlib.machinery.SourceFileLoader
                            and spec.origin == selected + "/tilelang/env.py")
                    spec.loader = TileLangSource(fullname, spec.origin)
                    return spec
                if fullname == CUTLASS_COMPILER:
                    require(target is None and spec is not None
                            and type(spec.loader) is importlib.machinery.SourceFileLoader
                            and spec.origin == selected + "/" + CUTLASS_SOURCE)
                    spec.loader = CutlassSource(fullname, spec.origin)
                    return spec
                if fullname == "cv2":
                    require(state["opencv_status"] == "not-loaded" and target is None
                            and spec is not None and type(spec.loader) is importlib.machinery.SourceFileLoader
                            and spec.origin == selected + "/cv2/__init__.py")
                    spec.loader = OpenCVSource(fullname, spec.origin)
                    return spec
                if fullname in SIX_SOURCES:
                    require(state["six"][fullname]["status"] == "not-loaded" and target is None
                            and spec is not None and type(spec.loader) is importlib.machinery.SourceFileLoader
                            and spec.origin == selected + "/" + SIX_SOURCES[fullname]["path"])
                    spec.loader = SixSource(fullname, spec.origin)
                    return spec
                if fullname in PATTERN_SOURCES:
                    require(spec is not None and type(spec.loader) is importlib.machinery.SourceFileLoader
                            and spec.origin == selected + "/" + PATTERN_SOURCES[fullname][0])
                    spec.loader = PatternSource(fullname, spec.origin)
                    return spec
            except BaseException:
                if fullname == "cv2":
                    state["opencv_status"] = "failed"
                if fullname in SIX_SOURCES:
                    state["six"][fullname]["status"] = "failed"
                if fullname in PATTERN_SOURCES:
                    state["pattern_status"] = "failed"
                raise
            if spec is None:
                return None
            origins = list(spec.submodule_search_locations or []) if spec.origin is None else [spec.origin]
            if not origins or any(not any(os.path.realpath(p) == root or os.path.realpath(p).startswith(root + "/")
                                          for root in layout["paths"]) for p in origins):
                reject()
            if isinstance(spec.loader, importlib.machinery.SourcelessFileLoader):
                reject()
            if fullname in SETUPTOOLS_MODULES:
                require(type(spec.loader) is importlib.machinery.SourceFileLoader
                        and spec.origin == selected + "/" + SETUPTOOLS_MODULES[fullname])
                spec.loader = SetuptoolsSource(fullname, spec.origin)
                return spec
            if fullname in ("importlib_metadata", "importlib_metadata._compat"):
                require(type(spec.loader) is importlib.machinery.SourceFileLoader)
                spec.loader = MetadataSource(fullname, spec.origin)
                return spec
            if type(spec.loader) is importlib.machinery.SourceFileLoader:
                spec.loader = SourceOnly(fullname, spec.origin)
            return spec

        @staticmethod
        def find_distributions(context=None):
            from importlib import metadata
            context = metadata.DistributionFinder.Context() if context is None else context
            check()
            if (type(context) not in (metadata.DistributionFinder.Context, state["backport_context"])
                    or set(vars(context)) - {"name", "path"} or type(context.path) not in (list, tuple)
                    or any(type(p) is not str or p not in layout["paths"] for p in context.path)
                    or not (context.name is None or type(context.name) is str)):
                reject()
            delegate = (metadata.MetadataPathFinder.find_distributions if type(context) is metadata.DistributionFinder.Context
                        else state["backport_delegate"])
            for distribution in delegate(type(context)(path=tuple(context.path), name=context.name)):
                check()
                if not any(os.path.realpath(distribution._path).startswith(root + os.sep) for root in context.path):
                    reject()
                yield distribution

    sys.meta_path[:] = [importlib.machinery.BuiltinImporter, importlib.machinery.FrozenImporter, Finder]
    state["finders"] = list(sys.meta_path)
    # Ordinary engine/JIT child processes are container-supervised, not granted
    # exceptions to the unchanged P0 guard. Python reentry uses only route().
    state["check"] = check
    sys._nib_lab_bootstrap = state
    check()
    if checkpoint is not None:
        checkpoint("complete", None)
    return state


REGISTRY_MODULE = "vllm.model_executor.models.registry"
# vLLM 5df63a7203cdb9328d824a27b682e77c7b9928be, registry.py _SUBPROCESS_COMMAND/_run.
REGISTRY_SHA256 = "16ed7ca91ec949ab88e5210d4fecb2cf3d08641c352b58a152fa48e785fc0243"


def registry_entry(state, helpers):
    """The exact upstream model-inspection subprocess, after sealed bootstrap."""
    import importlib
    require = helpers.require
    state["check"]()
    path = state["layout"]["selected_root"] + "/vllm/model_executor/models/registry.py"
    raw = helpers.read_source(path, 2 << 20)
    require(hashlib.sha256(raw).hexdigest() == REGISTRY_SHA256)
    code = compile(raw, path, "exec", flags=0, dont_inherit=True, optimize=0)
    matches = [item for item in code.co_consts if type(item) is types.CodeType and item.co_name == "_run"]
    require(len(matches) == 1)
    module = importlib.import_module(REGISTRY_MODULE)
    require(type(module) is types.ModuleType and vars(module).get("__file__") == path)
    entry = vars(module).get("_run")
    require(type(entry) is types.FunctionType and entry.__globals__ is vars(module)
            and helpers.same_code(entry.__code__, matches[0]))
    state["check"]()
    entry()
    state["check"]()


WORKER_LOG_MODULE = "vllm.v1.executor.multiproc_executor"
WORKER_LOG_SOURCE = "vllm/v1/executor/multiproc_executor.py"
WORKER_LOG_SHA256 = "948c15e09617b02e2d0746d9ee20b43168b083da71313d05f06b29a753f8570b"
WORKER_LOG_SITES = frozenset((953, 959, 1000, 1063))


@contextmanager
def observed_worker_logs(state, helpers, observer):
    """Observe pinned worker catches before RPC transport stringifies errors."""
    import logging
    original = logging.getLogRecordFactory()
    path = state["layout"]["selected_root"] + "/" + WORKER_LOG_SOURCE

    def factory(*args, **kwargs):
        record = original(*args, **kwargs)
        try:
            # vLLM 5df63a7: startup, busy loop, async output and worker RPC catches.
            # Never format the message, arguments, exception or stack locals.
            if (type(record) is logging.LogRecord and record.name == WORKER_LOG_MODULE
                    and record.pathname == path and record.lineno in WORKER_LOG_SITES
                    and type(record.exc_info) is tuple and len(record.exc_info) == 3
                    and isinstance(record.exc_info[1], BaseException)):
                raw = helpers.read_source(path, 2 << 20)
                helpers.require(hashlib.sha256(raw).hexdigest() == WORKER_LOG_SHA256)
                observer(record.exc_info[1])
        except BaseException:
            pass
        return record

    logging.setLogRecordFactory(factory)
    try:
        yield
    finally:
        if logging.getLogRecordFactory() is factory:
            logging.setLogRecordFactory(original)


def observed_spawn(values, observer):
    """Observe child target failures without changing multiprocessing outcomes."""
    from multiprocessing.process import BaseProcess
    from multiprocessing.spawn import spawn_main
    original = BaseProcess.run

    def run(process):
        try:
            return original(process)
        except BaseException as error:
            try:
                observer(error)
            except BaseException:
                pass
            raise

    BaseProcess.run = run
    try:
        spawn_main(**values)
    finally:
        BaseProcess.run = original


def dispatch_lab(args, state, helpers, worker_observer=None):
    if args == ["-m", REGISTRY_MODULE]:
        registry_entry(state, helpers)
        return
    kind, values = helpers.route(args)
    if kind == "spawn":
        from multiprocessing.spawn import spawn_main
        state["spawn_entry"] = True
        sys.argv[:] = [state["layout"]["launcher"], "--multiprocessing-fork"]
        if worker_observer is None:
            spawn_main(**values)
        else:
            with observed_worker_logs(state, helpers, worker_observer):
                observed_spawn(values, worker_observer)
    else:
        from multiprocessing.resource_tracker import main
        main(*values)
