# SPDX-License-Identifier: Apache-2.0
"""Internal synthetic child probe, not an engine launcher or arbitrary Python CLI."""

import sys

if not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
    sys.exit("Isolated synthetic canary required.")

import ast
import hashlib
import importlib.machinery
import json
import os
from pathlib import Path
import platform
import sysconfig


def require(value):
    if not value:
        raise ValueError("synthetic child rejected")


def digest(value):
    return hashlib.sha256((json.dumps(value, sort_keys=True, indent=2) + "\n").encode()).hexdigest()


def bootstrap():
    raw = os.environ.get("NIB_PYTHON_CANARY", "")
    require(len(raw) <= 8192)
    layout = json.loads(raw)
    require(set(layout) == {"paths", "executable", "python", "payload"})
    paths = sysconfig.get_paths()
    expected = list(dict.fromkeys([paths["stdlib"], sysconfig.get_config_var("DESTSHARED"),
                                  paths["purelib"] + "/nvidia_cutlass_dsl/dsl_packages", paths["purelib"]]))
    require(layout["paths"] == expected and all(isinstance(p, str) and os.path.isabs(p) for p in expected))
    require(layout["executable"] == os.path.realpath(sys.executable) and layout["python"] == platform.python_version())
    require(Path(layout["payload"]) == Path(__file__).absolute().parent and sys.pycache_prefix is None)
    require(not any(name == "vllm" or name.startswith(("vllm.", "torch", "nvidia_cutlass_dsl")) for name in sys.modules))
    # Observe proposed paths without permitting any dependency/engine import.
    # This guard belongs only to the synthetic canary, not a runtime launcher.
    allowed = [os.path.realpath(paths["stdlib"]), os.path.realpath(sysconfig.get_config_var("DESTSHARED"))]
    excluded = [os.path.realpath(paths[key]) for key in ("purelib", "platlib")]
    class StdlibFinder:
        @staticmethod
        def find_spec(fullname, path=None, target=None):
            spec = importlib.machinery.PathFinder.find_spec(fullname, path, target)
            if spec is None:
                return None  # Preserve stdlib's optional platform-module probes.
            require(isinstance(spec.origin, str))
            origin = os.path.realpath(spec.origin)
            require(any(origin.startswith(root + os.sep) for root in allowed)
                    and not any(origin == root or origin.startswith(root + os.sep) for root in excluded))
            return spec
    sys.meta_path[:] = [importlib.machinery.BuiltinImporter, importlib.machinery.FrozenImporter, StdlibFinder]
    sys.path[:] = expected
    require(sys.flags.isolated and sys.flags.no_site and sys.flags.ignore_environment and sys.dont_write_bytecode)
    # Every wrapper entry runs this before dispatching to spawn_main/forkserver.
    # Reentry via runpy during prepare() must not erase the earlier marker.
    if not hasattr(sys, "_nib_canary_dispatch"):
        sys._nib_canary_dispatch = None
    import multiprocessing
    multiprocessing.set_executable(str(Path(layout["payload"]) / "python-canary"))
    multiprocessing.set_forkserver_preload([])
    return layout


def state():
    require(sys._nib_canary_dispatch in ("fresh", "fork", "spawn", "forkserver"))
    require(sys.path == LAYOUT["paths"])
    return {"flags": {"isolated": bool(sys.flags.isolated), "no_site": bool(sys.flags.no_site),
                      "dont_write_bytecode": sys.dont_write_bytecode, "ignore_environment": bool(sys.flags.ignore_environment)},
            "path_sha256": digest(sys.path), "before_unpickle": True,
            "no_engine_modules": not any(name == "vllm" or name.startswith(("vllm.", "torch", "nvidia_cutlass_dsl")) for name in sys.modules)}


def target(pipe, grandchild):
    result = state()
    if grandchild:
        result["grandchild"] = child("spawn", False)
    pipe.send_bytes(json.dumps(result).encode())
    pipe.close()


def child(method, grandchild):
    import multiprocessing
    context = multiprocessing.get_context(method)
    reader, writer = context.Pipe(duplex=False)
    process = context.Process(target=target, args=(writer, grandchild))
    try:
        process.start()
        writer.close()
        require(reader.poll(10))
        data = reader.recv_bytes(4096)
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


def route(args):
    # Accept only the pinned stdlib bootstrap shapes; never eval received code.
    require(len(args) in (5, 6) and args[:3] == ["-B", "-S", "-I"] and args[3] == "-c")
    require(len(args[4]) <= 1024)
    nodes = ast.parse(args[4]).body
    require(len(nodes) == 2 and isinstance(nodes[0], ast.ImportFrom) and nodes[0].level == 0
            and len(nodes[0].names) == 1 and nodes[0].names[0].asname is None
            and isinstance(nodes[1], ast.Expr) and isinstance(nodes[1].value, ast.Call))
    call, imported = nodes[1].value, nodes[0]
    require(isinstance(call.func, ast.Name) and call.func.id == imported.names[0].name)
    def fd(node):
        require(isinstance(node, ast.Constant) and type(node.value) is int and 0 <= node.value < 1 << 20)
        os.fstat(node.value)
        return node.value
    if imported.module == "multiprocessing.spawn":
        require(imported.names[0].name == "spawn_main" and len(args) == 6 and args[-1] == "--multiprocessing-fork"
                and not call.args and len(call.keywords) == 2
                and {key.arg for key in call.keywords} == {"tracker_fd", "pipe_handle"})
        return "spawn", {key.arg: fd(key.value) for key in call.keywords}
    require(len(args) == 5 and imported.names[0].name == "main")
    if imported.module == "multiprocessing.resource_tracker":
        require(len(call.args) == 1 and not call.keywords)
        return "tracker", [fd(call.args[0])]
    require(imported.module == "multiprocessing.forkserver" and len(call.args) == 3 and len(call.keywords) == 1
            and isinstance(call.args[2], ast.List) and not call.args[2].elts
            and call.keywords[0].arg is None and isinstance(call.keywords[0].value, ast.Dict)
            and not call.keywords[0].value.keys)
    return "forkserver", [fd(call.args[0]), fd(call.args[1]), []]


def main():
    args = sys.argv[1:]
    if args == ["fresh"]:
        sys._nib_canary_dispatch = "fresh"
        print(json.dumps(state()))
        return
    if args == ["children"]:
        from multiprocessing import forkserver, resource_tracker
        try:
            sys._nib_canary_dispatch = "fork"
            result = {"fork": child("fork", False), "spawn": child("spawn", True), "forkserver": child("forkserver", False)}
        finally:
            forkserver._forkserver._stop()
            resource_tracker._resource_tracker._stop()
        print(json.dumps(result))
        return
    kind, values = route(args)
    sys._nib_canary_dispatch = kind
    if kind == "spawn":
        from multiprocessing.spawn import spawn_main
        sys.argv = [sys.argv[0], "--multiprocessing-fork"]
        spawn_main(**values)
    elif kind == "forkserver":
        from multiprocessing.forkserver import main as start
        start(*values)
    else:
        from multiprocessing.resource_tracker import main as start
        start(*values)


try:
    LAYOUT = bootstrap()
    if __name__ == "__main__":
        main()
except Exception:
    sys.exit("Synthetic Python canary failed.")
