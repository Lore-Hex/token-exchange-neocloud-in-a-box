# SPDX-License-Identifier: Apache-2.0
"""Appended to the reviewed bootstrap by the preparer; no generic Python CLI."""

from contextlib import contextmanager

# read_source/install/dispatch/observation are from the exact concatenated
# engine_python_bootstrap.py, not an import search through installed packages.

MODULES = ("vllm.entrypoints.launchers.api_server.entry", "vllm.entrypoints.openai.api_server",
           "vllm.v1.engine.async_llm", "vllm.v1.executor.multiproc_executor")
ROOT = "/opt/nib"
APPLICATION_SECONDS = 720
IMPORT_SECONDS = 300
ATTACH_SECONDS = 1080
# Public source observations, not a claim that these upstream files were
# independently measured in the installed wheel. See the task's source table.
IMPORT_ENVIRONMENT = {
    "PYTORCH_NVML_BASED_CUDA_CHECK": "1", "TORCHINDUCTOR_COMPILE_THREADS": "1",
    "TRITON_CACHE_AUTOTUNING": "1", "TILELANG_CLEANUP_TEMP_FILES": "1",
    "TORCH_DEVICE_BACKEND_AUTOLOAD": "0",
    "OTEL_PYTHON_CONTEXT": "contextvars_context", "OTEL_PROPAGATORS": "none",
    "OPENBLAS_MAIN_FREE": "1", "GOTOBLAS_MAIN_FREE": "1",
}
ENVIRONMENT_SOURCES = (
    {"repository": "vllm-project/vllm", "revision": "5df63a7203cdb9328d824a27b682e77c7b9928be",
     "path": "vllm/__init__.py", "bytes": 3662, "git_blob": "16101ca245f661a4a03f7bc9c91feefed19b7704",
     "sha256": "087e15875e49ba50f4969c2229d450bb8397302ea9344b3a3bd06ee400263ebd"},
    {"repository": "vllm-project/vllm", "revision": "5df63a7203cdb9328d824a27b682e77c7b9928be",
     "path": "vllm/env_override.py", "bytes": 37970, "git_blob": "bfcdfa7af2369faefe3f7a13a9a07475dcfedaaa",
     "sha256": "8a22650f862565a9ce2ba4441d39a99afbcecc2b60664be2272dd18e2df1cd1d"},
    {"repository": "pytorch/pytorch", "revision": "cf30153c4c131c8164ee7798e5022d810682e2cb",
     "path": "torch/__init__.py", "bytes": 111841, "git_blob": "629122af795c92b734143f0ee0999f28fab8d7d2",
     "sha256": "cf40c075c95864036e835795756d69b8cccfafa76f3bcde5eba9d06065ccd3d1"},
    {"repository": "pytorch/pytorch", "revision": "cf30153c4c131c8164ee7798e5022d810682e2cb",
     "path": "torch/_inductor/async_compile.py", "bytes": 34432, "git_blob": "0962f6b89dbe99e0c64f147f75292cb5acd9dd99",
     "sha256": "401933b518592b8958bb137ddd963c8dd623f4c63b9de215c933424ccc296c7d"},
    {"repository": "numpy/numpy", "revision": "2b686f659642080e2fc708719385de6e8be0955f",
     "path": "numpy/_core/__init__.py", "bytes": 5612, "git_blob": "4b90877138a3ae5e06f35eb668200931073f394b",
     "sha256": "1fde7ecd24f4087ea99e739b8d75175e23e0b6e6fd337e4804669813eab8c2b5"},
)

