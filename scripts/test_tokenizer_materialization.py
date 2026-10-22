# SPDX-License-Identifier: Apache-2.0
"""Synthetic loader/schema fixtures only; not a substitute for pinned image controls."""

import ast
import copy
from contextlib import contextmanager
import hashlib
import importlib.machinery
import os
import subprocess
import sys
import types
import unittest
from unittest import mock

import engine_lab_bootstrap as adapter
import engine_python_bootstrap as helpers
import engine_tokenizer_diagnostic as diagnostic
import engine_tokenizer_parity as parity
import test_engine_cpu_entry as cpu_fixture
import test_engine_tokenizer_diagnostic as diagnostic_fixture
import test_engine_tokenizer_parity as parity_fixture
import test_deepseek_lab as lab_fixture
import yaml

# Small fake classes, not copies of vLLM or Pydantic. Real source hashes are
# replaced only inside these unit fixtures, never in a production entry point.
DONOR = b'''class ChatCompletionRequest:
    def _materialize_tool_calls_after(self):
        for msg in self.messages:
            if not isinstance(msg, dict):
                continue
            tool_calls = msg.get("tool_calls")
            if tool_calls is not None and not isinstance(tool_calls, list):
                msg["tool_calls"] = list(tool_calls)
        return self
ChatCompletionToolsParam = object
'''
TARGET = b'''from vllm.entrypoints.openai.chat_completion.protocol import ChatCompletionToolsParam
def model_validator(*, mode):
    assert mode == "after"
    return lambda f: f
class TokenizeChatRequest:
    pass
'''


def pins(raw, donor=DONOR):
    return {adapter.TOKENIZER_TARGET: (len(raw), hashlib.sha256(raw).hexdigest()),
            adapter.TOKENIZER_DONOR: (len(donor), hashlib.sha256(donor).hexdigest())}


@contextmanager
def binding():
    modules = {}
    for name in (adapter.TOKENIZER_TARGET, adapter.TOKENIZER_DONOR):
        path = "/selected/" + name.replace(".", "/") + ".py"
        module = types.ModuleType(name)
        loader = importlib.machinery.SourceFileLoader(name, path)
        module.__spec__ = importlib.machinery.ModuleSpec(name, loader, origin=path)
        module.__loader__, module.__file__ = loader, path
        modules[name] = module
    target, donor = modules[adapter.TOKENIZER_TARGET], modules[adapter.TOKENIZER_DONOR]
    support = types.SimpleNamespace(require=helpers.require, same_code=helpers.same_code,
                                    read_source=lambda *args: DONOR)
    guard = adapter.TokenizerAdapter("/selected", support)
    with mock.patch.dict(adapter.TOKENIZER_PINS, pins(TARGET)), mock.patch.dict(sys.modules, modules):
        exec(compile(DONOR, donor.__file__, "exec", dont_inherit=True), donor.__dict__)
        guard.begin(target, target.__loader__)
        code = adapter.tokenizer_code(TARGET, target.__file__, helpers.require)
        exec(code, target.__dict__)
        cls = target.TokenizeChatRequest
        cls.__pydantic_validator__ = object()
        cls.__pydantic_decorators__ = types.SimpleNamespace(model_validators={adapter.TOKENIZER_METHOD:
            types.SimpleNamespace(func=getattr(cls, adapter.TOKENIZER_METHOD), info=types.SimpleNamespace(mode="after"))})
        guard.bind(code, importlib.machinery.SourceFileLoader)
        yield guard, target, donor


