# SPDX-License-Identifier: Apache-2.0
"""Restricted spawn bootstrap for the bounded import smoke, not a Python sandbox."""

import ast
import hashlib
import importlib.machinery
import json
import os
import stat
import sys

# Exact public dependency sources; immutable revisions and provenance in the task
# doc. Only these loaders receive a compatibility adapter, never generic hooks.
METADATA_SOURCES = {
    "importlib_metadata/__init__.py": "bbb130e3e524a73358fa46ba93e591903850652d5a9242cc7d6b367849d4986a",
    "importlib_metadata/_compat.py": "542e590cb953f8172c85ab824a174526f30d2e7b4925f67334ad667866be7a7c",
    "opentelemetry/context/contextvars_context.py": "6074f62ec7694e56c9d1fdbfc5e927805e24d59796f94fbfe27ccf91c67fa41d",
}
CONTEXT_ENTRYPOINT = ("opentelemetry_context", "contextvars_context",
                     "opentelemetry.context.contextvars_context:ContextVarsRuntimeContext")
CONTEXT_MODULE = "opentelemetry.context.contextvars_context"
PROCESS_EVENTS = ("subprocess.Popen", "multiprocessing.spawnv_passfds", "os.exec",
                  "os.posix_spawn", "os.system", "os.fork", "os.forkpty")
PROCESS_COMMANDS = ("uname-processor", "gxx-version", "ptxas-version", "other")
# Classification only, never authorization. No basename matching, path
# resolution, shell parsing or arbitrary string conversion. Sources in task doc.
PROCESS_SHAPES = {
    ("uname", "-p"): "uname-processor",
    ("g++", "--version"): "gxx-version",
    ("/usr/local/lib/python3.12/dist-packages/triton/backends/nvidia/bin/ptxas", "--version"): "ptxas-version",
    ("/usr/local/lib/python3.12/dist-packages/triton/backends/nvidia/bin/ptxas-blackwell", "--version"): "ptxas-version",
}
PROCESS_CALLERS = {
    "platform": "stdlib-platform",
    "torch._inductor.cpp_builder": "torch-cpp-builder",
    "triton.backends.nvidia.compiler": "triton-nvidia-compiler",
    "triton.knobs": "triton-knobs",
}
PROCESS_CALLER_FRAMES = 32


def process_command(executable, argv):
    def text(value):
        if type(value) not in (str, bytes) or len(value) > 256:
            return None
        if type(value) is bytes:
            try:
                return value.decode("ascii")
            except UnicodeDecodeError:
                return None
        return value
    if type(argv) not in (list, tuple) or len(argv) != 2:
        return "other"
    command = (text(argv[0]), text(argv[1]))
    return PROCESS_SHAPES.get(command, "other") if text(executable) == command[0] else "other"


def process_caller():
    """Nearest recognized module within a finite scan, not a trusted identity."""
    frame = sys._getframe(1)
    try:
        for _ in range(PROCESS_CALLER_FRAMES):
            if frame is None:
                break
            name = frame.f_globals.get("__name__")
            if type(name) is str and len(name) <= 128 and name in PROCESS_CALLERS:
                return PROCESS_CALLERS[name]
            frame = frame.f_back
        return "other"
    finally:
        del frame


def require(value):
    if not value:
        raise ValueError("sealed Python bootstrap rejected")


def read_source(path, limit=2 << 20):
    """Bounded, anchored regular-file read; never resolve a link or wait on FIFO."""
    require(type(path) is str and path.startswith("/") and os.path.normpath(path) == path)
    parent = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        parts = path.split("/")[1:]
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = child
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=parent)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= limit)
            data = stream.read(limit + 1)
            def identity(info):
                return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size,
                        info.st_mtime_ns, info.st_ctime_ns)
            require(len(data) == before.st_size and identity(before) == identity(os.fstat(stream.fileno()))
                    and identity(before) == identity(os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)))
            return data
    finally:
        os.close(parent)


