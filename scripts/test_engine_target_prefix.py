# SPDX-License-Identifier: Apache-2.0
"""Fake tokenizer boundary controls, not actual original-policy fit evidence."""

import copy
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

import engine_target_prefix as fit
import engine_tokenizer_parity as parity
import engine_local_correctness as correctness
import tau_agent_prefix as prefix
import test_tau_agent_prefix as fixtures
import test_engine_cpu_entry as cpu_tests

ROOT = Path(__file__).resolve().parent.parent


class PrefixCountTests(unittest.TestCase):
    def counter(self, count):
        tokenizer, tool = mock.Mock(), mock.Mock()
        tokenizer.apply_chat_template.side_effect = [[1]] * 4 + [[2] * count, "PRIVATE-render"]
        tokenizer.encode.side_effect = [[3], [2] * count, [2] * count + [3]]
        tool.return_value.model_dump.return_value = {"type": "function", "function": {}}
        vocabulary = {"normalizer": {"type": "Sequence", "normalizers": []},
            "added_tokens": [{"id": 3, "content": "<user>", "special": False,
            "single_word": False, "lstrip": False, "rstrip": False, "normalized": True}]}
        return tokenizer, tool, vocabulary

    def measure(self, tokenizer, tool, vocabulary):
        return fit.measure(fixtures.body(), tokenizer, tool, parity, correctness, prefix, vocabulary, "<user>")

    def test_boundary_output_reserve_and_no_truncation_or_full_fit_claim(self):
        for count, outcome in ((3840, "full-fit-unproven"), (3841, "context-exceeded"), (8192, "context-exceeded")):
            tokenizer, tool, vocabulary = self.counter(count)
            row = self.measure(tokenizer, tool, vocabulary)
            self.assertEqual((row["prefix_tokens"], row["result"]), (count, outcome))
            self.assertFalse(row["full_fit_proven"] or row["generation_performed"])
            self.assertNotIn("PRIVATE", fit.encoded(row).decode())
            for call in tokenizer.apply_chat_template.call_args_list:
                self.assertNotIn("truncation", call.kwargs)
                self.assertNotIn("max_length", call.kwargs)
            self.assertEqual(tokenizer.apply_chat_template.call_args.args, (fixtures.body()["messages"],))
            self.assertEqual(tokenizer.encode.call_args.args, ("PRIVATE-render<user>",))

    def test_boundary_and_result_drift_fail_closed(self):
        for change in (lambda v: v["added_tokens"][0].update(lstrip=True),
                       lambda v: v["added_tokens"][0].update(normalized=False),
                       lambda v: v["added_tokens"].append({"content": "<user>continued"}),
                       lambda v: v["added_tokens"].append({"content": ">continued"}),
                       lambda v: v["added_tokens"].append(copy.deepcopy(v["added_tokens"][0]))):
            tokenizer, tool, vocabulary = self.counter(20)
            change(vocabulary)
            with self.assertRaises(ValueError):
                self.measure(tokenizer, tool, vocabulary)
        tokenizer, tool, vocabulary = self.counter(20)
        tokenizer.encode.side_effect = [[3], [2] * 20, [999]]
        with self.assertRaises(ValueError):
            self.measure(tokenizer, tool, vocabulary)
        row = self.measure(*self.counter(20))
        for change in (lambda r: r.update(full_fit_proven=True), lambda r: r.update(prefix_tokens=True),
                       lambda r: r.update(result="fits"), lambda r: r.update(response="PRIVATE"),
                       lambda r: r.update(boundary_verified=False), lambda r: r.update(output_reserve=1)):
            altered = copy.deepcopy(row)
            change(altered)
            with self.assertRaises(ValueError):
                fit.validate(altered, parity, correctness)
        with self.assertRaises(ValueError):
            fit.validate(row, parity, correctness, "b" * 64)

    def test_prefix_payload_is_opt_in_and_source_bound(self):
        cpu_tests.ContractTests.setUpClass()
        tool = cpu_tests.tool
        extra = dict(cpu_tests.ContractTests.extra)
        for name in tool.PREFIX_FILES:
            extra[name] = (ROOT / name).read_bytes()
        # Synthetic model bytes only; the production pin checker is exercised
        # separately with wrong real-size inputs below, never bypassed in a run.
        files = {"tokenizer.json": b"synthetic", "tokenizer_config.json": b"{}"}
        model_pins = {name: (len(raw), tool.overlay.digest(raw)) for name, raw in files.items()}
        raw = extra[tool.PREFIX_FILES[1]]
        extra[tool.PREFIX_FILES[1]] += ("\nMODEL_FILES = " + repr(model_pins) + "\n").encode()
        payload, contract = tool.prepare_files("1" * 40, cpu_tests.ContractTests.bundle, extra,
                                               agent_prefix=fixtures.body(), tokenizer_files=files)
        self.assertEqual(contract["diagnostic"], fit.MODE)
        self.assertEqual(contract["sources"]["agent-prefix.json"], prefix.digest(prefix.encoded(fixtures.body())))
        for name, data in payload.items():
            if name.startswith("model/"):
                self.assertEqual(contract["model_files"][name.removeprefix("model/")],
                                 {"bytes": len(data), "sha256": tool.overlay.digest(data)})
            elif name not in ("python", "entry.json"):
                self.assertEqual(contract["sources"][name], tool.overlay.digest(data))
        extra[tool.PREFIX_FILES[1]] = raw
        with self.assertRaises(ValueError):
            tool.prepare_files("1" * 40, cpu_tests.ContractTests.bundle, extra,
                               agent_prefix=fixtures.body(), tokenizer_files=files)
        with self.assertRaises(ValueError):
            tool.prepare_files("1" * 40, cpu_tests.ContractTests.bundle, extra, tokenizer_continuation=True,
                               agent_prefix=fixtures.body(), tokenizer_files=files)
        default = tool.prepare_files("1" * 40, cpu_tests.ContractTests.bundle, cpu_tests.ContractTests.extra)
        self.assertEqual(default, (cpu_tests.ContractTests.files, cpu_tests.ContractTests.contract))

    def test_optional_private_result_pipe_and_missing_result_never_grant_success(self):
        cpu, lab = cpu_tests.cpu, cpu_tests.lab
        popen = subprocess.Popen
        expected = self.measure(*self.counter(20))
        for report in (fit.encoded(expected), b"", b"x" * 4097):
            def start(argv, **kwargs):
                self.assertEqual(argv[6], "--prefix-fd")
                self.assertEqual(kwargs["pass_fds"], tuple(int(argv[n]) for n in (3, 5, 7)))
                packet = b"\0\0" + cpu_tests.finder_same() + bytes(cpu.EXCEPTION_PACKET_BYTES)
                program = ("import os,sys\nos.write(int(sys.argv[1]),bytes(range(1,17)))\n"
                           "os.write(int(sys.argv[2])," + repr(packet) + ")\n"
                           "os.write(int(sys.argv[3])," + repr(report) + ")\n")
                return popen([sys.executable, "-I", "-S", "-B", "-c", program, argv[3], argv[5], argv[7]], **kwargs)
            with mock.patch.object(cpu.subprocess, "Popen", side_effect=start):
                row = cpu.observe_entry(lab, agent_prefix=True)
            self.assertEqual(row["agent_prefix"], expected if report == fit.encoded(expected) else None)
            self.assertNotIn("PRIVATE", fit.encoded(row).decode())
        with mock.patch.object(cpu.subprocess, "Popen", side_effect=OSError("PRIVATE")) as start:
            row = cpu.observe_entry(lab, agent_prefix=True)
        self.assertIsNone(row["agent_prefix"])
        for fd in start.call_args.kwargs["pass_fds"]:
            with self.assertRaises(OSError):
                os.fstat(fd)

    def test_source_bound_report_validator_rejects_unearned_or_wrong_request_count(self):
        cpu_tests.ContractTests.setUpClass()
        baseline = cpu_tests.ContractTests()
        row = baseline.valid_report()
        row["application"].update(git_sha=baseline.application["git_sha"],
                                 source_bundle_sha256=baseline.application["source_bundle_sha256"])
        sources = {name: (ROOT / source).read_bytes() for name, source in zip(
            ("target-prefix.py", "tokenizer-parity.py", "correctness.py", "agent-prefix.py"), cpu_tests.tool.PREFIX_FILES)}
        sources["agent-prefix.json"] = prefix.encoded(fixtures.body())
        row["diagnostic"] = fit.MODE
        row["entry"]["agent_prefix"] = self.measure(*self.counter(20))
        args = (cpu_tests.tool.overlay, row, baseline.application, baseline.measured, cpu_tests.tool.base.smoke)
        self.assertTrue(cpu_tests.cpu.validate(*args, prefix_sources=sources))
        with self.assertRaises(ValueError):
            cpu_tests.cpu.validate(*args)
        for change in (lambda r: r["entry"].update(agent_prefix=None),
                       lambda r: r["entry"]["agent_prefix"].update(request_sha256="e" * 64),
                       lambda r: r["entry"]["agent_prefix"].update(raw_content="PRIVATE"),
                       lambda r: r["entry"].update(exit_code=1)):
            altered = copy.deepcopy(row)
            change(altered)
            with self.assertRaises(ValueError):
                cpu_tests.cpu.validate(cpu_tests.tool.overlay, altered, baseline.application, baseline.measured,
                                       cpu_tests.tool.base.smoke, prefix_sources=sources)


if __name__ == "__main__":
    unittest.main()
