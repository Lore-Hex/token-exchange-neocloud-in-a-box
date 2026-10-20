# SPDX-License-Identifier: Apache-2.0
"""Read-only NVML observations. No setters, attestation or qualification."""

import ctypes as c
import json
import re
import sys

# ABI: vLLM 5df63a7 vllm/third_party/pynvml.py (12.570.86),
# lines 1167-1195, 2724-2754, 6384-6425. NVLE=2 is documented by
# NVIDIA NVML R595, Confidential Computing definitions, page 105.
# These are named protocol values, not a policy inferred from nonzero integers.
FEATURE_ENABLED = 1
ENVIRONMENT_PROD = 2
DEVTOOLS_OFF = 0
ACCEPTING_WORK = 1
MULTIGPU = {1: "protected-pcie", 2: "nvle"}
CALL_STAGES = {
    "nvmlInit_v2": "init", "nvmlShutdown": "shutdown",
    "nvmlDeviceGetCount_v2": "count", "nvmlDeviceGetHandleByIndex_v2": "handle",
    "nvmlDeviceGetName": "device-name", "nvmlSystemGetConfComputeSettings": "settings-abi",
    "nvmlSystemGetConfComputeGpusReadyState": "readiness",
    "nvmlDeviceGetConfComputeMemSizeInfo": "memory",
    "nvmlSystemGetDriverVersion": "driver-version", "nvmlSystemGetCudaDriverVersion_v2": "cuda-version",
}
POLICY_STAGES = ("required-count", "driver-policy", "cc-policy", "readiness-policy", "memory-policy")
TRACE_STAGES = ("ctypes-loading", *CALL_STAGES.values(), *POLICY_STAGES)


class Settings(c.Structure):
    _fields_ = [(name, c.c_uint) for name in
                ("version", "environment", "ccFeature", "devToolsMode", "multiGpuMode")]


class ProtectedMemory(c.Structure):
    _fields_ = [("protectedMemSizeKib", c.c_ulonglong), ("unprotectedMemSizeKib", c.c_ulonglong)]


def require(value):
    if not value:
        raise ValueError("lab hardware observation rejected")


class NVML:
    def __init__(self, trace=None):
        self.trace = trace
        self.checkpoint("ctypes-loading")
        self.library = c.CDLL("libnvidia-ml.so.1")

    def checkpoint(self, stage):
        if getattr(self, "trace", None) is not None:
            self.trace.at(stage)

    def call(self, name, *args):
        require(name in CALL_STAGES)
        trace = getattr(self, "trace", None)
        try:
            if trace is not None:
                trace.begin(name, args)
            function = getattr(self.library, name)
            function.restype = c.c_int
            code = function(*args)
            if trace is not None:
                trace.end(name, args, code)
            require(code == 0)
        except Exception:
            if trace is not None:
                trace.failed()
            raise

    def observe(self):
        self.call("nvmlInit_v2")
        try:
            count = c.c_uint()
            self.call("nvmlDeviceGetCount_v2", c.byref(count))
            self.checkpoint("required-count")
            require(count.value == 4)
            settings = Settings(version=0x01000014)
            self.call("nvmlSystemGetConfComputeSettings", c.byref(settings))
            driver, cuda = c.create_string_buffer(80), c.c_int()
            self.call("nvmlSystemGetDriverVersion", driver, c.c_uint(len(driver)))
            self.call("nvmlSystemGetCudaDriverVersion_v2", c.byref(cuda))
            ready = c.c_uint()
            self.call("nvmlSystemGetConfComputeGpusReadyState", c.byref(ready))
            devices = []
            for index in range(count.value):
                handle, name, memory = c.c_void_p(), c.create_string_buffer(96), ProtectedMemory()
                self.call("nvmlDeviceGetHandleByIndex_v2", c.c_uint(index), c.byref(handle))
                self.call("nvmlDeviceGetName", handle, name, c.c_uint(len(name)))
                self.call("nvmlDeviceGetConfComputeMemSizeInfo", handle, c.byref(memory))
                devices.append({"b200": b"B200" in name.value.split(),
                                "protected_kib": memory.protectedMemSizeKib})
            self.checkpoint("driver-policy")
            return {"feature": settings.ccFeature, "environment": settings.environment,
                    "devtools": settings.devToolsMode, "multigpu": settings.multiGpuMode,
                    "ready": ready.value, "devices": devices, "driver": driver.value.decode("ascii"),
                    "cuda_driver": cuda.value}
        except Exception:
            if self.trace is not None:
                self.trace.failed()
            raise
        finally:
            self.call("nvmlShutdown")