def route(args, *, check_fd=True):
    """Parse only pinned CPython spawn/tracker forms; never eval received code."""
    require(len(args) in (5, 6) and args[:4] == ["-B", "-S", "-I", "-c"] and len(args[4]) <= 1024)
    nodes = ast.parse(args[4]).body
    require(len(nodes) == 2 and isinstance(nodes[0], ast.ImportFrom) and nodes[0].level == 0
            and len(nodes[0].names) == 1 and nodes[0].names[0].asname is None
            and isinstance(nodes[1], ast.Expr) and isinstance(nodes[1].value, ast.Call))
    call, imported = nodes[1].value, nodes[0]
    require(isinstance(call.func, ast.Name) and call.func.id == imported.names[0].name)
    def fd(node):
        require(isinstance(node, ast.Constant) and type(node.value) is int and 0 <= node.value < 1 << 20)
        if check_fd:
            os.fstat(node.value)
        return node.value
    if imported.module == "multiprocessing.spawn":
        require(imported.names[0].name == "spawn_main" and len(args) == 6 and args[-1] == "--multiprocessing-fork"
                and not call.args and len(call.keywords) == 2
                and {key.arg for key in call.keywords} == {"tracker_fd", "pipe_handle"})
        return "spawn", {key.arg: fd(key.value) for key in call.keywords}
    require(imported.module == "multiprocessing.resource_tracker" and imported.names[0].name == "main"
            and len(args) == 5 and len(call.args) == 1 and not call.keywords)
    return "tracker", [fd(call.args[0])]


def check_preparation(data, layout):
    required = {"log_to_stderr", "authkey", "name", "sys_path", "sys_argv", "orig_dir", "dir", "start_method", "init_main_from_path"}
    require(type(data) is dict and required <= set(data) <= required | {"log_level"}
            and data["sys_path"] == layout["paths"] and data["sys_argv"] == [layout["launcher"]]
            and data["orig_dir"] == data["dir"] == "/" and data["start_method"] == "spawn"
            and data["init_main_from_path"] == layout["launcher"] and data["log_to_stderr"] is False
            and type(data["name"]) is str and 0 < len(data["name"]) <= 128
            and isinstance(data["authkey"], bytes) and 0 < len(data["authkey"]) <= 128
            and ("log_level" not in data or type(data["log_level"]) is int))


