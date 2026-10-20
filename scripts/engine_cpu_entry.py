# SPDX-License-Identifier: Apache-2.0
"""CPU-only pre-serve boundary diagnostic, appended to reviewed source helpers."""

import signal
import subprocess
import time

ROOT = "/opt/nib"
APPLICATION_SECONDS = 720
ENTRY_SECONDS = 300
ATTACH_SECONDS = 1080
STATE_FIELDS = ("paths", "executable", "spawn_executable", "start_method", "cwd",
                "pycache_prefix", "plugins", "worker_method", "numa_environment", "finders")
SCHEMA = "nib.engine-cpu-entry/v1"


def load_module(name, raw, path):
    module = types.ModuleType(name)
    module.__file__ = path
    exec(compile(raw, path, "exec", flags=0, dont_inherit=True), module.__dict__)
    return module


def context(expected):
    import platform
    import re
    require(re.fullmatch(r"[0-9a-f]{64}", expected) and __file__ == ROOT + "/launch.py")
    raw = read_source(ROOT + "/entry.json")
    require(hashlib.sha256(raw).hexdigest() == expected)
    contract = json.loads(raw)
    require(contract["schema"] == "nib.engine-cpu-entry-input/v1"
            and contract["status"] == "BLOCKED" and contract["deployable"] is False
            and contract["confidential"] is False and contract["execution"] == "software"
            and platform.system() == "Linux" and platform.machine() == "x86_64"
            and platform.python_version() == "3.12.3" and sys.pycache_prefix is None
            and os.getcwd() == "/" and dict(os.environ) == contract["environment"])
    executable = read_source(contract["interpreter"]["resolved"], 16 << 20)
    require(contract["interpreter"]["resolved"] == "/usr/bin/python3.12"
            and len(executable) == contract["interpreter"]["bytes"]
            and hashlib.sha256(executable).hexdigest() == contract["interpreter"]["sha256"])
    require(type(contract["sources"]) is dict and len(contract["sources"]) <= 110)
    sources = {}
    for name, sha in contract["sources"].items():
        require(re.fullmatch(r"[A-Za-z0-9_./-]+", name) and not name.startswith("/")
                and all(part not in ("", ".", "..") for part in name.split("/")))
        data = read_source(ROOT + "/" + name)
        require(hashlib.sha256(data).hexdigest() == sha)
        sources[name] = data
    require(sum(map(len, sources.values())) <= 8 << 20)
    lab = load_module("engine_lab", sources["lab.py"], ROOT + "/lab.py")
    sys.modules["engine_lab"] = lab
    require(read_source(ROOT + "/python") == lab.wrapper_bytes(
        contract["interpreter"]["resolved"], contract["environment"], expected))
    modules = [load_module(name, sources["application/" + name], ROOT + "/application/" + name)
               for name in ("engine_overlay.py", "engine_image_diagnostic.py", "engine_python_descriptor.py", "application.py")]
    overlay, _, _, app = modules
    require(overlay.digest(sources["application/contract.json"]) == contract["application_contract_sha256"])
    application = overlay.load_json(sources["application/contract.json"])
    app.require_contract(overlay, application)
    require(application["git_sha"] == contract["git_sha"] and application["base_image"] == contract["base_image"]
            and application["official_config"] == contract["official_config"]
            and contract["measurement_sha256"] == app.EVIDENCE_SHA)
    measured = app.measurement(overlay, sources["application/measurement.json"])
    require(contract["paths"] == measured["proposed_paths"])
    plan = overlay.load_json(sources["launch-plan.json"])
    require(contract["arguments"] == lab.lab_args(plan) and contract["environment"] == lab.lab_environment(plan))
    return contract, sources, modules, application, measured, lab


def state_mismatches():
    """Observe the existing lab guard predicates; do not replace its check."""
    state = getattr(sys, "_nib_lab_bootstrap", None)
    if state is None:
        return None
    layout = state["layout"]
    spawn, mp = sys.modules["multiprocessing.spawn"], sys.modules["multiprocessing"]
    return {
        "paths": sys.path != layout["paths"],
        "executable": sys.executable != layout["wrapper"],
        "spawn_executable": spawn.get_executable() != os.fsencode(layout["wrapper"]),
        "start_method": mp.get_start_method() != "spawn",
        "cwd": os.getcwd() != "/",
        "pycache_prefix": sys.pycache_prefix is not None,
        "plugins": os.environ.get("VLLM_PLUGINS") != "",
        "worker_method": os.environ.get("VLLM_WORKER_MULTIPROC_METHOD") != "spawn",
        "numa_environment": "NUMACTL_PYTHON" in os.environ,
        "finders": state["finders"] is not None and sys.meta_path != state["finders"],
    }


def state_bytes():
    try:
        row = state_mismatches()
        value = 65535 if row is None else sum(int(row[name]) << index for index, name in enumerate(STATE_FIELDS))
    except Exception:
        value = 65535
    return value.to_bytes(2, "big")


