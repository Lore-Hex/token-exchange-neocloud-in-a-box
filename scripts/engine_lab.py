# SPDX-License-Identifier: Apache-2.0
"""Appended to exact reviewed bootstrap helpers; separate bounded CC lab entry."""

import fcntl
import http.client
import json
import signal
import stat
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
PARITY = None
RUNTIME_OBSERVER = None
PROVIDER_SESSION = None
STREAM_LAB = STREAM_COHORT = None
TAU_OWNER = None
ENTRY_CHECKPOINTS = 17
EXCEPTION_PACKET_BYTES = 3 + 8 * 36
WORKER_EXCEPTION_PATH = "/cache/worker-exception.bin"


def record_worker_exception(error, roots):
    """First failure wins in a fresh private cache; diagnostic errors are inert."""
    if type(error) is SystemExit and error.code in (None, 0):
        return
    fd = None
    try:
        packet = worker_exception_packet(error, roots)
        fd = os.open(WORKER_EXCEPTION_PATH,
                     os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        require(os.write(fd, packet) == WORKER_EXCEPTION_PACKET_BYTES)
    except BaseException:
        pass
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass


def read_worker_exception():
    try:
        raw = read_source(WORKER_EXCEPTION_PATH, WORKER_EXCEPTION_PACKET_BYTES)
    except FileNotFoundError:
        return None
    require(len(raw) == WORKER_EXCEPTION_PACKET_BYTES)
    return decode_worker_exception(raw)


class EntryCheckpointWriter:
    """One fixed byte per boundary, on the explicitly inherited pipe only."""

    def __init__(self, argument):
        require(type(argument) is str and argument.isascii() and argument.isdecimal()
                and 0 < len(argument) <= 7 and str(int(argument)) == argument)
        fd = int(argument)
        require(2 < fd <= 1048575 and stat.S_ISFIFO(os.fstat(fd).st_mode)
                and fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_WRONLY
                and not os.get_blocking(fd))
        os.set_inheritable(fd, False)
        self.fd, self.last = fd, 0

    def emit(self, code):
        require(self.fd is not None and type(code) is int
                and code == self.last + 1 and code <= ENTRY_CHECKPOINTS)
        require(os.write(self.fd, bytes([code])) == 1)
        self.last = code

    def bootstrap(self, stage, index):
        if stage == "setuptools-source":
            require(type(index) is int and 0 <= index < 4)
            code = 4 + index
        else:
            require(index is None and stage in ("preconditions", "vendor-root", "install", "complete"))
            code = {"preconditions": 3, "vendor-root": 8, "install": 9, "complete": 10}[stage]
        self.emit(code)

    def close(self):
        if self.fd is not None:
            fd, self.fd = self.fd, None
            os.close(fd)


class EntryCheckpointReader:
    """Bounded nonblocking supervisor observations, never an authorization."""

    def __init__(self, observation):
        self.observation = observation
        self.reader, self.writer = os.pipe()
        try:
            os.set_blocking(self.reader, False)
            os.set_blocking(self.writer, False)
        except BaseException:
            os.close(self.reader)
            os.close(self.writer)
            raise

    def close_writer(self):
        if self.writer is not None:
            fd, self.writer = self.writer, None
            os.close(fd)

    def poll(self):
        if self.observation["status"] != "open":
            return
        # At most 18 bytes across two reads, including an overflow/EOF check.
        budget = ENTRY_CHECKPOINTS + 1
        for _ in range(2):
            try:
                data = os.read(self.reader, budget)
            except BlockingIOError:
                return
            except OSError:
                self.observation["status"] = "read-failed"
                return
            if not data:
                self.observation["status"] = "eof"
                return
            budget -= len(data)
            for code in data:
                last = self.observation["last_id"] or 0
                if last == ENTRY_CHECKPOINTS:
                    self.observation["status"] = "overflow"
                    return
                if code != last + 1:
                    self.observation["status"] = "invalid"
                    return
                self.observation["last_id"] = code
            if budget == 0:
                return

    def finish(self):
        self.close_writer()
        self.poll()
        if self.observation["status"] == "open":
            self.observation["status"] = "incomplete"
        if self.reader is not None:
            fd, self.reader = self.reader, None
            os.close(fd)


def lab_args(plan, replicas=1, *, model=MODEL):
    if type(model) is not str or not 0 < len(model) <= 128 or not all(c in "abcdefghijklmnopqrstuvwxyz0123456789-_." for c in model):
        raise ValueError("lab model name rejected")
    if type(replicas) is not int or replicas not in (1, 2):
        raise ValueError("lab parallel profile rejected")
    arguments = list(plan["fixed_engine_arguments"])
    if arguments.count("--data-parallel-size") != 1:
        raise ValueError("lab parallel profile rejected")
    index = arguments.index("--data-parallel-size") + 1
    if index == len(arguments) or arguments[index] != "1":
        raise ValueError("lab parallel profile rejected")
    arguments[index] = str(replicas)
    # Existing reviewed all-off controls, followed only by fixed lab limits.
    return arguments + [
        "--model", "/model", "--tokenizer", "/model", "--served-model-name", model,
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
            # tokenizers v0.23.3 utils/parallelism.rs: false selects serial
            # iterators before encode_batch; do not size a pool per GPU worker.
            "TOKENIZERS_PARALLELISM": "false",
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


def model_identity(contract):
    if "model_profile" not in contract:
        require("model-profile.py" not in contract.get("sources", {}))
        return None
    raw = read_source(ROOT + "/model-profile.py")
    require(hashlib.sha256(raw).hexdigest() == contract["sources"].get("model-profile.py"))
    profiles = load("lab_model_profiles", raw, ROOT + "/model-profile.py")
    identity = profiles.validate_contract(contract)
    require("provider_conformance" not in contract or
            (contract.get("gpu_security") == "nonconfidential" and contract.get("gpu_family") == "B300"))
    if "provider_conformance" in contract:
        require("provider-session.py" in contract["sources"])
        raw = read_source(ROOT + "/provider-session.py")
        require(hashlib.sha256(raw).hexdigest() == contract["sources"]["provider-session.py"])
        load("selected_provider_session", raw, ROOT + "/provider-session.py").enabled(contract)
    require(contract["operation"] == "local-correctness"
            and (contract.get("gpu_family") == "B300" or
                 (contract.get("gpu_family") == "B200" and identity["name"] == "uncensored"))
            and (contract.get("gpu_security", "confidential") == "confidential" or
                 (contract.get("gpu_security") == "nonconfidential" and contract.get("gpu_family") == "B300"))
            and type(contract.get("data_parallel_size")) is int and contract["data_parallel_size"] == 2)
    profiles.validate_manifest(identity["name"], read_source(ROOT + "/pack/files.sha256"))
    return identity


def validate_settings(contract, plan, replicas):
    identity = model_identity(contract)
    options = {"model": identity["served_model_name"]} if identity else {}
    arguments = lab_args(plan, replicas, **options)
    tuning = tuning_module(contract)
    if tuning is not None:
        arguments = tuning.arguments(arguments, contract["execution_profile"])
    require(contract["arguments"] == arguments and contract["environment"] == lab_environment(plan)
            and contract["pack_revision"] == (identity["hf_revision"] if identity else MODEL_REVISION)
            and contract["pack_bytes"] == (identity["total_bytes"] if identity else TOTAL_BYTES))
    return identity


def tuning_module(contract):
    if "execution_profile" not in contract:
        require("tuning.py" not in contract.get("sources", {}))
        return None
    raw = read_source(ROOT + "/tuning.py")
    require(hashlib.sha256(raw).hexdigest() == contract["sources"].get("tuning.py"))
    tuning = load("lab_tuning", raw, ROOT + "/tuning.py")
    tuning.enabled(contract)
    return tuning


def lab_context(expected):
    global PROCESSES, GPU_PROBE, CORRECTNESS, PARITY, RUNTIME_OBSERVER, PROVIDER_SESSION, STREAM_LAB, STREAM_COHORT, TAU_OWNER
    import platform
    import re
    require(re.fullmatch(r"[0-9a-f]{64}", expected) and __file__ == ROOT + "/launch.py")
    raw = read_source(ROOT + "/lab.json")
    require(hashlib.sha256(raw).hexdigest() == expected)
    contract = json.loads(raw)
    family = contract.get("gpu_family", "B200")
    require(family in ("B200", "B300")
            and (family == "B200" or contract["operation"] in ("local-correctness", "pre-serve")))
    require(contract["schema"] == "nib.deepseek-lab-input/v1" and contract["status"] == "BLOCKED"
            and contract["deployable"] is False and contract["confidential"] is False
            and contract["operation"] in ("functional", "hardware-diagnostic", "local-correctness", "pre-serve")
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
    GPU_PROBE.security_profile(contract)
    replicas = GPU_PROBE.parallel_size(contract)
    if contract["operation"] == "local-correctness":
        CORRECTNESS = load("lab_correctness", sources["correctness.py"], ROOT + "/correctness.py")
    if "tokenizer_parity" in contract:
        PARITY = load("lab_tokenizer_parity", sources["tokenizer-parity.py"], ROOT + "/tokenizer-parity.py")
        require(PARITY.enabled(contract))
    if "tokenizer_materialization" in contract:
        tokenizer_adapter_options(contract, require)
    if "runtime_observation" in contract:
        RUNTIME_OBSERVER = load("lab_runtime_observer", sources["runtime-observation.py"], ROOT + "/runtime-observation.py")
        require(RUNTIME_OBSERVER.enabled(contract))
    if "stream_cohort" in contract:
        STREAM_LAB = load("lab_stream", sources["stream-lab.py"], ROOT + "/stream-lab.py")
        STREAM_LAB.enabled(contract)
        STREAM_COHORT = STREAM_LAB.load(sources, contract["sources"])
    if "provider_conformance" in contract:
        PROVIDER_SESSION = load("lab_provider_session", sources["provider-session.py"], ROOT + "/provider-session.py")
        PROVIDER_SESSION.enabled(contract)
    if "tau_owner" in contract:
        loader = load("lab_tau_sources", sources["tau-owner-sources.py"], ROOT + "/tau-owner-sources.py")
        TAU_OWNER = loader.load(sources, contract["sources"])
        TAU_OWNER.selected(contract)
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
    validate_settings(contract, plan, replicas)
    return contract, sources, modules, application, measured


def quiet_command(argv, timeout, *, output=False):
    # Reviewed per-stream bounds, timeout and whole-group teardown.
    result = PROCESSES.execute_process_group(argv, env=dict(os.environ), timeout=timeout, max_output_bytes=32768)
    result.check_returncode()
    return result.stdout if output else b""


def hardware(family="B200", security="confidential", expected=4):
    raw = quiet_command([ROOT + "/python", "probe"], 15, output=True)
    if security == "nonconfidential":
        return GPU_PROBE.validate_nonconfidential_report(json.loads(raw), family=family, expected=expected)
    require(security == "confidential")
    return GPU_PROBE.validate_report(json.loads(raw), family=family, expected=expected)


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
    return bounded_request("/v1/chat/completions", payload, deadline)


def bounded_request(path, payload, deadline):
    """A whole-request deadline, including slow headers or trickling bodies."""
    require(path in ("/tokenize", "/v1/chat/completions"))
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
        return http_request(path, data=payload, timeout=remaining)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        restored = previous_timer[0] - (time.monotonic() - before)
        if restored <= 0:
            os._exit(124)
        signal.setitimer(signal.ITIMER_REAL, restored)


def completion_result(raw, *, model=MODEL):
    row = json.loads(raw)
    require(type(row) is dict and "error" not in row and row.get("object") == "chat.completion"
            and row.get("model") == model and type(row.get("choices")) is list and len(row["choices"]) == 1)
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


def local_tokenizer(contract, modules, application):
    """Private child emits only hashes/counts, never tokenizer or library output."""
    require(PARITY.enabled(contract))
    report_fd = os.dup(1)
    os.set_inheritable(report_fd, False)
    try:
        with open(os.devnull, "r+b", buffering=0) as sink:
            for fd in (0, 1, 2):
                os.dup2(sink.fileno(), fd)
        signal.signal(signal.SIGALRM, lambda *_: os._exit(124))
        signal.alarm(PARITY.LOCAL_SECONDS)
        recheck_sources(modules[0], application, contract["paths"])
        PARITY.verify_sources(read_source, contract["paths"][-1], CORRECTNESS)
        PARITY.verify_model(read_source)
        state = install_lab({"paths": contract["paths"], "stdlib_roots": contract["paths"][:2],
                             "wrapper": ROOT + "/python", "launcher": ROOT + "/launch.py"}, sys.modules[__name__])
        from vllm.tokenizers.deepseek_v41 import DeepseekV41Tokenizer
        from vllm.entrypoints.openai.chat_completion.protocol import ChatCompletionToolsParam
        tokenizer = DeepseekV41Tokenizer.from_pretrained("/model", local_files_only=True, trust_remote_code=False)
        row = PARITY.local_measure(tokenizer, ChatCompletionToolsParam, CORRECTNESS)
        PARITY.validate_local(row, CORRECTNESS)
        if "stream_cohort" in contract:
            row = STREAM_LAB.child(tokenizer, row, hashlib.sha256(read_source(ROOT + "/lab.json")).hexdigest(),
                                   STREAM_COHORT, time.monotonic() + STREAM_COHORT.PREPARE_SECONDS)
        state["check"]()
        PARITY.verify_model(read_source)
        if "stream_cohort" in contract:
            PARITY.verify_sources(read_source, contract["paths"][-1], CORRECTNESS)
            recheck_sources(modules[0], application, contract["paths"])
        raw = PARITY.encoded(row)
        require(len(raw) <= 8192 and os.write(report_fd, raw) == len(raw))
    finally:
        os.close(report_fd)


def prepare_local_tokenizer(contract, started):
    remaining = started + TOTAL_SECONDS - CORRECTNESS.TEARDOWN_RESERVE - time.monotonic()
    require(remaining > 0)
    value = CORRECTNESS.strict_json(quiet_command([ROOT + "/python", "tokenizer-parity"],
                                                 min(PARITY.LOCAL_SECONDS, remaining), output=True))
    if "stream_cohort" in contract:
        return STREAM_LAB.admit_child(value, hashlib.sha256(read_source(ROOT + "/lab.json")).hexdigest(),
                                      STREAM_COHORT, PARITY, CORRECTNESS)
    return value, None


def lab_layout(contract):
    layout = {"paths": contract["paths"], "stdlib_roots": contract["paths"][:2],
              "wrapper": ROOT + "/python", "launcher": ROOT + "/launch.py"}
    if "tokenizer_materialization" in contract:
        layout.update(tokenizer_adapter_options(contract, require))
    return layout


def prepare_engine(contract, checkpoints):
    state = install_lab(lab_layout(contract), sys.modules[__name__],
                        checkpoint=checkpoints.bootstrap)
    # At this boundary all metadata/paths are established before engine imports.
    checkpoints.emit(11)
    from vllm.entrypoints.cli.serve import ServeSubcommand
    checkpoints.emit(12)
    from vllm.utils.argparse_utils import FlexibleArgumentParser
    checkpoints.emit(13)
    parser = FlexibleArgumentParser()
    serve = ServeSubcommand()
    serve.subparser_init(parser.add_subparsers(dest="subparser"))
    checkpoints.emit(14)
    args = parser.parse_args(["serve", *contract["arguments"]])
    checkpoints.emit(15)
    serve.validate(args)
    checkpoints.emit(16)
    state["check"]()
    return serve, args


def run_engine(contract, checkpoints, errors=None):
    serve, args = prepare_engine(contract, checkpoints)
    checkpoints.emit(17)
    # Checkpoints end here. Keep the non-inheritable error pipe in this process
    # so an exception from serve.cmd is observable before readiness.
    checkpoints.close()
    if errors is not None:
        require(errors.fd is not None and not os.get_inheritable(errors.fd))
    serve.cmd(args)


def read_entry_exception(channel):
    """One fixed packet or EOF. Never persist stderr, messages or locals."""
    raw = os.read(channel.reader, EXCEPTION_PACKET_BYTES + 1)
    if not raw:
        return None
    require(len(raw) == EXCEPTION_PACKET_BYTES and os.read(channel.reader, 1) == b"")
    return decode_exception(raw)


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


def observe_startup(engine, observation, checkpoints=None, *, model=MODEL):
    """Record only supervisor observations; never consume the engine's logs."""
    started = time.monotonic()
    observation["outcome"] = "supervisor-failure"
    try:
        deadline = started + STARTUP_SECONDS
        while True:
            if checkpoints is not None:
                checkpoints.poll()
            code = engine.poll()
            if code is not None:
                require(type(code) is int and -128 <= code <= 255)
                observation.update(outcome="child-exited", exit_code=code)
                raise ValueError("engine-startup-rejected")
            if time.monotonic() >= deadline:
                observation["outcome"] = "startup-timeout"
                raise ValueError("engine-startup-rejected")
            observation["health_probes"] += 1
            try:
                http_request("/health")
                break
            except (OSError, ValueError, http.client.HTTPException):
                time.sleep(1)
        observation["outcome"] = "models-rejected"
        models = json.loads(http_request("/v1/models"))
        require(type(models) is dict and type(models.get("data")) is list
                and all(type(row) is dict for row in models["data"])
                and [row.get("id") for row in models["data"]] == [model])
        observation["outcome"] = "ready"
    finally:
        if checkpoints is not None:
            checkpoints.finish()
        elapsed = time.monotonic() - started
        if not 0 <= elapsed <= TOTAL_SECONDS:
            observation.update(outcome="supervisor-failure", exit_code=None)
            raise ValueError("engine-startup-clock-rejected")
        observation["wait_ms"] = int(elapsed * 1000)


def observe_pre_serve(engine, observation, checkpoints):
    started = time.monotonic()
    observation["outcome"] = "supervisor-failure"
    try:
        try:
            code = engine.wait(timeout=300)
        except subprocess.TimeoutExpired:
            observation["outcome"] = "startup-timeout"
            raise
        require(type(code) is int and -128 <= code <= 255)
        observation.update(outcome="child-exited", exit_code=code)
        checkpoints.finish()
        require(code == 0 and checkpoints.observation == {"status": "eof", "last_id": 16})
        observation["outcome"] = "prepared"
    finally:
        observation["wait_ms"] = int((time.monotonic() - started) * 1000)


def initialize_cache():
    for path in ("/cache/home", "/cache/tmp", "/cache/hf", "/cache/deep-gemm", "/cache/triton",
                 "/cache/inductor", "/cache/torch", "/cache/xdg"):
        Path(path).mkdir(mode=0o700, parents=True, exist_ok=True)


def check_scratch_mounts():
    # Also called as an ordinary module, without the concatenated bootstrap.
    import os
    # Docker tmpfs defaults to noexec. JIT shared libraries need exec on the
    # private cache, never on the general temporary mount.
    required = os.ST_NOSUID | os.ST_NODEV
    for path, executable in (("/cache", True), ("/tmp", False)):
        flags = os.statvfs(path).f_flag
        if not (flags & required == required and not flags & os.ST_RDONLY
                and bool(flags & os.ST_NOEXEC) != executable):
            raise ValueError("sealed Python bootstrap rejected")


def observe_runtime(engine, contract, row, started):
    require(engine.poll() is None and len(row["samples"]) < 2)
    remaining = started + TOTAL_SECONDS - CORRECTNESS.TEARDOWN_RESERVE - time.monotonic()
    require(remaining > RUNTIME_OBSERVER.SECONDS + RUNTIME_OBSERVER.CLEANUP_SECONDS)
    event = RUNTIME_OBSERVER.EVENTS[len(row["samples"])]
    raw = quiet_command([ROOT + "/python", "runtime-observation", str(engine.pid), event],
                        RUNTIME_OBSERVER.SECONDS, output=True)
    sample = CORRECTNESS.strict_json(raw)
    require(engine.poll() is None)
    candidate = {**row, "samples": [*row["samples"], sample]}
    RUNTIME_OBSERVER.validate(candidate, contract, row["contract_sha256"])
    row["samples"].append(sample)


def application_and_request(contract, sources, modules, application, measured):
    overlay, diagnostic, descriptor, app = modules
    identity = model_identity(contract)
    model = identity["served_model_name"] if identity else MODEL
    model_options = {"model": model} if identity else {}
    result = {"schema": "nib.deepseek-lab-result/v2", "status": "BLOCKED", "deployable": False,
              "confidential": False, "functional_pass": False, "stage": "hardware",
              "application": None, "hardware": None, "completion": None,
              "startup": None, "engine_stopped": False}
    if identity is not None:
        result["model_profile"] = identity
    correctness = contract["operation"] == "local-correctness"
    parity = contract.get("tokenizer_parity") is True
    if "provider_conformance" in contract:
        PROVIDER_SESSION.enabled(contract)
        result["provider_conformance"] = None
    if "stream_cohort" in contract:
        STREAM_LAB.enabled(contract)
        result["stream_cohort"] = None
    if "tau_owner" in contract:
        TAU_OWNER.selected(contract)
        result["tau_owner"] = None
    owner_errors = (TAU_OWNER.worker.Abort,) if "tau_owner" in contract else ()
    if "tokenizer_materialization" in contract:
        result.update(tokenizer_adapter_options(contract, require))
    if "runtime_observation" in contract:
        require(RUNTIME_OBSERVER.enabled(contract))
        result["runtime_observation"] = {"schema": RUNTIME_OBSERVER.SCHEMA,
            "contract_sha256": hashlib.sha256(read_source(ROOT + "/lab.json")).hexdigest(),
            "tau_admission": False, "quality_eligible": False, "samples": []}
    pre_serve = contract["operation"] == "pre-serve"
    if correctness:
        if parity:
            result.update(schema="nib.deepseek-lab-tokenizer-parity/v1", tokenizer_parity=None)
        else:
            result.update(schema="nib.deepseek-lab-correctness-result/v2", correctness=None)
    if pre_serve:
        result.update(schema="nib.deepseek-lab-pre-serve/v1", entry_pass=False)
    engine = checkpoints = errors = None
    started = time.monotonic()
    try:
        check_scratch_mounts()
        security = GPU_PROBE.security_profile(contract)
        count = 4 * GPU_PROBE.parallel_size(contract)
        result["hardware"] = hardware(contract.get("gpu_family", "B200"), security, count)
        if not pre_serve:
            result["stage"] = "weights"
            quiet_command([ROOT + "/nib", "model", "check-local", "--workers", "8", ROOT + "/pack", "/model"], WEIGHTS_SECONDS)
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
        require(hardware(contract.get("gpu_family", "B200"), security, count) == result["hardware"])
        if parity:
            PARITY.verify_sources(read_source, contract["paths"][-1], CORRECTNESS)
        elif correctness:
            CORRECTNESS.verify_sources(read_source, contract["paths"][-1])
        tuning = tuning_module(contract)
        if tuning is not None:
            tuning.verify_sources(read_source, contract["paths"][-1])
        if "stream_cohort" in contract:
            # A bad benchmark fixture must not consume a full model startup.
            local, stream_workload = prepare_local_tokenizer(contract, started)
        require(time.monotonic() - started < WEIGHTS_SECONDS + APPLICATION_SECONDS + 60)
        result["stage"] = "startup"
        result["startup"] = {"outcome": "launch-failed", "wait_ms": 0,
                             "exit_code": None, "health_probes": 0,
                             "exception_diagnostic": None,
                             "worker_exception_diagnostic": None,
                             "checkpoints": {"status": "not-started", "last_id": None}}
        checkpoints = EntryCheckpointReader(result["startup"]["checkpoints"])
        errors = EntryCheckpointReader({"status": "not-started", "last_id": None})
        try:
            engine = subprocess.Popen(
                [ROOT + "/python", "engine", "--checkpoint-fd", str(checkpoints.writer),
                 "--error-fd", str(errors.writer)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True, pass_fds=(checkpoints.writer, errors.writer))
            checkpoints.observation["status"] = "open"
        finally:
            checkpoints.close_writer()
            errors.close_writer()
        try:
            if pre_serve:
                observe_pre_serve(engine, result["startup"], checkpoints)
            else:
                observe_startup(engine, result["startup"], checkpoints, **model_options)
        finally:
            # A live server keeps its error writer open. Read only after exit,
            # when a complete fixed packet or EOF can be required without blocking.
            if engine.poll() is not None:
                result["startup"]["exception_diagnostic"] = read_entry_exception(errors)
                result["startup"]["worker_exception_diagnostic"] = read_worker_exception()
        if pre_serve:
            require(result["startup"]["exception_diagnostic"] is None
                    and hardware(contract.get("gpu_family", "B200"), security, count) == result["hardware"])
            result.update(stage="complete", entry_pass=True)
            return result
        result["stage"] = "request"
        if "runtime_observation" in contract:
            observe_runtime(engine, contract, result["runtime_observation"], started)
        if parity:
            if "stream_cohort" in contract:
                PARITY.verify_model(read_source)
                PARITY.verify_sources(read_source, contract["paths"][-1], CORRECTNESS)
            else:
                local, _ = prepare_local_tokenizer(contract, started)
            result["tokenizer_parity"] = PARITY.run(bounded_request, local, model, CORRECTNESS,
                deadline=started + TOTAL_SECONDS - CORRECTNESS.TEARDOWN_RESERVE,
                **({"max_model_len": tuning.context_limit(contract)} if tuning else {}))
        elif correctness:
            result["correctness"] = CORRECTNESS.run(
                correctness_request, model, deadline=started + TOTAL_SECONDS - CORRECTNESS.TEARDOWN_RESERVE)
        else:
            raw = http_request("/v1/chat/completions", timeout=REQUEST_SECONDS, data={
                "model": model, "messages": [{"role": "user", "content": "Compute 1 + 1. Reply with the single digit only."}],
                "max_tokens": 128, "temperature": 0, "stream": False})
            result["completion"] = completion_result(raw, **model_options)
        if "provider_conformance" in contract:
            require(result["tokenizer_parity"]["all_cases_pass"] is True and engine.poll() is None)
            result["provider_conformance"] = PROVIDER_SESSION.guest_wait(
                result["runtime_observation"]["contract_sha256"], result["runtime_observation"]["samples"][0],
                min(time.monotonic() + PROVIDER_SESSION.SECONDS,
                    started + TOTAL_SECONDS - CORRECTNESS.TEARDOWN_RESERVE))
        if "tau_owner" in contract:
            require(result["tokenizer_parity"]["all_cases_pass"] is True and engine.poll() is None)
            result["tau_owner"] = TAU_OWNER.serve(engine, contract,
                result["runtime_observation"]["contract_sha256"], result["runtime_observation"]["samples"][0],
                result["tokenizer_parity"], started + TOTAL_SECONDS - CORRECTNESS.TEARDOWN_RESERVE)
        if "stream_cohort" in contract:
            require(result["tokenizer_parity"]["all_cases_pass"] is True and engine.poll() is None)
            result["stream_cohort"] = STREAM_LAB.run(engine, contract,
                hashlib.sha256(read_source(ROOT + "/lab.json")).hexdigest(), stream_workload,
                STREAM_COHORT, RUNTIME_OBSERVER, started + TOTAL_SECONDS - CORRECTNESS.TEARDOWN_RESERVE,
                **model_options)
        require(hardware(contract.get("gpu_family", "B200"), security, count) == result["hardware"])
        if "runtime_observation" in contract:
            observe_runtime(engine, contract, result["runtime_observation"], started)
        result["functional_pass"] = (result["tokenizer_parity" if parity else "correctness"]["all_cases_pass"] if correctness else
                                    (result["completion"]["final_content_present"]
                                     and result["completion"]["synthetic_answer_match"]
                                     and result["completion"]["finish"] == "stop"))
        if "provider_conformance" in contract:
            result["functional_pass"] = result["functional_pass"] and result["provider_conformance"]["accepted"]
        if "tau_owner" in contract:
            result["functional_pass"] = result["functional_pass"] and TAU_OWNER.validate_result(
                result["tau_owner"])["status"] == "evaluated"
        if "stream_cohort" in contract:
            result["functional_pass"] = result["functional_pass"] and STREAM_LAB.validate(result["stream_cohort"],
                contract, hashlib.sha256(read_source(ROOT + "/lab.json")).hexdigest(), STREAM_COHORT, **model_options)
        result["stage"] = "complete"
    except (Exception, *owner_errors):
        result["functional_pass"] = False
    finally:
        if checkpoints is not None:
            checkpoints.finish()
        if errors is not None:
            errors.finish()
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
        if pre_serve and not result["engine_stopped"]:
            result["entry_pass"] = False
    return result


def lab_entry():
    if len(sys.argv) == 3 and sys.argv[2] == "probe-diagnostic":
        print(json.dumps(diagnostic_child(sys.argv[1]), sort_keys=True))
        return
    require(sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode and len(sys.argv) >= 3)
    if sys.argv[2] == "engine":
        require(len(sys.argv) == 7 and sys.argv[3] == "--checkpoint-fd"
                and sys.argv[5] == "--error-fd" and sys.argv[4] != sys.argv[6])
        checkpoints = EntryCheckpointWriter(sys.argv[4])
        errors = None
        roots = (ROOT + "/",)
        try:
            errors = EntryCheckpointWriter(sys.argv[6])
            checkpoints.emit(1)
            contract, _, modules, application, _ = lab_context(sys.argv[1])
            roots = (ROOT + "/", *tuple(path.rstrip("/") + "/" for path in contract["paths"]))
            require(contract["operation"] in ("functional", "local-correctness", "pre-serve"))
            checkpoints.emit(2)
            recheck_sources(modules[0], application, contract["paths"])
            if contract["operation"] == "pre-serve":
                prepare_engine(contract, checkpoints)
            else:
                run_engine(contract, checkpoints, errors)
        except BaseException as error:
            if errors is not None and errors.fd is not None:
                try:
                    packet = exception_packet(error, roots)
                    require(os.write(errors.fd, packet) == EXCEPTION_PACKET_BYTES)
                except Exception:
                    pass
            raise
        finally:
            if errors is not None:
                errors.close()
            checkpoints.close()
        return
    contract, sources, modules, application, measured = lab_context(sys.argv[1])
    mode, rest = sys.argv[2], sys.argv[2:]
    if mode == "stream-collector":
        require(len(rest) == 1 and STREAM_LAB is not None and STREAM_LAB.concurrent(contract))
        STREAM_COHORT.c8.child(model_identity(contract)["served_model_name"])
        return
    if mode == "runtime-observation":
        require(len(rest) == 3 and RUNTIME_OBSERVER.enabled(contract)
                and re.fullmatch(r"[1-9][0-9]{0,9}", rest[1]) and rest[2] in RUNTIME_OBSERVER.EVENTS)
        signal.signal(signal.SIGALRM, lambda *_: os._exit(124))
        signal.alarm(RUNTIME_OBSERVER.SECONDS)
        sample = RUNTIME_OBSERVER.observe(int(rest[1]), contract, sys.argv[1], rest[2],
                                         time.monotonic() + RUNTIME_OBSERVER.SECONDS)
        print(json.dumps(sample, sort_keys=True))
        return
    if mode == "tokenizer-parity":
        require(len(rest) == 1 and contract.get("tokenizer_parity") is True)
        local_tokenizer(contract, modules, application)
        return
    if contract["operation"] == "hardware-diagnostic":
        require(mode == "diagnose-hardware" and len(rest) == 1)
        signal.signal(signal.SIGALRM, lambda *_: os._exit(124))
        signal.alarm(60)
        print(json.dumps(diagnostic_request(), sort_keys=True))
        return
    require(contract["operation"] in ("functional", "local-correctness", "pre-serve"))
    if mode == "apply" and len(rest) == 1:
        initialize_cache()
        # Persistent alarm covers native probes, HTTP reads, application and all
        # error paths. The independent host watchdog also enforces the deadline.
        signal.signal(signal.SIGALRM, lambda *_: os._exit(124))
        signal.alarm(TOTAL_SECONDS)
        print(json.dumps(application_and_request(contract, sources, modules, application, measured), sort_keys=True))
    elif mode == "probe" and len(rest) == 1:
        install_lab({"paths": contract["paths"], "stdlib_roots": contract["paths"][:2],
                     "wrapper": ROOT + "/python", "launcher": ROOT + "/launch.py"}, sys.modules[__name__])
        family = contract.get("gpu_family", "B200")
        count = 4 * GPU_PROBE.parallel_size(contract)
        if GPU_PROBE.security_profile(contract) == "nonconfidential":
            observation = GPU_PROBE.validate_nonconfidential(
                GPU_PROBE.NVML().observe(family, nonconfidential=True, expected=count), family=family, expected=count)
        else:
            observation = GPU_PROBE.validate(GPU_PROBE.NVML().observe(family, expected=count), family=family, expected=count)
        print(json.dumps(observation, sort_keys=True))
    else:
        recheck_sources(modules[0], application, contract["paths"])
        state = install_lab(lab_layout(contract), sys.modules[__name__])
        if mode == "-c":
            path = vecisa_library(rest, state["layout"], sys.modules[__name__])
            require(os.environ["LD_LIBRARY_PATH"] == contract["environment"]["LD_LIBRARY_PATH"])
            signal.signal(signal.SIGALRM, lambda *_: os._exit(124))
            signal.alarm(55)
            vecisa_load(path, sys.modules[__name__])
        else:
            roots = (ROOT + "/", *tuple(path.rstrip("/") + "/" for path in contract["paths"]))
            observer = lambda error: record_worker_exception(error, roots)
            try:
                dispatch_lab(rest, state, sys.modules[__name__], worker_observer=observer)
            except BaseException as error:
                observer(error)
                raise


if __name__ == "__main__":
    try:
        lab_entry()
    except Exception:
        sys.exit("Lab operation rejected.")