def install(layout):
    """Called from a hash-bound launcher before any engine import or unpickle.

    Low-level API for fake-package tests. Production layout validation and
    source/image/receipt binding belong to the caller, not user-supplied paths.
    """
    require(set(layout) == {"paths", "stdlib_roots", "wrapper", "launcher", "environment"})
    require(sys.flags.isolated and sys.flags.no_site and sys.flags.ignore_environment and sys.dont_write_bytecode
            and sys.pycache_prefix is None and len(layout["paths"]) == len(set(layout["paths"]))
            and layout["stdlib_roots"] == layout["paths"][:2]
            and all(type(p) is str and os.path.isabs(p) and os.path.normpath(p) == p
                    for p in layout["paths"] + [layout["wrapper"], layout["launcher"]]))
    previous = getattr(sys, "_nib_smoke_guard", None)
    if previous is not None:
        require(previous["layout"] == layout)
        previous["check"]()
        return previous
    require(not any(name == "vllm" or name.startswith(("vllm.", "torch", "nvidia_cutlass_dsl")) for name in sys.modules))
    require(not any(name == "importlib_metadata" or name.startswith("importlib_metadata.") for name in sys.modules))
    require(CONTEXT_MODULE not in sys.modules)
    require("sitecustomize" not in sys.modules and "usercustomize" not in sys.modules)
    import multiprocessing as mp
    import multiprocessing.context as context
    import multiprocessing.process as process
    import multiprocessing.spawn as spawn
    import multiprocessing.util as util
    import site
    from importlib import metadata

    require(os.getcwd() == "/" and dict(os.environ) == layout["environment"]
            and os.environ.get("VLLM_WORKER_MULTIPROC_METHOD") == "spawn"
            and os.environ.get("VLLM_PLUGINS") == "" and "NUMACTL_PYTHON" not in os.environ)
    sys.path[:] = layout["paths"]
    sys.argv[:] = [layout["launcher"]]
    sys.executable = layout["wrapper"]
    mp.set_start_method("spawn", force=True)
    mp.set_executable(layout["wrapper"])
    failures = {key: 0 for key in ("state", "method", "executable", "preparation", "process", "import", "site")}
    state = {"layout": layout, "failures": failures, "before_unpickle": True, "spawn_entry": False,
             "finders": None, "tracker_pids": [], "backport_context": None, "backport_delegate": None,
             "backport_loading": False, "backport_class": None, "entrypoint_loads": 0,
             "context_module": None, "context_entrypoint": None, "first_state_mismatch": None,
             "first_denied_process": None}

    def reject(kind):
        failures[kind] += 1
        raise ValueError("sealed Python operation rejected")

    def reject_process(event, executable=None, argv=None):
        if state["first_denied_process"] is None:
            state["first_denied_process"] = {"event": event, "command": process_command(executable, argv),
                                             "caller": process_caller()}
        reject("process")

    def state_check(field, matches):
        if not matches:
            if state["first_state_mismatch"] is None:
                state["first_state_mismatch"] = field
            reject("state")

    def check():
        expected_argv = [layout["launcher"]] + (["--multiprocessing-fork"] if state["spawn_entry"] else [])
        state_check("paths", sys.path == layout["paths"])
        state_check("argv", sys.argv == expected_argv)
        state_check("executable", sys.executable == layout["wrapper"] and spawn.get_executable() == os.fsencode(layout["wrapper"]))
        state_check("env", dict(os.environ) == layout["environment"])
        state_check("cwd", os.getcwd() == "/")
        state_check("method", mp.get_start_method() == "spawn")
        state_check("pycache", sys.pycache_prefix is None)
        state_check("finders", state["finders"] is None or sys.meta_path == state["finders"])

    original_context = context.BaseContext.get_context
    def get_context(self, method=None):
        if method not in (None, "spawn"):
            reject("method")
        check()
        return original_context(self, "spawn")
    context.BaseContext.get_context = get_context
    # DefaultContext overrides this method; bind both public entry paths.
    context.DefaultContext.get_context = get_context
    mp.get_context = context._default_context.get_context

    original_method = context.DefaultContext.set_start_method
    def set_method(self, method, force=False):
        if method != "spawn":
            reject("method")
        return original_method(self, method, force)
    context.DefaultContext.set_start_method = set_method
    mp.set_start_method = context._default_context.set_start_method

    def set_executable(path):
        if os.fsencode(path) != os.fsencode(layout["wrapper"]):
            reject("executable")
    spawn.set_executable = set_executable
    mp.set_executable = set_executable
    def no_site(*args, **kwargs):
        reject("site")
    site.main = site.addsitedir = no_site

    original_start = process.BaseProcess.start
    def start(self):
        check()
        if getattr(self, "_start_method", None) not in (None, "spawn"):
            reject("method")
        return original_start(self)
    process.BaseProcess.start = start

    original_data, original_prepare = spawn.get_preparation_data, spawn.prepare
    def validate(data):
        try:
            check_preparation(data, layout)
        except (ValueError, TypeError, KeyError):
            reject("preparation")
    def get_data(name):
        check()
        data = original_data(name)
        validate(data)
        return data
    def prepare(data):
        check()
        validate(data)
        state["spawn_entry"] = False
        sys.argv[:] = [layout["launcher"]]
        original_prepare(data)
        check()
    spawn.get_preparation_data, spawn.prepare = get_data, prepare

    original_spawn = util.spawnv_passfds
    def spawnv(path, args, passfds):
        check()
        if os.fsencode(path) != os.fsencode(layout["wrapper"]) or os.fsdecode(args[0]) != layout["wrapper"]:
            reject("executable")
        try:
            kind, _ = route([os.fsdecode(arg) for arg in args[1:]])
        except (ValueError, OSError, SyntaxError, TypeError):
            reject_process("multiprocessing.spawnv_passfds", path, args)
        pid = original_spawn(path, args, passfds)
        if kind == "tracker":
            state["tracker_pids"].append(pid)
        return pid
    util.spawnv_passfds = spawnv

    def pinned_source(relative):
        try:
            raw = read_source(layout["paths"][-1] + "/" + relative)
            if hashlib.sha256(raw).hexdigest() != METADATA_SOURCES[relative]:
                reject("import")
            return raw
        except (OSError, ValueError, KeyError):
            reject("import")

    def guard_entrypoint(api):
        original_load = api.EntryPoint.load
        def load(entry):
            check()
            distribution = entry.dist
            if ((entry.group, entry.name, entry.value) != CONTEXT_ENTRYPOINT
                    or type(distribution) is not api.PathDistribution
                    or os.path.realpath(distribution._path.parent) != layout["paths"][-1]
                    or distribution.metadata["Name"] != "opentelemetry-api" or distribution.version != "1.40.0"):
                reject("import")
            pinned_source("opentelemetry/context/contextvars_context.py")
            if CONTEXT_MODULE in sys.modules and (state["context_module"] is None
                    or sys.modules[CONTEXT_MODULE] is not state["context_module"]):
                reject("import")
            result = original_load(entry)
            if (state["context_module"] is None or sys.modules.get(CONTEXT_MODULE) is not state["context_module"]
                    or result is not state["context_entrypoint"]):
                reject("import")
            check()
            state["entrypoint_loads"] += 1
            return result
        api.EntryPoint.load = load

    def register_metadata(cls):
        check()
        module = sys.modules.get("importlib_metadata")
        if (not state["backport_loading"] or state["backport_class"] is not None or module is None
                or cls.__module__ != "importlib_metadata" or cls.__name__ != "MetadataPathFinder"
                or not issubclass(cls, module.DistributionFinder)):
            reject("import")
        state["backport_class"] = cls
        state["backport_context"] = module.DistributionFinder.Context
        state["backport_delegate"] = cls.find_distributions
        cls.find_distributions = classmethod(lambda cls, context=None: Finder.find_distributions(
            module.DistributionFinder.Context() if context is None else context))
        # Do not append an unrestricted finder or disable stdlib discovery.
        return cls

    class VerifiedLoader:
        def __init__(self, original, relative):
            self.original, self.relative = original, relative

        def create_module(self, spec):
            return self.original.create_module(spec)

        def exec_module(self, module):
            check()
            raw = pinned_source(self.relative)
            main = self.relative == "importlib_metadata/__init__.py"
            context_target = self.relative == "opentelemetry/context/contextvars_context.py"
            if main:
                if metadata.version("importlib_metadata") != "8.7.1":
                    reject("import")
                state["backport_loading"] = True
            elif context_target:
                if state["context_module"] is not None:
                    reject("import")
            elif not state["backport_loading"]:
                reject("import")
            try:
                # Execute verified source, not a possibly precompiled counterpart.
                exec(compile(raw, module.__file__, "exec"), module.__dict__)
                if main:
                    if module.MetadataPathFinder is not state["backport_class"]:
                        reject("import")
                    guard_entrypoint(module)
                elif context_target:
                    state["context_module"] = module
                    state["context_entrypoint"] = module.ContextVarsRuntimeContext
                else:
                    module.install = register_metadata
            finally:
                if main:
                    state["backport_loading"] = False
            check()

    class Finder:
        @staticmethod
        def find_distributions(context=None):
            check()
            from importlib import metadata
            if context is None:
                context = metadata.DistributionFinder.Context()
            if (type(context) not in (metadata.DistributionFinder.Context, state["backport_context"])
                    or set(vars(context)) - {"name", "path"}):
                reject("import")
            paths, name = context.path, context.name
            if (type(paths) not in (list, tuple)
                    or not all(type(p) is str and p in layout["paths"] for p in paths)
                    or not (name is None or type(name) is str)):
                reject("import")
            paths = tuple(paths)
            # Copy the validated search roots; neither lazy discovery nor an
            # explicit context may widen them. Enumeration never loads entries.
            kind = type(context)
            sealed = kind(path=paths, name=name)
            delegate = (metadata.MetadataPathFinder.find_distributions if kind is metadata.DistributionFinder.Context
                        else state["backport_delegate"])
            for distribution in delegate(sealed):
                check()
                if not any(os.path.realpath(distribution._path).startswith(root + os.sep) for root in paths):
                    reject("import")
                yield distribution

        @staticmethod
        def find_spec(fullname, path=None, target=None):
            check()
            if fullname.split(".", 1)[0] in ("sitecustomize", "usercustomize"):
                reject("import")
            spec = importlib.machinery.PathFinder.find_spec(fullname, path, target)
            if spec is None:
                return None
            origins = list(spec.submodule_search_locations or []) if spec.origin is None else [spec.origin]
            if not origins or not all(type(p) is str and any(os.path.realpath(p).startswith(root + os.sep)
                or os.path.realpath(p) == root for root in layout["paths"]) for p in origins):
                reject("import")
            if fullname in ("importlib_metadata", "importlib_metadata._compat", CONTEXT_MODULE):
                relative = ("opentelemetry/context/contextvars_context.py" if fullname == CONTEXT_MODULE else
                            "importlib_metadata/" + ("__init__.py" if fullname == "importlib_metadata" else "_compat.py"))
                if (spec.origin != layout["paths"][-1] + "/" + relative
                        or type(spec.loader) is not importlib.machinery.SourceFileLoader):
                    reject("import")
                spec.loader = VerifiedLoader(spec.loader, relative)
            return spec
    sys.meta_path[:] = [importlib.machinery.BuiltinImporter, importlib.machinery.FrozenImporter, Finder]
    state["finders"] = list(sys.meta_path)
    guard_entrypoint(metadata)

    def audit(event, args):
        if event in ("os.exec", "os.posix_spawn", "os.system", "os.fork", "os.forkpty"):
            executable, argv = args[:2] if event in ("os.exec", "os.posix_spawn") and len(args) >= 2 else (None, None)
            reject_process(event, executable, argv)
        if event == "subprocess.Popen":
            check()
            executable, argv, cwd, env = args
            # Fresh subprocesses may reenter the single fixed observation mode,
            # never ordinary Python, arbitrary code, NUMA, or an external shell.
            if not (executable == layout["wrapper"] and argv == [layout["wrapper"], "fresh"]
                    and cwd in (None, "/") and (env is None or env == layout["environment"])):
                reject_process(event, executable, argv)
    sys.addaudithook(audit)
    state["check"] = check
    sys._nib_smoke_guard = state
    check()
    return state


