# SPDX-License-Identifier: Apache-2.0
"""Offline patch/branch/ordering contracts, not GPU memory-coherence tests."""

import __future__
import ast
import builtins
from contextlib import contextmanager
import copy
from functools import cache, wraps
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import traceback
import types
import unittest
from unittest.mock import patch

if "CHECKER" not in globals():
    path = Path(__file__).with_name("check-engine-candidate.py")
    CHECKER = types.ModuleType("engine_candidate_checker")
    CHECKER.__file__ = str(path)
    sys.modules[CHECKER.__name__] = CHECKER
    exec(compile(path.read_bytes(), str(path), "exec"), CHECKER.__dict__)
if "BUNDLE" not in globals():
    BUNDLE = CHECKER.working_bundle()


def definition(source, *names):
    node = ast.parse(source)
    for name in names:
        node = next(n for n in node.body if getattr(n, "name", None) == name)
    return copy.deepcopy(node)


def functions(source, names, environment, imports=None):
    """Execute only explicitly selected reviewed AST nodes, with blocked imports."""
    def restricted_import(name, *args, **kwargs):
        if imports is not None and name in imports:
            return imports[name]
        raise AssertionError("unexpected upstream import")
    namespace = {"__builtins__": {**vars(builtins), "__import__": restricted_import},
                 "cache": cache, "wraps": wraps, **environment}
    nodes = []
    for namespec in names:
        nodes.append(definition(source, *namespec.split(".")))
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])),
                 "reviewed-helper-only", "exec", flags=__future__.annotations.compiler_flag), namespace)
    return namespace


def constructor(source, name, environment, imports):
    """Keep the real constructor and super() cell, with no other class methods."""
    node = definition(source, name)
    node.body = [n for n in node.body if getattr(n, "name", None) == "__init__"]
    isolated = ast.unparse(ast.Module(body=[node], type_ignores=[]))
    return functions(isolated, [name], environment, imports)[name]


class CopyTrace:
    def __init__(self):
        self.events = []
        self.active = "compute"
        self.prep = object()
        self.compute = types.SimpleNamespace(wait_stream=self.wait)
        self.torch = types.SimpleNamespace(
            device=lambda value: value,
            cuda=types.SimpleNamespace(current_stream=lambda device: self.compute, stream=self.stream))

    def wait(self, stream):
        assert stream is self.prep and self.active == "compute"
        self.events.append("wait")

    @contextmanager
    def stream(self, stream):
        assert stream is self.prep
        self.active = "prep"
        try:
            yield
        finally:
            self.active = "compute"

    def tensor(self, name):
        return TraceTensor(self, name)


class TraceTensor:
    def __init__(self, trace, name):
        self.trace, self.name = trace, name
        self.device = "cuda:0"

    def __getitem__(self, index):
        self.trace.events.append(("slice", self.name, index.stop))
        return TraceTensor(self.trace, self.name + "-slice")

    def to(self, **kwargs):
        assert kwargs == {"device": "cuda:0", "non_blocking": True}
        assert self.trace.active == "prep"
        self.trace.events.append("upload")
        return self.trace.tensor("staged")

    def copy_(self, src, *, non_blocking):
        assert non_blocking is True
        self.trace.events.append("copy-" + self.trace.active)
        return self

    def record_stream(self, stream):
        assert stream is self.trace.compute and self.trace.active == "compute"
        self.trace.events.append("record")

    def is_pinned(self):
        raise AssertionError("pinning must not decide ordering")


class CandidateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = CHECKER.verify(BUNDLE)
        cls.manifest, cls.local = CHECKER.metadata(BUNDLE)

    def test_all_patch_stages_have_exact_hashes_and_blocked_status(self):
        self.assertEqual(set(self.files), set(CHECKER.PATHS))
        self.assertEqual(self.manifest["status"], "BLOCKED")
        self.assertIs(self.manifest["deployable"], False)
        self.assertIsNone(self.manifest["image"])
        self.assertEqual(self.local[CHECKER.PATCHES[1]], self.local["upstream/" + CHECKER.COMMITS[1] + ".patch"])
        self.assertEqual(len(CHECKER.PATCHES), 5)
        for item in self.manifest["files"]:
            self.assertEqual(len(item["sha256"]), 6)
            self.assertEqual(item["sha256"][3] != item["sha256"][4],
                             item["path"] == "vllm/utils/torch_utils.py")
            self.assertEqual(item["sha256"][4] != item["sha256"][5], item["path"] in (
                "vllm/v1/conf_compute_utils.py", "vllm/distributed/device_communicators/cuda_communicator.py",
                "vllm/compilation/passes/fusion/allreduce_rms_fusion.py"))

    def test_private_patch_workspace_preserves_caller_umask_and_all_stage_bytes(self):
        # Separate processes avoid changing the test runner's process-wide mask.
        source = r'''
import json, os, stat, sys, types
from pathlib import Path
root = Path(sys.argv[1])
checker = types.ModuleType("private_patch_check")
checker.__file__ = str(root / "scripts/check-engine-candidate.py")
exec(compile(Path(checker.__file__).read_bytes(), checker.__file__, "exec"), checker.__dict__)
original_run = checker.subprocess.run
calls = []
outcome = sys.argv[3]
def checked_run(args, **kwargs):
    assert "preexec_fn" not in kwargs
    if "apply" in args:
        directory = Path(args[args.index("-C") + 1])
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
        calls.append(kwargs["umask"])
        if "--check" not in args:
            if outcome == "failure":
                return checker.subprocess.CompletedProcess(args, 1, b"", b"")
            if outcome == "cancelled":
                raise KeyboardInterrupt
    return original_run(args, **kwargs)
checker.subprocess.run = checked_run
mask = int(sys.argv[2], 8)
os.umask(mask)
try:
    stages = checker.verify_stages(checker.working_bundle())
    assert outcome == "success" and calls == [-1, 0o022] * 5
    result = [{name: checker.sha256(raw) for name, raw in stage.items()} for stage in stages]
except (ValueError, KeyboardInterrupt) as error:
    assert type(error) is (ValueError if outcome == "failure" else KeyboardInterrupt)
    assert outcome in ("failure", "cancelled") and calls == [-1, 0o022]
    result = outcome
finally:
    assert os.umask(mask) == mask
print(json.dumps(result, sort_keys=True))
'''
        expected = [{name: CHECKER.sha256(raw) for name, raw in stage.items()}
                    for stage in CHECKER.verify_stages(BUNDLE)]
        for mask, outcome in ((*((mask, "success") for mask in ("000", "022", "027", "077")),
                               ("077", "failure"), ("077", "cancelled"))):
            with self.subTest(mask=mask, outcome=outcome):
                result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", source,
                                         str(CHECKER.ROOT), mask, outcome], env=CHECKER.tool_env(),
                                        capture_output=True, timeout=60, check=True)
                self.assertEqual(json.loads(result.stdout), expected if outcome == "success" else outcome)
                self.assertEqual(result.stderr, b"")

    def test_patch_mode_mismatch_still_fails_closed(self):
        original_git = CHECKER.git
        def altered_mode(root, *args, **kwargs):
            result = original_git(root, *args, **kwargs)
            if args[0] == "apply" and "--check" not in args:
                (Path(root) / CHECKER.PATHS[0]).chmod(0o600)
            return result
        with patch.object(CHECKER, "git", side_effect=altered_mode):
            with self.assertRaisesRegex(ValueError, "patched file differs"):
                CHECKER.verify_stages(BUNDLE)

    def test_tampered_sources_patches_and_policy_are_rejected(self):
        for name in ("base/vllm/envs.py", *CHECKER.PATCHES, "LICENSE", "audit/" + CHECKER.AUDITS[0]):
            with self.subTest(name=name), self.assertRaises(ValueError):
                changed = dict(BUNDLE)
                changed[CHECKER.DIRECTORY + "/" + name] += b"\nchanged\n"
                CHECKER.verify(changed)
        for field, value in (("status", "READY"), ("deployable", True), ("image", "invented"),
                             ("base_revision", "0" * 40)):
            changed = dict(BUNDLE)
            manifest = copy.deepcopy(self.manifest)
            manifest[field] = value
            changed[CHECKER.DIRECTORY + "/candidate.json"] = json.dumps(manifest).encode()
            with self.subTest(field=field), self.assertRaises(ValueError):
                CHECKER.verify(changed)

    def test_resigned_bad_after_hash_and_unexpected_file_are_rejected(self):
        for stage in range(1, len(CHECKER.PATCHES) + 1):
            changed = dict(BUNDLE)
            manifest = copy.deepcopy(self.manifest)
            manifest["files"][0]["sha256"][stage] = "0" * 64
            changed[CHECKER.DIRECTORY + "/candidate.json"] = json.dumps(manifest).encode()
            with self.subTest(stage=stage), self.assertRaises(ValueError):
                CHECKER.verify(changed)
        changed = dict(BUNDLE)
        changed[CHECKER.DIRECTORY + "/ignored.py"] = b"raise Exception()"
        with self.assertRaises(ValueError):
            CHECKER.verify(changed)

    def test_new_source_git_blob_ids_are_enforced_even_after_sha256_resigning(self):
        for name in CHECKER.GIT_BLOBS:
            for alter_bytes in (False, True):
                changed = dict(BUNDLE)
                manifest = copy.deepcopy(self.manifest)
                audit = name in CHECKER.AUDITS
                item = next(item for item in manifest["source_audits" if audit else "files"] if item["path"] == name)
                if alter_bytes:
                    path = CHECKER.DIRECTORY + ("/audit/" if audit else "/base/") + name
                    changed[path] += b"\n# tampered\n"
                    item["base_bytes"] = len(changed[path])
                    if audit:
                        item["sha256"] = CHECKER.sha256(changed[path])
                    else:
                        item["sha256"][0] = CHECKER.sha256(changed[path])
                else:
                    item["git_blob"] = "0" * 40
                changed[CHECKER.DIRECTORY + "/candidate.json"] = json.dumps(manifest).encode()
                with self.subTest(name=name, alter_bytes=alter_bytes), self.assertRaisesRegex(ValueError, "pinned Git blob differs"):
                    CHECKER.metadata(changed)

    def test_release_torch_semantics_preserved_outside_added_branch(self):
        path = "vllm/utils/torch_utils.py"
        before = ast.parse(self.local["base/" + path])
        after = ast.parse(self.files[path])
        after.body = [n for n in after.body if getattr(n, "name", None) != "_confidential_compute_enabled"
                      and not (isinstance(n, ast.ImportFrom) and n.module == "functools")]
        function = next(n for n in after.body if getattr(n, "name", None) == "async_tensor_h2d")
        function.body = [n for n in function.body if not (isinstance(n, ast.If)
                         and ast.unparse(n.test) == "_confidential_compute_enabled()")]
        mapping = next(n for n in after.body if getattr(n, "name", None) == "get_accelerator_view_from_cpu_tensor")
        cuda = next(n for n in ast.walk(mapping) if isinstance(n, ast.If)
                    and ast.unparse(n.test) == "current_platform.is_cuda_alike()")
        guard = cuda.body.pop(0)
        self.assertIsInstance(guard, ast.If)
        self.assertEqual(ast.unparse(guard.test), "current_platform.is_confidential_compute()")
        # Whole-module AST equality includes lazy PIN_MEMORY, DP seeds, ultraquant,
        # direct-view behavior outside the new guard and every other release function.
        self.assertEqual(ast.dump(before), ast.dump(after))
        calls = []
        namespace = functions(self.files[path], ["__getattr__"],
                              {"is_pin_memory_available": lambda: calls.append("resolve") or True})
        self.assertEqual(calls, [])
        self.assertTrue(namespace["__getattr__"]("PIN_MEMORY"))
        self.assertEqual(calls, ["resolve"])

    def test_release_zmq_ownership_and_all_other_utils_preserved(self):
        path = "vllm/v1/utils.py"
        before = ast.parse(self.local["base/" + path])
        after = ast.parse(self.files[path])
        after.body = [n for n in after.body if not (isinstance(n, ast.ImportFrom)
                      and n.module == "vllm.v1.conf_compute_utils")]
        cls = next(n for n in after.body if getattr(n, "name", None) == "CpuGpuBuffer")
        method = next(n for n in cls.body if getattr(n, "name", None) == "copy_to_gpu")
        method.body = [n for n in method.body if not (isinstance(n, ast.If)
                       and ast.unparse(n.test) == "gpu.is_cuda and confidential_compute_enabled()")]
        self.assertEqual(ast.dump(before), ast.dump(after))

    def test_collective_release_semantics_preserved_outside_entry_guards(self):
        for path, name in (("vllm/distributed/device_communicators/cuda_communicator.py", "CudaCommunicator"),
                           ("vllm/compilation/passes/fusion/allreduce_rms_fusion.py", "AllReduceFusionPass")):
            before = ast.parse(self.local["base/" + path])
            after = ast.parse(self.files[path])
            cls = next(n for n in after.body if getattr(n, "name", None) == name)
            init = next(n for n in cls.body if getattr(n, "name", None) == "__init__")
            self.assertIsInstance(init.body[0], ast.ImportFrom)
            self.assertEqual(init.body[0].module, "vllm.v1.conf_compute_utils")
            self.assertEqual(ast.unparse(init.body[1].test), "confidential_collectives_selected()")
            init.body = init.body[2:]
            self.assertEqual(ast.dump(before), ast.dump(after))

    def collective_selector(self, platform):
        return functions(self.files["vllm/v1/conf_compute_utils.py"], ["confidential_collectives_selected"], {},
            {"vllm.platforms": types.SimpleNamespace(current_platform=platform)})["confidential_collectives_selected"]

    def communicator_fixture(self, detector=lambda: True):
        calls = []
        class Base:
            def __init__(self, cpu_group, device, device_group, unique_name, global_ranks,
                         global_world_size, use_all2all):
                calls.append("base")
                self.cpu_group, self.device, self.device_group = cpu_group, device, device_group
                self.world_size, self.use_all2all = global_world_size or 4, use_all2all
        def backend(name):
            def create(**kwargs):
                calls.append(name)
                return types.SimpleNamespace(disabled=False)
            return create
        flags = ("VLLM_ALLREDUCE_USE_SYMM_MEM", "VLLM_ALLREDUCE_USE_FLASHINFER",
                 "VLLM_ALLREDUCE_USE_FLASHINFER_PCIE_IPC", "VLLM_USE_NCCL_SYMM_MEM")
        env = types.SimpleNamespace(**dict.fromkeys(flags, False), VLLM_BATCH_INVARIANT=False)
        config = types.SimpleNamespace(parallel_config=types.SimpleNamespace(disable_custom_all_reduce=True),
                                       use_cumem_cudagraph_pool=False)
        state = types.SimpleNamespace(_ENABLE_CUSTOM_ALL_REDUCE=False)
        platform = types.SimpleNamespace(is_confidential_compute=detector, is_cuda=lambda: True, is_rocm=lambda: False)
        imports = {"vllm.distributed.parallel_state": state,
                   "vllm.v1.conf_compute_utils": types.SimpleNamespace(
                       confidential_collectives_selected=self.collective_selector(platform))}
        for module, cls in (("custom_all_reduce", "CustomAllreduce"), ("flashinfer_all_reduce", "FlashInferAllReduce"),
                            ("flashinfer_pcie_ipc_all_reduce", "FlashInferPcieIpcAllReduce"),
                            ("pynccl", "PyNcclCommunicator"), ("quick_all_reduce", "QuickAllReduce"),
                            ("symm_mem", "SymmMemCommunicator")):
            imports["vllm.distributed.device_communicators." + module] = types.SimpleNamespace(**{cls: backend(cls)})
        cls = constructor(self.files["vllm/distributed/device_communicators/cuda_communicator.py"],
            "CudaCommunicator", {"DeviceCommunicatorBase": Base, "current_platform": platform, "envs": env,
                "get_current_vllm_config_or_none": lambda: config,
                "rocm_aiter_ops": types.SimpleNamespace(is_custom_all_reduce_enabled=lambda: False),
                "is_symmetric_memory_enabled": lambda: env.VLLM_USE_NCCL_SYMM_MEM,
                "register_nccl_symmetric_ops": lambda comm: calls.append("register_nccl_symmetric_ops"),
                "AiterCustomAllreduce": backend("AiterCustomAllreduce")}, imports)
        cls._log_all_reduce_backend_selection = lambda self: calls.append("log")
        return types.SimpleNamespace(cls=cls, calls=calls, env=env, config=config, state=state, flags=flags)

    def instantiate_communicator(self, fixture, name="tp:0", world_size=4):
        return fixture.cls("cpu", device="cuda:0", device_group="device", unique_name=name,
                           global_world_size=world_size)

    def test_cc_all_off_constructs_only_ordinary_communicator_and_disables_fused_mhc(self):
        fixture = self.communicator_fixture()
        comm = self.instantiate_communicator(fixture)
        self.assertEqual(fixture.calls, ["base", "PyNcclCommunicator", "log"])
        for field in ("ca_comm", "symm_mem_comm", "fi_ar_comm", "fi_pcie_ipc_ar_comm", "qr_comm", "aiter_ar_comm"):
            self.assertIsNone(getattr(comm, field))
        config = types.SimpleNamespace(parallel_config=types.SimpleNamespace(tensor_parallel_size=4,
            enable_expert_parallel=False), model_config=types.SimpleNamespace(hf_config=types.SimpleNamespace(
                hidden_size=5120, hc_mult=4)))
        predicate = functions(self.local["audit/" + CHECKER.AUDITS[0]], ["supports_mhc_all_reduce"],
            {"cast": lambda name, value: value,
             "get_tp_group": lambda: types.SimpleNamespace(device_communicator=comm)})["supports_mhc_all_reduce"]
        self.assertFalse(predicate(config))
        comm.ca_comm = types.SimpleNamespace(mnnvl_lamport_ag_multicast_ptr=object())
        self.assertTrue(predicate(config))  # Real predicate positive control, no kernel execution.

    def test_cc_each_conflicting_control_rejects_before_all_construction(self):
        for control in ("disable_custom_all_reduce", "_ENABLE_CUSTOM_ALL_REDUCE", *self.communicator_fixture().flags):
            for name, world in (("tp:0", 4), ("dp:0", 4), ("tp:0", 1)):
                fixture = self.communicator_fixture()
                fixture.env.VLLM_BATCH_INVARIANT = True  # Masked flags must still reject.
                if control == "disable_custom_all_reduce":
                    fixture.config.parallel_config.disable_custom_all_reduce = False
                elif control == "_ENABLE_CUSTOM_ALL_REDUCE":
                    fixture.state._ENABLE_CUSTOM_ALL_REDUCE = True
                else:
                    setattr(fixture.env, control, True)
                with self.subTest(control=control, group=name, world=world), self.assertRaises(RuntimeError) as caught:
                    self.instantiate_communicator(fixture, name, world)
                self.assertEqual(str(caught.exception), "CC collective controls conflict with required policy")
                self.assertEqual(fixture.calls, [])

    def test_cc_malformed_controls_and_missing_config_are_sanitized_before_construction(self):
        secret = "synthetic-collective-secret\nforged"
        for control in ("disable_custom_all_reduce", "_ENABLE_CUSTOM_ALL_REDUCE", *self.communicator_fixture().flags):
            for value in (None, 0, 1, "0", secret):
                fixture = self.communicator_fixture()
                owner = (fixture.config.parallel_config if control == "disable_custom_all_reduce" else
                         fixture.state if control == "_ENABLE_CUSTOM_ALL_REDUCE" else fixture.env)
                setattr(owner, control, value)
                with self.subTest(control=control, value=value), self.assertRaises(RuntimeError) as caught:
                    self.instantiate_communicator(fixture)
                self.assertEqual(str(caught.exception), "CC collective controls unavailable or malformed")
                self.assertEqual(fixture.calls, [])
        fixture = self.communicator_fixture()
        fixture.cls.__init__.__globals__["get_current_vllm_config_or_none"] = lambda: None
        with self.assertRaisesRegex(RuntimeError, "^CC collective controls unavailable or malformed$"):
            self.instantiate_communicator(fixture)
        self.assertEqual(fixture.calls, [])

    def test_cc_real_env_reader_failures_do_not_leak_values(self):
        secret = "synthetic-env-secret\nforged"
        tree = ast.parse(self.files["vllm/envs.py"])
        table = next(n.value for n in tree.body if isinstance(n, ast.AnnAssign)
                     and ast.unparse(n.target) == "environment_variables")
        getters = {key.value: value for key, value in zip(table.keys, table.values)}
        for flag in self.communicator_fixture().flags:
            fixture = self.communicator_fixture()
            getter = eval(compile(ast.Expression(getters[flag]), "reviewed-env-reader", "eval"),
                          {"os": types.SimpleNamespace(getenv=lambda *args: secret)})
            class Env:
                def __getattr__(self, name):
                    return getter() if name == flag else getattr(fixture.env, name)
            fixture.cls.__init__.__globals__["envs"] = Env()
            try:
                self.instantiate_communicator(fixture)
            except RuntimeError:
                self.assertNotIn(secret, traceback.format_exc())
            else:
                self.fail("malformed environment accepted")
            self.assertEqual(fixture.calls, [])

    def test_non_cc_communicator_preserves_enabled_backend_setup(self):
        fixture = self.communicator_fixture(lambda: False)
        fixture.config.parallel_config.disable_custom_all_reduce = False
        fixture.state._ENABLE_CUSTOM_ALL_REDUCE = True
        for flag in fixture.flags:
            setattr(fixture.env, flag, True)
        comm = self.instantiate_communicator(fixture)
        self.assertEqual(fixture.calls, ["base", "PyNcclCommunicator", "register_nccl_symmetric_ops",
            "SymmMemCommunicator", "FlashInferAllReduce", "FlashInferPcieIpcAllReduce", "CustomAllreduce", "log"])
        self.assertIsNotNone(comm.ca_comm)

    def fusion_fixture(self, detector=lambda: True):
        calls = []
        class Base:
            def __init__(self, config):
                calls.append("base")
                self.model_dtype = "float16"
        def operation(name):
            def call(*args, **kwargs):
                calls.append(name)
                return object()
            return call
        platform = types.SimpleNamespace(is_confidential_compute=detector)
        config = types.SimpleNamespace(model_config=object(), scheduler_config=types.SimpleNamespace(max_num_batched_tokens=16),
            compilation_config=types.SimpleNamespace(pass_config=types.SimpleNamespace(
                fuse_allreduce_rms=True, flashinfer_max_size=lambda size: 32768)))
        cls = constructor(self.files["vllm/compilation/passes/fusion/allreduce_rms_fusion.py"],
            "AllReduceFusionPass", {"VllmPatternMatcherPass": Base,
                "get_tensor_model_parallel_world_size": lambda: 4, "get_tensor_model_parallel_rank": lambda: 0,
                "PatternMatcherPass": operation("matcher"), "_fused_ar_workspace_hidden_dim": lambda c: 5120,
                "get_tp_group": lambda: types.SimpleNamespace(cpu_group="cpu"), "flashinfer_comm": object(),
                "torch": types.SimpleNamespace(tensor=lambda *a, **kw: types.SimpleNamespace(element_size=lambda: 2)),
                "logger": types.SimpleNamespace(debug_once=operation("log")),
                "get_fi_ar_workspace": operation("workspace"), "get_fi_ar_quant_workspace": operation("quant_workspace"),
                "FlashInferFusedAllReduceParams": operation("params")},
            {"vllm.v1.conf_compute_utils": types.SimpleNamespace(
                confidential_collectives_selected=self.collective_selector(platform))})
        cls.register_patterns = lambda self: calls.append("register")
        cls.dump_patterns = lambda *args: calls.append("dump")
        return types.SimpleNamespace(cls=cls, config=config, calls=calls)

    def test_cc_fusion_constructor_blocks_explicit_true_before_workspace_or_registration(self):
        for requested in (True, False, None):
            fixture = self.fusion_fixture()
            fixture.config.compilation_config.pass_config.fuse_allreduce_rms = requested
            with self.subTest(requested=requested), self.assertRaises(RuntimeError) as caught:
                fixture.cls(fixture.config)
            self.assertEqual(str(caught.exception), "AllReduce fusion construction is disabled by the CC candidate")
            self.assertEqual(fixture.calls, [])

    def test_non_cc_fusion_preserves_workspace_and_pattern_setup(self):
        fixture = self.fusion_fixture(lambda: False)
        fixture.cls(fixture.config)
        self.assertEqual(fixture.calls, ["base", "matcher", "log", "workspace", "quant_workspace", "params", "register", "dump"])

    def test_collective_detection_failures_and_nonbooleans_reject_both_constructors(self):
        secret = "synthetic-collective-detection-secret"
        def failure():
            raise RuntimeError(secret)
        detectors = [failure] + [lambda value=value: value for value in (None, 0, 1, "false", object())]
        for detector in detectors:
            for fusion in (False, True):
                fixture = self.fusion_fixture(detector) if fusion else self.communicator_fixture(detector)
                try:
                    fixture.cls(fixture.config) if fusion else self.instantiate_communicator(fixture)
                except RuntimeError as error:
                    self.assertEqual(str(error), "CC collective selection unavailable or malformed")
                    self.assertNotIn(secret, traceback.format_exc())
                else:
                    self.fail("unqualified collective selection accepted")
                self.assertEqual(fixture.calls, [])

    def test_release_buffer_and_async_numpy_copy_semantics_preserved(self):
        path = "vllm/v1/worker/gpu/buffer_utils.py"
        before = ast.parse(self.local["base/" + path])
        after = ast.parse(self.files[path])
        after.body = [n for n in after.body if getattr(n, "name", None) not in ("StagedH2DBuffer", "_pool_buffer_cls")]
        imports = next(n for n in after.body if isinstance(n, ast.ImportFrom)
                       and n.module == "vllm.utils.platform_utils")
        self.assertEqual([n.name for n in imports.names], ["is_pin_memory_available", "is_uva_available"])
        imports.names = [n for n in imports.names if n.name != "is_pin_memory_available"]
        cls = next(n for n in after.body if getattr(n, "name", None) == "UvaBufferPool")
        old = next(n for n in before.body if getattr(n, "name", None) == "UvaBufferPool")
        # Only the pool constructor's class selection changes in the backport.
        constructor = next(n for n in cls.body if getattr(n, "name", None) == "__init__")
        old_constructor = next(n for n in old.body if getattr(n, "name", None) == "__init__")
        def class_choice(node):
            return isinstance(node, ast.Assign) and ast.unparse(node.targets[0]) == "self._buffer_cls"
        selection = next(n for n in constructor.body if class_choice(n))
        self.assertEqual(ast.unparse(selection.value), "_pool_buffer_cls()")
        selection.value = next(n.value for n in old_constructor.body if class_choice(n))
        self.assertEqual(ast.dump(before), ast.dump(after))
        self.assertIn(".to(tl.int64)", self.files[path].decode())
        path = "vllm/v1/worker/gpu/async_utils.py"
        before = definition(self.local["base/" + path], "async_copy_to_np")
        after = definition(self.files[path], "async_copy_to_np")
        self.assertEqual(ast.dump(before), ast.dump(after))
        self.assertIn("copy=True", ast.unparse(after))

    def selection(self, override=None, feature=91, error=None, available=True):
        events, messages = [], []
        def query():
            events.append("query")
            if error is not None:
                raise error
            if feature == "missing":
                return types.SimpleNamespace()
            return types.SimpleNamespace(ccFeature=feature)
        nvml = types.SimpleNamespace(nvmlInit=lambda: events.append("init"),
            nvmlShutdown=lambda: events.append("shutdown"), nvmlSystemGetConfComputeState=query,
            NVML_CC_SYSTEM_FEATURE_ENABLED=91)  # Deliberately not a guessed numeric enum.
        namespace = functions(self.files["vllm/platforms/cuda.py"],
            ["with_nvml_context", "_nvml_confidential_compute_enabled", "_detect_confidential_compute"],
            {"pynvml": nvml, "envs": types.SimpleNamespace(VLLM_CONFIDENTIAL_COMPUTE=override),
             "torch": types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: available)),
             "logger": types.SimpleNamespace(info=lambda text, *args: messages.append(text % args if args else text))})
        return namespace, events, messages

    def mapping_helper(self, detector, xpu=False):
        calls, alias = [], object()
        def selected():
            calls.append("select")
            return detector()
        def mapping(tensor, kind):
            calls.append((kind, tensor))
            return alias
        platform = types.SimpleNamespace(is_xpu=lambda: xpu, is_cuda_alike=lambda: not xpu,
                                         is_confidential_compute=selected)
        torch = types.SimpleNamespace(ops=types.SimpleNamespace(_C=types.SimpleNamespace(
            get_cuda_view_from_cpu_tensor=lambda tensor: mapping(tensor, "cuda"),
            get_xpu_view_from_cpu_tensor=lambda tensor: mapping(tensor, "xpu"))))
        helper = functions(self.files["vllm/utils/torch_utils.py"], ["get_accelerator_view_from_cpu_tensor"],
            {"torch": torch}, {"vllm.platforms": types.SimpleNamespace(current_platform=platform)})
        return helper["get_accelerator_view_from_cpu_tensor"], calls, alias

    def test_confidential_sink_rejects_before_mapping_without_copy_or_tensor_diagnostics(self):
        class PrivateTensor:
            def __repr__(self):
                raise AssertionError("tensor must not enter diagnostics")
        detectors = [lambda: True, self.selection("1")[0]["_detect_confidential_compute"],
                     self.selection()[0]["_detect_confidential_compute"]]
        for detector in detectors:
            helper, calls, _ = self.mapping_helper(detector)
            with self.subTest(detector=detector), self.assertRaises(RuntimeError) as caught:
                helper(PrivateTensor())
            self.assertEqual(str(caught.exception), "CUDA host mapping is disabled by the CC candidate")
            self.assertEqual(calls, ["select"])

    def test_nonconfidential_sink_preserves_raw_mapping_and_returned_alias(self):
        tensor = object()
        helper, calls, alias = self.mapping_helper(lambda: False)
        self.assertIs(helper(tensor), alias)
        self.assertEqual(calls, ["select", ("cuda", tensor)])

    def test_sink_detection_errors_never_map_and_remain_sanitized(self):
        secret = "synthetic-sink-secret\nforged"
        for kwargs in ({"override": secret}, {"error": RuntimeError(secret)}):
            namespace, _, messages = self.selection(**kwargs)
            helper, calls, _ = self.mapping_helper(namespace["_detect_confidential_compute"])
            try:
                helper(object())
            except (ValueError, RuntimeError):
                self.assertNotIn(secret, traceback.format_exc())
            else:
                self.fail("failed detection allowed host mapping")
            self.assertEqual(calls, ["select"])
            self.assertEqual(messages, [])

    def test_xpu_sink_preserves_mapping_without_cc_selection(self):
        tensor = types.SimpleNamespace(numel=lambda: 1, is_pinned=lambda: True)
        helper, calls, alias = self.mapping_helper(lambda: self.fail("XPU must not select CUDA CC"), xpu=True)
        self.assertIs(helper(tensor), alias)
        self.assertEqual(calls, [("xpu", tensor)])

    def test_explicit_copy_selection_is_not_nvml_or_attestation(self):
        namespace, events, messages = self.selection("1", error=Exception("unreachable"))
        self.assertIs(namespace["_detect_confidential_compute"](), True)
        self.assertEqual(events, [])
        self.assertIn("hardware is not attested", messages[0])

    def test_invalid_override_is_rejected_without_logging_it(self):
        for value in ("0", "", "true", "false", " 1", "1\n", "Bearer synthetic-secret\nforged", "2"):
            namespace, events, messages = self.selection(value)
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "must be exactly 1 or unset"):
                namespace["_detect_confidential_compute"]()
            self.assertEqual(events, [])
            self.assertEqual(messages, [])

    def test_nvml_errors_are_sanitized_not_cached_and_never_enable_uva(self):
        namespace, events, messages = self.selection(error=RuntimeError("synthetic-NVML-secret"))
        detector = namespace["_detect_confidential_compute"]
        platform = types.SimpleNamespace(is_cpu=lambda: False, is_confidential_compute=detector)
        uva = functions(self.files["vllm/utils/platform_utils.py"], ["is_uva_available"],
            {"is_pin_memory_available": lambda: True},
            {"vllm.platforms": types.SimpleNamespace(current_platform=platform)})["is_uva_available"]
        for _ in range(2):
            try:
                uva()
            except RuntimeError:
                self.assertNotIn("synthetic-NVML-secret", traceback.format_exc())
            else:
                self.fail("failed detection enabled a software path")
        self.assertEqual(events, ["init", "query", "shutdown"] * 2)
        self.assertEqual(messages, [])

    def test_disabled_unknown_missing_nvml_states_and_cuda_absence_fail(self):
        for feature in (0, 1, 2, -1, True, "91", "missing", None):
            namespace, _, _ = self.selection(feature=feature)
            with self.subTest(feature=feature), self.assertRaises(RuntimeError):
                namespace["_detect_confidential_compute"]()
        namespace, events, _ = self.selection(available=False)
        with self.assertRaises(RuntimeError):
            namespace["_detect_confidential_compute"]()
        self.assertEqual(events, [])

    def test_known_legacy_feature_only_selects_copy_path(self):
        namespace, events, messages = self.selection()
        detector = namespace["_detect_confidential_compute"]
        platform = types.SimpleNamespace(is_cpu=lambda: False, is_confidential_compute=detector)
        uva = functions(self.files["vllm/utils/platform_utils.py"], ["is_uva_available"],
            {"is_pin_memory_available": lambda: True},
            {"vllm.platforms": types.SimpleNamespace(current_platform=platform)})["is_uva_available"]
        self.assertFalse(uva())
        self.assertFalse(uva())
        self.assertEqual(events, ["init", "query", "shutdown"])
        self.assertIn("hardware is not attested", messages[0])

    def test_detection_initialization_shutdown_and_cuda_errors_are_sanitized(self):
        def fail():
            raise RuntimeError("synthetic-detection-secret")
        for stage in ("init", "shutdown", "cuda"):
            namespace, _, messages = self.selection()
            if stage == "cuda":
                namespace["torch"].cuda.is_available = fail
            else:
                setattr(namespace["pynvml"], "nvmlInit" if stage == "init" else "nvmlShutdown", fail)
            try:
                namespace["_detect_confidential_compute"]()
            except RuntimeError:
                self.assertNotIn("synthetic-detection-secret", traceback.format_exc())
            else:
                self.fail("detection failure accepted")
            self.assertEqual(messages, [])

    def test_staged_h2d_waits_and_records_before_consumption(self):
        for use_out in (False, True):
            trace = CopyTrace()
            helper = functions(self.files["vllm/v1/conf_compute_utils.py"], ["staged_h2d"],
                {"torch": trace.torch, "prep_stream": lambda device: trace.prep})["staged_h2d"]
            result = helper(trace.tensor("cpu"), device="cuda:0", out=trace.tensor("out") if use_out else None)
            trace.events.append("consume")
            self.assertEqual(result.name, "out" if use_out else "staged")
            self.assertEqual(trace.events, ["upload", "wait", "record"] +
                             (["copy-compute"] if use_out else []) + ["consume"])

    def test_staged_buffer_waits_and_records_including_slice(self):
        for count in (None, 0, 3):
            trace = CopyTrace()
            helper = functions(self.files["vllm/v1/worker/gpu/buffer_utils.py"], ["StagedH2DBuffer.uva"],
                {"torch": trace.torch},
                {"vllm.v1.conf_compute_utils": types.SimpleNamespace(prep_stream=lambda device: trace.prep)})["uva"]
            result = helper(types.SimpleNamespace(cpu=trace.tensor("cpu"), _gpu=trace.tensor("gpu")), count)
            trace.events.append("consume")
            prefix = [] if count is None else [("slice", "cpu", count), ("slice", "gpu", count)]
            self.assertEqual(trace.events, prefix + ["copy-prep", "wait", "record", "consume"])
            self.assertEqual(result.name, "gpu" if count is None else "gpu-slice")

    def test_fifo_symlink_oversize_and_redirect_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input"
            os.mkfifo(path)
            with self.assertRaises(ValueError):
                CHECKER.read_regular(path)
            path.unlink()
            path.symlink_to(Path(directory))
            with self.assertRaises(OSError):
                CHECKER.read_regular(path)
            path.unlink()
            path.write_bytes(b"too long")
            with self.assertRaises(ValueError):
                CHECKER.read_regular(path, 2)
        with self.assertRaises(ValueError):
            CHECKER.NoRedirect().redirect_request(None, None, None, None, None, None)

    def test_source_fetch_is_fixed_bounded_and_credential_free(self):
        records = []
        expected = {item["base_url"]: self.local["base/" + item["path"]]
                    for item in self.manifest["files"] if item["base_url"]}
        expected.update({item["base_url"]: self.local["audit/" + item["path"]]
                         for item in self.manifest["source_audits"]})
        expected["https://raw.githubusercontent.com/vllm-project/vllm/" + CHECKER.BASE + "/LICENSE"] = self.local["LICENSE"]
        def response(request, timeout):
            self.assertGreater(timeout, 0)
            self.assertLessEqual(timeout, 20)
            self.assertEqual(dict(request.header_items()), {"Accept-encoding": "identity"})
            data = expected[request.full_url]
            records.append(request.full_url)
            stream = io.BytesIO(data)
            stream.status, stream.headers = 200, {"Content-Length": str(len(data))}
            return stream
        opener = types.SimpleNamespace(open=response)
        with patch.dict(os.environ, {"HTTPS_PROXY": "https://synthetic-secret.invalid", "HF_TOKEN": "synthetic-secret"}), \
                patch.object(CHECKER.urllib.request, "build_opener", return_value=opener) as builder:
            proofs = CHECKER.fetch_sources(BUNDLE)
        self.assertEqual(len(proofs), 14)
        self.assertEqual(set(records), set(expected))
        self.assertEqual(builder.call_args.args[0].proxies, {})

    def test_source_size_hash_encoding_and_status_failures(self):
        original = self.local["base/" + CHECKER.PATHS[0]]
        cases = [(b"x" * len(original), {}, 200), (original + b"x", {}, 200),
                 (original[:-1], {}, 200), (original, {"Content-Length": "9999999"}, 200),
                 (original, {"Content-Encoding": "gzip"}, 200), (original, {}, 302)]
        for body, headers, status in cases:
            stream = io.BytesIO(body)
            stream.status, stream.headers = status, headers
            with self.subTest(headers=headers, status=status), \
                    patch.object(CHECKER.urllib.request, "build_opener",
                                 return_value=types.SimpleNamespace(open=lambda *a, **k: stream)), \
                    self.assertRaises(ValueError):
                CHECKER.fetch_sources(BUNDLE)

    def test_cli_requires_isolated_python(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recorder.py"
            path.write_bytes(BUNDLE[CHECKER.SCRIPT])
            result = subprocess.run([sys.executable, str(path)],
                                    capture_output=True, timeout=10, env=CHECKER.tool_env())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"Use python3 -I", result.stderr)

    def test_bootstrap_mismatch_and_bundle_digest_fail_closed(self):
        CHECKER.validate_bootstrap(BUNDLE)
        changed = dict(BUNDLE)
        changed[CHECKER.SCRIPT] += b"\n# changed\n"
        self.assertNotEqual(CHECKER.bundle_digest(changed), CHECKER.bundle_digest(BUNDLE))
        with self.assertRaises(ValueError):
            CHECKER.validate_bootstrap(changed)

    def test_committed_bundle_ignores_replacements_ignored_files_and_git_env(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            CHECKER.git(root, "init", "--quiet")
            for name, data in BUNDLE.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            CHECKER.git(root, "add", ".")
            def commit(message):
                CHECKER.git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                            "-c", "commit.gpgsign=false", "commit", "-qm", message)
                return CHECKER.git(root, "rev-parse", "HEAD").decode().strip()
            original = commit("original")
            (root / CHECKER.SCRIPT).write_bytes(b"forged source")
            CHECKER.git(root, "add", ".")
            forged = commit("forged")
            CHECKER.git(root, "replace", original, forged)
            CHECKER.git(root, "update-ref", "HEAD", original)
            default = subprocess.run(["git", "-C", str(root), "show", original + ":" + CHECKER.SCRIPT],
                env={key: value for key, value in CHECKER.tool_env().items() if key != "GIT_NO_REPLACE_OBJECTS"},
                capture_output=True, check=True, timeout=10)
            self.assertEqual(default.stdout, b"forged source")
            (root / CHECKER.DIRECTORY / "ignored.py").write_bytes(b"forged untracked")
            with patch.dict(os.environ, {"GIT_NO_REPLACE_OBJECTS": "0", "GIT_INDEX_FILE": "/unavailable",
                                         "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "invalid"}):
                revision, bundle = CHECKER.committed_bundle(root)
            self.assertEqual(revision, original)
            self.assertEqual(bundle, BUNDLE)

    def test_missing_promisor_blob_never_invokes_transport(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            CHECKER.git(root, "init", "--quiet")
            for name, data in BUNDLE.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            CHECKER.git(root, "add", ".")
            CHECKER.git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                        "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")
            missing = CHECKER.DIRECTORY + "/base/" + CHECKER.PATHS[0]
            oid = CHECKER.git(root, "rev-parse", "HEAD:" + missing).decode().strip()
            (root / ".git/objects" / oid[:2] / oid[2:]).unlink()
            marker, helper = root / "transport-called", root / "transport.py"
            helper.write_text("from pathlib import Path\nPath(" + repr(str(marker)) + ").touch()\n"
                              "raise SystemExit(1)\n")
            CHECKER.git(root, "config", "remote.origin.url", "ext::" + sys.executable + " -I -B " + str(helper))
            CHECKER.git(root, "config", "remote.origin.promisor", "true")
            CHECKER.git(root, "config", "remote.origin.partialclonefilter", "blob:none")
            CHECKER.git(root, "config", "protocol.ext.allow", "always")
            protected = CHECKER.tool_env()
            self.assertEqual(protected["GIT_NO_LAZY_FETCH"], "1")
            self.assertEqual(protected["GIT_ALLOW_PROTOCOL"], "")
            self.assertEqual(protected["GIT_TERMINAL_PROMPT"], "0")
            # The transport deny also protects Git versions without lazy-fetch control.
            for ignored in (None, "GIT_NO_LAZY_FETCH"):
                env = {key: value for key, value in protected.items() if key != ignored}
                with patch.object(CHECKER, "tool_env", return_value=env), self.assertRaises(ValueError):
                    CHECKER.committed_bundle(root)
                self.assertFalse(marker.exists())
                self.assertFalse((root / "evidence").exists())
            # Positive control: allow only this local helper, never a remote endpoint.
            env = {key: value for key, value in protected.items()
                   if key not in ("GIT_NO_LAZY_FETCH", "GIT_ALLOW_PROTOCOL")}
            result = subprocess.run(["git", "-C", str(root), "cat-file", "-s", oid],
                                    env=env, capture_output=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(marker.exists())


if __name__ == "__main__":
    unittest.main()