class AdapterTests(unittest.TestCase):
    def test_unpinned_source_fails_before_compilation(self):
        with self.assertRaises(ValueError):
            adapter.tokenizer_code(TARGET, "/selected/protocol.py", helpers.require)

    def test_transform_only_extends_import_and_adds_delegate(self):
        with mock.patch.dict(adapter.TOKENIZER_PINS, pins(TARGET)), mock.patch.object(adapter, "compile", create=True) as compile_mock:
            adapter.tokenizer_code(TARGET, "/selected/protocol.py", helpers.require)
        tree = compile_mock.call_args.args[0]
        self.assertEqual(len(tree.body), 3)
        self.assertEqual(tree.body[0].names[-1].name, "ChatCompletionRequest")
        self.assertEqual(ast.dump(tree.body[-1].body[0]), ast.dump(ast.Pass()))
        self.assertEqual(ast.dump(tree.body[-1].body[-1]), ast.dump(ast.parse(adapter.TOKENIZER_WRAPPER).body[0]))

    def test_structural_collision_and_duplicate_method_reject_even_with_fixture_pin(self):
        variants = [TARGET + b"\n_nib_tokenizer_check = None\n", TARGET.replace(b"    pass", b"    def _materialize_tool_calls_after(self): pass"),
                    TARGET.replace(b"import ChatCompletionToolsParam", b"import ChatCompletionToolsParam as Other"),
                    TARGET + b"\nclass TokenizeChatRequest: pass\n"]
        for raw in variants:
            with self.subTest(raw=raw[:30]), mock.patch.dict(adapter.TOKENIZER_PINS, pins(raw)), self.assertRaises(ValueError):
                adapter.tokenizer_code(raw, "/selected/protocol.py", helpers.require)

    def test_delegation_materializes_once_preserves_none_and_late_errors(self):
        with binding() as (guard, target, _):
            request = target.TokenizeChatRequest()
            request.messages = [{"role": "assistant", "content": None, "tool_calls": iter([{"id": "a"}])}]
            self.assertIs(request._materialize_tool_calls_after(), request)
            calls = request.messages[0]["tool_calls"]
            self.assertEqual(calls, [{"id": "a"}])
            self.assertIsNone(request.messages[0]["content"])
            self.assertIs(request._materialize_tool_calls_after().messages[0]["tool_calls"], calls)
            self.assertEqual(guard.verified(), adapter.tokenizer_adapter_identity())
            def late():
                yield {"id": "a"}
                raise TypeError("PRIVATE")
            request.messages[0]["tool_calls"] = late()
            with self.assertRaises(TypeError):
                request._materialize_tool_calls_after()
            guard.verified()

    def test_identity_drift_is_sticky_and_does_not_invoke_delegate(self):
        changes = [
            lambda g, t, d: setattr(d.ChatCompletionRequest, adapter.TOKENIZER_METHOD, lambda self: self),
            lambda g, t, d: setattr(getattr(d.ChatCompletionRequest, adapter.TOKENIZER_METHOD), "__code__", (lambda self: self).__code__),
            lambda g, t, d: setattr(t.TokenizeChatRequest, adapter.TOKENIZER_METHOD, lambda self: self),
            lambda g, t, d: setattr(t.TokenizeChatRequest, "__pydantic_validator__", object()),
            lambda g, t, d: setattr(t, "_NibChatCompletionRequest", object()),
            lambda g, t, d: setattr(t, "_nib_tokenizer_check", lambda: None),
            lambda g, t, d: setattr(t.__spec__, "origin", "/foreign.py"),
            lambda g, t, d: setattr(d, "__loader__", object()),
            lambda g, t, d: setattr(d, "list", lambda x: []),
            lambda g, t, d: sys.modules.__setitem__(adapter.TOKENIZER_TARGET, types.ModuleType("foreign")),
            lambda g, t, d: setattr(t.TokenizeChatRequest.__pydantic_decorators__.model_validators[adapter.TOKENIZER_METHOD].info,
                                    "mode", "before"),
        ]
        for change in changes:
            with binding() as (guard, target, donor):
                change(guard, target, donor)
                with self.assertRaises(ValueError):
                    guard.check()
                self.assertEqual(guard.status, "failed")
                with self.assertRaises(ValueError):
                    guard.check()

    def test_donor_code_and_bytes_must_match_before_activation(self):
        for drift in ("bytes", "code"):
            with binding() as (guard, target, donor):
                guard.status = "loading"
                if drift == "bytes":
                    guard.helpers.read_source = lambda *args: DONOR + b"\n"
                else:
                    setattr(donor.ChatCompletionRequest, adapter.TOKENIZER_METHOD, lambda self: self)
                with self.assertRaises(ValueError):
                    guard.bind(adapter.tokenizer_code(TARGET, target.__file__, helpers.require), importlib.machinery.SourceFileLoader)

    def test_no_preimport_or_second_activation(self):
        with binding() as (guard, target, _):
            with self.assertRaises(ValueError):
                guard.begin(target, target.__loader__)
            fresh = adapter.TokenizerAdapter("/selected", guard.helpers)
            with self.assertRaises(ValueError):
                fresh.check()