# Diagnostic references only. Installed bytes must match before compilation;
# these source observations do not assert installed coverage or permit a process.
CALLSITE_ADDITIONS = (
    ("ctypes-util", "python/cpython", "f6650f9ad73359051f3e558c2431a109bc016664", "Lib/ctypes/util.py", "stdlib", "ctypes/util.py", 13959,
     "a54310b64a080b0103f3de5df86b21d884bc2985760f3596ab85d458eae8cba5"),
    ("platform", "python/cpython", "f6650f9ad73359051f3e558c2431a109bc016664", "Lib/platform.py", "stdlib", "platform.py", 43331,
     "df2625b046c8aec56d87f0f46544ef879bdb899dced0b1d94dabbd4830bad60a"),
    ("triton-nvidia", "triton-lang/triton", "f797708c0626e5f9840ca5b0a98790e2c7cb09ad", "third_party/nvidia/backend/driver.py", "packages", "triton/backends/nvidia/driver.py", 13312,
     "992ba21e12ee170a904f3a1caca24a78ce3dd66b661087faea5973b995480f63"),
    ("triton-amd", "triton-lang/triton", "f797708c0626e5f9840ca5b0a98790e2c7cb09ad", "third_party/amd/backend/driver.py", "packages", "triton/backends/amd/driver.py", 18168,
     "532c1ca88181eba940d9e95dc5f1645ead4823c93d6d0c42d84251dd2ec30b7c"),
    ("triton-build", "triton-lang/triton", "f797708c0626e5f9840ca5b0a98790e2c7cb09ad", "python/triton/runtime/build.py", "packages", "triton/runtime/build.py", 3928,
     "26c6ddd4dba3534ce54790f12d2b75729f51e467c48b2f740dd62b7c75a85b39"),
    ("triton-knobs", "triton-lang/triton", "f797708c0626e5f9840ca5b0a98790e2c7cb09ad", "python/triton/knobs.py", "packages", "triton/knobs.py", 18341,
     "b0e6a0a241cbc52067b04c230b2ec149ae109ee4bf48d3b7706a18a8e526fc60"),
    ("torch-extension", "pytorch/pytorch", "cf30153c4c131c8164ee7798e5022d810682e2cb", "torch/utils/cpp_extension.py", "packages", "torch/utils/cpp_extension.py", 146418,
     "1494e212b205f4485267c7a89017929dca30f748ae55aec792557d460a83447a"),
    ("torch-builder", "pytorch/pytorch", "cf30153c4c131c8164ee7798e5022d810682e2cb", "torch/_inductor/cpp_builder.py", "packages", "torch/_inductor/cpp_builder.py", 104277,
     "cb369b351ac1021ecd6127e536ef35c1a6d57de6ae3689e95d097c9ab3ebad02"),
    ("torch-vec", "pytorch/pytorch", "cf30153c4c131c8164ee7798e5022d810682e2cb", "torch/_inductor/cpu_vec_isa.py", "packages", "torch/_inductor/cpu_vec_isa.py", 22427,
     "6cd49584e3cfdab9f0883c5a24d088f992909775f7d400ff0a4cefccbe6998a0"),
)
# Fixed public function IDs. A selector covers its complete compiled code, not
# an observed function/module name. Phase selectors additionally restrict offsets.
CALLSITE_FUNCTIONS = {
    "vllm-init": ("<module>",), "vllm-env": ("<module>", "_patch_inductor_fallback_allow_list"),
    "torch-init": ("<module>",),
    "ctypes-util": ("_findSoname_ldconfig", "_findLib_gcc", "_findLib_ld"),
    "platform": ("_Processor.from_subprocess", "_syscmd_file"),
    "triton-nvidia": ("libcuda_dirs", "library_dirs"),
    "triton-amd": ("_get_path_to_hip_runtime_dylib",),
    "triton-build": ("_build", "compile_module_from_src"),
    "triton-knobs": ("NvidiaTool.from_path",),
    "torch-extension": ("_find_cuda_home", "_find_rocm_home", "_find_sycl_home", "check_compiler_ok_for_platform",
                        "get_compiler_abi_compatibility_and_version", "_check_cuda_version", "is_ninja_available", "_run_ninja_build"),
    "torch-builder": ("cpp_compiler_search", "get_cpp_compiler", "get_compiler_version_info", "_run_compile_cmd"),
    "torch-vec": ("VecISA._probe_load", "VecISA.check_build", "VecISA.__bool__", "valid_vec_isa_list"),
}
CALLSITE_PHASES = {
    "vllm-init": (("vllm-init-env-import", "<module>", 14, "IMPORT_NAME", "vllm.env_override"),),
    "vllm-env": (("vllm-env-torch-import", "<module>", 150, "IMPORT_NAME", "torch"),
                 ("vllm-env-lowering-import", "_patch_inductor_fallback_allow_list", 894, "IMPORT_NAME", "torch._inductor"),
                 ("vllm-env-graph-import", "_patch_inductor_fallback_allow_list", 909, "IMPORT_NAME", "torch._inductor"),
                 ("vllm-env-fallback-call", "<module>", 917, "LOAD_NAME", "_patch_inductor_fallback_allow_list")),
}


