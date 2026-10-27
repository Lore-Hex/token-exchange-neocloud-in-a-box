# SPDX-License-Identifier: Apache-2.0
"""Execute workflow selection against local stubs, never sudo/build/boot."""
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

import yaml

from fetch_mkosi_tools import FetchFailure

ROOT = Path(__file__).absolute().parent.parent
WORKFLOW = ".github/workflows/debug-boot.yml"


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.workflow = yaml.safe_load((ROOT / WORKFLOW).read_bytes())
        self.job = self.workflow["jobs"]["boot"]
        self.steps = {step["id"]: step for step in self.job["steps"] if "id" in step}

    def run_step(self, step, network, topology, reply="pass", exit_code=0):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            tools = root / "tools"; tools.mkdir()
            scripts = {"go": "exit 99\n", "readlink": "printf '/compiler/go\\n'\n",
                       "sudo": 'printf "%s\\n" "$@" > "$CAPTURE"\nprintf "%s\\n" "$REPLY"\nexit "$CHILD_EXIT"\n'}
            for name, body in scripts.items():
                path = tools / name
                path.write_text("#!/bin/sh\n" + body)
                path.chmod(0o500)
            capture, output = root / "args", root / "output"
            result = subprocess.run(["/bin/bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", self.steps[step]["run"]],
                cwd=root, env={"PATH": str(tools) + ":/usr/bin:/bin", "HOME": str(root), "LANG": "C", "LC_ALL": "C",
                    "GUEST_NETWORK": network, "DEBUG_TOPOLOGY": topology, "GITHUB_OUTPUT": str(output),
                    "CAPTURE": str(capture), "REPLY": reply, "CHILD_EXIT": str(exit_code)},
                stdin=subprocess.DEVNULL, capture_output=True, timeout=5)
            return result, capture.read_text().splitlines() if capture.exists() else None, output.read_text() if output.exists() else "", str(root)

    def test_manual_default_off_unchanged_host_permissions_and_limits(self):
        self.assertEqual(set(self.workflow[True]), {"workflow_dispatch"})
        inputs = self.workflow[True]["workflow_dispatch"]["inputs"]
        self.assertEqual(set(inputs), {"debug_boot", "guest_network", "debug_topology"})
        for option in inputs.values():
            self.assertEqual(option["type"], "boolean")
            self.assertIs(option["default"], False)
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})
        self.assertEqual(self.workflow["concurrency"], {"group": "bounded-cpu-debug-boot", "cancel-in-progress": False})
        self.assertEqual(self.job["if"], "github.ref == 'refs/heads/main' && inputs.debug_boot")
        self.assertEqual((self.job["runs-on"], self.job["timeout-minutes"]), ("ubuntu-22.04", 35))
        self.assertEqual((self.steps["build"]["timeout-minutes"], self.steps["debug"]["timeout-minutes"]), (11, 18))
        self.assertEqual(self.steps["unused_export"]["timeout-minutes"], 5)
        self.assertIn("600s", self.steps["build"]["run"])
        raw = (ROOT / WORKFLOW).read_text()
        for forbidden in ("continue-on-error", "secrets.", "self-hosted", "workflow_run", "--admin", "gh run cancel"):
            self.assertNotIn(forbidden, raw)

    def test_finite_profile_gate_precedes_checkout_setup_and_fetch(self):
        self.assertEqual(self.job["steps"][0], self.steps["profile"])
        self.assertEqual(self.steps["profile"]["timeout-minutes"], 1)
        for name in ("profile", "debug"):
            self.assertEqual(self.steps[name]["env"], {"GUEST_NETWORK": "${{ inputs.guest_network }}",
                                                      "DEBUG_TOPOLOGY": "${{ inputs.debug_topology }}"})
        for network, topology in (("false", "false"), ("true", "false"), ("true", "true")):
            result, args, output, _ = self.run_step("profile", network, topology)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b"", b""))
            self.assertIsNone(args)
            self.assertEqual(output, "")

    def test_invalid_combinations_never_reach_compiler_launcher_or_metadata(self):
        invalid = [("false", "true")]
        invalid += [(bad, "false") for bad in ("", "TRUE", "1", "true\n", "true:false", "$(exit 0)")]
        invalid += [("true", bad) for bad in ("", "FALSE", "0", "false\n", "true:true", "; exit 0")]
        for step in ("profile", "debug"):
            for network, topology in invalid:
                with self.subTest(step=step, network=network, topology=topology):
                    result, args, output, _ = self.run_step(step, network, topology)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual((result.stdout, result.stderr), (b"", b""))
                    self.assertIsNone(args)
                    self.assertEqual(output, "")

    def test_selected_and_legacy_launcher_arguments_are_exact(self):
        for network, topology, options in (("false", "false", []), ("true", "false", ["--guest-network"]),
                                          ("true", "true", ["--guest-network", "--debug-topology"])):
            result, args, output, root = self.run_step("debug", network, topology)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b"", b""))
            self.assertEqual(args, ["--", "/usr/bin/python3.10", "-I", "-S", "-B", "host/run-debug-boot.py",
                "--downloads", root + "/build/native-install/inputs", "--archive", root + "/build/native-install/source.tar.gz",
                "--runtime-inputs", root + "/build/debug-boot/runtime-inputs", "--go", "/compiler/go", *options])
            self.assertEqual(output, "metadata_ready=true\n")

    def test_failed_metadata_retained_but_unknown_or_killed_owner_never_authorizes_upload(self):
        for reply, code, expected_output in (("fail", 0, "metadata_ready=true\n"), ("unknown", 0, ""),
                                             ("pass", 1, ""), ("pass", 137, "")):
            result, args, output, _ = self.run_step("debug", "true", "true", reply, code)
            self.assertNotEqual(result.returncode, 0)
            self.assertIsNotNone(args)
            self.assertEqual(output, expected_output)
        self.assertEqual(self.steps["unused_export"]["if"],
                         "${{ always() && steps.profile.outcome == 'success' && steps.debug.outcome == 'skipped' }}")
        uploads = [step for step in self.job["steps"] if step.get("uses", "").startswith("actions/upload-artifact@")]
        self.assertEqual(len(uploads), 2)
        for step in uploads:
            self.assertIn("metadata_ready == 'true'", step["if"])
            self.assertTrue(all(path.endswith(".json") and "*" not in path for path in step["with"]["path"].splitlines()))

    def test_original_native_build_and_cleanup_do_not_acquire_topology_options(self):
        for name in ("build", "unused_export"):
            self.assertEqual(self.steps[name]["env"]["GUEST_NETWORK"], "${{ inputs.guest_network }}")
            self.assertNotIn("DEBUG_TOPOLOGY", self.steps[name]["env"])
            self.assertNotIn("topology", self.steps[name]["run"])
        self.assertEqual(sum("host/run-debug-boot.py" in step.get("run", "") for step in self.job["steps"]), 1)
        self.assertNotIn("always()", self.steps["debug"].get("if", ""))

    def test_typed_fetch_failure_reaches_log_and_still_stops_following_work(self):
        step = next(row for row in self.job["steps"] if row.get("name") == "Fetch only authenticated public inputs")
        command = step["run"][step["run"].index("/usr/bin/python3.10 -I -S -B scripts/fetch_mkosi_tools.py"):]
        command = command.replace("/usr/bin/python3.10", shlex.quote(sys.executable))
        report = FetchFailure("archives/" + "a" * 64 + ".deb", 17, "a" * 64, "process-exit", 28).report
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "scripts").mkdir()
            (root / "build/native-install").mkdir(parents=True)
            (root / "scripts/fetch_mkosi_tools.py").write_text(
                "import json\nprint(json.dumps(" + repr(report) + ", sort_keys=True))\nraise SystemExit(1)\n")
            (root / "scripts/fetch_debug_boot_runtime.py").write_text(
                "from pathlib import Path\nPath('unexpected-runtime-fetch').touch()\n")
            result = subprocess.run(["/bin/bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", command],
                cwd=root, env={"PATH": "/usr/bin:/bin", "HOME": str(root), "LANG": "C", "LC_ALL": "C"},
                stdin=subprocess.DEVNULL, capture_output=True, timeout=5)
            self.assertEqual((result.returncode, result.stderr), (1, b""))
            self.assertEqual(result.stdout, (json.dumps(report, sort_keys=True) + "\n").encode())
            self.assertFalse((root / "unexpected-runtime-fetch").exists())
            self.assertFalse((root / "build/native-install/input-fetch.log").exists())


if __name__ == "__main__":
    unittest.main()
