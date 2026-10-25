# SPDX-License-Identifier: Apache-2.0
"""Required CI checker coverage must not inherit optional local skips."""

import io
from pathlib import Path
import unittest
from unittest import mock

import yaml

import check_checker_tests as gate

ROOT = Path(__file__).resolve().parent.parent


class CheckerGateTests(unittest.TestCase):
    def test_all_dependencies_use_existing_hash_locked_requirements(self):
        packages = gate.provider_conformance.requirements((ROOT / "scripts/e2e/requirements.lock").read_bytes())
        with mock.patch.object(gate.metadata, "version", side_effect=lambda name: packages[name][0]) as version, \
                mock.patch.object(gate.importlib, "import_module") as load:
            gate.check_dependencies()
        self.assertEqual(version.call_args_list, [mock.call(name) for name in packages])
        load.assert_called_once_with("tr_provider_check")

    def test_missing_wrong_version_or_broken_import_fails_before_tests(self):
        for failure in (gate.metadata.PackageNotFoundError("missing"), "wrong-version"):
            with self.subTest(failure=type(failure).__name__), \
                    mock.patch.object(gate.metadata, "version") as version, \
                    mock.patch.object(gate, "required_suite") as suite:
                if isinstance(failure, Exception):
                    version.side_effect = failure
                else:
                    version.return_value = failure
                with self.assertRaises((gate.metadata.PackageNotFoundError, RuntimeError)):
                    gate.main()
                suite.assert_not_called()
        packages = gate.provider_conformance.requirements((ROOT / "scripts/e2e/requirements.lock").read_bytes())
        with mock.patch.object(gate.metadata, "version", side_effect=lambda name: packages[name][0]), \
                mock.patch.object(gate.importlib, "import_module", side_effect=ImportError), \
                mock.patch.object(gate, "required_suite") as suite:
            with self.assertRaises(ImportError):
                gate.main()
            suite.assert_not_called()

    def test_exact_three_classes_include_every_current_test(self):
        self.assertEqual(gate.CASES, (
            "test_selected_service_conformance.PinnedCheckerTests",
            "test_selected_service_protocol.PinnedProtocolTests",
            "test_selected_external_conformance.ExternalCheckerTests",
        ))
        suite = gate.required_suite()
        ids = {test.id() for test in suite}
        loader = unittest.TestLoader()
        expected = {test.id() for name in gate.CASES for test in loader.loadTestsFromName(name)}
        self.assertEqual(ids, expected)
        self.assertGreaterEqual(len(ids), 32)

    def test_missing_and_empty_classes_fail_before_runner(self):
        with mock.patch.object(gate, "check_dependencies"), \
                mock.patch.object(gate, "CASES", ("no_such_checker_test_module.Missing",)), \
                mock.patch.object(gate.unittest, "TextTestRunner") as runner:
            with self.assertRaisesRegex(RuntimeError, "missing or empty"):
                gate.main()
            runner.assert_not_called()
        with mock.patch.object(gate, "check_dependencies"), \
                mock.patch.object(gate.unittest.TestLoader, "loadTestsFromName", return_value=unittest.TestSuite()), \
                mock.patch.object(gate.unittest, "TextTestRunner") as runner:
            with self.assertRaisesRegex(RuntimeError, "missing or empty"):
                gate.main()
            runner.assert_not_called()

    def test_external_checker_class_cannot_be_silently_skipped(self):
        suite = gate.required_suite()
        external = next(test for test in suite if test.id().startswith(
            "test_selected_external_conformance.ExternalCheckerTests."))
        result = unittest.TestResult()
        result.testsRun = suite.countTestCases()
        result.addSkip(external, "missing optional checker")
        with mock.patch.object(gate, "check_dependencies"), \
                mock.patch.object(gate, "required_suite", return_value=suite), \
                mock.patch.object(gate.unittest, "TextTestRunner") as runner:
            runner.return_value.run.return_value = result
            with self.assertRaisesRegex(RuntimeError, "failed, skipped"):
                gate.main()

    def test_success_runs_and_skips_failures_errors_expected_failures_fail(self):
        def fail():
            raise AssertionError("synthetic failure")

        def error():
            raise RuntimeError("synthetic error")

        def skip():
            raise unittest.SkipTest("missing optional checker")

        @unittest.expectedFailure
        def expected_failure():
            fail()

        @unittest.expectedFailure
        def unexpected_success():
            pass

        for method, accepted in ((lambda: None, True), (fail, False), (error, False), (skip, False),
                                 (expected_failure, False), (unexpected_success, False)):
            with self.subTest(method=method.__name__):
                class Case(unittest.TestCase):
                    test_case = staticmethod(method)

                suite = unittest.TestSuite([Case("test_case")])
                runner = unittest.TextTestRunner(stream=io.StringIO())
                with mock.patch.object(gate, "check_dependencies"), \
                        mock.patch.object(gate, "required_suite", return_value=suite), \
                        mock.patch.object(gate.unittest, "TextTestRunner", return_value=runner):
                    if accepted:
                        gate.main()
                    else:
                        with self.assertRaisesRegex(RuntimeError, "failed, skipped"):
                            gate.main()

    def test_incomplete_execution_fails_even_with_successful_result(self):
        suite = unittest.TestSuite([unittest.FunctionTestCase(lambda: None)])
        with mock.patch.object(gate, "check_dependencies"), \
                mock.patch.object(gate, "required_suite", return_value=suite), \
                mock.patch.object(gate.unittest, "TextTestRunner") as runner:
            runner.return_value.run.return_value = unittest.TestResult()
            with self.assertRaisesRegex(RuntimeError, "did not all execute"):
                gate.main()

    def test_workflow_runs_unconditionally_with_pinned_wheels_before_full_check(self):
        workflow = yaml.load((ROOT / ".github/workflows/check.yml").read_text(), Loader=yaml.BaseLoader)
        job = workflow["jobs"]["check"]
        self.assertNotIn("if", job)
        self.assertNotIn("continue-on-error", job)
        steps = job["steps"]
        required = [step for step in steps if step.get("name") == "Required pinned checker tests"]
        self.assertEqual(len(required), 1)
        self.assertEqual(set(required[0]), {"name", "run"})
        self.assertEqual(required[0]["run"].splitlines(), [
            'uv venv --python 3.12.13 "$RUNNER_TEMP/checker-tests"',
            'uv pip sync --python "$RUNNER_TEMP/checker-tests/bin/python" \\',
            '  --require-hashes --only-binary=:all: --strict scripts/e2e/requirements.lock',
            '"$RUNNER_TEMP/checker-tests/bin/python" -E -s -B -W error::ResourceWarning scripts/check_checker_tests.py',
        ])
        check = next(step for step in steps if step.get("name") == "Check repository")
        self.assertLess(steps.index(required[0]), steps.index(check))
        self.assertEqual(check["run"], "make check")


if __name__ == "__main__":
    unittest.main()