def dispatch(args, state):
    kind, values = route(args)
    if kind == "spawn":
        from multiprocessing.spawn import spawn_main
        state["spawn_entry"] = True
        sys.argv[:] = [state["layout"]["launcher"], "--multiprocessing-fork"]
        spawn_main(**values)
    else:
        from multiprocessing.resource_tracker import main
        main(*values)


def observation(state):
    state["check"]()
    return {"isolated": bool(sys.flags.isolated), "no_site": bool(sys.flags.no_site),
            "dont_write_bytecode": sys.dont_write_bytecode, "before_unpickle": state["before_unpickle"],
            "path_sha256": hashlib.sha256((json.dumps(sys.path, sort_keys=True, indent=2) + "\n").encode()).hexdigest(),
            "rejections": dict(state["failures"])}


def stop_tracker(state):
    """The pinned stdlib _stop discards waitpid status; require owned clean exit."""
    import signal
    import time
    from multiprocessing import resource_tracker
    state["check"]()
    tracker = resource_tracker._resource_tracker
    with tracker._lock:
        pid = tracker._pid
        require(type(pid) is int and pid > 0 and state["tracker_pids"] == [pid]
                and type(tracker._fd) is int and tracker._fd >= 0)
        os.close(tracker._fd)
        tracker._fd = None
        deadline = time.monotonic() + 10
        while True:
            waited, status = os.waitpid(pid, os.WNOHANG)
            if waited == pid:
                tracker._pid = None
                require(os.waitstatus_to_exitcode(status) == 0)
                return {"launched": True, "exit_zero": True}
            if time.monotonic() >= deadline:
                os.kill(pid, signal.SIGKILL)
                raise ValueError("sealed tracker completion rejected")
            time.sleep(0.01)
