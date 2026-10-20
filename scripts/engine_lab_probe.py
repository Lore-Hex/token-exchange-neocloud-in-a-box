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


class Settings(c.Structure):
    _fields_ = [(name, c.c_uint) for name in
                ("version", "environment", "ccFeature", "devToolsMode", "multiGpuMode")]


class ProtectedMemory(c.Structure):
    _fields_ = [("protectedMemSizeKib", c.c_ulonglong), ("unprotectedMemSizeKib", c.c_ulonglong)]


def require(value):
    if not value:
        raise ValueError("lab hardware observation rejected")


class NVML:
    def __init__(self):
        self.library = c.CDLL("libnvidia-ml.so.1")

    def call(self, name, *args):
        require(name in {"nvmlInit_v2", "nvmlShutdown", "nvmlDeviceGetCount_v2",
                         "nvmlDeviceGetHandleByIndex_v2", "nvmlDeviceGetName",
                         "nvmlSystemGetConfComputeSettings", "nvmlSystemGetConfComputeGpusReadyState",
                         "nvmlDeviceGetConfComputeMemSizeInfo", "nvmlSystemGetDriverVersion",
                         "nvmlSystemGetCudaDriverVersion_v2"})
        function = getattr(self.library, name)
        function.restype = c.c_int
        require(function(*args) == 0)

    def observe(self):
        self.call("nvmlInit_v2")
        try:
            count = c.c_uint()
            self.call("nvmlDeviceGetCount_v2", c.byref(count))
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
            return {"feature": settings.ccFeature, "environment": settings.environment,
                    "devtools": settings.devToolsMode, "multigpu": settings.multiGpuMode,
                    "ready": ready.value, "devices": devices, "driver": driver.value.decode("ascii"),
                    "cuda_driver": cuda.value}
        finally:
            self.call("nvmlShutdown")


def validate(raw):
    require(type(raw) is dict and set(raw) == {"feature", "environment", "devtools", "multigpu", "ready", "devices",
                                             "driver", "cuda_driver"})
    require(type(raw["driver"]) is str and re.fullmatch(r"[0-9]{3}\.[0-9]{1,3}(?:\.[0-9]{1,3})?", raw["driver"])
            and type(raw["cuda_driver"]) is int and 13000 <= raw["cuda_driver"] <= 99999)
    require(all(type(raw[key]) is int for key in ("feature", "environment", "devtools", "multigpu", "ready")))
    # Deliberately narrow initial CC experiment: no legacy-disabled/NVLE-only
    # interpretation, devtools, plain fallback, or unknown/mixed result.
    require(raw["feature"] == FEATURE_ENABLED and raw["environment"] == ENVIRONMENT_PROD
            and raw["devtools"] == DEVTOOLS_OFF and raw["ready"] == ACCEPTING_WORK
            and raw["multigpu"] in MULTIGPU and type(raw["devices"]) is list and len(raw["devices"]) == 4)
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


def main():
    require(sys.platform == "linux" and sys.flags.isolated and sys.flags.no_site
            and sys.dont_write_bytecode and len(sys.argv) == 1)
    print(json.dumps(validate(NVML().observe()), sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit("Lab hardware observation unavailable.")