def callsite_specs():
    sources = [{**source, "id": identifier, "root": "packages", "installed": source["path"]}
               for identifier, source in zip(("vllm-init", "vllm-env", "torch-init"), ENVIRONMENT_SOURCES[:3])]
    sources += [dict(zip(("id", "repository", "revision", "path", "root", "installed", "bytes", "sha256"), row))
                for row in CALLSITE_ADDITIONS]
    for source in sources:
        identifier = source["id"]
        # Specific phases precede their broader function within each frame.
        source["sites"] = [dict(zip(("id", "qualname", "line", "op", "arg"), row))
                           for row in CALLSITE_PHASES.get(identifier, ())]
        source["sites"] += [{"id": identifier + ":" + ("module" if name == "<module>" else name), "qualname": name}
                            for name in CALLSITE_FUNCTIONS[identifier]]
    return sources


def validate_callsites(require, value):
    specs = callsite_specs()
    require(type(value) is dict and set(value) == {"sources", "ids", "frames_scanned", "unmatched_frames",
            "scan_truncated", "output_truncated", "comparison_failed"})
    require(type(value["sources"]) is list and len(value["sources"]) == len(specs))
    eligible = []
    for row, spec in zip(value["sources"], specs):
        require(type(row) is dict and set(row) == {"id", "status"} and row["id"] == spec["id"]
                and row["status"] in ("verified", "missing", "read-rejected", "source-mismatch", "compile-rejected", "selector-rejected"))
        if row["status"] == "verified":
            eligible.extend(site["id"] for site in spec["sites"])
    require(type(value["ids"]) is list and len(value["ids"]) <= 8
            and all(type(item) is str and item in eligible for item in value["ids"])
            and len(value["ids"]) == len(set(value["ids"])))
    require(type(value["frames_scanned"]) is int and 0 <= value["frames_scanned"] <= 128
            and type(value["unmatched_frames"]) is int and 0 <= value["unmatched_frames"] <= value["frames_scanned"]
            and all(type(value[key]) is bool for key in ("scan_truncated", "output_truncated", "comparison_failed"))
            and (not value["scan_truncated"] or value["frames_scanned"] == 128)
            and (not value["output_truncated"] or len(value["ids"]) == 8)
            and (value["frames_scanned"] > 0 or value["comparison_failed"])
            and (not value["ids"] or value["unmatched_frames"] < value["frames_scanned"]))


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def load_module(name, raw, path):
    import types
    module = types.ModuleType(name)
    module.__file__ = path
    exec(compile(raw, path, "exec"), module.__dict__)
    return module


def wrapper_bytes(executable, environment, contract_sha):
    import shlex
    args = ["/usr/bin/env", "-i", *[key + "=" + environment[key] for key in sorted(environment)],
            executable, "-I", "-S", "-B", ROOT + "/launch.py", contract_sha]
    return ("#!/bin/sh\nexec " + shlex.join(args) + ' "$@"\n').encode()