def decode_state(raw):
    require(type(raw) is bytes and len(raw) == 2)
    value = int.from_bytes(raw, "big")
    require(value == 65535 or value < 1 << len(STATE_FIELDS))
    return None if value == 65535 else {name: bool(value & (1 << i)) for i, name in enumerate(STATE_FIELDS)}


def entry_child(expected, checkpoint_fd, state_fd, lab):
    checkpoints = lab.EntryCheckpointWriter(checkpoint_fd)
    state_writer = None
    try:
        require(state_fd != checkpoint_fd)
        state_writer = lab.EntryCheckpointWriter(state_fd)
        checkpoints.emit(1)
        contract, _, modules, application, _, verified_lab = context(expected)
        require(verified_lab.__file__ == lab.__file__)
        checkpoints.emit(2)
        verified_lab.recheck_sources(modules[0], application, contract["paths"])
        # The shared helper stops after the original state check. No ID 17,
        # serve.cmd, HTTP client, model loader, probe, or worker route is called.
        verified_lab.prepare_engine(contract, checkpoints)
    finally:
        try:
            if state_writer is not None:
                try:
                    require(os.write(state_writer.fd, state_bytes()) == 2)
                finally:
                    state_writer.close()
        finally:
            checkpoints.close()


def observe_entry(lab):
    result = {"outcome": "launch-failed", "exit_code": None,
              "checkpoints": {"status": "not-started", "last_id": None},
              "state_observation": "unavailable", "state_mismatches": None}
    checkpoints = lab.EntryCheckpointReader(result["checkpoints"])
    state = lab.EntryCheckpointReader({"status": "not-started", "last_id": None})
    process = None
    try:
        try:
            process = subprocess.Popen(
                [ROOT + "/python", "entry", "--checkpoint-fd", str(checkpoints.writer), "--state-fd", str(state.writer)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                pass_fds=(checkpoints.writer, state.writer), start_new_session=True)
            checkpoints.observation["status"] = "open"
        finally:
            checkpoints.close_writer()
            state.close_writer()
        result["outcome"] = "supervisor-failure"
        try:
            code = process.wait(timeout=ENTRY_SECONDS)
            require(type(code) is int and -128 <= code <= 255)
            result.update(outcome="exited", exit_code=code)
        except subprocess.TimeoutExpired:
            result["outcome"] = "timeout"
    except Exception:
        pass
    finally:
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=10)
        checkpoints.finish()
        try:
            # A two-byte bitset only, not stdout or a stack; extra bytes fail.
            raw = os.read(state.reader, 3)
            if len(raw) == 2 and os.read(state.reader, 1) == b"":
                result["state_mismatches"] = decode_state(raw)
                result["state_observation"] = "unavailable" if result["state_mismatches"] is None else "observed"
            elif raw:
                result["state_observation"] = "invalid"
        except (OSError, ValueError):
            result["state_observation"] = "invalid"
        finally:
            state.finish()
    return result


def diagnostic_pass(entry):
    return (entry["outcome"] == "exited" and entry["exit_code"] == 0
            and entry["checkpoints"] == {"status": "eof", "last_id": 16}
            and entry["state_observation"] == "observed"
            and entry["state_mismatches"] is not None and not any(entry["state_mismatches"].values()))


def apply_and_observe(contract, sources, modules, application, measured, lab):
    overlay, diagnostic, descriptor, app = modules
    result = {"schema": SCHEMA, "status": "BLOCKED", "execution": "software",
              "deployable": False, "confidential": False, "hardware_validated": False,
              "abi_validated": False, "inference_started": False, "entry_pass": False,
              "stage": "application-preflight", "application": None, "entry": None,
              "phase_elapsed_ms": {"application-preflight": None, "application": None, "entry": None}}
    phase = "application-preflight"
    started = time.monotonic()
    deadline = started + APPLICATION_SECONDS
    try:
        app.verify_layout(overlay, descriptor, diagnostic, application, measured)
        result["phase_elapsed_ms"][phase] = int((time.monotonic() - started) * 1000)
        phase = result["stage"] = "application"
        started = time.monotonic()
        stages = [{item["path"]: sources[f"application/stages/{n}/" + item["path"]] for item in application["files"]}
                  for n in range(1, 6)]
        with app.selected_tree(overlay, application, measured) as (tree, console):
            require(0 < deadline - time.monotonic() <= APPLICATION_SECONDS)
            tree.deadline = console[1].deadline = deadline
            before = app.inspect(overlay, tree, application, measured, console)
            result["application"] = app.apply(overlay, tree, application, stages, before, measured, console)
        require(time.monotonic() <= deadline)
        result["phase_elapsed_ms"][phase] = int((time.monotonic() - started) * 1000)
        phase = result["stage"] = "entry"
        started = time.monotonic()
        for name in ("home", "tmp", "hf", "deep-gemm", "triton", "inductor", "torch", "xdg"):
            os.mkdir("/cache/" + name, mode=0o700)
        result["entry"] = observe_entry(lab)
        result["entry_pass"] = diagnostic_pass(result["entry"])
        result["stage"] = "complete"
    except Exception:
        result["failure"] = "bounded-cpu-entry-rejected"
    finally:
        result["phase_elapsed_ms"][phase] = int((time.monotonic() - started) * 1000)
    return result


