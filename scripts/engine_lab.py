# SPDX-License-Identifier: Apache-2.0
"""Appended to exact reviewed bootstrap helpers; separate bounded CC lab entry."""

import http.client
import json
import signal
import subprocess
import time
from pathlib import Path

ROOT = "/opt/nib"
APPLICATION_SECONDS = 720
WEIGHTS_SECONDS = 1800
STARTUP_SECONDS = 1800
REQUEST_SECONDS = 120
CLEANUP_SECONDS = 60
TOTAL_SECONDS = 4800
MODEL = "deepseek-v4.1-flash-1017"
PACK = "models/deepseek-v4.1-flash-1017/1"
TOTAL_BYTES = 510311045295
MODEL_REVISION = "1bae0e1088993b9b82de08f59de5be5a34e42170"
# Exact public image config bd8a5d7, CUDA_HOME/LD_LIBRARY_PATH. The torch
# prefix is the measured selected root, not an ambient loader search path.
IMAGE_LD_PATH = "/usr/local/nvidia/lib64:/usr/local/cuda/lib64:/usr/local/cuda/lib64:/usr/local/nvidia/lib:/usr/local/nvidia/lib64:/usr/local/cuda/lib64"
PROCESSES = None
GPU_PROBE = None
CORRECTNESS = None


def lab_args(plan):
    # Existing reviewed all-off controls, followed only by fixed lab limits.
    return list(plan["fixed_engine_arguments"]) + [
        "--model", "/model", "--tokenizer", "/model", "--served-model-name", MODEL,
        "--port", "8000", "--api-server-count", "1", "--pipeline-parallel-size", "1",
        "--distributed-executor-backend", "mp", "--no-numa-bind",
        "--tokenizer-mode", "deepseek_v41", "--tool-call-parser", "deepseek_v41",
        "--enable-auto-tool-choice", "--reasoning-parser", "deepseek_v41",
        "--attention-config", '{"backend":"FLASHINFER_MLA_SPARSE_DSV41","indexer_kv_dtype":"mxfp4","indexer_sparse_logits":true}',
        "--kv-cache-dtype", "fp8", "--max-model-len", "4096",
        "--max-num-seqs", "1", "--max-num-batched-tokens", "4096", "--enforce-eager"]


def lab_environment(plan):
    return {**plan["fixed_engine_environment"],
            "VLLM_WORKER_MULTIPROC_METHOD": "spawn", "VLLM_HOST_IP": "127.0.0.1",
            "VLLM_ENGINE_READY_TIMEOUT_S": str(STARTUP_SECONDS), "OMP_NUM_THREADS": "1",
            "PYTORCH_NVML_BASED_CUDA_CHECK": "1", "TORCHINDUCTOR_COMPILE_THREADS": "1",
            "TRITON_CACHE_AUTOTUNING": "1", "TILELANG_CLEANUP_TEMP_FILES": "1",
            "TORCH_DEVICE_BACKEND_AUTOLOAD": "0", "OPENBLAS_MAIN_FREE": "1", "GOTOBLAS_MAIN_FREE": "1",
            "OTEL_PYTHON_CONTEXT": "contextvars_context", "OTEL_PROPAGATORS": "none",
            "PATH": "/usr/local/cuda/bin:/usr/local/bin:/usr/bin:/bin", "HOME": "/cache/home",
            "CUDA_HOME": "/usr/local/cuda",
            "LD_LIBRARY_PATH": "/usr/local/lib/python3.12/dist-packages/torch/lib:" + IMAGE_LD_PATH,
            "TMPDIR": "/cache/tmp", "LC_ALL": "C.UTF-8", "HF_HUB_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1", "HF_HOME": "/cache/hf",
            "DG_JIT_CACHE_DIR": "/cache/deep-gemm", "TRITON_CACHE_DIR": "/cache/triton",
            "TORCHINDUCTOR_CACHE_DIR": "/cache/inductor", "TORCH_EXTENSIONS_DIR": "/cache/torch",
            "XDG_CACHE_HOME": "/cache/xdg", "MAX_JOBS": "4"}