def context(expected):
    import platform
    import re
    require(re.fullmatch(r"[0-9a-f]{64}", expected) and __file__ == ROOT + "/launch.py")
    raw = read_source(ROOT + "/smoke.json")
    require(hashlib.sha256(raw).hexdigest() == expected)
    contract = json.loads(raw)
    require(contract["schema"] == "nib.engine-import-smoke-input/v1" and contract["status"] == "BLOCKED"
            and contract["deployable"] is False and contract["image"] is None and contract["modules"] == list(MODULES)
            and platform.system() == "Linux" and platform.machine() == "x86_64"
            and platform.python_version() == contract["python"] == "3.12.3"
            and contract["interpreter"]["resolved"] == "/usr/bin/python3.12"
            and sys.pycache_prefix is None and os.getcwd() == "/")
    require(dict(os.environ) == contract["environment"])
    require(read_source(ROOT + "/python") == wrapper_bytes(contract["interpreter"]["resolved"], contract["environment"], expected))
    executable = read_source(contract["interpreter"]["resolved"], 16 << 20)
    require(len(executable) == contract["interpreter"]["bytes"]
            and hashlib.sha256(executable).hexdigest() == contract["interpreter"]["sha256"])
    require(type(contract["sources"]) is dict and len(contract["sources"]) <= 100)
    sources = {}
    for name, digest in contract["sources"].items():
        require(re.fullmatch(r"[a-zA-Z0-9_./-]+", name) and not name.startswith("/")
                and all(part not in ("", ".", "..") for part in name.split("/")))
        data = read_source(ROOT + "/" + name)
        require(hashlib.sha256(data).hexdigest() == digest)
        sources[name] = data
    require(sum(map(len, sources.values())) <= 8 << 20)
    modules = [load_module(name, sources["application/" + name], ROOT + "/application/" + name)
               for name in ("engine_overlay.py", "engine_image_diagnostic.py", "engine_python_descriptor.py", "application.py")]
    overlay, diagnostic, descriptor, app = modules
    require(overlay.digest(sources["application/contract.json"]) == contract["application_contract_sha256"])
    application = overlay.load_json(sources["application/contract.json"])
    app.require_contract(overlay, application)
    require(application["git_sha"] == contract["git_sha"] and application["base_image"] == contract["base_image"]
            and application["official_config"] == contract["official_config"]
            and contract["measurement_sha256"] == app.EVIDENCE_SHA)
    measured = app.measurement(overlay, sources["application/measurement.json"])
    require(contract["paths"] == measured["proposed_paths"] and application["import_contract"] == app.import_contract(overlay, measured))
    require(contract["environment"]["VLLM_WORKER_MULTIPROC_METHOD"] == "spawn"
            and contract["environment"]["VLLM_PLUGINS"] == "" and "NUMACTL_PYTHON" not in contract["environment"])
    require(contract["import_environment_sources"] == list(ENVIRONMENT_SOURCES)
            and all(contract["environment"].get(key) == value for key, value in IMPORT_ENVIRONMENT.items()))
    require(contract["process_callsite_sources"] == callsite_specs())
    require(contract["metadata_source_sha256"] == METADATA_SOURCES
            and contract["permitted_dependency_entrypoint"] == list(CONTEXT_ENTRYPOINT))
    return contract, sources, modules, application, measured


def final_sources(overlay, application, measured):
    locations = {item["name"]: item["path"] for item in measured["runtime"]["locations"]}
    with overlay.directory(locations["purelib"]) as fd:
        tree = overlay.Tree(fd)
        tree.check_module_collisions(item["path"] for item in application["files"])
        for item in application["files"]:
            require(hashlib.sha256(tree.read(item["path"])).hexdigest() == item["sha256"][5])


def current():
    return observation(sys._nib_smoke_guard)


class BeforeUnpickle:
    def __reduce__(self):
        return current, ()


def imports():
    import importlib
    result = []
    for name in MODULES:
        try:
            importlib.import_module(name)
            current()
            require(not any(sys._nib_smoke_guard["failures"].values()))
            status, category = "pass", None
        except (Exception, SystemExit) as error:
            status = "rejected"
            category = ("guard" if any(sys._nib_smoke_guard["failures"].values()) else
                        "missing-module" if isinstance(error, ModuleNotFoundError) else
                        "import" if isinstance(error, ImportError) else
                        "OS" if isinstance(error, OSError) else
                        "runtime" if isinstance(error, RuntimeError) else "other")
        result.append({"module": name, "status": status, "error_category": category})
        if status != "pass":
            result.extend({"module": item, "status": "not-run", "error_category": None} for item in MODULES[len(result):])
            break
    return result


def child_target(writer, prepared, depth):
    result = {"before_unpickle": prepared, "state": current()}
    # The grandchild is launched before imports, just as the parent is. This
    # observes the restricted Python mechanism, not vLLM worker construction.
    if depth:
        result["grandchild"] = child(0)
    result["imports"] = imports()
    result["guard_rejections"] = dict(sys._nib_smoke_guard["failures"])
    result["first_state_mismatch"] = sys._nib_smoke_guard["first_state_mismatch"]
    result["first_denied_process"] = sys._nib_smoke_guard["first_denied_process"]
    result["dependency_entrypoint_loads"] = sys._nib_smoke_guard["entrypoint_loads"]
    writer.send_bytes(json_bytes(result))
    writer.close()


def child(depth):
    import multiprocessing as mp
    reader, writer = mp.get_context("spawn").Pipe(duplex=False)
    process = mp.get_context("spawn").Process(target=child_target, args=(writer, BeforeUnpickle(), depth))
    try:
        process.start()
        writer.close()
        require(reader.poll(120))
        data = reader.recv_bytes(32768)
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