def validate(raw, checkpoint=None):
    require(type(raw) is dict and set(raw) == {"feature", "environment", "devtools", "multigpu", "ready", "devices",
                                             "driver", "cuda_driver"})
    if checkpoint is not None:
        checkpoint("driver-policy")
    require(type(raw["driver"]) is str and re.fullmatch(r"[0-9]{3}\.[0-9]{1,3}(?:\.[0-9]{1,3})?", raw["driver"])
            and type(raw["cuda_driver"]) is int and 13000 <= raw["cuda_driver"] <= 99999)
    require(all(type(raw[key]) is int for key in ("feature", "environment", "devtools", "multigpu", "ready")))
    # Deliberately narrow initial CC experiment: no legacy-disabled/NVLE-only
    # interpretation, devtools, plain fallback, or unknown/mixed result.
    if checkpoint is not None:
        checkpoint("cc-policy")
    require(raw["feature"] == FEATURE_ENABLED and raw["environment"] == ENVIRONMENT_PROD
            and raw["devtools"] == DEVTOOLS_OFF and raw["multigpu"] in MULTIGPU)
    if checkpoint is not None:
        checkpoint("readiness-policy")
    require(raw["ready"] == ACCEPTING_WORK)
    if checkpoint is not None:
        checkpoint("memory-policy")
    require(type(raw["devices"]) is list and len(raw["devices"]) == 4)
    for row in raw["devices"]:
        require(type(row) is dict and set(row) == {"b200", "protected_kib"}
                and row["b200"] is True and type(row["protected_kib"]) is int
                and 120 << 20 <= row["protected_kib"] <= 1 << 30)
    return {"status": "observed", "selected_gpus": 4, "family": "B200",
            "cc_feature": "enabled", "environment": "production", "devtools": "off",
            "multigpu": MULTIGPU[raw["multigpu"]], "system_ready": True,
            "driver": raw["driver"], "cuda_driver": f"{raw['cuda_driver'] // 1000}.{(raw['cuda_driver'] % 1000) // 10}",
            "protected_kib": [row["protected_kib"] for row in raw["devices"]],
            "attestation_verified": False}


def validate_report(row):
    require(type(row) is dict and set(row) == {"status", "selected_gpus", "family", "cc_feature", "environment",
                                             "devtools", "multigpu", "system_ready", "driver", "cuda_driver",
                                             "protected_kib", "attestation_verified"})
    require(row["status"] == "observed" and type(row["selected_gpus"]) is int and row["selected_gpus"] == 4
            and row["family"] == "B200" and row["cc_feature"] == "enabled" and row["environment"] == "production"
            and row["devtools"] == "off" and row["multigpu"] in MULTIGPU.values()
            and row["system_ready"] is True and row["attestation_verified"] is False
            and type(row["driver"]) is str and re.fullmatch(r"[0-9]{3}\.[0-9]{1,3}(?:\.[0-9]{1,3})?", row["driver"])
            and type(row["cuda_driver"]) is str and re.fullmatch(r"[0-9]{2}\.[0-9]", row["cuda_driver"])
            and type(row["protected_kib"]) is list and len(row["protected_kib"]) == 4
            and all(type(n) is int and 120 << 20 <= n <= 1 << 30 for n in row["protected_kib"]))
    return row