def validate(overlay, report, application, measured, receipt_validator):
    fixed = {"schema": SCHEMA, "status": "BLOCKED", "execution": "software",
             "deployable": False, "confidential": False, "hardware_validated": False,
             "abi_validated": False, "inference_started": False}
    require(type(report) is dict
            and set(report) in (set(fixed) | {"entry_pass", "stage", "application", "entry", "phase_elapsed_ms"},
                                set(fixed) | {"entry_pass", "stage", "application", "entry", "phase_elapsed_ms", "failure"})
            and all(overlay.json_bytes(report[key]) == overlay.json_bytes(value) for key, value in fixed.items())
            and type(report["entry_pass"]) is bool
            and report["stage"] in ("application-preflight", "application", "entry", "complete"))
    require(("failure" in report) == (report["stage"] != "complete"))
    if "failure" in report:
        require(report["failure"] == "bounded-cpu-entry-rejected" and report["entry_pass"] is False)
    phases = ("application-preflight", "application", "entry")
    timings = report["phase_elapsed_ms"]
    reached = 3 if report["stage"] == "complete" else phases.index(report["stage"]) + 1
    require(type(timings) is dict and set(timings) == set(phases))
    for index, name in enumerate(phases):
        value = timings[name]
        require((type(value) is int and 0 <= value <= ATTACH_SECONDS * 1000) if index < reached else value is None)
    require(sum(value for value in timings.values() if value is not None) <= ATTACH_SECONDS * 1000)
    if report["application"] is not None:
        require(report["stage"] != "application-preflight")
        # Reuse the authoritative receipt validator, not an alternate integrity
        # check. This validation envelope does not claim a P0 import attempt.
        receipt_validator.validate(overlay, {
            "schema": "nib.engine-import-smoke/v1", "status": "BLOCKED", "deployable": False,
            "image": None, "abi_validated": False, "hardware_validated": False, "inference_started": False,
            "stage": "import-smoke", "application": report["application"], "smoke": None,
            "failure": "bounded-smoke-stage-rejected",
            "phase_elapsed_ms": {"application-preflight": 0, "application": 0, "import-smoke": 0}},
            application, measured)
    if reached == 3:
        require(report["application"] is not None
                and timings["application-preflight"] + timings["application"] <= APPLICATION_SECONDS * 1000)
    row = report["entry"]
    require((row is not None) == (report["stage"] == "complete"))
    if row is None:
        return False
    require(type(row) is dict and set(row) == {"outcome", "exit_code", "checkpoints", "state_observation", "state_mismatches"}
            and row["outcome"] in ("launch-failed", "supervisor-failure", "timeout", "exited")
            and row["state_observation"] in ("observed", "unavailable", "invalid"))
    require((type(row["exit_code"]) is int and -128 <= row["exit_code"] <= 255)
            if row["outcome"] == "exited" else row["exit_code"] is None)
    checkpoints = row["checkpoints"]
    require(type(checkpoints) is dict and set(checkpoints) == {"status", "last_id"}
            and checkpoints["status"] in ("not-started", "eof", "incomplete", "invalid", "overflow", "read-failed")
            and (checkpoints["last_id"] is None or type(checkpoints["last_id"]) is int and 1 <= checkpoints["last_id"] <= 16)
            and (checkpoints["status"] != "not-started" or checkpoints["last_id"] is None))
    observed = row["state_mismatches"]
    require((observed is not None) == (row["state_observation"] == "observed"))
    if observed is not None:
        require(type(observed) is dict and set(observed) == set(STATE_FIELDS)
                and all(type(value) is bool for value in observed.values()))
    require(report["entry_pass"] == diagnostic_pass(row))
    return report["entry_pass"]


def main():
    require(sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode)
    args = sys.argv[1:]
    require(len(args) == 2 and args[1] == "apply"
            or len(args) == 6 and args[1] == "entry" and args[2] == "--checkpoint-fd" and args[4] == "--state-fd")
    # Bound the running container even if its host-side Docker client dies.
    signal.signal(signal.SIGALRM, lambda *_: os._exit(124))
    signal.alarm(ATTACH_SECONDS - 15)
    # Silence native/dependency output before any source-bound module is loaded.
    saved = os.dup(1)
    os.set_inheritable(saved, False)
    null = os.open(os.devnull, os.O_WRONLY)
    os.dup2(null, 1)
    os.dup2(null, 2)
    os.close(null)
    try:
        contract, sources, modules, application, measured, lab = context(args[0])
        if args[1:] == ["apply"]:
            result = apply_and_observe(contract, sources, modules, application, measured, lab)
            raw = json.dumps(result, sort_keys=True).encode() + b"\n"
            require(len(raw) <= 65536)
            require(os.write(saved, raw) == len(raw))
        else:
            entry_child(args[0], args[3], args[5], lab)
    finally:
        os.close(saved)


if __name__ == "__main__":
    try:
        main()
    except (Exception, SystemExit):
        sys.exit(1)
