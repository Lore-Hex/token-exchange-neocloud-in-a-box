# SPDX-License-Identifier: Apache-2.0
"""Real graph-mode correctness evidence; no throughput inference from tiny cases."""

import test_selected_correctness_evidence as baseline


class SelectedGraphsCorrectnessEvidenceTests(baseline.SelectedCorrectnessEvidenceTests):
    directory = baseline.ROOT / "evidence/p2/2026-10-24-selected-graphs-correctness"
    hashes = {
        "run.json": "f1172fb0ca1b1b12ed57428b1f0625f7f10c823bd29267bcc14b3a4d3412957f",
        "output.log": "db0572d2eff960041ef5394011146a1170f603f7e81975ea8c50d0d666153900",
        "input-contract.json": "b0ccf3be7a0fa4ed143701b3ca78ce387ebae080ea25e007917fa008ca4861ed",
        "receipt.json": "a9546820bdd143e488fd48ab97775196929d21e18025b446d6676c44241c123a",
    }
    execution_profile, sequences = "batch8-graphs", "8"
    request_ms, startup_ms = 13454, 1237458
    window = ("2026-10-24T10:23:07Z", "2026-10-24T10:50:04Z")