class Trace:
    """Fixed fields only. Never retain NVML strings, handles or exception text."""
    def __init__(self):
        self.stage = "ctypes-loading"
        self.device = None
        self.row = {
            "policy_checks_passed": False, "first_failure": None, "calls": [],
            "required_count": 4, "settings_input_version": 0x01000014, "settings_struct_bytes": c.sizeof(Settings),
            "observed": {key: None for key in ("count", "settings_version", "feature", "environment", "devtools",
                                              "multigpu", "ready", "cuda_driver", "driver_format_valid")},
            "devices": [],
        }

    def at(self, stage):
        require(stage in TRACE_STAGES)
        self.stage = stage

    def failed(self):
        if self.row["first_failure"] is None:
            self.row["first_failure"] = self.stage

    def begin(self, name, args):
        self.at(CALL_STAGES[name])
        require(len(self.row["calls"]) < 20)
        if name == "nvmlDeviceGetHandleByIndex_v2":
            require(type(args[0]) is c.c_uint and 0 <= args[0].value < 4)
            self.device = args[0].value
        self.row["calls"].append({"stage": self.stage, "device": self.device if self.stage in
                                 ("handle", "device-name", "memory") else None, "return_code": None})

    def end(self, name, args, code):
        require(type(code) is int and -(1 << 31) <= code < 1 << 31)
        self.row["calls"][-1]["return_code"] = code
        if code != 0:
            self.failed()
            return
        observed = self.row["observed"]
        if name == "nvmlDeviceGetCount_v2":
            observed["count"] = args[0]._obj.value
        elif name == "nvmlSystemGetConfComputeSettings":
            settings = args[0]._obj
            observed.update(settings_version=settings.version, feature=settings.ccFeature,
                            environment=settings.environment, devtools=settings.devToolsMode, multigpu=settings.multiGpuMode)
        elif name == "nvmlSystemGetConfComputeGpusReadyState":
            observed["ready"] = args[0]._obj.value
        elif name == "nvmlSystemGetCudaDriverVersion_v2":
            observed["cuda_driver"] = args[0]._obj.value
        elif name == "nvmlSystemGetDriverVersion":
            observed["driver_format_valid"] = re.fullmatch(rb"[0-9]{3}\.[0-9]{1,3}(?:\.[0-9]{1,3})?", args[0].value) is not None
        elif name == "nvmlDeviceGetHandleByIndex_v2":
            self.row["devices"].append({"index": self.device, "b200": None, "protected_kib": None, "unprotected_kib": None})
        elif name == "nvmlDeviceGetName":
            self.row["devices"][-1]["b200"] = b"B200" in args[1].value.split()
        elif name == "nvmlDeviceGetConfComputeMemSizeInfo":
            memory = args[1]._obj
            self.row["devices"][-1].update(protected_kib=memory.protectedMemSizeKib,
                                           unprotected_kib=memory.unprotectedMemSizeKib)


def diagnose():
    trace = Trace()
    try:
        validate(NVML(trace).observe(), checkpoint=trace.at)
        trace.row["policy_checks_passed"] = True
    except Exception:
        trace.failed()
    validate_trace(trace.row)
    return trace.row


