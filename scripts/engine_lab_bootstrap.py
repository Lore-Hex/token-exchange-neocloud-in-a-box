# SPDX-License-Identifier: Apache-2.0
"""Isolated spawn-only lab bootstrap, NOT the P0 process-denying guard."""

import importlib.machinery
import ast
import ctypes
import hashlib
import os
import re
import stat
import sys

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


def vecisa_library(args, layout, helpers):
    """Recognize pinned VecISA's one Linux dlopen template; never eval input."""
    require = helpers.require
    require(len(args) == 2 and args[0] == "-c" and type(args[1]) is str and len(args[1]) <= 4096)
    raw = helpers.read_source(layout["paths"][-1] + "/torch/_inductor/cpu_vec_isa.py", 32768)
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


def install_lab(layout, helpers):
    """Install before engine imports or spawn unpickling; no subprocess sandbox."""
    require = helpers.require
    require(sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode
            and sys.pycache_prefix is None and os.getcwd() == "/")
    selected = layout["paths"][-1]
    vendor = selected + "/setuptools/_vendor"
    layout = {**layout, "paths": [*layout["paths"], vendor]}
    old = getattr(sys, "_nib_lab_bootstrap", None)
    if old is not None:
        require(old["layout"] == layout)
        old["check"]()
        return old
    require(not any(name == "vllm" or name.startswith("vllm.") or name == "torch" for name in sys.modules)
            and not {"sitecustomize", "usercustomize"} & set(sys.modules)
            and not any(name.split(".", 1)[0] in ("setuptools", "_distutils_hack", "distutils")
                        for name in sys.modules))
    # Verify before extending the path. Never execute .pth or load cached code.
    for relative, expected in SETUPTOOLS_SOURCES.items():
        require(hashlib.sha256(helpers.read_source(selected + "/" + relative, 32768)).hexdigest() == expected)
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in vendor.split("/")[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
    finally:
        os.close(fd)
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
             "distutils_active": False, "distutils_delegate": None}

    def reject():
        state["rejections"] += 1
        raise ValueError("lab Python boundary rejected")

    def check():
        if (sys.path != layout["paths"] or sys.executable != layout["wrapper"]
                or spawn.get_executable() != os.fsencode(layout["wrapper"])
                or mp.get_start_method() != "spawn" or os.getcwd() != "/"
                or sys.pycache_prefix is not None or os.environ.get("VLLM_PLUGINS") != ""
                or os.environ.get("VLLM_WORKER_MULTIPROC_METHOD") != "spawn"
                or "NUMACTL_PYTHON" in os.environ
                or (state["finders"] is not None and sys.meta_path != state["finders"])):
            reject()

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

    class Finder:
        @staticmethod
        def find_spec(fullname, path=None, target=None):
            check()
            if fullname.split(".", 1)[0] in ("sitecustomize", "usercustomize"):
                reject()
            if fullname == "distutils":
                require(state["distutils_active"] and state["distutils_delegate"] is not None and path is None)
                spec = state["distutils_delegate"](fullname, path, target)
                require(spec is not None and spec.origin == selected + "/setuptools/_distutils/__init__.py")
                return spec
            spec = importlib.machinery.PathFinder.find_spec(fullname, path, target)
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
    return state


def dispatch_lab(args, state, helpers):
    kind, values = helpers.route(args)
    if kind == "spawn":
        from multiprocessing.spawn import spawn_main
        state["spawn_entry"] = True
        sys.argv[:] = [state["layout"]["launcher"], "--multiprocessing-fork"]
        spawn_main(**values)
    else:
        from multiprocessing.resource_tracker import main
        main(*values)