def wrapper_bytes(executable, environment, sha):
    import shlex
    argv = ["/usr/bin/env", "-i", *[key + "=" + value for key, value in sorted(environment.items())],
            executable, "-I", "-S", "-B", ROOT + "/launch.py", sha]
    return ("#!/bin/sh\nexec " + " ".join(shlex.quote(a) for a in argv) + ' "$@"\n').encode()


def load(name, raw, path):
    module = types.ModuleType(name)
    module.__file__ = path
    exec(compile(raw, path, "exec", flags=0, dont_inherit=True), module.__dict__)
    return module


def lab_context(expected):
    global PROCESSES, GPU_PROBE, CORRECTNESS
    import platform
    import re
    require(re.fullmatch(r"[0-9a-f]{64}", expected) and __file__ == ROOT + "/launch.py")
    raw = read_source(ROOT + "/lab.json")
    require(hashlib.sha256(raw).hexdigest() == expected)
    contract = json.loads(raw)
    require(contract["schema"] == "nib.deepseek-lab-input/v1" and contract["status"] == "BLOCKED"
            and contract["deployable"] is False and contract["confidential"] is False
            and contract["operation"] in ("functional", "hardware-diagnostic", "local-correctness")
            and platform.system() == "Linux" and platform.machine() == "x86_64"
            and platform.python_version() == "3.12.3" and sys.pycache_prefix is None
            and os.getcwd() == "/" and dict(os.environ) == contract["environment"])
    require(read_source(ROOT + "/python") == wrapper_bytes(contract["interpreter"]["resolved"], contract["environment"], expected))
    data = read_source(contract["interpreter"]["resolved"], 16 << 20)
    require(len(data) == contract["interpreter"]["bytes"]
            and hashlib.sha256(data).hexdigest() == contract["interpreter"]["sha256"])
    sources = {}
    require(type(contract["sources"]) is dict and len(contract["sources"]) <= 110)
    for name, sha in contract["sources"].items():
        require(re.fullmatch(r"[A-Za-z0-9_./-]+", name) and not name.startswith("/")
                and all(p not in ("", ".", "..") for p in name.split("/")))
        data = read_source(ROOT + "/" + name, 64 << 20 if name == "nib" else 2 << 20)
        require(hashlib.sha256(data).hexdigest() == sha)
        if name != "nib":
            sources[name] = data
    require(sum(map(len, sources.values())) <= 8 << 20)
    PROCESSES = load("lab_processes", sources["processes.py"], ROOT + "/processes.py")
    GPU_PROBE = load("lab_gpu", sources["probe.py"], ROOT + "/probe.py")
    if contract["operation"] == "local-correctness":
        CORRECTNESS = load("lab_correctness", sources["correctness.py"], ROOT + "/correctness.py")
    modules = [load(name, sources["application/" + name], ROOT + "/application/" + name)
               for name in ("engine_overlay.py", "engine_image_diagnostic.py", "engine_python_descriptor.py", "application.py")]
    overlay, _, _, app = modules
    application = overlay.load_json(sources["application/contract.json"])
    require(overlay.digest(sources["application/contract.json"]) == contract["application_contract_sha256"])
    app.require_contract(overlay, application)
    measured = app.measurement(overlay, sources["application/measurement.json"])
    require(contract["paths"] == measured["proposed_paths"] and contract["git_sha"] == application["git_sha"]
            and contract["base_image"] == application["base_image"]
            and contract["official_config"] == application["official_config"])
    plan = overlay.load_json(sources["launch-plan.json"])
    require(contract["arguments"] == lab_args(plan) and contract["environment"] == lab_environment(plan)
            and contract["pack_revision"] == MODEL_REVISION and contract["pack_bytes"] == TOTAL_BYTES)
    return contract, sources, modules, application, measured


def quiet_command(argv, timeout, *, output=False):
    # Reviewed per-stream bounds, timeout and whole-group teardown.
    result = PROCESSES.execute_process_group(argv, env=dict(os.environ), timeout=timeout, max_output_bytes=32768)
    result.check_returncode()
    return result.stdout if output else b""


def hardware():
    raw = quiet_command([ROOT + "/python", "probe"], 15, output=True)
    return GPU_PROBE.validate_report(json.loads(raw))