def validate_trace(row):
    require(type(row) is dict and set(row) == {"policy_checks_passed", "first_failure", "calls", "required_count",
                                              "settings_input_version", "settings_struct_bytes", "observed", "devices"})
    require(type(row["policy_checks_passed"]) is bool
            and row["required_count"] == 4 and type(row["required_count"]) is int
            and type(row["settings_input_version"]) is int and row["settings_input_version"] == 0x01000014
            and type(row["settings_struct_bytes"]) is int and row["settings_struct_bytes"] == 20
            and row["first_failure"] in (None, *TRACE_STAGES)
            and row["policy_checks_passed"] == (row["first_failure"] is None))
    observed = row["observed"]
    require(type(observed) is dict and set(observed) == {"count", "settings_version", "feature", "environment",
                                                       "devtools", "multigpu", "ready", "cuda_driver", "driver_format_valid"})
    for key, value in observed.items():
        if key == "driver_format_valid":
            require(value is None or type(value) is bool)
        else:
            low, high = (-(1 << 31), 1 << 31) if key == "cuda_driver" else (0, 1 << 32)
            require(value is None or type(value) is int and low <= value < high)
    require(type(row["calls"]) is list and len(row["calls"]) <= 20
            and type(row["devices"]) is list and len(row["devices"]) <= 4)
    for call in row["calls"]:
        require(type(call) is dict and set(call) == {"stage", "device", "return_code"}
                and call["stage"] in CALL_STAGES.values()
                and (call["device"] is None or type(call["device"]) is int and 0 <= call["device"] < 4)
                and ((call["device"] is not None) == (call["stage"] in ("handle", "device-name", "memory"))))
        code = call["return_code"]
        require(code is None or type(code) is int and -(1 << 31) <= code < 1 << 31)
    for index, device in enumerate(row["devices"]):
        require(type(device) is dict and set(device) == {"index", "b200", "protected_kib", "unprotected_kib"}
                and type(device["index"]) is int and device["index"] == index
                and (device["b200"] is None or type(device["b200"]) is bool))
        for key in ("protected_kib", "unprotected_kib"):
            require(device[key] is None or type(device[key]) is int and 0 <= device[key] < 1 << 64)
    if row["policy_checks_passed"]:
        require(observed["count"] == 4 and observed["driver_format_valid"] is True
                and all(call["return_code"] == 0 for call in row["calls"]) and len(row["calls"]) == 19
                and row["calls"][-1]["stage"] == "shutdown")
        validate({"feature": observed["feature"], "environment": observed["environment"], "devtools": observed["devtools"],
                  "multigpu": observed["multigpu"], "ready": observed["ready"], "cuda_driver": observed["cuda_driver"],
                  "driver": "000.0", "devices": [{key: d[key] for key in ("b200", "protected_kib")} for d in row["devices"]]})
    require(len(json.dumps(row)) <= 8192)
    return row


def validate_diagnostic(row):
    require(type(row) is dict and set(row) == {"schema", "status", "deployable", "confidential", "functional_pass",
                                              "stage", "probe_execution", "child"}
            and row["schema"] == "nib.deepseek-hardware-diagnostic/v1" and row["status"] == "BLOCKED"
            and row["deployable"] is False and row["confidential"] is False and row["functional_pass"] is False
            and row["stage"] == "diagnostic"
            and row["probe_execution"] in ("completed", "timeout", "failed", "malformed"))
    child = row["child"]
    if row["probe_execution"] != "completed":
        require(child is None)
        return row
    require(type(child) is dict and set(child) == {"stage", "invocation", "bootstrap", "probe"}
            and child["stage"] in ("invocation", "source-binding", "bootstrap", "probe-unavailable", "probe"))
    flags = child["invocation"]
    require(type(flags) is dict and set(flags) == {"linux", "isolated", "no_site", "no_bytecode", "argument_shape"}
            and all(type(value) is bool for value in flags.values()))
    bootstrap = child["bootstrap"]
    require(type(bootstrap) is dict and set(bootstrap) == {"stage", "source_index"}
            and bootstrap["stage"] in ("not-run", "preconditions", "setuptools-source", "vendor-root", "install", "complete"))
    source_index = bootstrap["source_index"]
    require(type(source_index) is int and 0 <= source_index < 4 if bootstrap["stage"] == "setuptools-source" else source_index is None)
    if child["stage"] in ("invocation", "source-binding"):
        require(bootstrap["stage"] == "not-run")
    elif child["stage"] in ("probe-unavailable", "probe"):
        require(bootstrap["stage"] == "complete")
    else:
        require(bootstrap["stage"] not in ("not-run", "complete"))
    if child["stage"] == "invocation":
        require(not all(flags.values()) and child["probe"] is None)
    else:
        require(all(flags.values()))
        if child["stage"] == "probe":
            validate_trace(child["probe"])
        else:
            require(child["probe"] is None)
    return row


def main():
    require(sys.platform == "linux" and sys.flags.isolated and sys.flags.no_site
            and sys.dont_write_bytecode and len(sys.argv) == 1)
    print(json.dumps(validate(NVML().observe()), sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit("Lab hardware observation unavailable.")
