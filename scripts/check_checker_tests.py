#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Required CI coverage for optional-local, pinned-checker synthetic tests."""

import importlib
from importlib import metadata
from pathlib import Path
import unittest

import provider_conformance

ROOT = Path(__file__).resolve().parent.parent
CASES = (
    "test_selected_service_conformance.PinnedCheckerTests",
    "test_selected_service_protocol.PinnedProtocolTests",
    "test_selected_external_conformance.ExternalCheckerTests",
)


def check_dependencies():
    packages = provider_conformance.requirements((ROOT / "scripts/e2e/requirements.lock").read_bytes())
    for name, (version, _) in packages.items():
        if metadata.version(name) != version:
            raise RuntimeError("checker tests require the exact locked package versions")
    importlib.import_module("tr_provider_check")


def required_suite():
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for name in CASES:
        tests = loader.loadTestsFromName(name)
        if loader.errors or not tests.countTestCases():
            raise RuntimeError("required checker test class missing or empty")
        suite.addTests(tests)
    return suite


def main():
    check_dependencies()
    suite = required_suite()
    count = suite.countTestCases()
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if (not result.wasSuccessful() or result.testsRun != count or result.skipped
            or result.expectedFailures):
        raise RuntimeError("required checker tests failed, skipped, or did not all execute")


if __name__ == "__main__":
    main()