class FixedControlTests(unittest.TestCase):
    class Invalid(Exception):
        pass

    def request(self, **probe):
        request = types.SimpleNamespace(messages=probe["messages"])
        for msg in request.messages:
            if "tool_calls" in msg:
                calls = list(msg["tool_calls"])
                if any("function" not in call for call in calls):
                    raise self.Invalid()
                msg["tool_calls"] = calls
        request._materialize_tool_calls_after = lambda: request
        return request

    def exercise(self, **overrides):
        args = dict(probes=parity.probes(), request_type=self.request, normalize=copy.deepcopy,
                    encode=diagnostic_fixture.encode, parse=diagnostic_fixture.parse,
                    parse_async=diagnostic_fixture.parse_async, checkpoints=mock.Mock(), check=mock.Mock(),
                    validation_error=self.Invalid)
        args.update(overrides)
        diagnostic.exercise_fixed(**args)

    def test_all_controls_pass_without_probe_mutation(self):
        probes = parity.probes()
        original = copy.deepcopy(probes)
        checkpoints = mock.Mock()
        self.exercise(probes=probes, checkpoints=checkpoints)
        self.assertEqual(probes, original)
        self.assertEqual(checkpoints.emit.call_args_list, [mock.call(i) for i in (12, 13, 14, 15)])

    def test_unpatched_iterator_and_silent_invalid_element_acceptance_fail(self):
        for factory in (diagnostic_fixture.request_type,
                        lambda **p: types.SimpleNamespace(messages=p["messages"], _materialize_tool_calls_after=lambda: None)):
            with self.assertRaises(ValueError):
                self.exercise(request_type=factory)

    def test_negative_control_uses_json_array_not_python_generator(self):
        invalid_seen = []
        def factory(**probe):
            for msg in probe["messages"]:
                if "tool_calls" in msg:
                    self.assertIs(type(msg["tool_calls"]), list)
                    if len(msg["tool_calls"]) == 2 and "function" not in msg["tool_calls"][1]:
                        invalid_seen.append(True)
            return self.request(**probe)
        self.exercise(request_type=factory)
        self.assertEqual(invalid_seen, [True])

    def test_silent_second_item_drop_and_invalid_array_acceptance_reject(self):
        def drop(**probe):
            request = self.request(**probe)
            for msg in request.messages:
                if "tool_calls" in msg:
                    msg["tool_calls"] = msg["tool_calls"][:1]
            return request
        def accept(**probe):
            for msg in probe["messages"]:
                if "tool_calls" in msg:
                    msg["tool_calls"] = [call for call in msg["tool_calls"] if "function" in call]
            return self.request(**probe)
        for factory in (drop, accept):
            with self.assertRaises(ValueError):
                self.exercise(request_type=factory)


class LoaderTests(unittest.TestCase):
    setUp = lab_fixture.BootstrapTests.setUp
    run_fixture = lab_fixture.BootstrapTests.run_fixture

    def fixture(self, body, *, enabled=True, drift=None):
        target = TARGET.replace(b"class TokenizeChatRequest:", b'''from types import SimpleNamespace
class FakeBase:
    def __init_subclass__(cls):
        cls.__pydantic_validator__ = object()
        cls.__pydantic_decorators__ = SimpleNamespace(model_validators={})
        method = getattr(cls, "_materialize_tool_calls_after", None)
        if method is not None:
            cls.__pydantic_decorators__.model_validators["_materialize_tool_calls_after"] = SimpleNamespace(
                func=method, info=SimpleNamespace(mode="after"))
class TokenizeChatRequest(FakeBase):''')
        for name, raw in ((adapter.TOKENIZER_TARGET, target), (adapter.TOKENIZER_DONOR, DONOR)):
            parts = name.split(".")
            for count in range(1, len(parts)):
                directory = self.package.joinpath(*parts[:count])
                directory.mkdir(exist_ok=True)
                (directory / "__init__.py").touch()
            path = self.package.joinpath(*parts).with_suffix(".py")
            path.write_bytes(raw + (b"\nraise AssertionError('must not execute drifted source')\n" if drift == name else b""))
        header = "\nTOKENIZER_PINS = " + repr(pins(target)) + "\n"
        driver = '\nimport json\nfrom pathlib import Path\nLAYOUT = json.loads(Path(__file__).with_name("layout.json").read_text())\n'
        if enabled:
            driver += 'LAYOUT["tokenizer_materialization"] = tokenizer_adapter_identity()\n'
        driver += 'state = install_lab(LAYOUT, sys.modules[__name__])\n' + body + '\nprint(json.dumps({"ok": True}))\n'
        (self.root / "launch.py").write_bytes(self.source + header.encode() + driver.encode())

    def test_actual_finder_routes_only_opted_in_target_and_rejects_reload(self):
        self.fixture('''import importlib
module = importlib.import_module(TOKENIZER_TARGET)
request = module.TokenizeChatRequest()
request.messages = [{"tool_calls": iter([{"id": "fixed"}])}]
assert request._materialize_tool_calls_after() is request
assert request.messages[0]["tool_calls"] == [{"id": "fixed"}]
assert state["tokenizer_adapter_verified"]() == tokenizer_adapter_identity()
try: importlib.reload(module)
except ValueError: pass
else: raise AssertionError("reload accepted")
try: state["check"]()
except ValueError: pass
else: raise AssertionError("failure not sticky")''')
        self.assertEqual(self.run_fixture(), {"ok": True})

    def test_default_loader_does_not_add_validator(self):
        self.fixture('''import importlib
module = importlib.import_module(TOKENIZER_TARGET)
assert not hasattr(module.TokenizeChatRequest, TOKENIZER_METHOD)
assert "tokenizer_adapter_verified" not in state
state["check"]()''', enabled=False)
        self.assertEqual(self.run_fixture(), {"ok": True})

    def test_source_drift_denies_before_execution_and_sticks(self):
        for name in (adapter.TOKENIZER_TARGET, adapter.TOKENIZER_DONOR):
            self.fixture('''import importlib
try: importlib.import_module(TOKENIZER_TARGET)
except ValueError: pass
else: raise AssertionError("drift accepted")
try: state["check"]()
except ValueError: pass
else: raise AssertionError("failure not sticky")''', drift=name)
            self.assertEqual(self.run_fixture(), {"ok": True})


class BindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        diagnostic_fixture.BindingTests.setUpClass()
        cls.fixture = diagnostic_fixture.BindingTests

    def test_original_local_failure_is_byte_preserved_and_not_a_native_or_gpu_pass(self):
        import json
        directory = lab_fixture.ROOT / "evidence/p2/2026-10-22-tokenizer-continuation-local-39851d2"
        expected = {"run.json": "a5c81c9fd6ac53f1cf808a59623acaa5aa35687a94ce9a2d99cfef9f004a4849",
                    "output.log": "e264b1f30a4f969e89751f3df89583100c624d956d57784c00ee1c82bd49f1b9"}
        for name, sha in expected.items():
            self.assertEqual(hashlib.sha256((directory / name).read_bytes()).hexdigest(), sha)
        report = json.loads((directory / "output.log").read_bytes())
        self.assertFalse(report["entry_pass"])
        self.assertEqual(report["cleanup"], "confirmed")
        self.assertEqual(report["experiment"]["entry"]["checkpoints"], {"status": "eof", "last_id": 15})
        self.assertEqual(report["experiment"]["entry"]["exception_diagnostic"]["type"], "type")

    def test_first_fixed_control_failure_is_preserved(self):
        import json
        directory = lab_fixture.ROOT / "evidence/p2/2026-10-22-tokenizer-materialization-local-34f7968"
        expected = {"run.json": "e0e9c4cc7a784ca12e4fb773d6df6d61e98d77d6db9603173e35136d3c1ee8e4",
                    "output.log": "2d5f3ed48a562fe072a87d79fef636a387d7d79745fc29e017c41f4b733bf75e"}
        for name, sha in expected.items():
            self.assertEqual(hashlib.sha256((directory / name).read_bytes()).hexdigest(), sha)
        report = json.loads((directory / "output.log").read_bytes())
        self.assertFalse(report["entry_pass"])
        self.assertFalse(report["experiment"]["adapter_verified"])
        self.assertEqual(report["cleanup"], "confirmed")
        self.assertEqual(report["experiment"]["entry"]["checkpoints"], {"status": "eof", "last_id": 12})
        self.assertEqual(report["experiment"]["entry"]["exception_diagnostic"]["type"], "value")

    def test_cpu_fixed_contract_preserves_application_probe_and_bounds(self):
        tool, f = self.fixture.tool, self.fixture.fixture
        original, before = tool.prepare_files("1" * 40, f.bundle, self.fixture.extra, True)
        fixed, after = tool.prepare_files("1" * 40, f.bundle, self.fixture.extra, True, True)
        self.assertEqual(after["tokenizer_materialization"], adapter.tokenizer_adapter_identity())
        for path in original:
            if path not in ("entry.json", "python"):
                self.assertEqual(original[path], fixed[path])
        self.assertEqual(set(after) - set(before), {"tokenizer_materialization"})
        for key in before:
            self.assertEqual(before[key], after[key])
        with self.assertRaises(ValueError):
            tool.prepare_files("1" * 40, f.bundle, self.fixture.extra, False, True)

    def test_options_reject_wrong_mode_extra_fields_and_identity_types(self):
        valid = {"diagnostic": "tokenizer-continuation", "tokenizer_materialization": adapter.tokenizer_adapter_identity()}
        self.assertTrue(adapter.tokenizer_adapter_options(valid, helpers.require))
        for contract in ({"tokenizer_materialization": valid["tokenizer_materialization"]},
                         {**valid, "tokenizer_materialization": {**valid["tokenizer_materialization"], "extra": 1}},
                         {**valid, "tokenizer_materialization": {**valid["tokenizer_materialization"], "disk_candidate_changed": 0}}):
            with self.assertRaises(ValueError):
                adapter.tokenizer_adapter_options(contract, helpers.require)

    def test_receipt_requires_matching_identity_and_observed_clean_entry(self):
        f, tool = self.fixture.fixture(), self.fixture.tool
        report = f.valid_report()
        report.update(diagnostic="tokenizer-continuation", tokenizer_materialization=adapter.tokenizer_adapter_identity(),
                      adapter_verified=True)
        def validate(row, identity=adapter.tokenizer_adapter_identity()):
            row["application"].update(git_sha=f.application["git_sha"],
                                      source_bundle_sha256=f.application["source_bundle_sha256"])
            return cpu_fixture.cpu.validate(tool.overlay, row, f.application, f.measured, tool.base.smoke, identity)
        self.assertTrue(validate(report))
        for change in ({"adapter_verified": False}, {"tokenizer_materialization": {}}, {"entry_pass": False}):
            with self.assertRaises(ValueError):
                validate({**report, **change})
        with self.assertRaises(ValueError):
            validate(report, None)

    def test_gpu_contract_changes_only_opt_in_adapter_not_probe_arguments_or_bounds(self):
        parity_fixture.HarnessTests.setUpClass()
        fixture = parity_fixture.HarnessTests
        host, f = lab_fixture.host, fixture.fixture
        args = (f.builder, "1" * 40, f.bundle, fixture.extra, b"fake", "local-correctness", "B300")
        baseline_files, baseline = host.make_payload(*args, tokenizer_parity=True)
        files, fixed = host.make_payload(*args, tokenizer_parity=True, tokenizer_materialization=True)
        self.assertEqual(fixed["tokenizer_materialization"], adapter.tokenizer_adapter_identity())
        for path in baseline_files:
            if path not in ("lab.json", "python"):
                self.assertEqual(files[path], baseline_files[path])
        for key in ("arguments", "environment", "paths", "manifest_sha256", "pack_revision", "pack_bytes"):
            self.assertEqual(fixed[key], baseline[key])
        with mock.patch.object(host, "read", return_value=b"synthetic contract digest"):
            self.assertEqual(host.create_command("n", "o", "/bundle", "/weights", baseline, {"config": {"Env": []}}),
                             host.create_command("n", "o", "/bundle", "/weights", fixed, {"config": {"Env": []}}))
        with self.assertRaises(ValueError):
            host.make_payload(*args, tokenizer_materialization=True)
        with mock.patch.object(lab_fixture.lab, "tokenizer_adapter_options", adapter.tokenizer_adapter_options, create=True):
            self.assertEqual(lab_fixture.lab.lab_layout(fixed)["tokenizer_materialization"], fixed["tokenizer_materialization"])
            self.assertNotIn("tokenizer_materialization", lab_fixture.lab.lab_layout(baseline))

    def test_workflow_both_booleans_fail_closed_and_forward_to_validation(self):
        workflow = yaml.load((lab_fixture.ROOT / ".github/workflows/engine-cpu-entry.yml").read_text(), Loader=yaml.BaseLoader)
        job = workflow["jobs"]["diagnostic"]
        self.assertEqual(workflow["on"]["workflow_dispatch"]["inputs"]["tokenizer_materialization"]["default"], "false")
        for step in (job["steps"][4], job["steps"][6]):
            for continuation, fixed in (("true", "true"), ("false", "true"), ("true", ""), ("true", "TRUE")):
                stub = 'docker() { return 0; }\npython3() { printf "%s\\n" "$@"; }\n'
                result = subprocess.run(["bash", "-c", stub + step["run"]], cwd=lab_fixture.ROOT,
                    env={**os.environ, "SLUG": "unit", "TOKENIZER_CONTINUATION": continuation, "TOKENIZER_MATERIALIZATION": fixed},
                    capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0 if continuation == fixed == "true" else 2)
                if result.returncode == 0:
                    self.assertEqual(result.stdout.decode().splitlines()[-2:], ["--tokenizer-continuation", "--tokenizer-materialization"])
                else:
                    self.assertEqual(result.stdout, b"")


if __name__ == "__main__":
    unittest.main()
