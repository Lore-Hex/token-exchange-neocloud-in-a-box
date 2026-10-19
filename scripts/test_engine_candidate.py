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

    def test_tampered_sources_patches_and_policy_are_rejected(self):
        for name in ("base/vllm/envs.py", CHECKER.PATCHES[0], CHECKER.PATCHES[1], "LICENSE"):
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
        changed = dict(BUNDLE)
        manifest = copy.deepcopy(self.manifest)
        manifest["files"][0]["sha256"][1] = "0" * 64
        changed[CHECKER.DIRECTORY + "/candidate.json"] = json.dumps(manifest).encode()
        with self.assertRaises(ValueError):
            CHECKER.verify(changed)
        changed = dict(BUNDLE)
        changed[CHECKER.DIRECTORY + "/ignored.py"] = b"raise Exception()"
        with self.assertRaises(ValueError):
            CHECKER.verify(changed)

    def test_release_torch_semantics_preserved_outside_added_branch(self):
        path = "vllm/utils/torch_utils.py"
        before = ast.parse(self.local["base/" + path])
        after = ast.parse(self.files[path])
        after.body = [n for n in after.body if getattr(n, "name", None) != "_confidential_compute_enabled"
                      and not (isinstance(n, ast.ImportFrom) and n.module == "functools")]
        function = next(n for n in after.body if getattr(n, "name", None) == "async_tensor_h2d")
        function.body = [n for n in function.body if not (isinstance(n, ast.If)
                         and ast.unparse(n.test) == "_confidential_compute_enabled()")]
        # Whole-module AST equality includes lazy PIN_MEMORY, DP seeds, ultraquant,
        # direct-view helper and every other release function, not just strings.
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
        self.assertEqual(len(proofs), 11)
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


if __name__ == "__main__":
    unittest.main()