@contextmanager
def timed_phase(result, name):
    import time
    result["stage"] = name
    started = time.monotonic()
    try:
        yield
    finally:
        result["phase_elapsed_ms"][name] = int((time.monotonic() - started) * 1000)


def apply_and_smoke(contract, sources, modules, application, measured):
    import time
    overlay, diagnostic, descriptor, app = modules
    result = {"schema": "nib.engine-import-smoke/v1", "status": "BLOCKED", "deployable": False,
              "image": None, "abi_validated": False, "hardware_validated": False, "inference_started": False,
              "stage": "application-preflight", "application": None, "smoke": None,
              "phase_elapsed_ms": dict.fromkeys(("application-preflight", "application", "import-smoke"))}
    try:
        deadline = time.monotonic() + APPLICATION_SECONDS
        with timed_phase(result, "application-preflight"):
            app.verify_layout(overlay, descriptor, diagnostic, application, measured)
            stages = [{item["path"]: sources[f"application/stages/{n}/" + item["path"]] for item in application["files"]}
                      for n in range(1, 6)]
            overlay.require(time.monotonic() < deadline)
        with timed_phase(result, "application"):
            with app.selected_tree(overlay, application, measured) as (tree, console):
                application_budget(overlay, app, application, tree, console, deadline)
                before = app.inspect(overlay, tree, application, measured, console)
                applied = app.apply(overlay, tree, application, stages, before, measured, console)
            overlay.require(time.monotonic() < deadline)
            result["application"] = applied
        with timed_phase(result, "import-smoke"):
            processes = load_module("smoke_processes", sources["processes.py"], ROOT + "/processes.py")
            completed = processes.execute_process_group([ROOT + "/python", "smoke"], env=contract["environment"],
                                                         timeout=IMPORT_SECONDS, max_output_bytes=65536)
            completed.check_returncode()
            require(not completed.stderr)
            result["smoke"] = overlay.load_json(completed.stdout)
        result["stage"] = "complete"
    except (Exception, SystemExit):
        result["failure"] = "bounded-smoke-stage-rejected"
    return result


def application_budget(overlay, app, application, tree, console, deadline):
    import time
    app.require_contract(overlay, application)
    # Eight whole-inventory passes share this single deadline. Generic Tree's
    # 300-second default and every byte/entry/native/link rule are untouched.
    overlay.require(0 < deadline - time.monotonic() <= APPLICATION_SECONDS)
    tree.deadline = console[1].deadline = deadline


def entry():
    # Capture and then silence both streams before any engine/dependency import.
    # Only schema-validated fixed fields may be returned through the saved fd.
    args = sys.argv[1:]
    saved = os.dup(1)
    null = os.open(os.devnull, os.O_WRONLY)
    os.dup2(null, 1)
    os.dup2(null, 2)
    os.close(null)
    try:
        if __name__ != "__main__":
            # runpy reentry during spawn.prepare already has a verified guard.
            require(hasattr(sys, "_nib_smoke_guard"))
            return
        require(len(args) >= 2)
        contract, sources, modules, application, measured = context(args[0])
        if args[1:] == ["apply"]:
            result = apply_and_smoke(contract, sources, modules, application, measured)
        else:
            final_sources(modules[0], application, measured)
            layout = {"paths": contract["paths"], "stdlib_roots": contract["paths"][:2],
                      "wrapper": ROOT + "/python", "launcher": ROOT + "/launch.py", "environment": contract["environment"]}
            catalog = compiled_catalog(callsite_specs(), {"stdlib": contract["paths"][0], "packages": contract["paths"][-1]})
            state = install(layout, catalog)
            if args[1:] == ["fresh"]:
                result = {"state": current(), "imports": imports(), "guard_rejections": dict(state["failures"]),
                          "first_state_mismatch": state["first_state_mismatch"],
                          "first_denied_process": state["first_denied_process"],
                          "dependency_entrypoint_loads": state["entrypoint_loads"]}
            elif args[1:] == ["smoke"]:
                import multiprocessing as mp
                import subprocess
                from multiprocessing import resource_tracker
                lock = mp.Lock()
                try:
                    result = {"spawn": child(1)}
                    fresh = subprocess.run([ROOT + "/python", "fresh"], capture_output=True, timeout=120, check=True)
                    require(not fresh.stderr)
                    result["fresh"] = json.loads(fresh.stdout)
                    result["parent"] = current()
                finally:
                    del lock
                    tracker = stop_tracker(state)
                result["tracker"] = tracker
            else:
                try:
                    dispatch(args[1:], state)
                except SystemExit as exit:
                    require(exit.code in (None, 0))
                return
        data = json_bytes(result)
        require(len(data) <= 65536)
        with os.fdopen(saved, "wb", closefd=False) as stream:
            stream.write(data)
    except (Exception, SystemExit):
        # No exception strings or raw import output leave this payload.
        if __name__ == "__main__":
            os.write(saved, b'{"failure":"sealed-smoke-entry-rejected"}\n')
            raise SystemExit(1) from None
        raise
    finally:
        os.close(saved)


