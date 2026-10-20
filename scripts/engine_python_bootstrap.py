# SPDX-License-Identifier: Apache-2.0
"""Restricted spawn bootstrap for the bounded import smoke, not a Python sandbox."""

import ast
import hashlib
import importlib.machinery
import json
import os
import stat
import sys


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
    require("sitecustomize" not in sys.modules and "usercustomize" not in sys.modules)
    import multiprocessing as mp
    import multiprocessing.context as context
    import multiprocessing.process as process
    import multiprocessing.spawn as spawn
    import multiprocessing.util as util
    import site

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
             "finders": None, "tracker_pids": []}

    def reject(kind):
        failures[kind] += 1
        raise ValueError("sealed Python operation rejected")

    def check():
        expected_argv = [layout["launcher"]] + (["--multiprocessing-fork"] if state["spawn_entry"] else [])
        if not (sys.path == layout["paths"] and sys.argv == expected_argv
                and sys.executable == layout["wrapper"] and spawn.get_executable() == os.fsencode(layout["wrapper"])
                and dict(os.environ) == layout["environment"] and os.getcwd() == "/"
                and mp.get_start_method() == "spawn" and sys.pycache_prefix is None
                and (state["finders"] is None or sys.meta_path == state["finders"])):
            reject("state")

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
            reject("process")
        pid = original_spawn(path, args, passfds)
        if kind == "tracker":
            state["tracker_pids"].append(pid)
        return pid
    util.spawnv_passfds = spawnv

    class Finder:
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
            return spec
    sys.meta_path[:] = [importlib.machinery.BuiltinImporter, importlib.machinery.FrozenImporter, Finder]
    state["finders"] = list(sys.meta_path)

    def audit(event, args):
        if event in ("os.exec", "os.posix_spawn", "os.system", "os.fork", "os.forkpty"):
            reject("process")
        if event == "subprocess.Popen":
            check()
            executable, argv, cwd, env = args
            # Fresh subprocesses may reenter the single fixed observation mode,
            # never ordinary Python, arbitrary code, NUMA, or an external shell.
            if not (executable == layout["wrapper"] and argv == [layout["wrapper"], "fresh"]
                    and cwd in (None, "/") and (env is None or env == layout["environment"])):
                reject("process")
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
