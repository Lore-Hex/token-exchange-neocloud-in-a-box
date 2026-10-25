#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Cross-language pin/predicate parity; never executes vendor binaries."""
import ast
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


def literals(path):
    result = {}
    for node in ast.parse((ROOT / path).read_text()).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                result[node.targets[0].id] = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                pass
    return result


class GuestAppraisalContractTests(unittest.TestCase):
    def test_vendor_and_guest_payload_pins_match(self):
        host = literals("host/attest-lab-gpus.py")
        probe = literals("scripts/mkosi_appraisal_probe.py")
        runtime = (ROOT / "guest/appraisal/runtime_linux.go").read_text()
        claims = (ROOT / "guest/appraisal/claims.go").read_text()
        self.assertIn('const sdkSource = "' + host["SOURCE"] + '"', claims)
        for name, (size, digest) in probe["FILES"].items():
            self.assertEqual(host["FILES"]["/" + name], (size, digest))
            self.assertIn('"' + digest + '"', runtime)
            self.assertIn(str(size), runtime)
        self.assertIn('const libraryAlias = "/' + probe["ALIAS"] + '"', runtime)
        for lock in ("guest-runtime-ubuntu2404.json", "r595-ubuntu2404.json"):
            value = json.loads((ROOT / "image/inputs" / lock).read_text())
            packages = {row["Package"]: row for row in value["packages"]}
            for name in ("nvattest", "libnvat"):
                self.assertEqual(packages[name]["Version"], "2.0.0.1790976895-1")

    def test_security_predicates_match_existing_appraiser(self):
        host = literals("host/attest-lab-gpus.py")
        claims = (ROOT / "guest/appraisal/claims.go").read_text()
        body = re.search(r"var trueClaims = \[\]string\{(.*?)\n\}", claims, re.S).group(1)
        self.assertEqual(re.findall(r'"([^"\n]+)"', body), list(host["TRUE_CLAIMS"]))
        defaults = re.search(r"for key, value := range map\[string\]any\{(.*?)\} \{", claims, re.S).group(1)
        self.assertEqual(re.findall(r'"([^"\n]+)"\s*:', defaults), list(host["SIGNED_SERIALIZATION_DEFAULTS"]))
        self.assertIn('"x-nvidia-device-type": "gpu"', defaults)
        self.assertIn('"x-nvidia-gpu-claims-version": "4.0"', defaults)
        for field in ("x-nvidia-mismatch-measurement-records", "x-nvidia-mismatch-opaque-data-records"):
            self.assertIn('"' + field + '": nil', defaults)
        self.assertIn('"x-nvidia-gpu-driver-version": "595.91.07"', claims)

    def test_collector_does_not_enter_image_or_production_gate(self):
        for directory in ("image", "guest/systemd", "guest/cmd"):
            for path in (ROOT / directory).rglob("*"):
                if path.is_file() and path.suffix in (".go", ".service", ".sh", ".conf"):
                    self.assertNotIn(b"guest/appraisal", path.read_bytes(), path)
                    self.assertNotIn(b"nib-h100-appraisal", path.read_bytes(), path)
        self.assertIn("GPU cryptographic appraisal is not implemented",
                      (ROOT / "internal/verify/verify.go").read_text())


if __name__ == "__main__":
    unittest.main()