if __name__ in ("__main__", "__mp_main__"):
    entry()


def validate(overlay, report, application, measured):
    """Host-side exact field validation; never publish arbitrary child JSON."""
    fixed = {"schema": "nib.engine-import-smoke/v1", "status": "BLOCKED", "deployable": False,
             "image": None, "abi_validated": False, "hardware_validated": False, "inference_started": False}
    def same(actual, expected):
        return overlay.json_bytes(actual) == overlay.json_bytes(expected)
    overlay.require(type(report) is dict and all(same(report.get(key), value) for key, value in fixed.items())
                    and set(report) <= set(fixed) | {"stage", "application", "smoke", "failure", "phase_elapsed_ms"}
                    and {"stage", "application", "smoke", "phase_elapsed_ms"} <= set(report)
                    and report["stage"] in {"application-preflight", "application", "import-smoke", "complete"})
    phases = ("application-preflight", "application", "import-smoke")
    timings = report["phase_elapsed_ms"]
    reached = 3 if report["stage"] == "complete" else phases.index(report["stage"]) + 1
    overlay.require(type(timings) is dict and set(timings) == set(phases))
    for i, name in enumerate(phases):
        value = timings[name]
        overlay.require((type(value) is int and 0 <= value <= ATTACH_SECONDS * 1000) if i < reached else value is None)
    overlay.require(sum(value for value in timings.values() if value is not None) <= ATTACH_SECONDS * 1000)
    if reached == 3:
        overlay.require(timings["application-preflight"] + timings["application"] <= APPLICATION_SECONDS * 1000)
    if "failure" in report:
        overlay.require(report["failure"] == "bounded-smoke-stage-rejected" and report["stage"] != "complete")
    else:
        overlay.require(report["stage"] == "complete")
    applied = report["application"]
    if applied is not None:
        expected_keys = {"schema", "status", "deployable", "hardware_validated", "abi_validated", "image_origin_verified",
            "scope", "stages", "before_inventory_sha256", "after_inventory_sha256", "removed_bytecode_files", "git_sha",
            "source_bundle_sha256", "base_image", "native_inventory_unchanged", "wheel_record", "dependency_scope", "packaging",
            "image", "application_measurement_sha256", "import_contract_sha256", "generic_overlay_admission", "engine_executed",
            "startup_hooks", "alternate_roots", "classified_nonhost_assets"}
        overlay.require(type(applied) is dict and set(applied) == expected_keys)
        # Fixed-value receipt fields are generated by the already-reviewed writer.
        values = {"schema": "nib.engine-overlay/v1", "status": "BLOCKED", "deployable": False,
            "hardware_validated": False, "abi_validated": False, "image_origin_verified": False,
            "scope": "exact-image-experimental-application-only", "stages": 5, "git_sha": application["git_sha"],
            "source_bundle_sha256": application["source_bundle_sha256"], "base_image": application["base_image"],
            "native_inventory_unchanged": True, "wheel_record": "original-preserved-overlay-receipt-required",
            "dependency_scope": "selected-exact-upstream-pins-only", "image": None,
            "application_measurement_sha256": application["application_measurement_sha256"],
            "import_contract_sha256": overlay.digest(overlay.json_bytes(application["import_contract"])),
            "generic_overlay_admission": "not-granted", "engine_executed": False,
            "startup_hooks": "unchanged-inert-only-under-isolated-no-site-bootstrap",
            "alternate_roots": "unchanged-excluded-from-import-contract",
            "classified_nonhost_assets": "four-exact-published-crosscompile-assets-not-host-abi"}
        overlay.require(all(same(applied.get(key), value) for key, value in values.items()))
        import re
        overlay.require(all(type(applied[key]) is str and re.fullmatch(r"[0-9a-f]{64}", applied[key])
                            for key in ("before_inventory_sha256", "after_inventory_sha256")))
        overlay.require(type(applied["removed_bytecode_files"]) is int
                        and applied["removed_bytecode_files"] == measured["packaging"]["removal_count"])
        overlay.require(same(applied["packaging"], {
            "policy_sha256": overlay.digest(overlay.json_bytes(overlay.PACKAGING_POLICY)),
            "removal_plan_sha256": measured["packaging"]["removal_plan_sha256"],
            "removed_unlisted_bytecode_files": measured["packaging"]["removal_count"],
            "removal_verified_before_patch": True, "bytecode_executed": False, "plugins_loaded": False,
            "entrypoint_scope": "exact-upstream-map-only", "listed_bytecode_policy": "unchanged"}))
    overlay.require((applied is not None) == (report["stage"] in {"import-smoke", "complete"}))
    smoke = report["smoke"]
    overlay.require((smoke is not None) == (report["stage"] == "complete"))
    if smoke is None:
        return False
    failures = {key: 0 for key in ("state", "method", "executable", "preparation", "process", "import", "site")}
    expected_state = {"isolated": True, "no_site": True, "dont_write_bytecode": True, "before_unpickle": True,
                      "path_sha256": overlay.digest(overlay.json_bytes(measured["proposed_paths"])), "rejections": failures}
    overlay.require(type(smoke) is dict and set(smoke) == {"spawn", "fresh", "parent", "tracker"}
                    and same(smoke["parent"], expected_state) and same(smoke["tracker"], {"launched": True, "exit_zero": True}))
    rows = []
    for key in ("spawn", "grandchild", "fresh"):
        row = smoke["spawn"]["grandchild"] if key == "grandchild" else smoke[key]
        keys = {"state", "imports", "guard_rejections", "first_state_mismatch", "first_denied_process", "dependency_entrypoint_loads"} | ({"before_unpickle"} if key != "fresh" else set()) | ({"grandchild"} if key == "spawn" else set())
        overlay.require(type(row) is dict and set(row) == keys and same(row["state"], expected_state)
                        and (key == "fresh" or same(row["before_unpickle"], expected_state)))
        overlay.require(type(row["guard_rejections"]) is dict and set(row["guard_rejections"]) == set(failures)
                        and all(type(value) is int and 0 <= value <= 1000000 for value in row["guard_rejections"].values()))
        overlay.require(row["first_state_mismatch"] in {None, "env", "paths", "finders", "argv", "cwd", "executable", "method", "pycache"}
                        and (row["first_state_mismatch"] is None) == (row["guard_rejections"]["state"] == 0))
        denied = row["first_denied_process"]
        overlay.require((denied is None) == (row["guard_rejections"]["process"] == 0))
        if denied is not None:
            overlay.require(type(denied) is dict and set(denied) == {"event", "command", "caller", "callsites"}
                            and denied["event"] in ("subprocess.Popen", "multiprocessing.spawnv_passfds", "os.exec",
                                "os.posix_spawn", "os.system", "os.fork", "os.forkpty")
                            and denied["command"] in ("uname-processor", "gxx-version", "ptxas-version", "other")
                            and denied["caller"] in ("stdlib-platform", "torch-cpp-builder", "triton-nvidia-compiler", "triton-knobs", "other")
                            and (denied["command"] == "other" or denied["event"] in ("subprocess.Popen", "os.exec", "os.posix_spawn")))
            validate_callsites(overlay.require, denied["callsites"])
        overlay.require(type(row["dependency_entrypoint_loads"]) is int and 0 <= row["dependency_entrypoint_loads"] <= 1000000)
        overlay.require(type(row["imports"]) is list and len(row["imports"]) == len(MODULES))
        seen_failure = False
        for name, item in zip(MODULES, row["imports"]):
            overlay.require(type(item) is dict and set(item) == {"module", "status", "error_category"} and item["module"] == name
                            and item["status"] in ({"not-run"} if seen_failure else {"pass", "rejected"}))
            overlay.require(item["error_category"] in {"guard", "missing-module", "import", "OS", "runtime", "other"}
                            if item["status"] == "rejected" else item["error_category"] is None)
            seen_failure |= item["status"] == "rejected"
        rows.append(row)
    return all(not any(row["guard_rejections"].values()) and all(item["status"] == "pass" for item in row["imports"]) for row in rows)