def diagnostic_child(expected):
    """Same pre-NVML startup as probe; no weights, application or engine path."""
    row = {"stage": "invocation", "invocation": {
        "linux": sys.platform == "linux", "isolated": bool(sys.flags.isolated),
        "no_site": bool(sys.flags.no_site), "no_bytecode": bool(sys.dont_write_bytecode),
        "argument_shape": len(sys.argv) == 3 and sys.argv[2] == "probe-diagnostic"},
        "bootstrap": {"stage": "not-run", "source_index": None}, "probe": None}
    def checkpoint(stage, source_index):
        require(stage in ("preconditions", "setuptools-source", "vendor-root", "install", "complete")
                and (type(source_index) is int and 0 <= source_index < 4 if stage == "setuptools-source" else source_index is None))
        row["bootstrap"] = {"stage": stage, "source_index": source_index}
    try:
        require(all(row["invocation"].values()))
        row["stage"] = "source-binding"
        contract, _, _, _, _ = lab_context(expected)
        require(contract["operation"] == "hardware-diagnostic")
        row["stage"] = "bootstrap"
        install_lab({"paths": contract["paths"], "stdlib_roots": contract["paths"][:2],
                     "wrapper": ROOT + "/python", "launcher": ROOT + "/launch.py"}, sys.modules[__name__], checkpoint=checkpoint)
        row["stage"] = "probe-unavailable"
        row["probe"] = GPU_PROBE.diagnose()
        row["stage"] = "probe"
    except Exception:
        pass
    return row


def diagnostic_request():
    row = {"schema": "nib.deepseek-hardware-diagnostic/v1", "status": "BLOCKED",
           "deployable": False, "confidential": False, "functional_pass": False,
           "stage": "diagnostic", "probe_execution": "failed", "child": None}
    try:
        raw = quiet_command([ROOT + "/python", "probe-diagnostic"], 15, output=True)
        row["probe_execution"] = "malformed"
        candidate = {**row, "probe_execution": "completed", "child": json.loads(raw)}
        GPU_PROBE.validate_diagnostic(candidate)
        row = candidate
    except subprocess.TimeoutExpired:
        row["probe_execution"] = "timeout"
    except Exception:
        pass
    return row


def http_request(path, *, data=None, timeout=2):
    connection = http.client.HTTPConnection("127.0.0.1", 8000, timeout=timeout)
    try:
        raw = None if data is None else json.dumps(data).encode()
        connection.request("GET" if raw is None else "POST", path, body=raw,
                           headers={} if raw is None else {"Content-Type": "application/json"})
        response = connection.getresponse()
        body = response.read((1 << 20) + 1)
        require(response.status == 200 and len(body) <= 1 << 20)
        return body
    finally:
        connection.close()


def correctness_request(payload, deadline):
    """A whole-request deadline, including slow headers or trickling bodies."""
    before = time.monotonic()
    remaining = deadline - before
    if remaining <= 0:
        raise TimeoutError("request deadline")
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    require(previous_timer[0] > 0 and previous_timer[1] == 0)
    def expired(*_):
        raise TimeoutError("request deadline")
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, min(previous_timer[0], remaining))
    try:
        return http_request("/v1/chat/completions", data=payload, timeout=remaining)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        restored = previous_timer[0] - (time.monotonic() - before)
        if restored <= 0:
            os._exit(124)
        signal.setitimer(signal.ITIMER_REAL, restored)


def completion_result(raw):
    row = json.loads(raw)
    require(type(row) is dict and "error" not in row and row.get("object") == "chat.completion"
            and row.get("model") == MODEL and type(row.get("choices")) is list and len(row["choices"]) == 1)
    choice = row["choices"][0]
    require(type(choice) is dict and choice.get("index") == 0 and choice.get("finish_reason") in ("stop", "length"))
    message = choice.get("message")
    require(type(message) is dict and message.get("role") == "assistant"
            and any(type(message.get(k)) is str and len(message[k]) > 0 for k in ("content", "reasoning", "reasoning_content")))
    usage = row.get("usage")
    require(type(usage) is dict and all(type(usage.get(k)) is int for k in ("prompt_tokens", "completion_tokens", "total_tokens"))
            and 0 < usage["completion_tokens"] <= 128 and 0 < usage["prompt_tokens"] <= 4096
            and usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"])
    content = message.get("content")
    final_content_present = type(content) is str and bool(content.strip())
    return {"generated": True, "finish": choice["finish_reason"],
            "final_content_present": final_content_present,
            "synthetic_answer_match": final_content_present and content.strip() == "2",
            "prompt_tokens": usage["prompt_tokens"], "completion_tokens": usage["completion_tokens"]}


def run_engine(contract):
    state = install_lab({"paths": contract["paths"], "stdlib_roots": contract["paths"][:2],
                         "wrapper": ROOT + "/python", "launcher": ROOT + "/launch.py"}, sys.modules[__name__])
    # At this boundary all metadata/paths are established before engine imports.
    from vllm.entrypoints.cli.serve import ServeSubcommand
    from vllm.utils.argparse_utils import FlexibleArgumentParser
    parser = FlexibleArgumentParser()
    serve = ServeSubcommand()
    serve.subparser_init(parser.add_subparsers(dest="subparser"))
    args = parser.parse_args(["serve", *contract["arguments"]])
    serve.validate(args)
    state["check"]()
    serve.cmd(args)


def recheck_sources(overlay, application, paths):
    # Child entry rechecks every changed module and rejects reappeared bytecode
    # or import-name shadows before unpickling/importing the engine.
    with overlay.directory(paths[-1]) as fd:
        tree = overlay.Tree(fd)
        tree.check_module_collisions(item["path"] for item in application["files"])
        for item in application["files"]:
            require(overlay.digest(tree.read(item["path"])) == item["sha256"][5])
            parent, leaf = item["path"].rsplit("/", 1)
            with tree.parent(item["path"]) as (directory, _):
                with os.scandir(directory) as entries:
                    for count, entry in enumerate(entries):
                        require(count < 10000 and entry.name not in (leaf + "c", leaf + "o"))
                try:
                    cache = os.open("__pycache__", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                except FileNotFoundError:
                    continue
                try:
                    with os.scandir(cache) as entries:
                        for count, entry in enumerate(entries):
                            require(count < 10000 and not overlay.bytecode_for(parent + "/__pycache__/" + entry.name, item["path"]))
                finally:
                    os.close(cache)


def application_and_request(contract, sources, modules, application, measured):
    overlay, diagnostic, descriptor, app = modules
    result = {"schema": "nib.deepseek-lab-result/v1", "status": "BLOCKED", "deployable": False,
              "confidential": False, "functional_pass": False, "stage": "hardware",
              "application": None, "hardware": None, "completion": None, "engine_stopped": False}
    correctness = contract["operation"] == "local-correctness"
    if correctness:
        result.update(schema="nib.deepseek-lab-correctness-result/v1", correctness=None)
    engine = None
    started = time.monotonic()
    try:
        result["hardware"] = hardware()
        result["stage"] = "weights"
        quiet_command([ROOT + "/nib", "model", "check-local", ROOT + "/pack", "/model"], WEIGHTS_SECONDS)
        result["stage"] = "application"
        app.verify_layout(overlay, descriptor, diagnostic, application, measured)
        stages = [{item["path"]: sources[f"application/stages/{n}/" + item["path"]] for item in application["files"]}
                  for n in range(1, 6)]
        with app.selected_tree(overlay, application, measured) as (tree, console):
            tree.deadline = console[1].deadline = time.monotonic() + APPLICATION_SECONDS
            before = app.inspect(overlay, tree, application, measured, console)
            result["application"] = app.apply(overlay, tree, application, stages, before, measured, console)
            # apply_verified_stages ends with the final full inventory, checking
            # exact source, all preserved native bytes and all five receipts.
        result["stage"] = "prelaunch"
        require(hardware() == result["hardware"])
        if correctness:
            CORRECTNESS.verify_sources(read_source, contract["paths"][-1])
        require(time.monotonic() - started < WEIGHTS_SECONDS + APPLICATION_SECONDS + 60)
        result["stage"] = "startup"
        engine = subprocess.Popen([ROOT + "/python", "engine"], stdin=subprocess.DEVNULL,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        deadline = time.monotonic() + STARTUP_SECONDS
        while True:
            require(engine.poll() is None and time.monotonic() < deadline)
            try:
                http_request("/health")
                break
            except (OSError, ValueError, http.client.HTTPException):
                time.sleep(1)
        models = json.loads(http_request("/v1/models"))
        require(type(models) is dict and type(models.get("data")) is list
                and [row.get("id") for row in models["data"]] == [MODEL])
        result["stage"] = "request"
        if correctness:
            result["correctness"] = CORRECTNESS.run(
                correctness_request, MODEL, deadline=started + TOTAL_SECONDS - CORRECTNESS.TEARDOWN_RESERVE)
        else:
            raw = http_request("/v1/chat/completions", timeout=REQUEST_SECONDS, data={
                "model": MODEL, "messages": [{"role": "user", "content": "Compute 1 + 1. Reply with the single digit only."}],
                "max_tokens": 128, "temperature": 0, "stream": False})
            result["completion"] = completion_result(raw)
        require(hardware() == result["hardware"])
        result["functional_pass"] = (result["correctness"]["all_cases_pass"] if correctness else
                                    (result["completion"]["final_content_present"]
                                     and result["completion"]["synthetic_answer_match"]
                                     and result["completion"]["finish"] == "stop"))
        result["stage"] = "complete"
    except Exception:
        result["functional_pass"] = False
    finally:
        if engine is not None:
            try:
                os.killpg(engine.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                code = engine.wait(timeout=CLEANUP_SECONDS)
                result["engine_stopped"] = True
                if code not in (0, -signal.SIGTERM):
                    result["functional_pass"] = False
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(engine.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                engine.wait(timeout=10)
                result["functional_pass"] = False
        # Whole-container destruction, not leader exit, establishes final cleanup.
    return result


def lab_entry():
    if len(sys.argv) == 3 and sys.argv[2] == "probe-diagnostic":
        print(json.dumps(diagnostic_child(sys.argv[1]), sort_keys=True))
        return
    require(sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode and len(sys.argv) >= 3)
    contract, sources, modules, application, measured = lab_context(sys.argv[1])
    mode, rest = sys.argv[2], sys.argv[2:]
    if contract["operation"] == "hardware-diagnostic":
        require(mode == "diagnose-hardware" and len(rest) == 1)
        signal.signal(signal.SIGALRM, lambda *_: os._exit(124))
        signal.alarm(60)
        print(json.dumps(diagnostic_request(), sort_keys=True))
        return
    require(contract["operation"] in ("functional", "local-correctness"))
    if mode == "apply" and len(rest) == 1:
        for path in ("/cache/home", "/cache/tmp", "/cache/hf", "/cache/deep-gemm", "/cache/triton",
                     "/cache/inductor", "/cache/torch", "/cache/xdg"):
            Path(path).mkdir(mode=0o700, parents=True, exist_ok=True)
        # Persistent alarm covers native probes, HTTP reads, application and all
        # error paths. The independent host watchdog also enforces the deadline.
        signal.signal(signal.SIGALRM, lambda *_: os._exit(124))
        signal.alarm(TOTAL_SECONDS)
        print(json.dumps(application_and_request(contract, sources, modules, application, measured), sort_keys=True))
    elif mode == "probe" and len(rest) == 1:
        install_lab({"paths": contract["paths"], "stdlib_roots": contract["paths"][:2],
                     "wrapper": ROOT + "/python", "launcher": ROOT + "/launch.py"}, sys.modules[__name__])
        print(json.dumps(GPU_PROBE.validate(GPU_PROBE.NVML().observe()), sort_keys=True))
    elif mode == "engine" and len(rest) == 1:
        recheck_sources(modules[0], application, contract["paths"])
        run_engine(contract)
    else:
        recheck_sources(modules[0], application, contract["paths"])
        state = install_lab({"paths": contract["paths"], "stdlib_roots": contract["paths"][:2],
                             "wrapper": ROOT + "/python", "launcher": ROOT + "/launch.py"}, sys.modules[__name__])
        if mode == "-c":
            path = vecisa_library(rest, state["layout"], sys.modules[__name__])
            require(os.environ["LD_LIBRARY_PATH"] == contract["environment"]["LD_LIBRARY_PATH"])
            signal.signal(signal.SIGALRM, lambda *_: os._exit(124))
            signal.alarm(55)
            vecisa_load(path, sys.modules[__name__])
        else:
            dispatch_lab(rest, state, sys.modules[__name__])


if __name__ == "__main__":
    try:
        lab_entry()
    except Exception:
        sys.exit("Lab operation rejected.")
